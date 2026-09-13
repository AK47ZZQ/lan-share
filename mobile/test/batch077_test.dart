// 批次 077（v1.0.0，bug·882a / f6f2 修复批）：更新包本地缓存判定。
//
// 纯逻辑部分可单测；真实文件 IO / sha256 流式摘要由 E2E 覆盖。
// - 882a：服务端限速桶隔离（上传独立桶）+ App 端 429 退避——落点分别在
//   app/asgi.py 与 data/api/api_client.dart（网络行为，由服务端 pytest 与
//   真机验收覆盖，此处不重复）。
// - f6f2：下载前查本地缓存，复用已校验过的包，避免"取消安装后重下几十 MB"。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/update_service.dart';

void main() {
  group('UpdateInfo（f6f2 缓存判定所需字段）', () {
    test('fromJson 解析 sha256/size/available', () {
      final info = UpdateInfo.fromJson({
        'version': '1.0.0',
        'build': 100,
        'notes': '首个正式版',
        'sha256': 'abc123',
        'available': true,
        'size': 104619352,
      });
      expect(info.version, '1.0.0');
      expect(info.build, 100);
      expect(info.sha256, 'abc123');
      expect(info.size, 104619352);
      expect(info.available, isTrue);
    });

    test('sha256 缺失的旧格式响应：字段为 null（缓存判定据此直接放弃复用）', () {
      final info = UpdateInfo.fromJson({
        'version': '1.0.0',
        'build': 100,
        'notes': '',
        'available': true,
      });
      expect(info.sha256, isNull);
      expect(info.size, isNull);
    });
  });

  group('isNewer（build 单调发布号优先）', () {
    UpdateInfo remote(int build) => UpdateInfo(
        version: '9.9.9', build: build, notes: '',
        sha256: null, available: true, size: null);

    test('远端 build 更大 → 有更新', () {
      expect(UpdateService.isNewer(remote(85), 84), isTrue);
    });

    test('远端 build 相同或更小 → 无更新（同版本不重复提示）', () {
      expect(UpdateService.isNewer(remote(84), 84), isFalse);
      expect(UpdateService.isNewer(remote(83), 84), isFalse);
    });
  });

  group('isInstallerName（缓存复用与清运共用判定）', () {
    test('合法分发包名', () {
      expect(UpdateService.isInstallerName('lanshare-1.0.0.apk'), isTrue);
      expect(UpdateService.isInstallerName('lanshare-0.28.10.apk'), isTrue);
    });

    test('半截下载/非包文件不算（缓存复用必须跳过）', () {
      expect(UpdateService.isInstallerName('lanshare-1.0.0.apk.part'), isFalse);
      expect(UpdateService.isInstallerName('lanshare.apk'), isFalse);
      expect(UpdateService.isInstallerName('note.md'), isFalse);
    });
  });
}
