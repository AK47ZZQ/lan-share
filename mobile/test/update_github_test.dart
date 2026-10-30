// 双更新源解析的单测（v1.1.17 新增）。
//
// 覆盖 UpdateInfo.fromGitHub 这个纯函数——它负责把 GitHub Release 的 JSON
// 翻成 App 认识的版本信息，是「GitHub 能不能作为更新源」的唯一逻辑入口。
// 边界比正常路径重要：任何一个字段缺失都必须返回 null（让 check() 回落
// 到服务端），绝不能拼出一个 sha256 为空的 UpdateInfo——下载层是
// fail-closed 的，没有校验值就拒装，提前在这里挡掉能给出更清楚的行为。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/update_service.dart';

/// 造一个形似 GitHub Release 的 map
Map<String, dynamic> gh({
  String tag = 'app-v1.1.16',
  String assetName = 'lanshare-v1.1.16+123.apk',
  String? digest = 'sha256:dd74a1a9bb2f8ede7213eadfd968bab40fb4ec945e6cb47795203636694b62b8',
  String url = 'https://github.com/AK47ZZQ/lan-share/releases/download/app-v1.1.16/x.apk',
  int size = 105701736,
  String body = '依赖安全版',
}) =>
    {
      'tag_name': tag,
      'body': body,
      'assets': [
        {
          'name': assetName,
          'digest': digest,
          'size': size,
          'browser_download_url': url,
        }
      ],
    };

void main() {
  group('UpdateInfo.fromGitHub 正常解析', () {
    test('完整 JSON 能解出四要素：version / build / sha256 / url', () {
      final info = UpdateInfo.fromGitHub(gh())!;
      expect(info.version, '1.1.16');
      expect(info.build, 123);
      expect(info.sha256, startsWith('dd74a1a9'));
      expect(info.url, contains('releases/download'));
      expect(info.size, 105701736);
      expect(info.available, isTrue);
      expect(info.source, 'github');
    });

    test('tag 三种写法都能提取出版本号', () {
      for (final tag in ['app-v1.1.16', 'v1.1.16', '1.1.16', 'release-1.1.16']) {
        final info = UpdateInfo.fromGitHub(gh(tag: tag));
        expect(info?.version, '1.1.16', reason: 'tag=$tag');
      }
    });

    test('build 号从 asset 文件名里解析（tag 里没有）', () {
      final info = UpdateInfo.fromGitHub(gh(assetName: 'lanshare-v2.0.3+456.apk'))!;
      expect(info.build, 456);
    });

    test('digest 带 sha256: 前缀会被剥掉，只留 hex', () {
      final info = UpdateInfo.fromGitHub(gh())!;
      expect(info.sha256, isNot(startsWith('sha256:')));
      expect(info.sha256, hasLength(64));
    });
  });

  group('UpdateInfo.fromGitHub 边界：一律返回 null', () {
    test('没有 assets', () {
      expect(UpdateInfo.fromGitHub({'tag_name': 'app-v1.1.16', 'assets': []}),
          isNull);
    });

    test('assets 里没有 .apk 文件', () {
      final j = gh()..['assets'] = [
        {'name': 'source.zip', 'digest': null, 'browser_download_url': 'https://x'}
      ];
      expect(UpdateInfo.fromGitHub(j), isNull);
    });

    test('asset 名里没有 +build 号', () {
      expect(UpdateInfo.fromGitHub(gh(assetName: 'lanshare-v1.1.16.apk')), isNull);
    });

    test('tag_name 里提取不出版本号', () {
      expect(UpdateInfo.fromGitHub(gh(tag: 'nightly')), isNull);
    });

    test('url 为空', () {
      expect(UpdateInfo.fromGitHub(gh(url: '')), isNull);
    });

    test('JSON 完全为空', () {
      expect(UpdateInfo.fromGitHub(<String, dynamic>{}), isNull);
    });
  });

  group('digest 缺失的特殊处理：不返回 null，但 sha256 为 null', () {
    test('无 digest 时仍能解析出版本，但 sha256 = null', () {
      // 这一条是刻意为之：老版本 Release 可能没有 digest 字段。
      // 保留版本信息让 UI 能显示「有新版」，下载层会因为 sha256 缺失而拒装
      // 并抛出明确错误，比在这里静默返回 null（用户看不到任何提示）要好。
      final info = UpdateInfo.fromGitHub(gh(digest: null))!;
      expect(info.version, '1.1.16');
      expect(info.sha256, isNull);
    });

    test('digest 不是 sha256 格式（比如 md5）时同样置空', () {
      final info = UpdateInfo.fromGitHub(gh(digest: 'md5:abc123'))!;
      expect(info.sha256, isNull);
    });
  });

  group('服务端源的默认字段', () {
    test('fromJson 默认 source=server 且 url 为 null', () {
      final info = UpdateInfo.fromJson({
        'version': '1.1.16',
        'build': 123,
        'notes': '',
        'sha256': 'abc',
        'available': true,
        'size': 100,
      });
      expect(info.source, 'server');
      expect(info.url, isNull);
    });
  });

  group('isNewer', () {
    test('build 号大才算新版本', () {
      final remote = UpdateInfo.fromGitHub(gh())!;
      expect(UpdateService.isNewer(remote, 122), isTrue);
      expect(UpdateService.isNewer(remote, 123), isFalse);
      expect(UpdateService.isNewer(remote, 124), isFalse);
    });
  });
}
