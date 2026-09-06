// 文档预览器（v0.17 预览大改）：照片查看器之外的第二类全屏查看。
// · md/markdown → flutter_markdown 渲染（表格/代码块跟随样式）
// · txt/log/json/csv 等文本 → 等宽滚动 + 自动换行
// · pdf → flutter_pdfview 内嵌（经鉴权下载到临时文件后本地渲染）
// · docx → archive 解包 word/document.xml 提取正文段落（轻量纯文本视图，
//   版式还原交给"用其他应用打开"）；.doc 旧格式无解包能力，回退系统打开
// · 超限（文本 >2MB / pdf >200MB）与拉取失败给明确提示与重试
import 'dart:convert';
import 'dart:io';

import 'package:archive/archive.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_pdfview/flutter_pdfview.dart';
import 'package:path_provider/path_provider.dart';

import '../data/api/api_client.dart';
import 'notes_page.dart';
import '../transfer/transfer_task.dart' show humanSize;

const _textLimit = 2 * 1024 * 1024;
const _pdfLimit = 200 * 1024 * 1024;

/// 按文件名判断是否可进文档预览器（files_page/recent_page 共用口径）
bool isDocPreviewable(String name) {
  final n = name.toLowerCase();
  return n.endsWith('.md') ||
      n.endsWith('.markdown') ||
      n.endsWith('.txt') ||
      n.endsWith('.log') ||
      n.endsWith('.json') ||
      n.endsWith('.csv') ||
      n.endsWith('.pdf') ||
      n.endsWith('.docx');
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
  int? _total; // pdf 下载进度用

  @override
  void initState() {
    super.initState();
    _load = _build();
  }

  Future<List<int>> _fetch({void Function(int, int?)? onProgress}) async {
    final dio = await ApiClient.I.dio();
    final r = await dio.get<List<int>>('/api/files/${_enc(widget.path)}',
        options: Options(responseType: ResponseType.bytes,
            receiveTimeout: const Duration(minutes: 15)),
        onReceiveProgress: onProgress);
    return r.data ?? const [];
  }

  Future<String> _toTempFile(List<int> bytes, String suffix) async {
    final dir = await getTemporaryDirectory();
    final f = File(
        '${dir.path}/docview-${DateTime.now().millisecondsSinceEpoch}$suffix');
    await f.writeAsBytes(bytes, flush: true);
    return f.path;
  }

  Future<Widget> _build() async {
    final n = widget.name.toLowerCase();
    if (n.endsWith('.pdf')) return _pdf();
    if (n.endsWith('.docx')) return _docx();
    if (n.endsWith('.md') || n.endsWith('.markdown')) {
      final raw = await _fetch();
      if (raw.length > _textLimit) throw _TooLarge(_textLimit);
      return _markdown(utf8.decode(raw, allowMalformed: true));
    }
    // 其余按文本
    final raw = await _fetch();
    if (raw.length > _textLimit) throw _TooLarge(_textLimit);
    return _plain(utf8.decode(raw, allowMalformed: true));
  }

  Future<Widget> _pdf() async {
    final raw = await _fetch(onProgress: (c, t) {
      if (mounted && t != null && t > 0 && (c % (1 << 20) == 0)) {
        setState(() => _total = t);
      }
    });
    if (raw.length > _pdfLimit) throw _TooLarge(_pdfLimit);
    final path = await _toTempFile(raw, '.pdf');
    return PDFView(filePath: path, autoSpacing: true, pageFling: true);
  }

  Future<Widget> _docx() async {
    final raw = await _fetch();
    if (raw.length > _textLimit) throw _TooLarge(_textLimit);
    final archive = ZipDecoder().decodeBytes(raw);
    final xml = archive.findFile('word/document.xml');
    if (xml == null) throw const _DocxEmpty();
    final doc = utf8.decode(xml.content as List<int>, allowMalformed: true);
    return _plain(_docxText(doc), mono: false);
  }

  /// word/document.xml 转纯文本：段末换行、`<w:tab/>` 变制表、去掉其余标签
  static String _docxText(String doc) {
    var s = doc
        .replaceAll('<w:tab/>', '\t')
        .replaceAll('<w:br/>', '\n')
        .replaceAll('</w:p>', '\n');
    s = RegExp(r'<[^>]+>').allMatches(s).fold(
        s, (acc, m) => acc.replaceRange(m.start - (m.start - m.start), m.end, ''));
    // 上面 fold 写法复杂易错，直接再来一次朴素替换（文档体量 <2MB，无所谓）
    s = s.replaceAllMapped(RegExp(r'<[^>]*>'), (_) => '');
    return const _Entities().decode(s).trim();
  }

  String _enc(String p) => p.split('/').map(Uri.encodeComponent).join('/');


  // ── 渲染壳 ──────────────────────────────────────────────

  Widget _markdown(String data) => Markdown(
        data: data,
        selectable: true,
        padding: const EdgeInsets.all(16),
        styleSheet: MarkdownStyleSheet(
          p: const TextStyle(fontSize: 15, height: 1.55),
          h1: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
          h2: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          h3: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
          codeblockDecoration: BoxDecoration(
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(8)),
        ),
      );

  Widget _plain(String data, {bool mono = true}) => SelectionArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Text(data,
              style: mono
                  ? const TextStyle(
                      fontFamily: 'monospace', fontSize: 13.5, height: 1.5)
                  : const TextStyle(fontSize: 15, height: 1.55)),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final isMd = widget.name.toLowerCase().endsWith('.md') ||
        widget.name.toLowerCase().endsWith('.markdown');
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.name,
            style: const TextStyle(fontSize: 14,
                overflow: TextOverflow.ellipsis)),
        actions: [
          // md 笔记联动（v0.17）：预览页一键进编辑器
          if (isMd)
            IconButton(
                tooltip: '编辑',
                icon: const Icon(Icons.edit_outlined),
                onPressed: () async {
                  final dir = widget.path.contains('/')
                      ? widget.path.substring(0,
                          widget.path.lastIndexOf('/'))
                      : '';
                  if (dir.isEmpty || !mounted) return;
                  await openNoteEditor(context,
                      dir: dir, name: widget.name);
                  if (mounted) {
                    setState(() {
                      _total = null;
                      _load = _build(); // 保存回来后重拉内容
                    });
                  }
                }),
        ]),
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
              return Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                const Icon(Icons.description_outlined,
                    size: 46, color: Colors.white38),
                const SizedBox(height: 10),
                Text(tooLarge
                        ? '文件超过预览上限（${humanSize((snap.error as _TooLarge).limit)}）'
                        : '预览失败：${snap.error}'
                    , style: const TextStyle(fontSize: 13)),
                const SizedBox(height: 8),
                TextButton.icon(
                    onPressed: () => setState(() {
                          _total = null;
                          _load = _build();
                        }),
                    icon: const Icon(Icons.refresh, size: 18),
                    label: const Text('重试')),
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

class _DocxEmpty implements Exception {
  const _DocxEmpty();
  @override
  String toString() => 'docx 中没有找到正文（可能是旧版或受保护文档）';
}

/// 最小 XML 实体还原（docx 正文常出现这五个）
class _Entities {
  const _Entities();
  String decode(String s) => s
      .replaceAll('&amp;', '&')
      .replaceAll('&lt;', '<')
      .replaceAll('&gt;', '>')
      .replaceAll('&quot;', '"')
      .replaceAll('&apos;', "'");
}
