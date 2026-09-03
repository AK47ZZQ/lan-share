// 2026-09-03 主/备隧道批次纯逻辑单测：公网入口清单模型（/api/app/entries）
// 与内网/公网地址判定（备份跳过/徽标/上传提醒共用）。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/entries_service.dart';
import 'package:lanshare_app/data/net.dart';

void main() {
  group('AppEntries：/api/app/entries 解析', () {
    test('完整字段', () {
      final e = AppEntries.fromJson({
        'public': 'https://your-frp-domain.com:xxxxx',
        'public_alt': 'https://your-frp-domain.com:xxxxx',
        'cert_sha256': 'D3:7A:5A',
      });
      expect(e.pub, 'https://your-frp-domain.com:xxxxx');
      expect(e.pubAlt, 'https://your-frp-domain.com:xxxxx');
      expect(e.publicAddresses, hasLength(2));
      expect(e.publicAddresses.first, 'https://your-frp-domain.com:xxxxx'); // 主在前
    });

    test('未配置备用 → 只有主', () {
      final e = AppEntries.fromJson(
          {'public': 'https://your-frp-domain.com:xxxxx', 'public_alt': null});
      expect(e.publicAddresses, hasLength(1));
    });

    test('都未配置 → 空列表（选择器隐藏公网区）', () {
      final e = AppEntries.fromJson({'public': null, 'public_alt': null});
      expect(e.publicAddresses, isEmpty);
    });

    test('cert_sha256 缺失 → 空串不炸', () {
      final e = AppEntries.fromJson({'public': 'x'});
      expect(e.certSha256, '');
    });

    test('fingerprintMatches：忽略冒号与大小写', () {
      final e = AppEntries.fromJson({'cert_sha256': 'd3:7a:5a'});
      expect(e.fingerprintMatches('D3:7A:5A'), isTrue);
      expect(e.fingerprintMatches('D37A5A'), isTrue);
      expect(e.fingerprintMatches('aa:bb'), isFalse);
      expect(e.fingerprintMatches(''), isFalse); // 空指纹绝不匹配
    });
  });

  group('net.dart：内网/公网判定', () {
    test('RFC1918 与环回 = 内网', () {
      expect(isPublicAddress('https://192.168.1.100:8765'), isFalse);
      expect(isPublicAddress('https://10.0.0.2:8765'), isFalse);
      expect(isPublicAddress('https://172.16.5.4:8765'), isFalse);
      expect(isPublicAddress('https://172.32.5.4:8765'), isTrue); // 172 仅 16-31 是私网
      expect(isPublicAddress('https://127.0.0.1:8765'), isFalse);
    });
    test('frp 域名 = 公网（备用隧道场景）', () {
      expect(isPublicAddress('https://your-frp-domain.com:xxxxx'), isTrue);
      expect(isPublicAddress('https://nas.example.com'), isTrue);
    });
    test('mDNS/.lan 后缀 = 内网', () {
      expect(isPublicAddress('https://desktop-hermes.local:8765'), isFalse);
      expect(isPublicAddress('https://nas.lan'), isFalse);
    });
    test('IPv6：环回/ULA = 内网，GUA = 公网', () {
      expect(isPublicHost('::1'), isFalse);
      expect(isPublicHost('fd00::1'), isFalse);
      expect(isPublicHost('fe80::1'), isFalse);
      expect(isPublicHost('2408:8207::1'), isTrue);
    });
    test('空/解析失败按公网处理（宁可信其有，省流优先）', () {
      expect(isPublicHost(''), isTrue);
      expect(isPublicAddress(''), isTrue);
    });
  });
}
