// 0.12.0 批次：文件页双布局（网格/列表）的纯逻辑部分——Entry 可预览判定
// 与布局设置持久化。注意：扩展名集合是与服务端 /thumb/ 的 IMG_EXTS |
// VID_EXTS（files.py:739）逐字对齐的副本，服务端集合变更无法由本测试
// 捕获（非契约测试），改服务端集合时须人工同步 files_page.dart。
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:lanshare_app/ui/files_page.dart';
import 'package:lanshare_app/state/settings.dart';

Entry _e({String? ext, bool isDir = false}) => Entry(
    name: 'f${ext ?? ""}',
    path: 'share/f${ext ?? ""}',
    isDir: isDir,
    isPrivateRoot: false,
    size: 1,
    sizeHuman: '1 B',
    modified: '2026-09-05T10:00:00',
    ext: ext);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('Entry 可预览判定（对齐服务端 IMG_EXTS | VID_EXTS）', () {
    test('图片扩展名全部可预览', () {
      for (final ext in [
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.heif'
      ]) {
        expect(_e(ext: ext).previewable, isTrue, reason: ext);
        expect(_e(ext: ext).isVideo, isFalse, reason: ext);
      }
    });

    test('视频扩展名全部可预览且标记 isVideo', () {
      for (final ext in ['.mp4', '.mkv', '.mov', '.webm', '.avi']) {
        expect(_e(ext: ext).previewable, isTrue, reason: ext);
        expect(_e(ext: ext).isVideo, isTrue, reason: ext);
      }
    });

    test('扩展名大小写不敏感（服务端 suffix.lower()）', () {
      expect(_e(ext: '.JPG').previewable, isTrue);
      expect(_e(ext: '.MP4').isVideo, isTrue);
    });

    test('文档类可预览（v0.17：md/pdf/docx/txt 走 DocViewerPage）', () {
      expect(_e(ext: '.pdf').previewable, isTrue);
      expect(_e(ext: '.md').previewable, isTrue);
      expect(_e(ext: '.docx').previewable, isTrue);
      expect(_e(ext: '.txt').previewable, isTrue);
    });

    test('不可预览类型：目录/无扩展名/压缩包/音频', () {
      expect(_e(isDir: true, ext: null).previewable, isFalse);
      // 目录即使名字带图扩展名（如 "Photos.jpg" 文件夹）也不进预览
      expect(_e(isDir: true, ext: '.jpg').previewable, isFalse);
      expect(_e(ext: null).previewable, isFalse);
      expect(_e(ext: '.zip').previewable, isFalse);
      expect(_e(ext: '.mp3').previewable, isFalse);
    });
  });

  group('文件页布局设置持久化', () {
    test('默认横向列表（与 Web 默认 grid 不同是刻意的：手机上列表信息密度高）',
        () => expect(const AppSettings().filesGrid, isFalse));

    test('setFilesGrid 落 SharedPreferences 且状态即时生效', () async {
      SharedPreferences.setMockInitialValues({});
      final c = SettingsController();
      await Future<void>.delayed(Duration.zero); // 等 _load
      expect(c.state.filesGrid, isFalse);

      await c.setFilesGrid(true);
      expect(c.state.filesGrid, isTrue);
      final sp = await SharedPreferences.getInstance();
      expect(sp.getBool('files_grid'), isTrue);

      // 新实例从持久层读回 true
      final c2 = SettingsController();
      await Future<void>.delayed(Duration.zero);
      expect(c2.state.filesGrid, isTrue);
    });
  });
}
