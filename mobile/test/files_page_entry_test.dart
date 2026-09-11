// files_page 核心纯逻辑冒烟（B10）：Entry 预览分类判定矩阵 + fromJson 容错。
// 仓库测试哲学：只测可纯算部分，网络/插件路径由 E2E 覆盖——故不 mock dio，
// 直接钉 Entry 的类型判定（相邻翻页范围与缩略图路径都依赖它）。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/ui/files_page.dart';

Entry mk(String name, {bool isDir = false, String? ext}) => Entry(
    name: name,
    path: 'shared/$name',
    isDir: isDir,
    isPrivateRoot: false,
    size: 1,
    sizeHuman: '1B',
    modified: '2026-09-12T00:00:00',
    ext: ext);

void main() {
  group('Entry 预览分类判定（files_page 相邻翻页范围的过滤依据）', () {
    test('图片四格式含 HEIC 新格式，大小写不敏感', () {
      for (final e in ['a.jpg', 'b.PNG', 'c.webp', 'd.heic', 'e.jpeg']) {
        final entry = mk(e, ext: e.substring(e.indexOf('.')));
        expect(entry.isImage, isTrue, reason: e);
        expect(entry.previewable, isTrue, reason: e);
      }
    });

    test('视频含 v0.21 补齐的 m4v/3gp', () {
      for (final e in ['a.mp4', 'b.mov', 'c.m4v', 'd.3gp']) {
        final entry = mk(e, ext: e.substring(e.indexOf('.')));
        expect(entry.isVideo, isTrue, reason: e);
        expect(entry.isImage, isFalse, reason: e);
      }
    });

    test('文档类走 isDocPreviewable（md/pdf/docx/txt）', () {
      for (final e in ['a.md', 'b.pdf', 'c.docx', 'd.txt']) {
        final entry = mk(e, ext: e.substring(e.indexOf('.')));
        expect(entry.isDoc, isTrue, reason: e);
        expect(entry.previewable, isTrue, reason: e);
      }
    });

    test('音频点击进内建播放器', () {
      for (final e in ['a.mp3', 'b.flac', 'c.m4a', 'd.wav']) {
        final entry = mk(e, ext: e.substring(e.indexOf('.')));
        expect(entry.isAudio, isTrue, reason: e);
        expect(entry.previewable, isTrue, reason: e);
      }
    });

    test('目录永远不可预览（即使名字像图片）', () {
      final d = mk('照片.jpg', isDir: true, ext: '.jpg');
      expect(d.isImage, isFalse);
      expect(d.previewable, isFalse);
    });

    test('ext 缺失回退按名字扩展名判定（isDoc 用 name；img/vid 用 ext）', () {
      expect(mk('a.md').isDoc, isTrue); // isDoc 看名字
      expect(mk('a.jpg').isImage, isFalse); // isImage 看 ext（null 不命中）
      expect(mk('a.jpg', ext: '.jpg').isImage, isTrue);
    });

    test('未知扩展名不可预览', () {
      final e = mk('a.xyz', ext: '.xyz');
      expect(e.previewable, isFalse);
      expect(mk('blob', ext: null).previewable, isFalse);
    });
  });

  group('Entry.fromJson 容错（服务端字段缺省不崩）', () {
    test('全字段齐备', () {
      final e = Entry.fromJson({
        'name': 'x.pdf', 'path': 'shared/x.pdf', 'is_dir': false,
        'size': 12, 'size_human': '12B', 'modified': '2026-09-12',
        'ext': '.pdf', 'abs_path': 'C:/shared/x.pdf',
      });
      expect(e.isDoc, isTrue);
      expect(e.size, 12);
      expect(e.absPath, 'C:/shared/x.pdf');
    });

    test('旧服务端无 ext/abs_path/is_private_root 时不抛', () {
      final e = Entry.fromJson({
        'name': 'y.txt', 'path': 'shared/y.txt', 'is_dir': false,
        'size': 3, 'size_human': '3B', 'modified': '',
      });
      expect(e.previewable, isTrue); // isDoc 按 name 判定仍可用
      expect(e.absPath, isNull);
      expect(e.isPrivateRoot, isFalse);
    });

    test('size 非整数（double）安全落 int', () {
      final e = Entry.fromJson({
        'name': 'z', 'path': 'z', 'is_dir': true,
        'size': 1.5, 'size_human': '', 'modified': '',
      });
      expect(e.size, 1); // toInt 截断
      expect(e.isDir, isTrue);
    });
  });
}
