// 富文本笔记（v0.19 新增）：像写日记一样排版，存成 .html 文件。
// · 引擎：html_editor_enhanced（Summernote 内核 WebView）——表格/字体
//   颜色大小/图片 base64 内嵌/对齐缩进/引用代码块等主流笔记能力
//   （Quill 的 Delta 模型没有表格，弃选；HTML 文件即笔记，网盘可移植、
//   Web 端浏览器可直接预览）
// · RichNoteViewer：只读渲染（DocViewerPage 对 .html 的预览视图）
// · RichNoteEditorPage：编辑 + 保存（multipart 直传，同纯文本笔记通道）
// · 图片插入：相册选取 → base64 data URI 内嵌进 HTML（自包含单文件，
//   移动/改名不断链）；表情：常用 emoji 网格一键插入（系统键盘也可）
import 'dart:async';
import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:html_editor_enhanced/html_editor.dart';

import '../data/api/errors.dart';
import '../data/api/files_api.dart';
import 'theme/design_tokens.dart';

/// 常用表情快捷面板（系统键盘自带 emoji，这里给"像笔记 App"的快捷入口）
const _kEmojis = [
  '😀', '😄', '😅', '😂', '🥰', '😍', '🤔', '😴',
  '👍', '👏', '🙏', '💪', '🔥', '✨', '❤️', '💔',
  '🎉', '🎂', '🍎', '🍚', '☕', '🌈', '☀️', '🌙',
  '📌', '📝', '✅', '❌', '⭐', '💡', '🚀', '🐱',
];

String _imageMime(String ext) => switch (ext.toLowerCase()) {
      '.png' => 'image/png',
      '.jpg' || '.jpeg' => 'image/jpeg',
      '.gif' => 'image/gif',
      '.webp' => 'image/webp',
      '.bmp' => 'image/bmp',
      '.svg' => 'image/svg+xml',
      _ => 'application/octet-stream',
    };

// ── 只读预览（DocViewer 对 .html 用）────────────────────────

class RichNoteViewer extends StatelessWidget {
  final String html;
  const RichNoteViewer({super.key, required this.html});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return HtmlEditor(
      controller: HtmlEditorController(),
      htmlEditorOptions: HtmlEditorOptions(
        initialText: html,
        disabled: true, // 只读（包内字段名是 disabled，不是 readOnly）
        darkMode: isDark,
        autoAdjustHeight: false,
      ),
      // 只读预览不显示工具栏：空按钮列表
      htmlToolbarOptions: const HtmlToolbarOptions(
          toolbarType: ToolbarType.nativeScrollable,
          defaultToolbarButtons: []),
      otherOptions: OtherOptions(height: MediaQuery.of(context).size.height),
    );
  }
}

// ── 编辑器 ─────────────────────────────────────────────────

/// 把富文本编辑器 push 出去（notes_page / doc_viewer 共用）
Future<void> openRichNoteEditor(BuildContext context,
    {required String dir, required String name, bool isNew = false}) async {
  await Navigator.push(context, MaterialPageRoute(
      builder: (_) =>
          RichNoteEditorPage(dir: dir, name: name, isNew: isNew)));
}

class RichNoteEditorPage extends StatefulWidget {
  final String dir;
  final String name;
  final bool isNew;
  const RichNoteEditorPage(
      {super.key, required this.dir, required this.name,
      this.isNew = false});

  @override
  State<RichNoteEditorPage> createState() => _RichNoteEditorPageState();
}

class _RichNoteEditorPageState extends State<RichNoteEditorPage> {
  final _ctrl = HtmlEditorController();
  bool _loading = true;
  bool _saving = false;
  bool _dirty = false;
  String? _error;
  String _initialHtml = '';
  Timer? _autoTimer; // 自动保存定时器（改进建议 9.6-P0-12）

