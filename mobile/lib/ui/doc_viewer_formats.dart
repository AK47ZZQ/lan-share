// part of doc_viewer.dart —— 取数与格式族渲染（B9 拆分）。
// 同 library 私有互通：extension on _DocViewerPageState，方法与拆分前逐字一致
//（零行为变化）。覆盖：_fetch/_toTempFile/_newMediaDir/_build 与
// office/pdf/docx/csv/rtf/doc 各格式族 + 系统打开兜底 + markdown/纯文本渲染壳。

part of 'doc_viewer.dart';

// extension on State 内调用 setState/mounted 等会触发 Flutter 的 protected
// 提示（面向『子类外』场景）；本 extension 只应用于包内 _DocViewerPageState，
// 属受控用法，精准豁免。
// ignore_for_file: invalid_use_of_protected_member

extension _DocViewerFormats on _DocViewerPageState {


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
}
