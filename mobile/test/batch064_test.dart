// 0.7.0 落位重构纯逻辑单测（backup_dest.dart）：
// 图片→照片/<u>/<相册>/、视频→视频/<u>/<相册>/、相册名安全化。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/backup/backup_dest.dart';

void main() {
  group('safeBucketName（相册名安全化）', () {
    test('正常相册名原样保留', () {
      expect(safeBucketName('Camera'), 'Camera');
      expect(safeBucketName('Screenshots'), 'Screenshots');
      expect(safeBucketName('微信相册'), '微信相册');
      expect(safeBucketName('2026.01 旅行'), '2026.01 旅行'); // 内部点/空格合法
    });

    test('路径分隔符与 Windows 非法字符替换为 _', () {
      expect(safeBucketName('a/b\\c:d*e?f"g<h>i|j'), 'a_b_c_d_e_f_g_h_i_j');
      expect(safeBucketName('相册/../私密'), '相册_.._私密'); // 不产生路径穿越
    });

    test('控制符剔除', () {
      expect(safeBucketName('相\x00册\x1f名'), '相_册_名');
    });

    test('空/纯点/纯空白 → 未分类', () {
      expect(safeBucketName(''), kUncategorizedAlbum);
      expect(safeBucketName('   '), kUncategorizedAlbum);
      expect(safeBucketName('..'), kUncategorizedAlbum);
      expect(safeBucketName('. .. '), kUncategorizedAlbum);
    });

    test('首尾空白与点去除，内部保留', () {
      expect(safeBucketName('  相册  '), '相册');
      expect(safeBucketName('..相册..'), '相册');
    });

    test('超长截断 60 字符', () {
      final long = '长' * 100;
      expect(safeBucketName(long).length, 60);
    });
  });

  group('backupDestDir（落位拼装）', () {
    test('图片 → 照片/<u>/<相册>/', () {
      expect(
          backupDestDir(username: 'debug', kind: 'image', bucket: 'Camera'),
          '照片/debug/Camera');
    });

    test('视频 → 视频/<u>/<相册>/', () {
      expect(
          backupDestDir(username: 'debug', kind: 'video', bucket: 'Camera'),
          '视频/debug/Camera');
    });

    test('空相册名 → 未分类', () {
      expect(
          backupDestDir(username: 'debug', kind: 'image', bucket: ''),
          '照片/debug/未分类');
    });

    test('用户名中的特殊字符不在此处理（引擎上游已有 bad_username 校验）', () {
      // username 来源登录态并经过引擎白名单校验；此处只验证拼装语义
      expect(
          backupDestDir(username: 'user@example.com',
              kind: 'image', bucket: 'Camera'),
          '照片/user@example.com/Camera');
    });
  });

  test('手动上传常量', () {
    expect(kManualUploadAlbum, '手机上传');
  });
}
