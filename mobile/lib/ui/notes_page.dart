// MD 笔记（v0.17 新功能，v0.18 完善）：服务端任意目录下的 .md/.txt 即笔记。
// · 列表：默认打开上次用的目录（prefs 记忆），可换目录（DirPickerPage）；
//   顶部搜索按文件名过滤；长按条目可重命名/删除（服务端 rename/回收站）
// · 新建：输标题 → 选目录 → 进编辑器（保存时 multipart 直传）
// · 编辑：拉取远端内容 → 全屏 md 源码编辑 → 保存覆盖上传；脏态返回拦确认；
//   md 可在源码/渲染间切换；每次输入自动存本地草稿，崩溃/误退可恢复
// · 预览联动：DocViewerPage 看文本时点编辑笔进编辑器
import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/api_client.dart';
import 'dir_picker_page.dart';
import 'rich_note_editor.dart';

const _kNotesLastDir = 'notes_last_dir';
String _draftKey(String remotePath) => 'note_draft_$remotePath';

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
  List<_Note>? _notes;
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

  /// 分类树（从笔记的 category 字段聚合）：嵌套子路径合并成节点
  List<_CatNode> get _catTree {
    final root = <_CatNode>{};
    for (final n in _notes ?? const <_Note>[]) {
      if (n.category.isEmpty) continue;
      var cur = root;
      var acc = '';
      for (final seg in n.category.split('/')) {
        acc = acc.isEmpty ? seg : '$acc/$seg';
        final hit = cur.where((c) => c.name == seg).toList();
        final node = hit.isEmpty
            ? () {
                final nn = _CatNode(seg, acc);
                cur.add(nn);
                return nn;
              }()
            : hit.first;
        cur = node.children;
      }
    }
    List<_CatNode> toList(Set<_CatNode> s) {
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
      return (_notes ?? const <_Note>[]).where((n) => n.category.isEmpty).length;
    }
    return (_notes ?? const <_Note>[])
        .where((n) => n.category == cat || n.category.startsWith('$cat/'))
        .length;
  }

  List<_Note> get _filtered {
    var all = _notes ?? const <_Note>[];
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
            n.category.startsWith('$_catFilter!/');
      }).toList();
    }
    List<_Note> sorted;
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
    final d = sp.getString(_kNotesLastDir);
    if (!mounted) return;
    if (d == null || d.isEmpty) {
      setState(() { _dir = null; _loading = false; });
    } else {
      _pickDir(d);
    }
  }

  Future<void> _pickDir(String dir) async {
    setState(() { _dir = dir; _loading = true; _error = null; });
    (await SharedPreferences.getInstance()).setString(_kNotesLastDir, dir);
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
      final dio = await ApiClient.I.dio();
      // v0.21：聚合列表端点——一次拿到全部笔记（含子分类与前 200 字摘要），
      // 分类/移动/重命名/删除仍走 files 通道
      final r = await dio.get('/api/notes/list',
          queryParameters: {'path': _dir!, 'sort': _sort});
      final items = (r.data['items'] as List? ?? const []);
      final notes = <_Note>[];
      for (final e in items) {
        final m = e as Map;
        notes.add(_Note(
            name: (m['name'] as String?) ?? '',
            path: (m['path'] as String?) ?? '',
            sizeHuman: (m['size_human'] as String?) ?? '',
            modified: (m['modified'] as String?) ?? '',
            category: (m['category'] as String?) ?? '',
            summary: (m['summary'] as String?) ?? '',
            ext: (m['ext'] as String?) ?? '',
            mtime: (m['mtime'] as num?)?.toDouble() ?? 0,
            ctime: (m['ctime'] as num?)?.toDouble() ?? 0));
      }
      if (!mounted) return;
      setState(() { _notes = notes; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      final s = e.toString();
      setState(() {
        _error = s.length > 140 ? s.substring(0, 140) : s;
        _loading = false;
      });
    }
  }

  String _enc(String p) => p.split('/').map(Uri.encodeComponent).join('/');

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
                  const SizedBox(height: 4),
                  // 类型三选一：md 渲染预览 / txt 纯文本 / html 富文本日记
                  SegmentedButton<int>(
                      segments: const [
                        ButtonSegment(value: 0, label: Text('Markdown')),
                        ButtonSegment(value: 1, label: Text('纯文本')),
                        ButtonSegment(value: 2, label: Text('富文本')),
                      ],
                      selected: {kind},
                      onSelectionChanged: (s) => setD(() => kind = s.first)),
                  const SizedBox(height: 4),
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
    (await SharedPreferences.getInstance()).setString(_kNotesLastDir, d);
    if (!mounted) return;
    // 同名预检（review P2-6）：/api/upload 对同名文件是静默覆盖，新建变顶替
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/files/${_enc(d)}');
      final exists = (r.data['items'] as List? ?? const []).any((e) =>
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
  Future<void> _rename(_Note n) async {
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
      final dio = await ApiClient.I.dio();
      await dio.post('/api/rename', data: {'path': n.path, 'new_name': newName});
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
      final dio = await ApiClient.I.dio();
      await dio.post('/api/move', data: {
        'sources': paths,
        'dest_dir': destDir,
      });
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
          List<Widget> tiles(List<_CatNode> nodes, int depth) => [
                for (final node in nodes) ...[
                  ListTile(
                      dense: true,
                      contentPadding:
                          EdgeInsets.only(left: 8.0 + depth * 14, right: 4),
                      leading: const Icon(Icons.folder_outlined, size: 20),
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
      final dio = await ApiClient.I.dio();
      await dio.post(
          '/api/mkdir/${_enc('${_dir!}/${segs.join('/')}')}');
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
      final dio = await ApiClient.I.dio();
      if (count > 0) {
        // 只搬该分类直属+子级里的笔记（startsWith 前缀），再删目录
        final paths = (_notes ?? const <_Note>[])
            .where((n) =>
                n.category == cat || n.category.startsWith('$cat/'))
            .map((n) => n.path)
            .toList();
        if (paths.isNotEmpty) {
          await dio.post('/api/move',
              data: {'sources': paths, 'dest_dir': _dir!});
        }
      }
      await dio.delete('/api/files/${_enc('${_dir!}/$cat')}');
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

  void _togglePin(_Note n) {
    setState(() {
      _pinned.contains(n.path) ? _pinned.remove(n.path) : _pinned.add(n.path);
    });
    _saveMarks();
  }

  void _toggleStar(_Note n) {
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
      final dio = await ApiClient.I.dio();
      for (final p in _selected.toList()) {
        try {
          await dio.delete('/api/files/${_enc(p)}');
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

  /// 左侧分类栏：可开合；分类可嵌套；分类与"未分类"都是拖拽落点
  /// （长按笔记条目拖进来即移动）
  Widget _catPanel(ThemeData th) {
    final cs = th.colorScheme;
    Widget dropWrap(String? cat, Widget child) => DragTarget<String>(
        builder: (ctx, candidates, _) => Container(
            decoration: BoxDecoration(
                color: candidates.isNotEmpty ? cs.primaryContainer : null,
                borderRadius: BorderRadius.circular(8)),
            child: child),
        onAcceptWithDetails: (d) => _moveTo([d.data], cat ?? ''));
    return AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
        width: _catsOpen ? 190 : 0,
        child: ClipRect(
            child: SizedBox(
                width: _catsOpen ? 190 : 0,
                child: Container(
                    color: cs.surfaceContainerHighest.withValues(alpha: 0.45),
                    child: Column(children: [
                      Padding(
                          padding: const EdgeInsets.fromLTRB(8, 8, 8, 0),
                          child: SizedBox(
                              width: double.infinity,
                              child: OutlinedButton.icon(
                                  onPressed: _newCategory,
                                  icon: const Icon(Icons.create_new_folder_outlined,
                                      size: 18),
                                  label: const Text('新建分类',
                                      style: TextStyle(fontSize: 13))))),
                      Expanded(
                          child: ListView(padding: const EdgeInsets.all(4), children: [
                        dropWrap(null, ListTile(
                            dense: true,
                            selected: _catFilter == null && !_favOnly,
                            leading: Icon(Icons.apps, size: 20,
                                color: _catFilter == null && !_favOnly ? cs.primary : null),
                            title: const Text('全部', style: TextStyle(fontSize: 13.5)),
                            onTap: () => setState(() { _catFilter = null; _favOnly = false; }))),
                        dropWrap('', ListTile(
                            dense: true,
                            selected: _catFilter == '' && !_favOnly,
                            leading: Icon(Icons.folder_off_outlined, size: 20,
                                color: _catFilter == '' && !_favOnly ? cs.primary : null),
                            title: const Text('未分类', style: TextStyle(fontSize: 13.5)),
                            trailing: Text('${_countOf('')}',
                                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                            onTap: () => setState(() { _catFilter = ''; _favOnly = false; }))),
                        ListTile(
                            dense: true,
                            selected: _favOnly,
                            leading: Icon(_favOnly ? Icons.star : Icons.star_border,
                                size: 20, color: _favOnly ? cs.tertiary : null),
                            title: const Text('收藏', style: TextStyle(fontSize: 13.5)),
                            onTap: () => setState(() => _favOnly = !_favOnly)),
                        if (_catTree.isNotEmpty)
                          const Padding(
                              padding: EdgeInsets.fromLTRB(12, 8, 8, 2),
                              child: Text('分类',
                                  style: TextStyle(
                                      fontSize: 11, fontWeight: FontWeight.bold))),
                        for (final node in _catTree)
                          _catTile(node, 0, th, dropWrap),
                      ])),
                    ])))));
  }

  Widget _catTile(_CatNode node, int depth, ThemeData th,
      Widget Function(String?, Widget) dropWrap) {
    final cs = th.colorScheme;
    final selected = _catFilter == node.path;
    final expanded = _expandedCats.contains(node.path);
    final hasChildren = node.childList.isNotEmpty;
    return Column(children: [
      dropWrap(node.path, ListTile(
          dense: true,
          selected: selected,
          contentPadding: EdgeInsets.only(left: 4.0 + depth * 14, right: 4),
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
                  size: 20,
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
              icon: const Icon(Icons.delete_outline, size: 16),
              tooltip: '删除分类',
              onPressed: () => _deleteCategory(node.path)),
          onTap: () => setState(
              () => _catFilter = selected ? null : node.path))),
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
        const SizedBox(height: 8),
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
    return RefreshIndicator(
        onRefresh: () async => _load(),
        child: ListView.builder(
            padding: const EdgeInsets.fromLTRB(8, 4, 8, 88),
            itemCount: _filtered.length,
            itemBuilder: (_, i) => _noteCard(_filtered[i], cs)));
  }

  Widget _noteCard(_Note n, ColorScheme cs) {
    final sel = _selected.contains(n.path);
    final starred = _starred.contains(n.path);
    final pinned = _pinned.contains(n.path);
    final card = Container(
        margin: const EdgeInsets.only(bottom: 8),
        decoration: BoxDecoration(
            color: sel ? cs.primaryContainer.withValues(alpha: 0.35) : cs.surface,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
                color: sel ? cs.primary : cs.outlineVariant.withValues(alpha: 0.5))),
        child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: () async {
              if (_selecting) {
                setState(() =>
                    sel ? _selected.remove(n.path) : _selected.add(n.path));
                return;
              }
              await openNoteEditor(context, dir: _dir!, name: n.name);
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
                padding: const EdgeInsets.fromLTRB(12, 10, 6, 10),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(children: [
                        if (_selecting)
                          Padding(
                              padding: const EdgeInsets.only(right: 8),
                              child: Icon(
                                  sel ? Icons.check_circle : Icons.circle_outlined,
                                  size: 20, color: cs.primary)),
                        if (pinned) ...[
                          Icon(Icons.push_pin, size: 14, color: cs.primary),
                          const SizedBox(width: 4),
                        ],
                        if (starred) ...[
                          Icon(Icons.star, size: 14, color: cs.tertiary),
                          const SizedBox(width: 4),
                        ],
                        Expanded(
                            child: Text(n.name,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                    fontWeight: FontWeight.w600, fontSize: 14.5))),
                        if (n.ext == '.html' || n.ext == '.htm')
                          Padding(
                              padding: const EdgeInsets.only(left: 4),
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
            borderRadius: BorderRadius.circular(12),
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
  Future<void> _noteMenu(_Note n) async {
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
                    padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
                    child: TextField(
                        controller: _searchCtrl,
                        autofocus: true,
                        onChanged: (_) => setState(() {}),
                        decoration: InputDecoration(
                            isDense: true,
                            hintText: '按文件名过滤…',
                            prefixIcon: const Icon(Icons.search, size: 20),
                            border: OutlineInputBorder(
                                borderRadius: BorderRadius.circular(10))))),
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
              const SizedBox(height: 12),
              const Text('还没选过笔记目录', style: TextStyle(fontSize: 15)),
              const SizedBox(height: 6),
              const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 32),
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
          : Row(children: [
              _catPanel(Theme.of(context)),
              Expanded(child: _listBody(Theme.of(context))),
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
                              borderRadius: BorderRadius.circular(10),
                              child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: 16, vertical: 8),
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
                            borderRadius: BorderRadius.circular(10),
                            child: Padding(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 16, vertical: 8),
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
                            borderRadius: BorderRadius.circular(10),
                            child: Padding(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 16, vertical: 8),
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

class _Note {
  final String name;
  final String path;
  final String sizeHuman;
  final String modified;
  // v0.21 分类体系字段（/api/notes/list 下发）
  final String category; // 相对笔记目录的子路径，''=未分类
  final String summary;  // 前 200 字纯文本摘要
  final String ext;      // 含点的扩展名（.md/.txt/.html）
  final double mtime;
  final double ctime;
  const _Note(
      {required this.name, required this.path,
      required this.sizeHuman, required this.modified,
      this.category = '', this.summary = '', this.ext = '',
      this.mtime = 0, this.ctime = 0});
}

/// 分类树节点（从笔记 category 聚合而来；可变，树构建期使用）
class _CatNode {
  final String name;   // 最后一段（显示名）
  final String path;   // 完整分类路径
  final Set<_CatNode> children = {};
  List<_CatNode> childList = []; // 排序后的子节点（_catTree 里填充）
  _CatNode(this.name, this.path);
}

// ── 编辑器 ─────────────────────────────────────────────────

class NoteEditorPage extends StatefulWidget {
  final String dir;
  final String name;
  final bool isNew;
  const NoteEditorPage(
      {super.key, required this.dir, required this.name,
      this.isNew = false});

  @override
  State<NoteEditorPage> createState() => _NoteEditorPageState();
}

class _NoteEditorPageState extends State<NoteEditorPage> {
  final _ctrl = TextEditingController();
  final _focus = FocusNode();
  bool _loading = true;
  bool _saving = false;
  bool _dirty = false;
  bool _preview = false; // md 源码/渲染切换（v0.18）
  String? _error;
  Timer? _autoTimer; // 自动保存定时器（改进建议 9.6-P0-12）

  bool get _isMd =>
      widget.name.toLowerCase().endsWith('.md') ||
      widget.name.toLowerCase().endsWith('.markdown');

  @override
  void initState() {
    super.initState();
    _loadContent();
    // 自动保存：每 30s 静默覆盖上传一次脏内容，失焦时也存一次；
    // 失败时内容仍在编辑器+本地草稿，可再次保存
    _autoTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted && _dirty && !_saving && !_loading && _error == null) {
        _save(silent: true);
      }
    });
    _focus.addListener(_onFocusChanged);
  }

  void _onFocusChanged() {
    if (!_focus.hasFocus && _dirty && !_saving && !_loading && _error == null) {
      _save(silent: true);
    }
  }

  @override
  void dispose() {
    _autoTimer?.cancel();
    _ctrl.dispose();
    _focus.dispose();
    super.dispose();
  }

  String get _remotePath =>
      '${widget.dir}/${widget.name}'.replaceAll('//', '/');

  /// AppBar 标题：笔记在子文件夹（分类）里时显示"分类名 / 文件名"，
  /// 直接在笔记根目录则只显示文件名
  String get _title {
    final seg =
        widget.dir.split('/').where((s) => s.trim().isNotEmpty).toList();
    if (seg.isEmpty) return widget.name;
    return '${seg.last} / ${widget.name}';
  }

  Future<void> _loadContent() async {
    if (widget.isNew) {
      if (!mounted) return;
      // 不预置脏（review P3）：没打字就退出不该弹"放弃修改"；
      // onChanged 首次输入即置脏
      setState(() => _loading = false);
      _focus.requestFocus();
      await _restoreDraft(remote: null);
      return;
    }
    try {
      final dio = await ApiClient.I.dio();
      // /preview/ 通道（v0.17.2）：/files/ 被 Caddy forward_auth 拦
      // （authcheck 只认 Cookie），App Bearer 必 401；/preview 对非图片
      // 回 FileResponse，与下载引擎同款
      final r = await dio.get<List<int>>(
          '/preview/${_enc(_remotePath)}',
          options: Options(responseType: ResponseType.bytes));
      final text = utf8.decode(r.data ?? const [], allowMalformed: true);
      if (!mounted) return;
      setState(() {
        _ctrl.text = text;
        _loading = false;
      });
      await _restoreDraft(remote: text);
    } catch (e) {
      if (!mounted) return;
      final s = e.toString();
      setState(() {
        _error = s.length > 140 ? s.substring(0, 140) : s;
        _loading = false;
      });
    }
  }

  /// 草稿恢复（v0.18）：上次编辑没保存就退出/崩溃时，本地草稿优先
  /// （比远端新才恢复）；给 SnackBar 一键丢弃。isNew 时 remote 传 null
  Future<void> _restoreDraft({String? remote}) async {
    final sp = await SharedPreferences.getInstance();
    final draft = sp.getString(_draftKey(_remotePath));
    if (draft == null || draft.isEmpty || !mounted) return;
    if (remote != null && draft == remote) {
      // 草稿与远端一致（上次其实保存过但清 key 失败）：静默清掉
      // ignore: discarded_futures
      sp.remove(_draftKey(_remotePath));
      return;
    }
    setState(() {
      _ctrl.text = draft;
      _dirty = true;
    });
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: const Text('已恢复上次未保存的草稿'),
        duration: const Duration(seconds: 4),
        action: SnackBarAction(
            label: '丢弃',
            onPressed: () {
              // ignore: discarded_futures
              SharedPreferences.getInstance().then((sp) =>
                  sp.remove(_draftKey(_remotePath)));
              if (!mounted) return;
              setState(() {
                _ctrl.text = remote ?? '';
                _dirty = false;
              });
            })));
  }

  void _onChanged(String v) {
    if (!_dirty) setState(() => _dirty = true);
    // 草稿即写（v0.18）：崩溃/误杀进程也不丢内容；保存成功后清除
    // ignore: discarded_futures
    SharedPreferences.getInstance()
        .then((sp) => sp.setString(_draftKey(_remotePath), v));
  }

  String _enc(String p) => p.split('/').map(Uri.encodeComponent).join('/');

  /// silent=true 为自动保存：成功不弹 SnackBar（避免打扰），失败仍提示
  Future<bool> _save({bool silent = false}) async {
    if (_saving) return false;
    setState(() => _saving = true);
    try {
      final dio = await ApiClient.I.dio();
      // on_dup=overwrite（改进建议 9.6-P0-12）：就地编辑直接覆盖源文件，
      // 不走"旧文件进回收站"，杜绝回收站堆积同一笔记的多个副本
      await dio.post(
          '/api/upload?path=${Uri.encodeComponent(widget.dir)}'
          '&on_dup=overwrite',
          data: FormData.fromMap({
            'file': MultipartFile.fromString(_ctrl.text,
                filename: widget.name),
          }));
      if (!mounted) return true;
      // 保存成功清草稿（v0.18）
      // ignore: discarded_futures
      SharedPreferences.getInstance().then((sp) =>
          sp.remove(_draftKey(_remotePath)));
      setState(() => _dirty = false);
      if (!silent) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('已保存到 $_remotePath'),
                duration: const Duration(seconds: 2)));
      }
      return true;
    } on DioException catch (e) {
      if (!mounted) return false;
      final d = e.response?.data;
      final detail = d is Map && d['detail'] != null
          ? d['detail'].toString()
          : null;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(detail ?? '保存失败，请稍后重试')));
      return false;
    } catch (_) {
      if (!mounted) return false;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('保存失败，请稍后重试')));
      return false;
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return PopScope(
      canPop: !_dirty,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop || !_dirty) return;
        final discard = await showDialog<bool>(
            context: context,
            builder: (dctx) => AlertDialog(
                    title: const Text('放弃未保存的修改？'),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(dctx, false),
                          child: const Text('继续编辑')),
                      FilledButton(
                          onPressed: () => Navigator.pop(dctx, true),
                          child: const Text('放弃')),
                    ]));
        if (discard == true && context.mounted) Navigator.pop(context);
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(_title,
              style: const TextStyle(fontSize: 14)),
          actions: [
            // md 渲染切换（v0.18）：源码/预览一键切（编辑态切走也保留内容）
            if (_isMd && !_loading && _error == null)
              IconButton(
                  tooltip: _preview ? '继续编辑' : '渲染预览',
                  icon: Icon(_preview ? Icons.edit_outlined : Icons.visibility_outlined),
                  onPressed: () => setState(() => _preview = !_preview)),
            if (_dirty && !_saving)
              Padding(
                  padding: const EdgeInsets.only(right: 4),
                  child: Center(
                      child: Text('未保存',
                          style: TextStyle(
                              fontSize: 11.5, color: cs.tertiary)))),
            IconButton(
                tooltip: '保存',
                icon: _saving
                    ? const SizedBox(
                        width: 18, height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.save_outlined),
                onPressed: _save),
          ],
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ListView(children: [
                    const SizedBox(height: 100),
                    Center(
                        child: Text(_error!,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                                fontSize: 12, color: Colors.grey))),
                    const SizedBox(height: 8),
                    Center(
                        child: TextButton(
                            onPressed: _loadContent,
                            child: const Text('重试'))),
                  ])
                : _preview
                    ? Markdown(
                        data: _ctrl.text,
                        selectable: true,
                        padding: const EdgeInsets.all(16),
                        styleSheet: MarkdownStyleSheet(
                          p: const TextStyle(fontSize: 15, height: 1.55),
                          h1: const TextStyle(
                              fontSize: 24, fontWeight: FontWeight.bold),
                          h2: const TextStyle(
                              fontSize: 20, fontWeight: FontWeight.bold),
                          h3: const TextStyle(
                              fontSize: 17, fontWeight: FontWeight.w600),
                        ))
                    : Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: TextField(
                        controller: _ctrl,
                        focusNode: _focus,
                        maxLines: null,
                        expands: true,
                        textAlignVertical: TextAlignVertical.top,
                        keyboardType: TextInputType.multiline,
                        onChanged: _onChanged,
                        style: const TextStyle(
                            fontSize: 14.5,
                            height: 1.55,
                            fontFamily: 'monospace'),
                        decoration: const InputDecoration(
                            border: InputBorder.none,
                            hintText: '# 标题\n\n用 Markdown 写笔记…'))),
      ),
    );
  }
}
