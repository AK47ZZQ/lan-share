// MD 笔记（v0.17 新功能）：服务端任意目录下的 .md 文件即笔记。
// · 列表：默认打开上次用的目录（prefs 记忆），可换目录（DirPickerPage）
// · 新建：输标题 → 选目录 → 进编辑器（保存时 multipart 直传）
// · 编辑：拉取远端内容 → 全屏 md 源码编辑 → 保存覆盖上传；脏态返回拦确认
// · 预览联动：DocViewerPage 看 md 时点编辑笔进编辑器
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/api_client.dart';
import 'dir_picker_page.dart';

const _kNotesLastDir = 'notes_last_dir';

/// 把笔记编辑器 push 出去的便捷入口（doc_viewer/notes_page 共用）
Future<void> openNoteEditor(BuildContext context,
    {required String dir, required String name, bool isNew = false}) async {
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

  @override
  void initState() {
    super.initState();
    _restore();
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
        if (name.toLowerCase().endsWith('.md') &&
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
    final name = await showDialog<String>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: const Text('新建笔记'),
                content: TextField(
                    controller: nameCtrl,
                    autofocus: true,
                    maxLength: 60,
                    decoration: const InputDecoration(
                        labelText: '标题',
                        hintText: '保存为 <标题>.md')),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, nameCtrl.text.trim()),
                      child: const Text('选目录并创建')),
                ]));
    if (name == null || name.isEmpty || !mounted) return;
    // ignore: use_build_context_synchronously
    final d = await Navigator.push<String>(context, MaterialPageRoute(
        builder: (_) => const DirPickerPage(title: '保存到哪个目录？')));
    if (d == null || !mounted) return;
    (await SharedPreferences.getInstance()).setString(_kNotesLastDir, d);
    if (!mounted) return;
    await openNoteEditor(context,
        dir: d, name: '$name.md', isNew: true);
    if (_dir != null) { setState(() => _loading = true); _load(); }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: Text(_dir == null || _dir!.isEmpty ? '笔记' : '笔记 · $_dir',
            style: const TextStyle(fontSize: 16)),
        actions: [
          IconButton(
              tooltip: '切换目录',
              icon: const Icon(Icons.folder_open),
              onPressed: _chooseDir),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
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
                  child: Text('笔记就是网盘里的 .md 文件，存哪个文件夹由你定。'
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
                  : (_notes == null || _notes!.isEmpty)
                      ? ListView(children: const [
                          SizedBox(height: 120),
                          Center(child: Text('这个目录还没有 .md 笔记')),
                          SizedBox(height: 6),
                          Center(
                              child: Text('点右下角「新建笔记」写第一篇',
                                  style: TextStyle(
                                      fontSize: 12, color: Colors.grey))),
                        ])
                      : RefreshIndicator(
                          onRefresh: () async => _load(),
                          child: ListView.builder(
                              itemCount: _notes!.length,
                              itemBuilder: (_, i) {
                                final n = _notes![i];
                                return ListTile(
                                  leading: Icon(Icons.description_outlined,
                                      color: cs.primary),
                                  title: Text(n.name,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis),
                                  subtitle: Text(
                                      '${n.modified.split('T').first} · ${n.sizeHuman}',
                                      style: const TextStyle(fontSize: 12)),
                                  trailing: const Icon(
                                      Icons.edit_outlined, size: 18),
                                  onTap: () async {
                                    await openNoteEditor(context,
                                        dir: _dir!, name: n.name);
                                    if (mounted) {
                                      setState(() => _loading = true);
                                      _load();
                                    }
                                  },
                                );
                              })),
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
  String? _error;

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
      setState(() { _loading = false; _dirty = true; });
      _focus.requestFocus();
      return;
    }
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get<List<int>>(
          '/api/files/${_enc(_remotePath)}',
          options: Options(responseType: ResponseType.bytes));
      final text = utf8.decode(r.data ?? const [], allowMalformed: true);
      if (!mounted) return;
      setState(() {
        _ctrl.text = text;
        _loading = false;
      });
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
      setState(() => _dirty = false);
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('已保存到 $_remotePath'),
              duration: const Duration(seconds: 2)));
      return true;
    } catch (e) {
      if (!mounted) return false;
      final d = e is Exception ? '保存失败，请稍后重试' : '保存失败';
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(d)));
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
                : Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: TextField(
                        controller: _ctrl,
                        focusNode: _focus,
                        maxLines: null,
                        expands: true,
                        textAlignVertical: TextAlignVertical.top,
                        keyboardType: TextInputType.multiline,
                        onChanged: (_) => setState(() => _dirty = true),
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
