// 文档预览器（v0.19 预览兼容重构）：
// · md/markdown → flutter_markdown 渲染
// · txt/log/json/yaml/py/js 等文本 → 等宽滚动 + 自动换行
// · pdf → flutter_pdfview 内嵌（经鉴权下载到临时文件后本地渲染）
// · docx → doc_extract 纯 Dart 解析：标题/加粗/斜体/列表/表格转 Markdown，
//   内嵌图片落临时目录随 Markdown 渲染（v0.17 只能抽正文，v0.19 版式重构）
// · xlsx/csv → 表格化 Markdown；pptx → 逐页大纲；rtf/doc → 尽力提取文本
// · html/htm → 富文本笔记预览（WebView 只读，与编辑器同源渲染）
// · 任意格式 → App 栏「用其他应用打开」兜底（下载临时文件后系统打开）
// · 超限与失败给明确提示与重试
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_pdfview/flutter_pdfview.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';

import '../data/api/files_api.dart';
import 'doc_extract.dart';
import 'notes_page.dart';
import 'rich_note_editor.dart';
import 'theme/design_tokens.dart';
import '../transfer/transfer_task.dart' show humanSize;

const _textLimit = 2 * 1024 * 1024;
const _pdfLimit = 200 * 1024 * 1024;
const _officeLimit = 100 * 1024 * 1024;
const _openExtLimit = 100 * 1024 * 1024;

/// 按文件名判断是否可进文档预览器（files_page/recent_page 共用口径）。
/// v0.19 扩面：docx 富转换、xlsx/pptx 解析、rtf/doc 尽力提取、html 富预览。
bool isDocPreviewable(String name) {
  final n = name.toLowerCase();
  return n.endsWith('.md') ||
      n.endsWith('.markdown') ||
      n.endsWith('.txt') ||
      n.endsWith('.log') ||
      n.endsWith('.json') ||
      n.endsWith('.csv') ||
      n.endsWith('.pdf') ||
      n.endsWith('.docx') ||
      n.endsWith('.doc') ||
      n.endsWith('.xlsx') ||
      n.endsWith('.pptx') ||
      n.endsWith('.rtf') ||
      n.endsWith('.html') ||
      n.endsWith('.htm') ||
      n.endsWith('.yaml') ||
      n.endsWith('.yml') ||
      n.endsWith('.xml') ||
      n.endsWith('.ini') ||
      n.endsWith('.conf') ||
      n.endsWith('.cfg') ||
      n.endsWith('.properties') ||
      n.endsWith('.py') ||
      n.endsWith('.js') ||
      n.endsWith('.ts') ||
      n.endsWith('.dart') ||
      n.endsWith('.html') ||
      n.endsWith('.css') ||
      n.endsWith('.sh') ||
      n.endsWith('.bat') ||
      n.endsWith('.ps1') ||
      n.endsWith('.sql') ||
      n.endsWith('.srt');
}

/// 这些后缀同时可在预览页一键进编辑器（文本类进纯文本编辑器，html 进富文本）
bool isNoteEditable(String name) {
  final n = name.toLowerCase();
  return n.endsWith('.md') ||
      n.endsWith('.markdown') ||
      n.endsWith('.txt') ||
      n.endsWith('.log') ||
      n.endsWith('.json') ||
      n.endsWith('.csv') ||
      n.endsWith('.yaml') ||
      n.endsWith('.yml') ||
      n.endsWith('.xml') ||
      n.endsWith('.ini') ||
      n.endsWith('.conf') ||
      n.endsWith('.py') ||
      n.endsWith('.js') ||
      n.endsWith('.ts') ||
      n.endsWith('.dart') ||
      n.endsWith('.css') ||
      n.endsWith('.sh') ||
      n.endsWith('.bat') ||
      n.endsWith('.ps1') ||
      n.endsWith('.sql') ||
      n.endsWith('.srt');
}

class DocViewerPage extends StatefulWidget {
  final String path; // 远端相对路径（/ 分隔）
  final String name;
  const DocViewerPage({super.key, required this.path, required this.name});

  @override
  State<DocViewerPage> createState() => _DocViewerPageState();
}

