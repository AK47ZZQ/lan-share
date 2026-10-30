// b8b3 局域网自动再发现：AppEntries.lan 字段解析（服务端广播的局域网地址）。
// 旧服务端无 lan 键 → 空表（向后兼容）；服务端下发 → 原样保留，供
// AutoSwitch 在历史候选全失败时作为再发现候选。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/auto_switch.dart';
import 'package:lanshare_app/data/entries_service.dart';

void main() {
  group('AppEntries.lan：局域网地址广播解析', () {
    test('服务端下发 lan 列表', () {
      final e = AppEntries.fromJson({
        'public': null,
        'public_alt': null,
        'lan': ['https://192.168.1.100:8765', 'https://10.0.0.5:8765'],
        'cert_sha256': 'AA:BB',
      });
      expect(e.lan, ['https://192.168.1.100:8765', 'https://10.0.0.5:8765']);
      expect(e.publicAddresses, isEmpty);
    });

    test('旧服务端无 lan 键：空表兜底（不炸解析）', () {
      final e = AppEntries.fromJson({
        'public': 'https://your-frp-domain.com:xxxxx',
        'cert_sha256': 'AA:BB',
      });
      expect(e.lan, isEmpty);
      expect(e.publicAddresses, ['https://your-frp-domain.com:xxxxx']);
    });

    test('lan 为 null：空表兜底', () {
      final e = AppEntries.fromJson({'lan': null});
      expect(e.lan, isEmpty);
    });
  });

  group('AutoSwitch.advertisedLanCandidates：再发现候选过滤', () {
    test('https-only：http 候选被剔除（红队口径延续）', () {
      final got = AutoSwitch.advertisedLanCandidates(
        advertised: ['https://192.168.1.100:8765', 'http://192.168.1.101:8765'],
        current: 'https://your-frp-domain.com:xxxxx',
        history: const [],
      );
      expect(got, ['https://192.168.1.100:8765']);
    });

    test('排除当前地址与历史已有项，归一去重', () {
      final got = AutoSwitch.advertisedLanCandidates(
        advertised: [
          'https://192.168.1.100:8765',
          'https://192.168.1.100:8765/', // 尾斜杠归一后重复
          'https://your-frp-domain.com:xxxxx', // 当前公网地址
          'https://192.168.1.99:8765', // 历史已有
        ],
        current: 'https://your-frp-domain.com:xxxxx',
        history: ['https://10.0.0.5:8765', 'https://192.168.1.99:8765'],
      );
      expect(got, ['https://192.168.1.100:8765']);
    });

    test('上限 6：异常超长列表不撑爆探测', () {
      final got = AutoSwitch.advertisedLanCandidates(
        advertised: List.generate(10, (i) => 'https://10.0.0.$i:8765'),
        current: 'https://your-frp-domain.com:xxxxx',
        history: const [],
      );
      expect(got.length, 6);
    });

    test('消融对照：未排除历史时旧 IP 死项会重复探测（当前行为已排除）', () {
      // 若未来有人删掉 history 过滤，此用例红：历史候选已探失败，
      // 再探一遍纯浪费 2s 超时窗口
      final got = AutoSwitch.advertisedLanCandidates(
        advertised: ['https://10.0.0.5:8765'],
        current: 'https://your-frp-domain.com:xxxxx',
        history: ['https://10.0.0.5:8765'],
      );
      expect(got, isEmpty);
    });
  });
}

