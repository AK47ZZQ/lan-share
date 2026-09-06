// 文档内容提取库（v0.19 预览兼容重构）：纯 Dart，基于 archive 解包，
// 不依赖任何原生库——质量目标是"内容完整可读"而非版式还原。
// · docx → Markdown（标题/加粗/斜体/列表/表格/内嵌图片落临时目录）
// · xlsx → Markdown 表格（前 3 个工作表，行列有截断上限）
// · csv  → Markdown 表格
// · pptx → 逐页大纲文本
// · rtf  → 纯文本（控制字剥离）
// · doc  → 二进制启发式抽取（老 Word 无公开文档结构，尽力而为）
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart';

const _rowCap = 200;
const _colCap = 30;
const _sheetCap = 3;
const _textCap = 200000;

/// 最小 XML 实体还原（docx/xlsx 正文常出现这五个）
String xmlDecode(String s) => s
    .replaceAll('&amp;', '&')
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&quot;', '"')
    .replaceAll('&apos;', "'");

String _escCell(String s) => s.replaceAll('|', r'\|').replaceAll('\n', ' ');

// ── docx → Markdown ─────────────────────────────────────────

/// 解包 docx，转成 Markdown。图片落到 [mediaDir]（null 则丢弃图片），
/// 正文以 `file:///` URI 引用。解析失败抛异常由调用方兜底提示。
String docxToMarkdown(List<int> bytes, {String? mediaDir}) {
  final zip = ZipDecoder().decodeBytes(bytes);
  final doc = _zipText(zip, 'word/document.xml');
  if (doc.isEmpty) throw const FormatException('docx 中没有正文');
  // 关系表：rId → media 路径（图片引用要用）
  final rels = <String, String>{};
  final relsXml = _zipText(zip, 'word/_rels/document.xml.rels');
  for (final m in RegExp(
          r'<Relationship\b[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"')
      .allMatches(relsXml)) {
    rels[m.group(1)!] = m.group(2)!;
  }

  final body = _between(doc, '<w:body>', '</w:body>') ?? doc;
  final out = StringBuffer();

  // 表格先摘出来（其内部含 <w:p>，先摘才能按段落切顶层）
  final parts = <Object>[]; // String=段落文本Markdown / _MdTable
  var cursor = 0;
  for (final m in RegExp(r'<w:tbl>.*?</w:tbl>', dotAll: true)
      .allMatches(body)) {
    _pushParagraphs(body.substring(cursor, m.start), parts, zip, mediaDir, rels);
    parts.add(_MdTable(_tableCells(m.group(0)!)));
    cursor = m.end;
  }
  _pushParagraphs(body.substring(cursor), parts, zip, mediaDir, rels);

  var prevBlank = true; // 开头压掉空行
  for (final p in parts) {
    if (p is _MdTable) {
      out.write('\n');
      out.write(p.markdown);
      out.write('\n');
      prevBlank = true;
      continue;
    }
    final line = p as String;
    if (line.isEmpty) {
      if (!prevBlank) out.write('\n');
      prevBlank = true;
      continue;
    }
    out.write(line);
    out.write('\n\n');
    prevBlank = false;
  }
  final md = out.toString();
  return md.length > _textCap ? md.substring(0, _textCap) : md;
}

void _pushParagraphs(String xml, List<Object> parts, Archive zip,
    String? mediaDir, Map<String, String> rels) {
  for (final pm in RegExp(r'<w:p\b[^>]*>.*?</w:p>|<w:p\b[^>]*/>', dotAll: true)
      .allMatches(xml)) {
    parts.add(_paragraphMarkdown(pm.group(0)!, zip, mediaDir, rels));
  }
}