class _DocViewerPageState extends State<DocViewerPage> {
  Future<Widget>? _load;
  int? _total; // 大文件下载进度用
  final _tmpFiles = <File>[]; // 本页生成的临时文件，dispose 清理
  final _tmpDirs = <Directory>[]; // docx 图片目录（递归清）
  bool _openingExternal = false;

  @override
  void initState() {
    super.initState();
    _load = _build();
  }

  @override
  void dispose() {
    for (final f in _tmpFiles) {
      // ignore: discarded_futures
      f.delete().catchError((_) => f);
    }
    for (final d in _tmpDirs) {
      // ignore: discarded_futures
      d.delete(recursive: true).catchError((_) => d);
    }
    super.dispose();
  }

  Future<List<int>> _fetch({required int limit, void Function(int, int?)? onProgress}) async {
    // 走 /preview/ 而非 /files/（v0.17.2）：Caddy 对 /files/* 挂了
    // forward_auth→/authcheck，而 authcheck 只查 Cookie 会话不认 App 的
    // Bearer——App 侧 /files 恒 401（还触发拦截器登出）。/preview 对
    // 非图片直接回 FileResponse，且走正常反代（下载引擎同款通道）。
    // HEAD 大小预检/15 分钟超时/进度透传已收进 data 层（T7 第四批）。
    try {
      return await FilesApi.previewChecked(widget.path,
          maxBytes: limit, onProgress: onProgress);
    } on FileTooLarge {
      throw _TooLarge(limit);
    }
  }

  Future<String> _toTempFile(List<int> bytes, String suffix) async {
    final dir = await getTemporaryDirectory();
    final f = File(
        '${dir.path}/docview-${DateTime.now().millisecondsSinceEpoch}$suffix');
    await f.writeAsBytes(bytes, flush: true);
    _tmpFiles.add(f); // dispose 时清理（review P2-2）
    return f.path;
  }

  /// docx 内嵌图片的落盘目录（dispose 时递归删除）
  Future<String> _newMediaDir() async {
    final dir = await getTemporaryDirectory();
    final d = Directory(
        '${dir.path}/docview-media-${DateTime.now().millisecondsSinceEpoch}');
    await d.create(recursive: true);
    _tmpDirs.add(d);
    return d.path;
  }

  Future<Widget> _build() async {
    final n = widget.name.toLowerCase();
    if (n.endsWith('.pdf')) return _pdf();
    if (n.endsWith('.docx')) return _docx();
    if (n.endsWith('.xlsx')) return _office(() => xlsxToMarkdown(_lastRaw!));
    if (n.endsWith('.pptx')) {
      return _office(() => pptxToText(_lastRaw!), mono: true);
    }
    if (n.endsWith('.rtf')) return _rtf();
    if (n.endsWith('.doc')) return _docLegacy();
    if (n.endsWith('.csv')) return _csv();
    if (n.endsWith('.html') || n.endsWith('.htm')) {
      final raw = await _fetch(limit: _officeLimit);
      return RichNoteViewer(html: utf8.decode(raw, allowMalformed: true));
    }
    if (n.endsWith('.md') || n.endsWith('.markdown')) {
      final raw = await _fetch(limit: _textLimit);
      if (raw.length > _textLimit) throw _TooLarge(_textLimit);
      return _markdown(utf8.decode(raw, allowMalformed: true));
    }
    // 其余按文本
    final raw = await _fetch(limit: _textLimit);
    if (raw.length > _textLimit) throw _TooLarge(_textLimit);
    return _plain(utf8.decode(raw, allowMalformed: true));
  }

  List<int>? _lastRaw; // office 系两段式：先拉字节，再转（便于复用拉取逻辑）

  /// zip 型 office 文档（docx/xlsx/pptx）：拉取 → 转换 → 渲染
  Future<Widget> _office(String Function() convert, {bool mono = false}) async {
    final raw = await _fetch(limit: _officeLimit, onProgress: (c, t) {
      if (mounted && t != null && t > 0 && (c % (512 * 1024) == 0)) {
        setState(() => _total = t);
      }
    });
    _lastRaw = raw;
    if (raw.length > _officeLimit) throw _TooLarge(_officeLimit);
    final md = convert();
    return mono ? _plain(md) : _markdown(md, images: true);
  }

