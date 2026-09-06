// v0.19 文档预览兼容重构：转换器单测（测试内自构造 docx/xlsx/pptx 样本）
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:archive/archive.dart';

import 'package:lanshare_app/ui/doc_extract.dart';

Archive _zip(Map<String, List<int>> files) {
  final a = Archive();
  files.forEach((name, content) {
    a.addFile(ArchiveFile.bytes(name, content));
  });
  return a;
}

List<int> _zipBytes(Map<String, List<int>> files) =>
    ZipEncoder().encode(_zip(files));

void main() {
  test('docxToMarkdown：标题/加粗/斜体/列表/表格/内嵌图片', () {
    final doc = '<?xml version="1.0"?><w:document><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        '<w:r><w:t>标题一</w:t></w:r></w:p>'
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>加粗</w:t></w:r>'
        '<w:r><w:rPr><w:i/></w:rPr><w:t>斜体</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>'
        '<w:r><w:t>列表项</w:t></w:r></w:p>'
        '<w:tbl><w:tr>'
        '<w:tc><w:p><w:r><w:t>甲</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>乙</w:t></w:r></w:p></w:tc>'
        '</w:tr><w:tr>'
        '<w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc>'
        '</w:tr></w:tbl>'
        '<w:p><w:r><w:drawing><a:blip r:embed="rId5"/></w:drawing></w:r></w:p>'
        '</w:body></w:document>';
    final rels = '<?xml version="1.0"?><Relationships>'
        '<Relationship Id="rId5" Target="media/image1.png"/>'
        '</Relationships>';
    final dir = Directory.systemTemp.createTempSync('docxtest-');
    addTearDown(() => dir.deleteSync(recursive: true));
    final md = docxToMarkdown(_zipBytes({
      'word/document.xml': utf8.encode(doc),
      'word/_rels/document.xml.rels': utf8.encode(rels),
      'word/media/image1.png': [1, 2, 3, 4],
    }), mediaDir: dir.path);

    expect(md, contains('# 标题一'));
    expect(md, contains('**加粗**'));
    expect(md, contains('*斜体*'));
    expect(md, contains('- 列表项'));
    expect(md, contains('| 甲 | 乙 |'));
    expect(md, contains('file:///'));
    // 图片真实落盘
    expect(File('${dir.path}/image1.png').existsSync(), isTrue);
  });

  test('xlsxToMarkdown：共享字符串/内联文本/数值', () {
    final md = xlsxToMarkdown(_zipBytes({
      'xl/workbook.xml': utf8.encode(
          '<workbook><sheets><sheet name="月度" sheetId="1" r:id="rId1"/></sheets></workbook>'),
      'xl/_rels/workbook.xml.rels': utf8.encode(
          '<Relationships><Relationship Id="rId1" '
          'Target="worksheets/sheet1.xml"/></Relationships>'),
      'xl/sharedStrings.xml': utf8.encode(
          '<sst><si><t>名称</t></si><si><t>数量</t></si></sst>'),
      'xl/worksheets/sheet1.xml': utf8.encode(
          '<worksheet><sheetData>'
          '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
          '<row r="2"><c r="A2"><v>123.45</v></c>'
          '<c r="B2" t="inlineStr"><is><t>你好</t></is></c></row>'
          '</sheetData></worksheet>'),
    }));
    expect(md, contains('## 月度'));
    expect(md, contains('| 名称 | 数量 |'));
    expect(md, contains('123.45'));
    expect(md, contains('你好'));
  });

  test('csvToMarkdown：引号内逗号与换行', () {
    final md = csvToMarkdown('名称,数量\n"苹果, 红",3\n"第\n二项",1\n');
    expect(md, contains('| 名称 | 数量 |'));
    expect(md, contains('苹果, 红'));
    expect(md, contains('第 二项'));
    expect(md, contains('| 3 |'));
  });

  test('rtfToText：控制字剥离/十六进制字节/Unicode/目的地跳过', () {
    // 注意：Dart raw string 里反斜杠不转义，尾部的 \' 需拆成相邻两段
    final text = rtfToText(
        r'{\rtf1{\fonttbl{\f0 Arial}}' r"caf\'e9 \par \u20013?\u22269?}");
    expect(text, contains('café'));
    expect(text, contains('\n'));
    expect(text, contains('中国'));
    expect(text, isNot(contains('fonttbl')));
  });

  test('pptxToText：逐页大纲', () {
    final text = pptxToText(_zipBytes({
      'ppt/slides/slide1.xml':
          utf8.encode('<p><a:t>第一页标题</a:t><a:t>要点</a:t></p>'),
      'ppt/slides/slide2.xml': utf8.encode('<p><a:t>第二页</a:t></p>'),
      'ppt/media/img.png': [1, 2, 3],
    }));
    expect(text, contains('【第 1 页】'));
    expect(text, contains('第一页标题'));
    expect(text, contains('【第 2 页】'));
  });

  test('docToTextBestEffort：UTF-16LE 中文 + ASCII 混排', () {
    final b = <int>[];
    void utf16(String s) {
      for (final cp in s.codeUnits) {
        b.addAll([cp & 0xff, cp >> 8]);
      }
    }

    utf16('你好世界');
    utf16('\r');
    utf16('hello 123'); // 真 .doc 的 UTF-16 段里 ASCII 高位为 0
    final text = docToTextBestEffort(b);
    expect(text, contains('你好世界'));
    expect(text, contains('hello 123'));
  });
}
