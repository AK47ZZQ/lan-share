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
// B9 拆分（part 同 library + extension，私有互通、行为零变化）：
// - doc_viewer_formats.dart  取数 + 各格式族渲染 + 系统打开兜底
//   （isDocPreviewable/isNoteEditable 纯函数门面保持在本文件顶层不动）
part 'doc_viewer_formats.dart';
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
  List<int>? _lastRaw; // office 系两段式：先拉字节再转（extension 不能声明实例字段，B9 回收主类）

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
