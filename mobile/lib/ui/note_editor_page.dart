// 全屏文本笔记编辑器（自 notes_page.dart 拆出的第二个页面类）。
// 行为沿革见 notes_page.dart 头注释；草稿 key 与列表页共享（notes_shared.dart）。
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/errors.dart';
import '../data/api/files_api.dart';
import 'notes_shared.dart';

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
      // /preview/ 通道（v0.17.2）：/files/ 被 Caddy forward_auth 拦
      // （authcheck 只认 Cookie），App Bearer 必 401；/preview 对非图片
      // 回 FileResponse，与下载引擎同款
      final bytes = await FilesApi.previewBytes(_remotePath);
      final text = utf8.decode(bytes, allowMalformed: true);
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
    final draft = sp.getString(draftKey(_remotePath));
    if (draft == null || draft.isEmpty || !mounted) return;
    if (remote != null && draft == remote) {
      // 草稿与远端一致（上次其实保存过但清 key 失败）：静默清掉
      // ignore: discarded_futures
      sp.remove(draftKey(_remotePath));
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
                  sp.remove(draftKey(_remotePath)));
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
        .then((sp) => sp.setString(draftKey(_remotePath), v));
  }

  /// silent=true 为自动保存：成功不弹 SnackBar（避免打扰），失败仍提示
  Future<bool> _save({bool silent = false}) async {
    if (_saving) return false;
    setState(() => _saving = true);
    try {
      // on_dup=overwrite（改进建议 9.6-P0-12）：就地编辑直接覆盖源文件，
      // 不走"旧文件进回收站"，杜绝回收站堆积同一笔记的多个副本
      await FilesApi.uploadText(widget.dir, widget.name, _ctrl.text,
          overwrite: true);
      if (!mounted) return true;
      // 保存成功清草稿（v0.18）
      // ignore: discarded_futures
      SharedPreferences.getInstance().then((sp) =>
          sp.remove(draftKey(_remotePath)));
      setState(() => _dirty = false);
      if (!silent) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('已保存到 $_remotePath'),
                duration: const Duration(seconds: 2)));
      }
      return true;
    } catch (e) {
      if (!mounted) return false;
      // 服务端 detail 优先（errors.dart 统一文案），其余回退通用提示
      final msg = errDetail(e);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(msg.isEmpty ? '保存失败，请稍后重试' : msg)));
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
        // 返回即存（9-10 交互优化）：有改动时返回 = 自动保存后退出，
        // 不再每次弹"放弃修改？"打断；内容为空视为误触，按放弃处理
        // （本地草稿保留，重开可恢复）；仅保存失败才弹确认
        if (_ctrl.text.trim().isEmpty) {
          if (context.mounted) Navigator.pop(context);
          return;
        }
        final ok = await _save(silent: true);
        if (!mounted) return;
        if (!ok) {
          if (!context.mounted) return;
          final discard = await showDialog<bool>(
              context: context,
              builder: (dctx) => AlertDialog(
                      title: const Text('保存失败，要放弃这次修改吗？'),
                      actions: [
                        TextButton(
                            onPressed: () => Navigator.pop(dctx, false),
                            child: const Text('继续编辑')),
                        FilledButton(
                            onPressed: () => Navigator.pop(dctx, true),
                            child: const Text('放弃')),
                      ]));
          if (discard == true && context.mounted) Navigator.pop(context);
          return;
        }
        if (!context.mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text('已保存到 $_remotePath'),
            duration: const Duration(seconds: 2)));
        Navigator.pop(context);
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
                    : Stack(children: [
                        Padding(
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
                                    hintText:
                                        '# 标题\n\n用 Markdown 写笔记…'))),
                      ]),
      ),
    );
  }
}
