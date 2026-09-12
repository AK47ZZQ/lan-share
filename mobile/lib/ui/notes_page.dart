// MD 笔记（v0.17 新功能，v0.18 完善）：服务端任意目录下的 .md/.txt 即笔记。
// · 列表：默认打开上次用的目录（prefs 记忆），可换目录（DirPickerPage）；
//   顶部搜索按文件名过滤；长按条目可重命名/删除（服务端 rename/回收站）
// · 新建：输标题 → 选目录 → 进编辑器（保存时 multipart 直传）
// · 编辑：拉取远端内容 → 全屏 md 源码编辑 → 保存覆盖上传；脏态返回拦确认；
//   md 可在源码/渲染间切换；每次输入自动存本地草稿，崩溃/误退可恢复
// · 预览联动：DocViewerPage 看文本时点编辑笔进编辑器
import 'package:flutter/material.dart';
import 'package:flutter/services.dart'
    show Clipboard, ClipboardData;
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/errors.dart';
import '../data/api/files_api.dart';
import '../data/api/notes_api.dart';
import 'doc_viewer.dart';
import 'dir_picker_page.dart';
import 'note_editor_page.dart';
import 'notes_models.dart';
import 'notes_shared.dart';
import 'rich_note_editor.dart';
import 'theme/design_tokens.dart';
import 'widgets/kit.dart';

// 编辑器已拆往 note_editor_page.dart；此处 export 保 doc_viewer 等既有
// `import 'notes_page.dart'` 的调用方零改动（backup_settings 拆分同款门面）。
export 'note_editor_page.dart';

/// 把笔记编辑器 push 出去的便捷入口（doc_viewer/notes_page 共用）。
/// v0.19：.html 是富文本笔记，路由到富文本编辑器。
Future<void> openNoteEditor(BuildContext context,
    {required String dir, required String name, bool isNew = false}) async {
  final n = name.toLowerCase();
  if (n.endsWith('.html') || n.endsWith('.htm')) {
    await openRichNoteEditor(context, dir: dir, name: name, isNew: isNew);
    return;
  }
  await Navigator.push(context, MaterialPageRoute(
      builder: (_) => NoteEditorPage(dir: dir, name: name, isNew: isNew)));
}

class NotesPage extends StatefulWidget {
  const NotesPage({super.key});

  @override
  State<NotesPage> createState() => _NotesPageState();
}

class _NotesPageState extends State<NotesPage> {
  String? _dir;
  List<Note>? _notes;
  bool _loading = true;
  String? _error;
  // v0.18：搜索（标题+摘要本地过滤，摘要来自 /api/notes/list）与条目长按管理
  bool _searching = false;
  final _searchCtrl = TextEditingController();
  // v0.20 多选：Set 非空即选择模式（与照片墙/文件页同款约定），
  // 长按进入，AppBar 切换，底部操作栏批量删除
  final _selected = <String>{};
  bool get _selecting => _selected.isNotEmpty;
  // v0.21 分类体系（改进建议 9.6-P1-7 方案 B）：
  //   分类 = 笔记目录下子文件夹（嵌套），数据来自 /api/notes/list 聚合
  String? _catFilter;            // null=全部分类 ''=未分类 其他=分类路径
  // v0.25：服务端下发的分类清单（path→后代笔记数）。**含空分类**——
  // 旧版只从笔记的 category 聚合，空分类永远不出现（新建分类"显示成功
  // 但侧栏看不到"的根因）。
  final Map<String, int> _serverCats = {};
  bool _favOnly = false;         // 只看收藏
  String _sort = 'modified';     // modified | created | name
  Set<String> _pinned = {};      // 置顶（本地偏好，存完整路径）
  Set<String> _starred = {};     // 收藏星标（本地偏好）
  bool _catsOpen = false;        // 左侧分类栏展开
  final _expandedCats = <String>{}; // 侧栏树展开的分类路径
  static const _kSort = 'notes_sort';
  static const _kPinned = 'notes_pinned';
  static const _kStarred = 'notes_starred';

