// v0.15.2 批次回归：自动切换地址归一（尾斜杠正则笔误修复）+
// 邀请码注册表单的剪贴板回填判定。切换状态机依赖真实网络，不做单测
// （模拟器 E2E 覆盖）；本批聚焦纯函数。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/api/trust.dart';
import 'package:lanshare_app/data/auto_switch.dart';
import 'package:lanshare_app/ui/invite_redeem_page.dart';

void main() {
  group('AutoSwitch.normUrl（v0.15.2 尾斜杠归一）', () {
    test('去尾部斜杠（单/多个）', () {
      expect(AutoSwitch.normUrl('https://your-frp-domain.com:xxxxx'),
          'https://your-frp-domain.com:xxxxx');
      expect(AutoSwitch.normUrl('https://your-frp-domain.com:xxxxx/'),
          'https://your-frp-domain.com:xxxxx');
      expect(AutoSwitch.normUrl('https://your-frp-domain.com:xxxxx///'),
          'https://your-frp-domain.com:xxxxx');
    });

    test('去首尾空白', () {
      expect(AutoSwitch.normUrl(' https://a.b:1/ '), 'https://a.b:1');
      expect(AutoSwitch.normUrl('\nhttps://a.b:1/\t'), 'https://a.b:1');
    });

    test('路径斜杠不受影响，只处理尾部', () {
      // 旧实现 r'/+\$' 把 \$ 当字面美元符，归一从未生效（回归锚点）
      expect(AutoSwitch.normUrl('https://a.b:1/x//y/'), 'https://a.b:1/x//y');
    });

    test('空串与异常输入不抛错', () {
      expect(AutoSwitch.normUrl(''), '');
      expect(AutoSwitch.normUrl('   '), '');
      expect(AutoSwitch.normUrl('///'), '');
      expect(AutoSwitch.normUrl('not a url'), 'not a url');
    });
  });

  group('邀请码剪贴板回填判定（invite_redeem_page 同款形状）', () {
    // token_urlsafe(12) → 16 位 [A-Za-z0-9_-]；直接引用页面公开的
    // inviteCodeShape，页内改动测试同步报警（审查 P3）
    final shape = InviteRedeemPage.inviteCodeShape;
    test('形如邀请码的 token 命中', () {
      expect(shape.hasMatch('4fK9xQ2mN8pR1sT5'), isTrue);
      expect(shape.hasMatch('a-b_c-d_e-f_g-h_'), isTrue);
    });
    test('普通文本/过短过长不命中', () {
      expect(shape.hasMatch('hello'), isFalse);
      expect(shape.hasMatch(''), isFalse);
      expect(shape.hasMatch('https://your-frp-domain.com:xxxxx'), isFalse);
      expect(shape.hasMatch('a' * 33), isFalse);
    });
  });

  group('TrustStore.normHex（预信任入库归一，审查 P0）', () {
    test('冒号大写 → 小写无冒号，与 fingerprint(X509) 对齐', () {
      expect(TrustStore.normHex('D3:7A:5A:F4'),
          'd37a5af4');
      expect(TrustStore.normHex('abcdef'), 'abcdef');
      expect(TrustStore.normHex(''), '');
    });
  });
}