  Future<Widget> _pdf() async {
    final raw = await _fetch(limit: _pdfLimit, onProgress: (c, t) {
      if (mounted && t != null && t > 0 && (c % (512 * 1024) == 0)) {
        setState(() => _total = t);
      }
    });
    if (raw.length > _pdfLimit) throw _TooLarge(_pdfLimit);
    final path = await _toTempFile(raw, '.pdf');
    return PDFView(filePath: path, autoSpacing: true, pageFling: true);
  }

  Future<Widget> _docx() async {
    final raw = await _fetch(limit: _officeLimit, onProgress: (c, t) {
      if (mounted && t != null && t > 0 && (c % (512 * 1024) == 0)) {
        setState(() => _total = t);
      }
    });
    _lastRaw = raw;
    if (raw.length > _officeLimit) throw _TooLarge(_officeLimit);
    final mediaDir = await _newMediaDir();
    final md = docxToMarkdown(raw, mediaDir: mediaDir);
    return _markdown(md, images: true);
  }

  Future<Widget> _csv() async {
    final raw = await _fetch(limit: _textLimit);
    final md = csvToMarkdown(utf8.decode(raw, allowMalformed: true));
    return _markdown(md);
  }

  Future<Widget> _rtf() async {
    final raw = await _fetch(limit: _textLimit);
    return _plain(rtfToText(utf8.decode(raw, allowMalformed: true)));
  }

  Future<Widget> _docLegacy() async {
    final raw = await _fetch(limit: _officeLimit, onProgress: (c, t) {
      if (mounted && t != null && t > 0 && (c % (512 * 1024) == 0)) {
        setState(() => _total = t);
      }
    });
    if (raw.length > _officeLimit) throw _TooLarge(_officeLimit);
    return _plain(docToTextBestEffort(raw));
  }