// 段落 → 一行 Markdown（标题/加粗/斜体/列表项/首图）
String _paragraphMarkdown(
    String pXml, Archive zip, String? mediaDir, Map<String, String> rels) {
  // 段落属性
  final pPr = _between(pXml, '<w:pPr>', '</w:pPr>') ?? '';
  final style = RegExp(r'<w:pStyle w:val="([^"]*)"').firstMatch(pPr)?.group(1);
  final isList = pPr.contains('<w:numPr>');
  // 标题：Heading1/heading 2/标题 1 等常见命名
  var heading = 0;
  final hm = RegExp(r'^(?:Heading|heading|标题)\s*(\d)').firstMatch(style ?? '');
  if (hm != null) heading = int.parse(hm.group(1)!);

  // 图片：rId → 解包落盘 → file:/// URI
  var image = '';
  final blip = RegExp(r'<a:blip[^>]*r:embed="([^"]+)"').firstMatch(pXml);
  if (blip != null) {
    final target = rels[blip.group(1)!];
    if (target != null && mediaDir != null) {
      final uri = _extractImageSync(zip, target, mediaDir);
      if (uri != null) image = ' ![]($uri)';
    }
  }

  // runs
  final text = StringBuffer();
  for (final rm in RegExp(r'<w:r\b[^>]*>.*?</w:r>|<w:r\b[^>]*/>', dotAll: true)
      .allMatches(pXml)) {
    final rXml = rm.group(0)!;
    final rPr = _between(rXml, '<w:rPr>', '</w:rPr>') ?? '';
    final bold = RegExp(r'<w:b\b\s*/>').hasMatch(rPr);
    final italic = RegExp(r'<w:i\b\s*/>').hasMatch(rPr);
    var t = '';
    for (final tm
        in RegExp(r'<w:t(?: [^>]*)?>(.*?)</w:t>', dotAll: true).allMatches(rXml)) {
      t += tm.group(1)!;
    }
    t = xmlDecode(t);
    if (rXml.contains('<w:tab/>')) t = '\t$t';
    if (rXml.contains('<w:br/>')) t = '$t\n';
    if (t.isEmpty) continue;
    if (bold) t = '**$t**';
    if (italic) t = '*$t*';
    text.write(t);
  }
  var line = text.toString();
  if (isList && !line.startsWith(RegExp(r'[-*]\s'))) line = '- $line';
  if (heading > 0 && heading <= 6) line = '${'#' * heading} $line';
  if (image.isNotEmpty) line = '$line$image';
  return line;
}

/// 把 docx 的 word/media 内嵌图片写到 [mediaDir]，返回 file:/// URI 或 null
String? _extractImageSync(Archive zip, String relTarget, String mediaDir) {
  try {
    final f = zip.findFile('word/$relTarget') ?? zip.findFile(relTarget);
    if (f == null) return null;
    final name = relTarget.split('/').last;
    final out = File('$mediaDir/$name');
    out.writeAsBytesSync(f.content as List<int>);
    return Uri.file(out.path).toString();
  } catch (_) {
    return null;
  }
}

class _MdTable {
  final List<List<String>> cells;
  _MdTable(this.cells);
  String get markdown {
    if (cells.isEmpty) return '';
    final w = cells.fold<int>(0, (s, r) => r.length > s ? r.length : s);
    final rows = cells
        .map((r) => '| ${[for (var i = 0; i < w; i++) _escCell(i < r.length ? r[i] : '')].join(' | ')} |')
        .toList();
    final sep = '|${List.filled(w, ' --- ').join('|')}|';
    return '${rows[0]}\n$sep\n${rows.skip(1).join('\n')}';
  }
}

List<List<String>> _tableCells(String tblXml) {
  final rows = <List<String>>[];
  for (final rm in RegExp(r'<w:tr\b[^>]*>.*?</w:tr>', dotAll: true)
      .allMatches(tblXml)) {
    final cells = <String>[];
    for (final cm in RegExp(r'<w:tc\b[^>]*>.*?</w:tc>', dotAll: true)
        .allMatches(rm.group(0)!)) {
      final buf = StringBuffer();
      for (final tm in RegExp(r'<w:t(?: [^>]*)?>(.*?)</w:t>', dotAll: true)
          .allMatches(cm.group(0)!)) {
        buf.write(tm.group(1)!);
      }
      cells.add(xmlDecode(buf.toString()).trim());
    }
    if (cells.isNotEmpty) rows.add(cells);
    if (rows.length >= _rowCap) break;
  }
  return rows;
}

// ── xlsx → Markdown ─────────────────────────────────────────

