// MD 笔记（v0.17 新功能，v0.18 完善）：服务端任意目录下的 .md/.txt 即笔记。
// · 列表：默认打开上次用的目录（prefs 记忆），可换目录（DirPickerPage）；
//   顶部搜索按文件名过滤；长按条目可重命名/删除（服务端 rename/回收站）
// · 新建：输标题 → 选目录 → 进编辑器（保存时 multipart 直传）
// · 编辑：拉取远端内容 → 全屏 md 源码编辑 → 保存覆盖上传；脏态返回拦确认；
//   md 可在源码/渲染间切换；每次输入自动存本地草稿，崩溃/误退可恢复
// · 预览联动：DocViewerPage 看文本时点编辑笔进编辑器
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
  // v0.18：搜索（纯本地文件名过滤）与条目长按管理
  bool _searching = false;
  final _searchCtrl = TextEditingController();
  // v0.20 多选：Set 非空即选择模式（与照片墙/文件页同款约定），
  // 长按进入，AppBar 切换，底部操作栏批量删除
  final _selected = <String>{};
  bool get _selecting => _selected.isNotEmpty;

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

  List<_Note> get _filtered {
    final all = _notes ?? const <_Note>[];
    final q = _searchCtrl.text.trim().toLowerCase();
    if (q.isEmpty) return all;
    return all.where((n) => n.name.toLowerCase().contains(q)).toList();
  }

  Future<void> _restore() async {
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
      final r = await dio.get('/api/files/${_enc(_dir!)}');
      final items = (r.data['items'] as List? ?? const []);
      final notes = <_Note>[];
      for (final e in items) {
        final m = e as Map;
        final name = (m['name'] as String?) ?? '';
        // v0.17.1：txt；v0.19：html（富文本）都算笔记（列表/新建/编辑全链路）
        final lower = name.toLowerCase();
        if ((lower.endsWith('.md') ||
                lower.endsWith('.markdown') ||
                lower.endsWith('.txt') ||
                lower.endsWith('.html') ||
                lower.endsWith('.htm')) &&
            (m['type'] as String?) != 'dir') {
          notes.add(_Note(
              name: name,
              path: '$_dir/$name'.replaceAll('//', '/'),
              sizeHuman: (m['size_human'] as String?) ?? '',
              modified: (m['modified'] as String?) ?? ''));
        }
      }
      notes.sort((a, b) => b.modified.compareTo(a.modified));
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

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
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
                : _dir == null || _dir!.isEmpty
                    ? '笔记'
                    : '笔记 · $_dir',
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
          // 搜索（v0.18）：纯本地文件名过滤，不额外打服务端
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
          : _loading
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
                              onPressed: () {
                                setState(() => _loading = true);
                                _load();
                              },
                              child: const Text('重试'))),
                    ])
                  : (_filtered.isEmpty)
                      ? ListView(children: [
                          const SizedBox(height: 120),
                          Center(
                              child: Text(_searchCtrl.text.trim().isEmpty
                                  ? '这个目录还没有 .md / .txt / .html 笔记'
                                  : '没有匹配「${_searchCtrl.text.trim()}」的笔记')),
                          const SizedBox(height: 6),
                          Center(
                              child: Text(
                                  _searchCtrl.text.trim().isEmpty
                                      ? '点右下角「新建笔记」写第一篇'
                                      : '换个关键词试试',
                                  style: const TextStyle(
                                      fontSize: 12, color: Colors.grey))),
                        ])
                      : RefreshIndicator(
                          onRefresh: () async => _load(),
                          child: ListView.builder(
                              itemCount: _filtered.length,
                              itemBuilder: (_, i) {
                                final n = _filtered[i];
                                final sel = _selected.contains(n.path);
                                return ListTile(
                                  leading: sel
                                      ? Icon(Icons.check_circle,
                                          color: cs.primary)
                                      : Icon(Icons.description_outlined,
                                          color: cs.primary),
                                  title: Text(n.name,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis),
                                  subtitle: Text(
                                      '${n.modified.split('T').first} · ${n.sizeHuman}',
                                      style: const TextStyle(fontSize: 12)),
                                  trailing: sel
                                      ? null
                                      : const Icon(
                                          Icons.edit_outlined, size: 18),
                                  selected: sel,
                                  onTap: () async {
                                    // 多选态：点按=勾选切换（与照片墙一致）
                                    if (_selecting) {
                                      setState(() => sel
                                          ? _selected.remove(n.path)
                                          : _selected.add(n.path));
                                      return;
                                    }
                                    await openNoteEditor(context,
                                        dir: _dir!, name: n.name);
                                    if (mounted) {
                                      setState(() => _loading = true);
                                      _load();
                                    }
                                  },
                                  onLongPress: () {
                                    if (_selecting) return;
                                    setState(() => _selected.add(n.path));
                                  },
                                );
                              })),
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
  const _Note(
      {required this.name, required this.path,
      required this.sizeHuman, required this.modified});
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

  bool get _isMd =>
      widget.name.toLowerCase().endsWith('.md') ||
      widget.name.toLowerCase().endsWith('.markdown');

  @override
  void initState() {
    super.initState();
    _loadContent();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    _focus.dispose();
    super.dispose();
  }

  String get _remotePath =>
      '${widget.dir}/${widget.name}'.replaceAll('//', '/');

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

  Future<bool> _save() async {
    if (_saving) return false;
    setState(() => _saving = true);
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/upload?path=${Uri.encodeComponent(widget.dir)}',
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
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('已保存到 $_remotePath'),
              duration: const Duration(seconds: 2)));
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
          title: Text(widget.name,
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