  /// 兜底：任意格式下载到临时文件后交系统应用打开（xls/ppt/zip 等的出路）
  Future<void> _openExternal() async {
    if (_openingExternal) return;
    setState(() => _openingExternal = true);
    try {
      final raw = await _fetch(limit: _openExtLimit, onProgress: (c, t) {
        if (mounted && t != null && t > 0 && (c % (1024 * 1024) == 0)) {
          setState(() => _total = t);
        }
      });
      final i = widget.name.lastIndexOf('.');
      final suffix = i >= 0 ? widget.name.substring(i) : '';
      // 外部 App 要读这个文件，不能进 dispose 清理名单
      final dir = await getTemporaryDirectory();
      final f = File(
          '${dir.path}/openext-${DateTime.now().millisecondsSinceEpoch}$suffix');
      await f.writeAsBytes(raw, flush: true);
      final r = await OpenFilex.open(f.path);
      if (mounted) {
        if (r.type != ResultType.done) {
          ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text('系统没有能打开这类文件的应用')));
        }
      }
    } on _TooLarge {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text('文件超过 100MB，请先下载后再打开')));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('打开失败，请稍后重试')));
      }
    } finally {
      if (mounted) setState(() => _openingExternal = false);
    }
  }

  // ── 渲染壳 ──────────────────────────────────────────────

  Widget _markdown(String data, {bool images = false}) => Markdown(
        data: data,
        selectable: true,
        padding: const EdgeInsets.all(Gap.md),
        sizedImageBuilder: images
            ? (config) {
                final uri = config.uri;
                if (uri.scheme == 'file') {
                  final f = File.fromUri(uri);
                  if (f.existsSync()) {
                    return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 6),
                        child: ClipRRect(
                            borderRadius: Radii.bXs,
                            child: Image.file(f,
                                width: 280,
                                fit: BoxFit.contain,
                                semanticLabel:
                                    f.uri.pathSegments.last)));
                  }
                }
                return const SizedBox.shrink();
              }
            : null,
        styleSheet: MarkdownStyleSheet(
          p: const TextStyle(fontSize: 15, height: 1.55),
          h1: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
          h2: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          h3: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
          codeblockDecoration: BoxDecoration(
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              borderRadius: Radii.bXs),
        ),
      );

  Widget _plain(String data, {bool mono = true}) => SelectionArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(Gap.md),
          child: Text(data,
              style: mono
                  ? const TextStyle(
                      fontFamily: 'monospace', fontSize: 13.5, height: 1.5)
                  : const TextStyle(fontSize: 15, height: 1.55)),
        ),
      );

  @override
  Widget build(BuildContext context) {
    // 可编辑文本（v0.17 md → v0.19 全文本+富文本）：预览页一键进编辑器
    final editable = isNoteEditable(widget.name) ||
        widget.name.toLowerCase().endsWith('.html') ||
        widget.name.toLowerCase().endsWith('.htm');
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.name,
            style: const TextStyle(fontSize: 14,
                overflow: TextOverflow.ellipsis)),
        actions: [
          if (editable)
            IconButton(
                tooltip: '编辑',
                icon: const Icon(Icons.edit_outlined),
                onPressed: () async {
                  final dir = widget.path.contains('/')
                      ? widget.path.substring(0,
                          widget.path.lastIndexOf('/'))
                      : '';
                  if (dir.isEmpty || !mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                        content: Text('根目录下的文件请先移入子目录再编辑'),
                        duration: Duration(seconds: 2)));
                    return;
                  }
                  await openNoteEditor(context,
                      dir: dir, name: widget.name);
                  if (mounted) {
                    setState(() {
                      _total = null;
                      _load = _build(); // 保存回来后重拉内容
                    });
                  }
                }),
          // 万能兜底（v0.19）：任何格式下载后交系统应用打开
          IconButton(
              tooltip: '用其他应用打开',
              icon: _openingExternal
                  ? const SizedBox(
                      width: 18, height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.open_in_new),
              onPressed: _openExternal),
        ],
      ),
      body: FutureBuilder<Widget>(
          future: _load,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                const CircularProgressIndicator(),
                if (_total != null) ...[
                  const SizedBox(height: 10),
                  Text('下载中 ${humanSize(_total!)}',
                      style: const TextStyle(fontSize: 12)),
                ],
              ]));
            }
            if (snap.hasError) {
              final tooLarge = snap.error is _TooLarge;
              final msg = snap.error.toString();
              final rough = snap.error is FormatException ||
                  msg.contains('未能') || msg.contains('粗略');
              return Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                const Icon(Icons.description_outlined,
                    size: 46, color: Colors.white38),
                const SizedBox(height: 10),
                Padding(
                    padding: const EdgeInsets.symmetric(horizontal: Gap.xxl),
                    child: Text(tooLarge
                            ? '文件超过预览上限（${humanSize((snap.error as _TooLarge).limit)}）'
                            : msg.length > 120
                                ? '预览失败：${msg.substring(0, 120)}'
                                : '预览失败：$msg',
                        textAlign: TextAlign.center,
                        style: const TextStyle(fontSize: 13))),
                const SizedBox(height: Gap.xs),
                Row(mainAxisSize: MainAxisSize.min, children: [
                  if (!tooLarge)
                    TextButton.icon(
                        onPressed: () => setState(() {
                              _total = null;
                              _load = _build();
                            }),
                        icon: const Icon(Icons.refresh, size: 18),
                        label: const Text('重试')),
                  TextButton.icon(
                      onPressed: _openExternal,
                      icon: const Icon(Icons.open_in_new, size: 18),
                      label: const Text('用其他应用打开')),
                ]),
                if (rough) ...[
                  const SizedBox(height: Gap.xxs),
                  Text('此类旧版格式仅能粗略提取，重要文档建议转换后再预览',
                      style: TextStyle(
                          fontSize: 11, color: Theme.of(context)
                              .colorScheme.onSurfaceVariant)),
                ],
              ]));
            }
            return snap.data ?? const SizedBox.shrink();
          }),
    );
  }
}

class _TooLarge implements Exception {
  final int limit;
  const _TooLarge(this.limit);
  @override
  String toString() => '文件过大';
}
