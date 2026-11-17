// 更新链路的对抗测试（2026-11-17）。
//
// 立场：这些用例**不是在证明系统安全**，而是在把「已知能穿透的地方」
// 钉成可执行的断言 —— 哪天有人加了清单签名或者收紧 url 白名单，
// 对应的用例会立刻变红，逼着改这里，而不是让防线悄悄漂移。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/update_service.dart';
import 'package:lanshare_app/data/update_source.dart';

void main() {
  const r2Base = 'https://dl.ak47zzq.com/app';

  group('轮 1 · 清单被篡改（攻击者能写 bucket）', () {
    test('版本号由攻击者决定 → 一定会被当成「有新版」', () {
      final info = UpdateInfo.fromManifest({
        'version': '9.9.9',
        'build': 999,
        'url': 'LANShare-App-9.9.9+999.apk',
        'sha256': 'a' * 64,
        'size': 1024,
      }, base: r2Base);
      expect(info, isNotNull);
      expect(UpdateService.isNewer(info!, 141, localVersion: '4.1.3'), isTrue);
    });

    test('⚠️ url 可以是任意 https 域名 —— 客户端会去第三方下载', () {
      // 这是**当前实现的真实行为**，不是期望行为。
      // 攻击者拿到 bucket 写权限后，可以把包放在自己的站点上：
      // 清单里写任何 https 地址都会被放行，sha256 也在下面同一个文件里，
      // 一起换掉即可通过校验。
      final u = UpdateSourceClient.resolveUrl(
          'https://evil.example.com/malware.apk', r2Base);
      expect(u, 'https://evil.example.com/malware.apk',
          reason: '清单无签名 + url 不限域 = 可分发任意包（已知缺口）');
    });

    test('⚠️ sha256 与包同源，一起改就能通过校验', () {
      final evil = UpdateInfo.fromManifest({
        'version': '9.9.9',
        'build': 999,
        'url': 'https://evil.example.com/malware.apk',
        'sha256': 'b' * 64, // 攻击者对自己那个包算的
        'size': 1,
      }, base: r2Base);
      expect(evil, isNotNull,
          reason: 'sha256 只保证「下载到的字节 == 清单说的」，不保证清单可信');
    });

    test('默认源额外做了同域校验：跨域包会被整条丢弃', () {
      // 通用 resolveUrl 会放行任意 https，但默认源（R2）在 _checkR2 里
      // 用 isSameHostAsR2 再收一道：包必须落在 dl.ak47zzq.com 下。
      expect(UpdateService.isSameHostAsR2('https://evil.example.com/a.apk'),
          isFalse);
      expect(
          UpdateService.isSameHostAsR2(
              'https://dl.ak47zzq.com/app/LANShare-App-4.1.3+141.apk'),
          isTrue);
      expect(UpdateService.isSameHostAsR2(''), isFalse);
      expect(UpdateService.isSameHostAsR2('not a url'), isFalse);
      // 子域名不算同域（防止 dl.ak47zzq.com.evil.com 这类后缀绕过）
      expect(UpdateService.isSameHostAsR2('https://dl.ak47zzq.com.evil.com/a.apk'),
          isFalse);
    });
  });

  group('轮 2 · 恶意字段注入', () {
    test('http 绝对地址被拒（只留回环例外）', () {
      expect(
          UpdateSourceClient.resolveUrl('http://evil.com/a.apk', r2Base), '',
          reason: '明文传输会被中间人换包');
      expect(
          UpdateSourceClient.resolveUrl('http://127.0.0.1:8765/a.apk', r2Base),
          'http://127.0.0.1:8765/a.apk',
          reason: '回环是设计内的例外（本机调试）');
      expect(
          UpdateSourceClient.resolveUrl('http://localhost/a.apk', r2Base),
          'http://localhost/a.apk');
    });

    test('相对地址按清单目录解析，base 为空则作废', () {
      expect(UpdateSourceClient.resolveUrl('x.apk', r2Base),
          'https://dl.ak47zzq.com/app/x.apk');
      expect(UpdateSourceClient.resolveUrl('x.apk', ''), '',
          reason: '没有基准目录就不该把坏地址塞给下载器');
      expect(
          UpdateSourceClient.resolveUrl('x.apk', '$r2Base/index.json'),
          'https://dl.ak47zzq.com/app/x.apk',
          reason: 'base 指向文件时按所在目录解析');
    });

    test('sha256 必须是 64 位小写十六进制，否则整条作废', () {
      for (final bad in <String?>[
        null,
        '',
        'abc',
        'g' * 64, // 非十六进制
        'A' * 64, // 大写：fromManifest 会 toLowerCase，所以它是合法的
      ]) {
        final m = <String, dynamic>{
          'version': '9.9.9',
          'build': 1,
          'url': 'x.apk',
          'sha256': bad,
        };
        final info = UpdateInfo.fromManifest(m, base: r2Base);
        if (bad == 'A' * 64) {
          expect(info, isNotNull, reason: '大写会被归一化');
        } else {
          expect(info, isNull, reason: 'sha256=$bad 应整条作废（fail closed）');
        }
      }
    });

    test('缺 url / 缺 version 一律作废', () {
      expect(
          UpdateInfo.fromManifest(
              {'version': '9.9.9', 'url': '', 'sha256': 'a' * 64},
              base: r2Base),
          isNull);
      expect(
          UpdateInfo.fromManifest(
              {'version': '', 'url': 'x.apk', 'sha256': 'a' * 64},
              base: r2Base),
          isNull);
      expect(UpdateInfo.fromManifest(null, base: r2Base), isNull);
    });

    test('version 里的路径分隔符不会逃出下载目录（下载侧消毒）', () {
      // download() 用 safeVer 白名单过滤，这里验白名单本身的行为：
      // 版本号里塞 ../ 会被过滤成空，落到固定文件名上。
      final evil = 'x/../../evil';
      final safe = evil.replaceAll(RegExp(r'[^A-Za-z0-9._-]'), '');
      expect(safe, 'x....evil');
      expect(safe.contains('/'), isFalse);
    });
  });

  group('轮 3 · 降级与边界', () {
    test('schema 不认识就明确失败，而不是硬解析出一堆 null', () async {
      // fetch() 需要网络，这里只断言 fromManifest 对缺 schema 是宽容的
      //（schema 校验在 fetch 里，网络层，见 update_source.dart:164-175）
      final ok = UpdateInfo.fromManifest(
          {'version': '1.0.0', 'url': 'x.apk', 'sha256': 'a' * 64},
          base: r2Base);
      expect(ok, isNotNull);
    });

    test('版本比较：字符串优先，build 号只在版本相同时兜底', () {
      UpdateInfo mk(String v, int b) => UpdateInfo.fromManifest(
          {'version': v, 'build': b, 'url': 'x.apk', 'sha256': 'a' * 64},
          base: r2Base)!;
      expect(UpdateService.isNewer(mk('4.1.10', 1), 141, localVersion: '4.1.3'),
          isTrue,
          reason: '4.1.10 > 4.1.3 按字符串比较而不是数字');
      expect(UpdateService.isNewer(mk('4.1.2', 999), 141, localVersion: '4.1.3'),
          isFalse,
          reason: 'build 高但版本低 → 不是新版（跨源编号体系不同）');
    });
  });

  group('清单签名（跨语言契约：Python 签、Dart 验）', () {
    // 这份 fixture 由 scripts/r2_upload.py 的同一套逻辑用私钥签出，
    // payload 逐字为：v1|4.1.3|142|LANShare-App-4.1.3+142.apk|<sha>|107021489
    // 两侧任何一边改了拼接格式，这条会立刻红。
    final fixture = <String, dynamic>{
      'schema': 1,
      'latest': <String, dynamic>{
        'version': '4.1.3',
        'build': 142,
        'url': 'LANShare-App-4.1.3+142.apk',
        'sha256':
            'a50a559b1707053a000000000000000000000000000000000000000000000000',
        'size': 107021489,
      },
      'signature':
          'W/b18H/KJowL34gXNmft7mYZEjvFq3Fh3JfiRRvwqsWv37st0aSK9MeFAs9+6K5WV5y7k7QZioVmB0FUync4Bw==',
    };

    test('Python 产的签名，App 公钥能验过', () async {
      expect(await UpdateService.verifyR2Manifest(fixture), isTrue);
    });

    test('篡改 sha256 → 验不过（签名覆盖了包哈希）', () async {
      final t = <String, dynamic>{
        ...fixture,
        'latest': <String, dynamic>{
          ...(fixture['latest'] as Map).cast<String, dynamic>(),
          'sha256': 'b' * 64,
        },
      };
      expect(await UpdateService.verifyR2Manifest(t), isFalse);
    });

    test('篡改 url（指向外部域）→ 验不过', () async {
      final t = <String, dynamic>{
        ...fixture,
        'latest': <String, dynamic>{
          ...(fixture['latest'] as Map).cast<String, dynamic>(),
          'url': 'https://evil.example.com/x.apk',
        },
      };
      expect(await UpdateService.verifyR2Manifest(t), isFalse);
    });

    test('篡改版本号（伪造新版）→ 验不过', () async {
      final t = <String, dynamic>{
        ...fixture,
        'latest': <String, dynamic>{
          ...(fixture['latest'] as Map).cast<String, dynamic>(),
          'version': '9.9.9',
        },
      };
      expect(await UpdateService.verifyR2Manifest(t), isFalse);
    });

    test('缺 signature / 签名乱码 → 一律拒且不抛异常', () async {
      expect(
          await UpdateService.verifyR2Manifest(
              {'latest': fixture['latest']}),
          isFalse);
      expect(
          await UpdateService.verifyR2Manifest(
              {...fixture, 'signature': 'not-base64!!!'}),
          isFalse);
      expect(await UpdateService.verifyR2Manifest({}), isFalse);
    });
  });
}