  @override
  void initState() {
    super.initState();
    _loadContent();
    // 自动保存：每 30s 静默覆盖上传一次脏内容；失败内容仍在编辑器，可重试
    _autoTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted && _dirty && !_saving && !_loading && _error == null) {
        _save(silent: true);
      }
    });
  }

  @override
  void dispose() {
    _autoTimer?.cancel();
    super.dispose();
  }

  String get _remotePath =>
      '${widget.dir}/${widget.name}'.replaceAll('//', '/');

  /// AppBar 标题：笔记在子文件夹（分类）里时显示"分类名 / 文件名"，
  /// 直接在笔记根目录则只显示文件名（与纯文本编辑器同款）
  String get _title {
    final seg =
        widget.dir.split('/').where((s) => s.trim().isNotEmpty).toList();
    if (seg.isEmpty) return widget.name;
    return '${seg.last} / ${widget.name}';
  }

  Future<void> _loadContent() async {
    if (widget.isNew) {
      if (!mounted) return;
      setState(() {
        _initialHtml = '';
        _loading = false;
      });
      return;
    }
    try {
      // /preview/ 通道：与纯文本笔记同款（/files/ 被 Caddy forward_auth 拦）
      final bytes = await FilesApi.previewBytes(_remotePath);
      _initialHtml = utf8.decode(bytes, allowMalformed: true);
      if (!mounted) return;
      setState(() => _loading = false);
    } catch (e) {
      if (!mounted) return;
      final s = e.toString();
      setState(() {
        _error = s.length > 140 ? s.substring(0, 140) : s;
        _loading = false;
      });
    }
  }

  /// silent=true 为自动保存：成功不弹 SnackBar（避免打扰），失败仍提示
  Future<bool> _save({bool silent = false}) async {
    if (_saving) return false;
    setState(() => _saving = true);
    try {
      final html = await _ctrl.getText(); // 包内返回非空 Future<String>
      // on_dup=overwrite（改进建议 9.6-P0-12）：就地编辑直接覆盖源文件，
      // 不走"旧文件进回收站"，杜绝回收站堆积同一笔记的多个副本
      await FilesApi.uploadText(widget.dir, widget.name,
          html.isEmpty ? '<p></p>' : html,
          mimeType: 'text/html', overwrite: true);
      if (!mounted) return true;
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

  /// 相册选图 → base64 内嵌（自包含单文件）
  Future<void> _insertImage() async {
    final fr = await FilePicker.platform.pickFiles(
        type: FileType.image, withData: true);
    final data = fr?.files.singleOrNull?.bytes;
    if (data == null || !mounted) return;
    final name = fr!.files.single.name;
    final mime = _imageMime(
        name.contains('.') ? name.substring(name.lastIndexOf('.')) : '');
    if (mime == 'application/octet-stream') {
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('不支持的图片格式')));
      return;
    }
    // controller.insertHtml 是同步 void，无需 await
    _ctrl.insertHtml(
        '<img src="data:$mime;base64,${base64Encode(data)}" '
        'style="max-width:100%;">');
    setState(() => _dirty = true);
  }

  Future<void> _insertEmoji() async {
    final e = await showModalBottomSheet<String>(
        context: context,
        showDragHandle: true,
        builder: (bctx) => SafeArea(
                child: Padding(
                    padding: const EdgeInsets.fromLTRB(Gap.sm, 0, Gap.sm, Gap.sm),
                    child: Wrap(spacing: 6, runSpacing: 6, children: [
                      for (final em in _kEmojis)
                        InkWell(
                            onTap: () => Navigator.pop(bctx, em),
                            borderRadius: Radii.bXs,
                            child: Padding(
                                padding: const EdgeInsets.all(6),
                                child:
                                    Text(em, style: const TextStyle(
                                        fontSize: 24)))),
                    ]))));
    if (e == null) return;
    _ctrl.insertHtml(e);
    setState(() => _dirty = true);
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
              style: const TextStyle(fontSize: 14,
                  overflow: TextOverflow.ellipsis)),
          actions: [
            if (_dirty && !_saving)
              Padding(
                  padding: const EdgeInsets.only(right: Gap.xxs),
                  child: Center(
                      child: Text('未保存',
                          style: TextStyle(
                              fontSize: 11.5, color: cs.tertiary)))),
            IconButton(
                tooltip: '插入图片',
                icon: const Icon(Icons.image_outlined),
                onPressed: _loading || _error != null
                    ? null
                    : _insertImage),
            IconButton(
                tooltip: '插入表情',
                icon: const Icon(Icons.emoji_emotions_outlined),
                onPressed: _loading || _error != null
                    ? null
                    : _insertEmoji),
            IconButton(
                tooltip: '保存',
                icon: _saving
                    ? const SizedBox(
                        width: 18, height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.save_outlined),
                onPressed: _loading || _error != null ? null : _save),
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
                    const SizedBox(height: Gap.xs),
                    Center(
                        child: TextButton(
                            onPressed: _loadContent,
                            child: const Text('重试'))),
                  ])
                : Column(children: [
                    Expanded(
                        child: HtmlEditor(
                      controller: _ctrl,
                      htmlEditorOptions: HtmlEditorOptions(
                        initialText: _initialHtml,
                        hint: '像写日记一样记录……',
                        darkMode:
                            Theme.of(context).brightness == Brightness.dark,
                      ),
                      htmlToolbarOptions: HtmlToolbarOptions(
                        toolbarType: ToolbarType.nativeGrid,
                        // defaultToolbarButtons 是 List<Toolbar>（不是聚合类）
                        defaultToolbarButtons: const [
                          StyleButtons(style: false),
                          FontSettingButtons(
                              fontName: false, fontSizeUnit: false),
                          FontButtons(
                              superscript: false, subscript: false),
                          ColorButtons(),
                          ListButtons(listStyles: false),
                          ParagraphButtons(
                              alignJustify: false,
                              textDirection: false,
                              lineHeight: false,
                              caseConverter: false),
                          InsertButtons(
                              picture: false, // 图片走 App 内相册选择（base64）
                              audio: false,
                              video: false,
                              otherFile: false),
                          OtherButtons(
                              fullscreen: false,
                              codeview: false,
                              help: false,
                              copy: false,
                              paste: false),
                        ],
                      ),
                      // Callbacks 挂在 HtmlEditor 顶层（不是 HtmlToolbarOptions），
                      // 内容变化事件名为 onChangeContent
                      callbacks: Callbacks(onChangeContent: (String? _) {
                        if (!_dirty) setState(() => _dirty = true);
                      }),
                      otherOptions: OtherOptions(
                          height: MediaQuery.of(context).size.height),
                    )),
                  ]),
      ),
    );
  }
}

// singleOrNull 在 collection 包，这里手写避免引依赖
extension _SingleOrNull<T> on List<T> {
  T? get singleOrNull => length == 1 ? first : null;
}