String xlsxToMarkdown(List<int> bytes) {
  final zip = ZipDecoder().decodeBytes(bytes);
  final shared = <String>[];
  final sst = _zipText(zip, 'xl/sharedStrings.xml');
  for (final m
      in RegExp(r'<si\b[^>]*>.*?</si>|<si\b[^>]*/>', dotAll: true)
          .allMatches(sst)) {
    final buf = StringBuffer();
    for (final t in RegExp(r'<t[^>]*>(.*?)</t>', dotAll: true)
        .allMatches(m.group(0)!)) {
      buf.write(t.group(1)!);
    }
    shared.add(xmlDecode(buf.toString()));
  }
  // 工作表名（按 workbook 顺序）+ 关系
  final names = <String>[];
  for (final m in RegExp(r'<sheet\b[^>]*name="([^"]+)"')
      .allMatches(_zipText(zip, 'xl/workbook.xml'))) {
    names.add(xmlDecode(m.group(1)!));
  }
  final rels = <String, String>{};
  for (final m in RegExp(
          r'<Relationship\b[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"')
      .allMatches(_zipText(zip, 'xl/_rels/workbook.xml.rels'))) {
    rels[m.group(1)!] = m.group(2)!;
  }
  final sheetsRe = RegExp(r'<sheet\b[^>]*r:id="([^"]+)"');
  final rids = sheetsRe.allMatches(_zipText(zip, 'xl/workbook.xml')).map((m) => m.group(1)!).toList();

  final out = StringBuffer();
  var count = 0;
  for (var i = 0; i < rids.length && count < _sheetCap; i++) {
    final target = rels[rids[i]];
    if (target == null) continue;
    final path = target.startsWith('/') ? target.substring(1) : 'xl/$target';
    final xml = _zipText(zip, path);
    if (xml.isEmpty) continue;
    final name = i < names.length ? names[i] : '工作表${i + 1}';
    final md = _sheetMarkdown(xml, shared);
    if (md == null) continue;
    out.write('## $name\n\n$md\n\n');
    count++;
  }
  final s = out.toString().trim();
  if (s.isEmpty) throw const FormatException('xlsx 中没有可读数据');
  return s.length > _textCap ? s.substring(0, _textCap) : s;
}

String? _sheetMarkdown(String xml, List<String> shared) {
  final rows = <List<String>>[];
  for (final rm
      in RegExp(r'<row\b[^>]*>.*?</row>|<row\b[^>]*/>', dotAll: true)
          .allMatches(xml)) {
    final rowXml = rm.group(0)!;
    final cells = <String>[];
    for (final cm
        in RegExp(r'<c\b[^>]*>.*?</c>|<c\b[^>]*/>', dotAll: true)
            .allMatches(rowXml)) {
      final cXml = cm.group(0)!;
      final type = RegExp(r'\bt="([^"]*)"').firstMatch(cXml)?.group(1) ?? 'n';
      var v = '';
      final vm = RegExp(r'<v>(.*?)</v>', dotAll: true).firstMatch(cXml);
      final inline = _between(cXml, '<is>', '</is>');
      if (type == 'inlineStr' && inline != null) {
        final buf = StringBuffer();
        for (final t in RegExp(r'<t[^>]*>(.*?)</t>', dotAll: true)
            .allMatches(inline)) {
          buf.write(t.group(1)!);
        }
        v = xmlDecode(buf.toString());
      } else if (vm != null) {
        final raw = xmlDecode(vm.group(1)!);
        v = type == 's'
            ? (int.tryParse(raw) != null &&
                    int.parse(raw) >= 0 &&
                    int.parse(raw) < shared.length
                ? shared[int.parse(raw)]
                : raw)
            : type == 'b'
                ? (raw == '1' ? 'TRUE' : 'FALSE')
                : raw;
      }
      cells.add(v);
      if (cells.length >= _colCap) break;
    }
    if (cells.any((c) => c.isNotEmpty)) rows.add(cells);
    if (rows.length >= _rowCap) {
      rows.add(const ['…（已截断）']);
      break;
    }
  }
  if (rows.isEmpty) return null;
  return _MdTable(rows).markdown;
}

// ── csv → Markdown ──────────────────────────────────────────

