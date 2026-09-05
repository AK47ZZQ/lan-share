// v0.15 批次回归：自动切换设置项（默认开/开关持久化/与 AutoSwitch 服务
// 同键写穿）。页面视觉重组不做 widget 测试（逻辑零变化，纯布局搬移）。
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:lanshare_app/data/auto_switch.dart';
import 'package:lanshare_app/state/settings.dart';

void main() {
  group('autoSwitch 设置（v0.15）', () {
    test('默认开启', () {
      SharedPreferences.setMockInitialValues({});
      final c = SettingsController();
      // 构造即异步 _load；直接断言默认值字段
      expect(const AppSettings().autoSwitch, true);
      expect(c.state.autoSwitch, true); // load 完成前也是默认值
    });

    test('关闭后持久化，重载仍为关', () async {
      SharedPreferences.setMockInitialValues({});
      final c = SettingsController();
      await Future.delayed(const Duration(milliseconds: 50)); // 等 _load
      await c.setAutoSwitch(false);
      final sp = await SharedPreferences.getInstance();
      expect(sp.getBool('autoswitch_enabled'), false);
      expect(c.state.autoSwitch, false);

      // 模拟重启：新 controller 从 prefs 恢复为关
      final c2 = SettingsController();
      await Future.delayed(const Duration(milliseconds: 50));
      expect(c2.state.autoSwitch, false);
    });
  });

  group('AutoSwitch 指纹归一（红队 P1 身份校验的基石）', () {
    test('normFp 去冒号+大写', () {
      expect(AutoSwitch.normFp('ab:cd:ef'), 'ABCDEF');
      expect(AutoSwitch.normFp(''), '');
    });

    test('sentinelFp：无缓存为空串，缓存后返回归一指纹', () async {
      SharedPreferences.setMockInitialValues({});
      expect(await AutoSwitch.sentinelFpForTest(), '');
      SharedPreferences.setMockInitialValues(
          {'autoswitch_cert_fp': 'aa:bb'});
      expect(await AutoSwitch.sentinelFpForTest(), 'AABB');
    });
  });
}