  @override
  void initState() {
    super.initState();
    _restore();
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadMarks() async {
    final sp = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _pinned = (sp.getStringList(_kPinned) ?? const []).toSet();
      _starred = (sp.getStringList(_kStarred) ?? const []).toSet();
      _sort = sp.getString(_kSort) ?? 'modified';
    });
  }

  Future<void> _saveMarks() async {
    final sp = await SharedPreferences.getInstance();
    await sp.setStringList(_kPinned, _pinned.toList());
    await sp.setStringList(_kStarred, _starred.toList());
  }

  /// 分类路径全集 = 服务端 categories（**含空分类**）∪ 笔记的 category
  Set<String> get _allCatPaths {
    final paths = <String>{};
    for (final n in _notes ?? const <Note>[]) {
      if (n.category.isNotEmpty) paths.add(n.category);
    }
    paths.addAll(_serverCats.keys);
    return paths;
  }

  /// 分类树（从分类路径全集聚合）：嵌套子路径合并成节点
  List<CatNode> get _catTree {
    final root = <CatNode>{};
    for (final cat in _allCatPaths) {
      var cur = root;
      var acc = '';
      for (final seg in cat.split('/')) {
        acc = acc.isEmpty ? seg : '$acc/$seg';
        final hit = cur.where((c) => c.name == seg).toList();
        final node = hit.isEmpty
            ? () {
                final nn = CatNode(seg, acc);
                cur.add(nn);
                return nn;
              }()
            : hit.first;
        cur = node.children;
      }
    }
    List<CatNode> toList(Set<CatNode> s) {
      final l = s.toList()..sort((a, b) => a.name.compareTo(b.name));
      for (final c in l) {
        c.childList = toList(c.children);
      }
      return l;
    }
    return toList(root);
  }

  int _countOf(String cat) {
    if (cat.isEmpty) {
      return (_notes ?? const <Note>[]).where((n) => n.category.isEmpty).length;
    }
    return (_notes ?? const <Note>[])
        .where((n) => n.category == cat || n.category.startsWith('$cat/'))
        .length;
  }

  List<Note> get _filtered {
    var all = _notes ?? const <Note>[];
    final q = _searchCtrl.text.trim().toLowerCase();
    if (q.isNotEmpty) {
      all = all
          .where((n) =>
              n.name.toLowerCase().contains(q) ||
              n.summary.toLowerCase().contains(q))
          .toList();
    }
    if (_favOnly) all = all.where((n) => _starred.contains(n.path)).toList();
    if (_catFilter != null) {
      all = all.where((n) {
        if (_catFilter!.isEmpty) return n.category.isEmpty;
        return n.category == _catFilter! ||
            n.category.startsWith('$_catFilter/');
      }).toList();
    }
    List<Note> sorted;
    switch (_sort) {
      case 'name':
        sorted = all.toList()
          ..sort((a, b) => a.name.toLowerCase().compareTo(b.name.toLowerCase()));
        break;
      case 'created':
        sorted = all.toList()..sort((a, b) => b.ctime.compareTo(a.ctime));
        break;
      default:
        sorted = all.toList()..sort((a, b) => b.mtime.compareTo(a.mtime));
    }
    // 置顶始终最前（组内保持原排序）
    sorted.sort((a, b) {
      final pa = _pinned.contains(a.path) ? 0 : 1;
      final pb = _pinned.contains(b.path) ? 0 : 1;
      return pa.compareTo(pb);
    });
    return sorted;
  }

  /// 目录展示名：取路径最后一段作分类名；目录名本身叫"笔记"时返回 null
  ///（与 AppBar 的"笔记"前缀重复，避免出现"笔记 · 笔记"）
  String? get _dirLabel {
    final d = _dir;
    if (d == null || d.isEmpty) return null;
    final seg = d.split('/').where((s) => s.trim().isNotEmpty).toList();
    if (seg.isEmpty) return null;
    return seg.last == '笔记' ? null : seg.last;
  }

  Future<void> _restore() async {
    await _loadMarks();
    final sp = await SharedPreferences.getInstance();
    final d = sp.getString(kNotesLastDir);
    if (!mounted) return;
    if (d == null || d.isEmpty) {
      setState(() { _dir = null; _loading = false; });
    } else {
      _pickDir(d);
    }
  }

  Future<void> _pickDir(String dir) async {
    setState(() { _dir = dir; _loading = true; _error = null; });
    (await SharedPreferences.getInstance()).setString(kNotesLastDir, dir);
    _load();
  }

  Future<void> _chooseDir() async {
    final d = await Navigator.push<String>(context, MaterialPageRoute(
        builder: (_) => const DirPickerPage(title: '选择笔记目录')));
    if (d == null || !mounted) return;
    _pickDir(d);
  }

  Future<void> _load() async {
    try {
      // v0.21：聚合列表端点——一次拿到全部笔记（含子分类与前 200 字摘要），
      // 分类/移动/重命名/删除仍走 files 通道
      final (items, cats) = await NotesApi.list(dir: _dir!, sort: _sort);
      final notes = [for (final e in items) Note.fromJson(e as Map)];
      if (!mounted) return;
      setState(() {
        _notes = notes;
        _serverCats
          ..clear()
          ..addAll(cats);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      // 404 = 记忆的上次目录已被删除（电脑端删/改名是常态，bug·0663 实锤）：
      // 回退到"选择目录"起始态并清记忆，不再把 DioException 原文甩用户脸上
      if (isNotFound(e) && (_dir?.isNotEmpty ?? false)) {
        (await SharedPreferences.getInstance()).remove(kNotesLastDir);
        if (!mounted) return;
        setState(() { _dir = null; _error = null; _loading = false; });
        showToast('该分类不存在（可能已被删除），已返回笔记首页');
        return;
      }
      final s = errDetail(e, max: 140);
      setState(() {
        _error = s.isEmpty ? e.toString().substring(0, 140.clamp(0, e.toString().length)) : s;
        _loading = false;
      });
    }
  }

  Future<void> _newNote() async {
    final nameCtrl = TextEditingController();
    // 0=Markdown 1=纯文本 2=富文本（.html，v0.19）
    var kind = 0;
    final name = await showDialog<String>(
        context: context,
        builder: (dctx) => StatefulBuilder(
          builder: (dctx, setD) => AlertDialog(
                title: const Text('新建笔记'),
                content: Column(mainAxisSize: MainAxisSize.min, children: [
                  TextField(
                      controller: nameCtrl,
                      autofocus: true,
                      maxLength: 60,
                      decoration: const InputDecoration(
                          labelText: '标题',
                          hintText: '保存为 <标题>.md / .txt / .html')),
                  const SizedBox(height: Gap.xxs),
                  // 类型三选一：md 渲染预览 / txt 纯文本 / html 富文本日记
                  SegmentedButton<int>(
                      segments: const [
                        ButtonSegment(value: 0, label: Text('Markdown')),
                        ButtonSegment(value: 1, label: Text('纯文本')),
                        ButtonSegment(value: 2, label: Text('富文本')),
                      ],
                      selected: {kind},
                      onSelectionChanged: (s) => setD(() => kind = s.first)),
                  const SizedBox(height: Gap.xxs),
                  if (kind == 2)
                    Text('富文本支持表格、图片、字体颜色大小，保存为 .html',
                        style: TextStyle(
                            fontSize: 11.5, color: Theme.of(dctx)
                                .colorScheme.onSurfaceVariant)),
                ]),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, nameCtrl.text.trim()),
                      child: const Text('选目录并创建')),
                ]),
        ));
    if (name == null || name.isEmpty || !mounted) return;
    final ext = switch (kind) { 1 => '.txt', 2 => '.html', _ => '.md' };
    // ignore: use_build_context_synchronously
    final d = await Navigator.push<String>(context, MaterialPageRoute(
        builder: (_) => const DirPickerPage(title: '保存到哪个目录？')));
    if (d == null || !mounted) return;
    (await SharedPreferences.getInstance()).setString(kNotesLastDir, d);
    if (!mounted) return;
    // 同名预检（review P2-6）：/api/upload 对同名文件是静默覆盖，新建变顶替
    try {
      final items = await FilesApi.listDir(d);
      final exists = items.any((e) =>
          ((e as Map)['name'] as String?)?.toLowerCase() ==
              '$name$ext'.toLowerCase());
      if (exists && mounted) {
        final goOn = await showDialog<bool>(
            context: context,
            builder: (dctx) => AlertDialog(
                    title: Text('「$name$ext」已存在'),
                    content: const Text('继续将覆盖同名文件'
                        '（旧版本会进服务端回收站）。'),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(dctx, false),
                          child: const Text('取消')),
                      FilledButton(
                          onPressed: () => Navigator.pop(dctx, true),
                          child: const Text('覆盖')),
                    ]));
        if (goOn != true || !mounted) return;
      }
    } catch (_) {
      // 预检失败不阻塞创建：保存时服务端行为照旧（覆盖），回收站可兜底
    }
    if (!mounted) return;
    await openNoteEditor(context,
        dir: d, name: '$name$ext', isNew: true);
    // P2-4/P3：await 后 setState 必须 mounted；首次使用直接把目录切过去
    if (!mounted) return;
    if (_dir == null || _dir != d) {
      _pickDir(d);
    } else {
      setState(() => _loading = true);
      _load();
    }
  }

  /// 重命名（v0.18）：服务端 /api/rename，成功后刷新列表
  Future<void> _rename(Note n) async {
    final ctrl = TextEditingController(text: n.name);
    final newName = await showDialog<String>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: const Text('重命名笔记'),
                content: TextField(
                    controller: ctrl,
                    autofocus: true,
                    maxLength: 120,
                    decoration: const InputDecoration(
                        labelText: '新文件名（含扩展名）')),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, ctrl.text.trim()),
                      child: const Text('确定')),
                ]));
    if (newName == null || newName.isEmpty || newName == n.name || !mounted) {
      return;
    }
    try {
      await FilesApi.rename(n.path, newName);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('已重命名'), duration: Duration(seconds: 2)));
      setState(() => _loading = true);
      _load();
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('重命名失败，请稍后重试')));
    }
  }

  /// 长按条目进入多选（v0.20，与照片墙/文件页一致）；单条管理由
  /// 选择栏承载：单选时"重命名"，删除始终可用。原底部弹单已并入。

  /// 批量移动到分类（9.6-P1-7 方案 B）：/api/move 一次搬运多路径；
  /// cat 为空串 = 移回未分类（笔记目录根部）
  Future<void> _moveTo(List<String> paths, String cat) async {
    if (paths.isEmpty) return;
    final destDir = cat.isEmpty ? _dir! : '${_dir!}/$cat';
    try {
      await FilesApi.move(paths, destDir);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(cat.isEmpty ? '已移到未分类' : '已移动到 $cat'),
          duration: const Duration(seconds: 2)));
      setState(() {
        _selected.clear();
        _loading = true;
      });
      _load();
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('移动失败，请稍后重试')));
    }
  }

  /// 分类选择器（移动目标）：树形展开；未分类固定在最上
  Future<String?> _pickCategory() async {
    return showDialog<String>(
        context: context,
        builder: (dctx) {
          List<Widget> tiles(List<CatNode> nodes, int depth) => [
                for (final node in nodes) ...[
                  ListTile(
                      dense: true,
                      contentPadding:
                          EdgeInsets.only(left: 8.0 + depth * 14, right: Gap.xxs),
                      leading: const Icon(Icons.folder_outlined, size: IconSizes.md),
                      title: Text(node.name,
                          maxLines: 1, overflow: TextOverflow.ellipsis),
                      onTap: () => Navigator.pop(dctx, node.path)),
                  ...tiles(node.childList, depth + 1),
                ]
              ];
          return AlertDialog(
                title: const Text('移动到分类'),
                content: SizedBox(
                    width: 280,
                    child: ListView(shrinkWrap: true, children: [
                      ListTile(
                          dense: true,
                          leading: const Icon(Icons.folder_copy_outlined),
                          title: const Text('未分类'),
                          onTap: () => Navigator.pop(dctx, '')),
                      ...tiles(_catTree, 0),
                    ])),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx),
                      child: const Text('取消')),
                ]);
        });
  }

  /// 新建分类 = 在笔记目录下建子文件夹（复用 /api/mkdir，可含子路径 a/b）
  Future<void> _newCategory() async {
    final ctrl = TextEditingController();
    final name = await showDialog<String>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: const Text('新建分类'),
                content: TextField(
                    controller: ctrl,
                    autofocus: true,
                    maxLength: 60,
                    decoration: const InputDecoration(
                        labelText: '分类名',
                        hintText: '支持子分类，如：日记/2026')),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, ctrl.text.trim()),
                      child: const Text('创建')),
                ]));
    if (name == null || name.isEmpty || !mounted) return;
    final segs = name.split('/').map((s) => s.trim()).where((s) => s.isNotEmpty).toList();
    if (segs.isEmpty) return;
    try {
      await FilesApi.mkdir('${_dir!}/${segs.join('/')}');
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('分类已创建'),
              duration: Duration(seconds: 2)));
      setState(() => _loading = true);
      _load();
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('创建失败（可能与现有笔记/分类重名）')));
    }
  }

  /// 删除分类：空分类直接删；有笔记需确认 → 先移回未分类再删目录
  Future<void> _deleteCategory(String cat) async {
    final count = _countOf(cat);
    final ok = await showDialog<bool>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text('删除分类「$cat」？'),
                content: Text(count > 0
                    ? '其中 $count 篇笔记将移回未分类。'
                    : '该分类是空的，可直接删除。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, true),
                      child: const Text('删除')),
                ]));
    if (ok != true || !mounted) return;
    try {
      if (count > 0) {
        // 只搬该分类直属+子级里的笔记（startsWith 前缀），再删目录
        final paths = (_notes ?? const <Note>[])
            .where((n) =>
                n.category == cat || n.category.startsWith('$cat/'))
            .map((n) => n.path)
            .toList();
        if (paths.isNotEmpty) {
          await FilesApi.move(paths, _dir!);
        }
      }
      await FilesApi.delete('${_dir!}/$cat');
      if (!mounted) return;
      if (_catFilter == cat) _catFilter = null;
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('分类已删除'),
              duration: Duration(seconds: 2)));
      setState(() => _loading = true);
      _load();
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('删除失败，请稍后重试')));
    }
  }

  void _togglePin(Note n) {
    setState(() {
      _pinned.contains(n.path) ? _pinned.remove(n.path) : _pinned.add(n.path);
    });
    _saveMarks();
  }

  void _toggleStar(Note n) {
    setState(() {
      _starred.contains(n.path) ? _starred.remove(n.path) : _starred.add(n.path);
    });
    _saveMarks();
  }

  Future<void> _pickSort() async {
    final s = await showModalBottomSheet<String>(
        context: context,
        showDragHandle: true,
        builder: (bctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              for (final e in const [
                ('modified', '按修改时间'),
                ('created', '按创建时间'),
                ('name', '按名称'),
              ])
                ListTile(
                    leading: Icon(_sort == e.$1
                        ? Icons.radio_button_checked
                        : Icons.radio_button_off),
                    title: Text(e.$2),
                    onTap: () => Navigator.pop(bctx, e.$1)),
            ])));
    if (s == null || s == _sort) return;
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kSort, s);
    setState(() => _sort = s);
    _load();
  }

  /// 批量删除（v0.20 多选）：逐条进服务端回收站，可恢复
  Future<void> _deleteSelected() async {
    final names = _filtered
        .where((n) => _selected.contains(n.path))
        .map((n) => n.name)
        .toList();
    final ok = await showDialog<bool>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text('删除 ${names.length} 篇笔记？'),
                content: Text('${names.take(5).join('、')}'
                    '${names.length > 5 ? ' 等 ${names.length} 篇' : ''}'
                    '，将进入服务端回收站（30 天内可恢复）。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, true),
                      child: const Text('删除')),
                ]));
    if (ok != true || !mounted) return;
    var done = 0, fail = 0;
    try {
      for (final p in _selected.toList()) {
        try {
          await FilesApi.delete(p);
          done++;
        } catch (_) {
          fail++;
        }
      }
    } catch (_) {
      fail = _selected.length;
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(fail == 0
            ? '已删除 $done 篇（可在回收站恢复）'
            : '已删除 $done 篇，失败 $fail 篇'),
        duration: const Duration(seconds: 2)));
    setState(() => _selected.clear());
    setState(() => _loading = true);
    _load();
  }

  // ── 分类侧栏 + 卡片列表（9.6-P1-7 方案 B）────────────────

  /// 左侧分类栏：覆盖式抽屉（宽约 70% 屏宽，点右侧空白收回）；分类可嵌套；
  /// 分类与"未分类"都是拖拽落点（长按笔记条目拖进来即移动）
  Widget _catPanel(ThemeData th) {
    final cs = th.colorScheme;
    final double panelW =
        (MediaQuery.of(context).size.width * 0.7).clamp(230.0, 320.0);
    Widget dropWrap(String? cat, Widget child) => DragTarget<String>(
        builder: (ctx, candidates, _) => Container(
            decoration: BoxDecoration(
                color: candidates.isNotEmpty ? cs.primaryContainer : null,
                borderRadius: Radii.bXs),
            child: child),
        onAcceptWithDetails: (d) => _moveTo([d.data], cat ?? ''));
    return AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
        width: _catsOpen ? panelW : 0,
        child: ClipRect(
            child: SizedBox(
                width: _catsOpen ? panelW : 0,
                child: Container(
                    // v0.28.2 修复反馈 7063：面板必须不透明——覆盖式抽屉底下
                    // 就是笔记列表，半透明会让列表文字透出来叠影
                    color: cs.surfaceContainerHighest,
                    child: Column(children: [
                      Padding(
                          padding: const EdgeInsets.fromLTRB(Gap.xs, Gap.xs, Gap.xs, 0),
                          child: SizedBox(
                              width: double.infinity,
                              child: OutlinedButton.icon(
                                  onPressed: _newCategory,
                                  icon: const Icon(Icons.create_new_folder_outlined,
                                      size: 18),
                                  label: const Text('新建分类',
                                      style: TextStyle(fontSize: 13))))),
                      Expanded(
                          child: ListView(padding: const EdgeInsets.all(Gap.xxs), children: [
                        dropWrap(null, ListTile(
                            dense: true,
                            selected: _catFilter == null && !_favOnly,
                            leading: Icon(Icons.apps, size: IconSizes.md,
                                color: _catFilter == null && !_favOnly ? cs.primary : null),
                            title: const Text('全部', style: TextStyle(fontSize: 13.5)),
                            onTap: () => setState(() {
                              _catFilter = null;
                              _favOnly = false;
                              _catsOpen = false;
                            }))),
                        dropWrap('', ListTile(
                            dense: true,
                            selected: _catFilter == '' && !_favOnly,
                            leading: Icon(Icons.folder_off_outlined, size: IconSizes.md,
                                color: _catFilter == '' && !_favOnly ? cs.primary : null),
                            title: const Text('未分类', style: TextStyle(fontSize: 13.5)),
                            trailing: Text('${_countOf('')}',
                                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                            onTap: () => setState(
                                () { _catFilter = ''; _favOnly = false; _catsOpen = false; }))),
                        ListTile(
                            dense: true,
                            selected: _favOnly,
                            leading: Icon(_favOnly ? Icons.star : Icons.star_border,
                                size: IconSizes.md, color: _favOnly ? cs.tertiary : null),
                            title: const Text('收藏', style: TextStyle(fontSize: 13.5)),
                            onTap: () => setState(
                                () => _favOnly = !_favOnly)),
                        if (_catTree.isNotEmpty)
                          const Padding(
                              padding: EdgeInsets.fromLTRB(Gap.sm, Gap.xs, Gap.xs, 2),
                              child: Text('分类',
                                  style: TextStyle(
                                      fontSize: 11, fontWeight: FontWeight.bold))),
                        for (final node in _catTree)
                          _catTile(node, 0, th, dropWrap),
                      ])),
                    ])))));
  }

  Widget _catTile(CatNode node, int depth, ThemeData th,
      Widget Function(String?, Widget) dropWrap) {
    final cs = th.colorScheme;
    final selected = _catFilter == node.path;
    final expanded = _expandedCats.contains(node.path);
    final hasChildren = node.childList.isNotEmpty;
    return Column(children: [
      dropWrap(node.path, ListTile(
          dense: true,
          selected: selected,
          contentPadding: EdgeInsets.only(left: 4.0 + depth * 14, right: Gap.xxs),
          leading: InkWell(
              onTap: hasChildren
                  ? () => setState(() => expanded
                      ? _expandedCats.remove(node.path)
                      : _expandedCats.add(node.path))
                  : null,
              child: Icon(
                  hasChildren
                      ? (expanded ? Icons.expand_more : Icons.chevron_right)
                      : Icons.folder_outlined,
                  size: IconSizes.md,
                  color: selected ? cs.primary : null)),
          title: Text(node.name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                  fontSize: 13.5, color: selected ? cs.primary : null)),
          subtitle: Text('${_countOf(node.path)} 篇',
              style: const TextStyle(fontSize: 10.5)),
          trailing: IconButton(
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.delete_outline, size: IconSizes.sm),
              tooltip: '删除分类',
              onPressed: () => _deleteCategory(node.path)),
          onTap: () => setState(() {
            _catFilter = selected ? null : node.path;
            _catsOpen = false;
          }))),
      if (expanded)
        for (final c in node.childList) _catTile(c, depth + 1, th, dropWrap),
    ]);
  }

  Widget _listBody(ThemeData th) {
    final cs = th.colorScheme;
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 100),
        Center(
            child: Text(_error!,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 12, color: Colors.grey))),
        const SizedBox(height: Gap.xs),
        Center(
            child: TextButton(
                onPressed: () {
                  setState(() => _loading = true);
                  _load();
                },
                child: const Text('重试'))),
      ]);
    }
    if (_filtered.isEmpty) {
      return ListView(children: [
        const SizedBox(height: 120),
        const Center(child: Icon(Icons.edit_note, size: 52, color: Colors.grey)),
        const SizedBox(height: 10),
        Center(
            child: Text(_searchCtrl.text.trim().isEmpty
                ? '还没有笔记，点右下角创建第一篇'
                : '没有匹配「${_searchCtrl.text.trim()}」的笔记')),
        const SizedBox(height: 6),
        Center(
            child: Text(_searchCtrl.text.trim().isEmpty
                ? '支持 Markdown / 纯文本 / 富文本，可按分类整理'
                : '换个关键词试试',
                style: const TextStyle(fontSize: 12, color: Colors.grey))),
      ]);
    }
    // v0.25 分类优先：先给分类横滑栏（全部/未分类/各分类，含空分类），
    // 再是笔记列表——分类是一级导航，不再藏进侧栏
    return Column(children: [
      if (!_searching && !_selecting) _catChips(Theme.of(context).colorScheme),
      Expanded(
          child: RefreshIndicator(
        onRefresh: () async => _load(),
        child: ListView.builder(
            padding: const EdgeInsets.fromLTRB(Gap.xs, Gap.xxs, Gap.xs, 88),
            itemCount: _filtered.length,
            itemBuilder: (_, i) => _noteCard(_filtered[i], cs))),
        ),
    ]);
  }

  /// 分类快捷栏（v0.25）：全部 / 未分类 / 各分类（含空分类，count=0）。
  /// 空分类也显示——新建分类立刻可见，不再"创建成功却找不到"。
  Widget _catChips(ColorScheme cs) {
    final top = _catTree;
    return SizedBox(
        height: 44,
        child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.fromLTRB(10, 6, 10, 2),
            children: [
              _chip(cs, '全部 ${_notes?.length ?? 0}', _catFilter == null,
                  () => setState(() { _catFilter = null; _favOnly = false; })),
              _chip(cs, '未分类 ${_countOf('')}', _catFilter == '',
                  () => setState(() { _catFilter = ''; _favOnly = false; })),
              for (final c in top)
                _chip(cs, '${c.name} ${_countOf(c.path)}',
                    _catFilter == c.path,
                    () => setState(() {
                      _catFilter = c.path;
                      _favOnly = false;
                      _expandedCats.add(c.path);
                    })),
            ]));
  }

  Widget _chip(ColorScheme cs, String label, bool selected,
      VoidCallback onTap) {
    final bg = selected ? cs.primaryContainer : cs.surfaceContainerHighest
        .withValues(alpha: 0.6);
    return Padding(
        padding: const EdgeInsets.only(right: Gap.xs),
        child: InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(18),
            child: Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: Gap.sm, vertical: 6),
                decoration: BoxDecoration(
                    color: bg,
                    borderRadius: BorderRadius.circular(18),
                    border: Border.all(
                        color: selected
                            ? cs.primary
                            : cs.outlineVariant.withValues(alpha: 0.5))),
                child: Text(label,
                    style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: selected
                            ? FontWeight.w600
                            : FontWeight.w400,
                        color: selected
                            ? cs.onPrimaryContainer
                            : cs.onSurfaceVariant)))));
  }

  Widget _noteCard(Note n, ColorScheme cs) {
    final sel = _selected.contains(n.path);
    final starred = _starred.contains(n.path);
    final pinned = _pinned.contains(n.path);
    final card = Container(
        margin: const EdgeInsets.only(bottom: Gap.xs),
        decoration: BoxDecoration(
            color: sel ? cs.primaryContainer.withValues(alpha: 0.35) : cs.surface,
            borderRadius: Radii.bControl,
            border: Border.all(
                color: sel ? cs.primary : cs.outlineVariant.withValues(alpha: 0.5))),
        child: InkWell(
            borderRadius: Radii.bControl,
            onTap: () async {
              if (_selecting) {
                setState(() =>
                    sel ? _selected.remove(n.path) : _selected.add(n.path));
                return;
              }
              // v0.25：点按默认**带格式预览**（DocViewerPage 渲染 md/文本），
              // 要改再点预览页的编辑笔进编辑器——阅读是高频动作，不该先见源码。
              await Navigator.push(context, MaterialPageRoute(
                  builder: (_) => DocViewerPage(path: n.path, name: n.name)));
              if (mounted) {
                setState(() => _loading = true);
                _load();
              }
            },
            onLongPress: () {
              if (_selecting) return;
              setState(() => _selected.add(n.path));
            },
            child: Padding(
                padding: const EdgeInsets.fromLTRB(Gap.sm, 10, 6, 10),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(children: [
                        if (_selecting)
                          Padding(
                              padding: const EdgeInsets.only(right: Gap.xs),
                              child: Icon(
                                  sel ? Icons.check_circle : Icons.circle_outlined,
                                  size: IconSizes.md, color: cs.primary)),
                        if (pinned) ...[
                          Icon(Icons.push_pin, size: 14, color: cs.primary),
                          const SizedBox(width: Gap.xxs),
                        ],
                        if (starred) ...[
                          Icon(Icons.star, size: 14, color: cs.tertiary),
                          const SizedBox(width: Gap.xxs),
                        ],
                        Expanded(
                            child: Text(n.name,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                    fontWeight: FontWeight.w600, fontSize: 14.5))),
                        if (n.ext == '.html' || n.ext == '.htm')
                          Padding(
                              padding: const EdgeInsets.only(left: Gap.xxs),
                              child: Text('富文本',
                                  style: TextStyle(
                                      fontSize: 10, color: cs.tertiary))),
                        if (!_selecting)
                          IconButton(
                              visualDensity: VisualDensity.compact,
                              icon: const Icon(Icons.more_vert, size: 18),
                              onPressed: () => _noteMenu(n)),
                      ]),
                      if (n.summary.isNotEmpty)
                        Padding(
                            padding: const EdgeInsets.only(top: 2),
                            child: Text(n.summary,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                    fontSize: 12.5,
                                    height: 1.4,
                                    color: cs.onSurfaceVariant))),
                      const SizedBox(height: 6),
                      Row(children: [
                        Text(
                            '${n.modified.split('T').first} · ${n.sizeHuman}',
                            style: TextStyle(
                                fontSize: 11, color: cs.onSurfaceVariant)),
                        if (n.category.isNotEmpty) ...[
                          const SizedBox(width: 6),
                          Icon(Icons.folder_outlined,
                              size: 12, color: cs.onSurfaceVariant),
                          const SizedBox(width: 2),
                          Flexible(
                              child: Text(n.category,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(
                                      fontSize: 11,
                                      color: cs.onSurfaceVariant))),
                        ],
                      ]),
                    ]))));
    // 侧栏展开时可长按拖动到分类（移动），拖拽与多选互斥
    // （LongPressDraggable 无 enabled，用 maxSimultaneousDrags=0 关闭）
    return LongPressDraggable<String>(
        data: n.path,
        maxSimultaneousDrags: (_selecting || !_catsOpen) ? 0 : 1,
        feedback: Material(
            elevation: 4,
            borderRadius: Radii.bControl,
            child: SizedBox(
                width: 220,
                child: ListTile(
                    dense: true,
                    leading: const Icon(Icons.description_outlined),
                    title: Text(n.name,
                        maxLines: 1, overflow: TextOverflow.ellipsis)))),
        childWhenDragging: Opacity(opacity: 0.4, child: card),
        child: card);
  }

  /// 单条操作菜单：置顶/收藏/移动到分类/重命名/删除
  Future<void> _noteMenu(Note n) async {
    final pinned = _pinned.contains(n.path);
    final starred = _starred.contains(n.path);
    final act = await showModalBottomSheet<String>(
        context: context,
        showDragHandle: true,
        builder: (bctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              ListTile(
                  leading: Icon(pinned ? Icons.push_pin_outlined : Icons.push_pin),
                  title: Text(pinned ? '取消置顶' : '置顶'),
                  onTap: () => Navigator.pop(bctx, 'pin')),
              ListTile(
                  leading: Icon(starred ? Icons.star_border : Icons.star),
                  title: Text(starred ? '取消收藏' : '收藏'),
                  onTap: () => Navigator.pop(bctx, 'star')),
              ListTile(
                  leading: const Icon(Icons.copy_outlined),
                  title: const Text('复制路径'),
                  subtitle: Text(n.absPath ?? '/${n.path}',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  onTap: () => Navigator.pop(bctx, 'copypath')),
              ListTile(
                  leading: const Icon(Icons.drive_file_move_outlined),
                  title: const Text('移动到分类'),
                  onTap: () => Navigator.pop(bctx, 'move')),
              ListTile(
                  leading: const Icon(Icons.drive_file_rename_outline),
                  title: const Text('重命名'),
                  onTap: () => Navigator.pop(bctx, 'rename')),
              ListTile(
                  leading: Icon(Icons.delete_outline,
                      color: Theme.of(bctx).colorScheme.error),
                  title: Text('删除', style: TextStyle(
                      color: Theme.of(bctx).colorScheme.error)),
                  onTap: () => Navigator.pop(bctx, 'delete')),
            ])));
    if (act == null || !mounted) return;
    switch (act) {
      case 'pin':
        _togglePin(n);
        break;
      case 'star':
        _toggleStar(n);
        break;
      case 'copypath':
        await Clipboard.setData(
            ClipboardData(text: n.absPath ?? '/${n.path}'));
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('已复制服务器路径')));
        }
        break;
      case 'move':
        final cat = await _pickCategory();
        if (cat != null && mounted) _moveTo([n.path], cat);
        break;
      case 'rename':
        _rename(n);
        break;
      case 'delete':
        setState(() => _selected
          ..clear()
          ..add(n.path));
        _deleteSelected();
        break;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        leading: _selecting
            ? IconButton(
                icon: const Icon(Icons.close),
                onPressed: () => setState(() => _selected.clear()))
            : null,
        title: Text(
            _selecting
                ? '已选 ${_selected.length} 篇'
                : _dirLabel == null
                    ? '笔记'
                    : '笔记 · $_dirLabel',
            style: const TextStyle(fontSize: 16)),
        actions: _selecting
            ? [
                TextButton(
                    onPressed: () => setState(() {
                          if (_selected.length == _filtered.length) {
                            _selected.clear();
                          } else {
                            _selected
                              ..clear()
                              ..addAll(_filtered.map((n) => n.path));
                          }
                        }),
                    child: Text(_selected.length == _filtered.length
                        ? '全不选'
                        : '全选')),
              ]
            : [
          // 排序（9.6-P1-7）：修改时间/创建时间/名称
          IconButton(
              tooltip: '排序',
              icon: const Icon(Icons.sort),
              onPressed: _pickSort),
          // 分类栏开关（方案 B 左侧常驻侧栏，手机默认收起）
          IconButton(
              tooltip: _catsOpen ? '收起分类栏' : '分类栏',
              icon: Icon(_catsOpen ? Icons.view_headline : Icons.view_week_outlined),
              onPressed: () => setState(() => _catsOpen = !_catsOpen)),
          // 搜索（v0.18）：标题+摘要过滤，不额外打服务端
          IconButton(
              tooltip: _searching ? '关闭搜索' : '搜索笔记',
              icon: Icon(_searching ? Icons.search_off : Icons.search),
              onPressed: () {
                setState(() {
                  _searching = !_searching;
                  if (!_searching) _searchCtrl.clear();
                });
              }),
          IconButton(
              tooltip: '切换目录',
              icon: const Icon(Icons.folder_open),
              onPressed: _chooseDir),
        ],
        bottom: _searching
            ? PreferredSize(
                preferredSize: const Size.fromHeight(52),
                child: Padding(
                    padding: const EdgeInsets.fromLTRB(Gap.sm, 0, Gap.sm, Gap.xs),
                    child: TextField(
                        controller: _searchCtrl,
                        autofocus: true,
                        onChanged: (_) => setState(() {}),
                        decoration: InputDecoration(
                            isDense: true,
                            hintText: '按文件名过滤…',
                            prefixIcon: const Icon(Icons.search, size: IconSizes.md),
                            border: OutlineInputBorder(
                                borderRadius: Radii.bSm)))),
              )
            : null,
      ),
      floatingActionButton: _selecting
          ? null
          : FloatingActionButton.extended(
              onPressed: _newNote,
              icon: const Icon(Icons.note_add),
              label: const Text('新建笔记')),
      body: _dir == null
          ? Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Icon(Icons.edit_note, size: 56, color: Colors.grey),
              const SizedBox(height: Gap.sm),
              const Text('还没选过笔记目录', style: TextStyle(fontSize: 15)),
              const SizedBox(height: 6),
              const Padding(
                  padding: EdgeInsets.symmetric(horizontal: Gap.xxl),
                  child: Text('笔记就是网盘里的 .md / .txt 文件，存哪个文件夹由你定。'
                      '点「新建笔记」选目录，或右上角直接切换目录。',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 12.5, color: Colors.grey))),
              const SizedBox(height: 14),
              FilledButton.icon(
                  onPressed: _chooseDir,
                  icon: const Icon(Icons.folder_open),
                  label: const Text('选择目录')),
            ]))
          : Stack(children: [
              Positioned.fill(child: _listBody(Theme.of(context))),
              // 覆盖式分类抽屉（9-10 交互修复）：不再内联挤压笔记列表；
              // 展开时右侧留遮罩空白，点空白即收回
              Positioned.fill(
                  child: IgnorePointer(
                      ignoring: !_catsOpen,
                      child: AnimatedContainer(
                          duration: const Duration(milliseconds: 200),
                          curve: Curves.easeOut,
                          color: _catsOpen
                              ? Colors.black.withValues(alpha: 0.30)
                              : Colors.transparent,
                          child: Row(children: [
                            _catPanel(Theme.of(context)),
                            Expanded(
                                child: GestureDetector(
                                    onTap: () =>
                                        setState(() => _catsOpen = false),
                                    child: const SizedBox.expand())),
                          ])))),
            ]),
      bottomNavigationBar: _selecting
          ? SafeArea(
              child: Container(
                  decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.surface,
                      border: Border(
                          top: BorderSide(
                              color: Theme.of(context).dividerColor,
                              width: 0.5))),
                  child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceAround,
                      children: [
                        if (_selected.length == 1)
                          InkWell(
                              onTap: () {
                                final n = _filtered.firstWhere(
                                    (n) => n.path == _selected.first);
                                _rename(n);
                              },
                              borderRadius: Radii.bSm,
                              child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: Gap.md, vertical: Gap.xs),
                                  child: Column(mainAxisSize: MainAxisSize.min,
                                      children: const [
                                        Icon(Icons.drive_file_rename_outline,
                                            size: 22),
                                        SizedBox(height: 2),
                                        Text('重命名',
                                            style: TextStyle(fontSize: 12)),
                                      ]))),
                        // 批量移动到分类（9.6-P1-7 方案 B）
                        InkWell(
                            onTap: () async {
                              final cat = await _pickCategory();
                              if (cat != null && mounted) {
                                _moveTo(_selected.toList(), cat);
                              }
                            },
                            borderRadius: Radii.bSm,
                            child: Padding(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: Gap.md, vertical: Gap.xs),
                                child: Column(mainAxisSize: MainAxisSize.min,
                                    children: const [
                                      Icon(Icons.drive_file_move_outlined,
                                          size: 22),
                                      SizedBox(height: 2),
                                      Text('移动',
                                          style: TextStyle(fontSize: 12)),
                                    ]))),
                        InkWell(
                            onTap: _deleteSelected,
                            borderRadius: Radii.bSm,
                            child: Padding(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: Gap.md, vertical: Gap.xs),
                                child: Column(mainAxisSize: MainAxisSize.min,
                                    children: [
                                  Icon(Icons.delete_outline,
                                      size: 22,
                                      color: Theme.of(context)
                                          .colorScheme
                                          .error),
                                  const SizedBox(height: 2),
                                  Text('删除',
                                      style: TextStyle(
                                          fontSize: 12,
                                          color: Theme.of(context)
                                              .colorScheme
                                              .error)),
                                ]))),
                      ])))
          : null,
    );
  }
}