String csvToMarkdown(String text) {
  final rows = <List<String>>[];
  final cells = <String>[];
  final cur = StringBuffer();
  var inQuote = false;
  for (var i = 0; i < text.length; i++) {
    final c = text[i];
    if (inQuote) {
      if (c == '"') {
        if (i + 1 < text.length && text[i + 1] == '"') {
          cur.write('"');
          i++;
        } else {
          inQuote = false;
        }
      } else {
        cur.write(c);
      }
    } else if (c == '"') {
      inQuote = true;
    } else if (c == ',') {
      cells.add(cur.toString());
      cur.clear();
    } else if (c == '\n' || c == '\r') {
      if (c == '\r' && i + 1 < text.length && text[i + 1] == '\n') i++;
      cells.add(cur.toString());
      cur.clear();
      // 复制进 rows 再清 cells（直接 clear 会把已入 rows 的同一对象清空）
      if (cells.any((x) => x.trim().isNotEmpty)) rows.add(List.of(cells));
      cells.clear();
      if (rows.length >= _rowCap) break;
    } else {
      cur.write(c);
    }
  }
  cells.add(cur.toString());
  if (cells.any((x) => x.trim().isNotEmpty) && rows.length < _rowCap) {
    rows.add(List.of(cells));
  }
  if (rows.isEmpty) throw const FormatException('csv 中没有数据');
  return _MdTable(rows).markdown;
}

// ── pptx → 大纲 ─────────────────────────────────────────────

String pptxToText(List<int> bytes) {
  final zip = ZipDecoder().decodeBytes(bytes);
  final slides = <int, ArchiveFile>{};
  for (final f in zip.files) {
    final m = RegExp(r'ppt/slides/slide(\d+)\.xml$').firstMatch(f.name);
    if (m != null) slides[int.parse(m.group(1)!)] = f;
  }
  if (slides.isEmpty) throw const FormatException('pptx 中没有幻灯片');
  final nums = slides.keys.toList()..sort();
  final out = StringBuffer();
  for (final n in nums) {
    final xml = utf8.decode(slides[n]!.content as List<int>, allowMalformed: true);
    out.write('【第 $n 页】\n');
    for (final m
        in RegExp(r'<a:t>(.*?)</a:t>', dotAll: true).allMatches(xml)) {
      final t = xmlDecode(m.group(1)!).trim();
      if (t.isNotEmpty) out.write('$t\n');
    }
    out.write('\n');
    if (out.length > _textCap) break;
  }
  return out.toString().trim();
}

// ── rtf → 纯文本 ────────────────────────────────────────────

String rtfToText(String rtf) {
  const skipDestinations = {
    'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict',
    'footer', 'header', 'footnote', 'themedata', 'listtable',
    'listoverridetable', 'generator', 'datastore', 'rsidtbl',
  };
  final sb = StringBuffer();
  var i = 0;
  var depth = 0;
  var skipUntilDepth = -1;
  while (i < rtf.length && sb.length < _textCap) {
    final c = rtf[i];
    if (c == '{') {
      depth++;
      i++;
    } else if (c == '}') {
      if (depth == skipUntilDepth) skipUntilDepth = -1;
      depth--;
      i++;
    } else if (c == '\\') {
      final rest = rtf.substring(i);
      final word = RegExp(r'^\\([a-zA-Z]+)(-?\d+)? ?').firstMatch(rest);
      final hex = RegExp(r"^\\'([0-9a-fA-F]{2})").firstMatch(rest);
      final sym = RegExp(r'^\\([{}\\*])').firstMatch(rest);
      if (word != null) {
        final kw = word.group(1)!;
        if (skipUntilDepth >= 0 && depth > skipUntilDepth) {
          // 在跳过的目的地区域内
        } else if (skipDestinations.contains(kw)) {
          skipUntilDepth = depth;
        } else if (kw == 'par' || kw == 'line') {
          sb.write('\n');
        } else if (kw == 'tab') {
          sb.write('\t');
        } else if (kw == 'emdash') {
          sb.write('—');
        } else if (kw == 'endash') {
          sb.write('–');
        } else if (kw == 'bullet') {
          sb.write('•');
        } else if (kw == 'u') {
          final n = int.tryParse(word.group(2) ?? '0') ?? 0;
          final cp = n < 0 ? 0x10000 + n : n;
          sb.write(String.fromCharCode(cp));
          // \uN 后面跟替换字符，通常 1 个
          final tail = RegExp(r'^\\u-?\d+\s?(.)').firstMatch(rest);
          if (tail != null && tail.group(1)!.isNotEmpty) {
            i += tail.group(1)!.length;
          }
        }
        i += word.end;
      } else if (hex != null) {
        // 高位字节（\'xx）：按 cp1252 近似，CJK 文本一般走 \u
        final b = int.parse(hex.group(1)!, radix: 16);
        if (b >= 0x20) sb.writeCharCode(b);
        i += hex.end;
      } else if (sym != null) {
        if (sym.group(1) == '*') {
          skipUntilDepth = depth; // \* 目的地整体跳过
        } else {
          sb.write(sym.group(1));
        }
        i += sym.end;
      } else {
        i += 2; // 未知转义
      }
    } else {
      if (skipUntilDepth < 0 && c != '\n' && c != '\r') sb.write(c);
      i++;
    }
  }
  return sb.toString().trim();
}

// ── doc（老 Word 二进制）启发式抽取 ──────────────────────────

/// 老版 .doc 是 OLE 复合文档，无官方公开的完整解析方案。这里做启发式
/// 抽取：识别连续的 UTF-16LE 段（中文/ASCII）与扩展 ASCII 段，拼出近似
/// 正文。结果标注"粗略提取"，不可用时由 UI 引导"用其他应用打开"。
String docToTextBestEffort(List<int> raw) {
  final b = raw is Uint8List ? raw : Uint8List.fromList(raw);
  final runs = <String>[];

  // 一对字节是否像 UTF-16LE 字符：ASCII/换行（高位 0）或常用 CJK 区
  bool pair(int i) {
    if (i + 1 >= b.length) return false;
    final lo = b[i], hi = b[i + 1];
    if (hi == 0) return (lo >= 0x20 && lo < 0x7f) || lo == 0x0d || lo == 0x0a;
    return hi >= 0x4e && hi <= 0x9f;
  }

  void emitUtf16(int start, int end) {
    final sb = StringBuffer();
    for (var i = start; i + 1 < end; i += 2) {
      final cp = (b[i + 1] << 8) | b[i];
      if (cp == 0x0d || cp == 0x0a) {
        sb.write('\n');
      } else if (cp >= 0x20) {
        sb.writeCharCode(cp);
      }
    }
    final s = sb.toString();
    if (s.replaceAll('\n', '').length >= 3) runs.add(s);
  }

  void emitAscii(int start, int end) {
    if (end - start >= 4) runs.add(String.fromCharCodes(b.sublist(start, end)));
  }

  var i = 0;
  while (i < b.length) {
    // UTF-16 段判定：连续对数 ≥4，或段里出现高位 0 的真 UTF-16 ASCII 对。
    // 纯 ASCII 文本（如 'hello'）按对切也常落在 0x4e..0x9f 区间内，
    // 长度阈值可把这类偶发垃圾对过滤掉。
    if (pair(i)) {
      var j = i;
      var cnt = 0;
      var hasZeroHi = false;
      while (j + 1 < b.length && pair(j)) {
        if (b[j + 1] == 0) hasZeroHi = true;
        j += 2;
        cnt++;
      }
      if (cnt >= 4 || (cnt >= 1 && hasZeroHi)) {
        emitUtf16(i, j);
        i = j;
        continue;
      }
    }
    // 单字节 ASCII 段
    if (b[i] >= 0x20 && b[i] < 0x7f) {
      var j = i;
      while (j < b.length && b[j] >= 0x20 && b[j] < 0x7f) {
        j++;
      }
      emitAscii(i, j);
      i = j;
      continue;
    }
    i++;
  }
  final text = runs.join('\n');
  if (text.trim().isEmpty) throw const FormatException('未能从 .doc 中提取出文本');
  return text.length > _textCap ? text.substring(0, _textCap) : text;
}

// ── 工具 ────────────────────────────────────────────────────

String _zipText(Archive zip, String path) {
  final f = zip.findFile(path);
  if (f == null) return '';
  return utf8.decode(f.content as List<int>, allowMalformed: true);
}

String? _between(String xml, String open, String close) {
  final s = xml.indexOf(open);
  if (s < 0) return null;
  final e = xml.indexOf(close, s + open.length);
  if (e < 0) return null;
  return xml.substring(s + open.length, e);
}
