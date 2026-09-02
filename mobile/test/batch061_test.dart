// 0.6.1 应用锁恶性锁死修复的回归单测：LockGate 生命周期归类 +
// 解锁标记 provider 语义。场景即 0.6.0 三环锁死的反向覆盖。
import 'package:flutter/material.dart' show AppLifecycleState;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/state/settings.dart';

void main() {
  group('LockGate：生命周期归类（只有真后台回来的 resumed 才落锁）', () {
    test('切后台再回来（paused→resumed）→ 落锁', () {
      final g = LockGate()..onState(AppLifecycleState.paused);
      expect(g.shouldLockOnResume(biometricBusy: false), isTrue);
    });

    test('hidden→resumed（Android 高版本全遮挡语义）→ 落锁', () {
      final g = LockGate()..onState(AppLifecycleState.hidden);
      expect(g.shouldLockOnResume(biometricBusy: false), isTrue);
    });

    test('指纹弹窗路径 inactive→resumed → 不落锁（0.6.0 解锁被弹回锁屏的根因）', () {
      final g = LockGate()..onState(AppLifecycleState.inactive);
      expect(g.shouldLockOnResume(biometricBusy: false), isFalse);
    });

    test('弹窗把 Activity 打到 paused 的机型：busy 时不落锁且标记被消费', () {
      final g = LockGate()..onState(AppLifecycleState.paused);
      // 弹窗在屏（解锁进行中）：不落锁
      expect(g.shouldLockOnResume(biometricBusy: true), isFalse);
      // 弹窗关闭后的后续 resumed 也不再补锁——标记已在 busy 判定中消费
      expect(g.shouldLockOnResume(biometricBusy: false), isFalse);
    });

    test('没进过后台的裸 resumed → 不落锁', () {
      final g = LockGate();
      expect(g.shouldLockOnResume(biometricBusy: false), isFalse);
    });

    test('解锁后再切后台再回来 → 再次落锁（应用锁本意）', () {
      final g = LockGate()..onState(AppLifecycleState.paused);
      expect(g.shouldLockOnResume(biometricBusy: false), isTrue); // 第一次锁
      g.onState(AppLifecycleState.paused);
      expect(g.shouldLockOnResume(biometricBusy: false), isTrue); // 解锁后再锁
    });
  });

  group('sessionUnlockedProvider：解锁标记语义', () {
    test('默认未解锁——冷启动 appLock 开启即锁屏', () {
      final c = ProviderContainer();
      addTearDown(c.dispose);
      expect(c.read(sessionUnlockedProvider), isFalse);
    });
    test('置 true 后（登出逃生/解锁成功）同进程不再锁', () {
      final c = ProviderContainer();
      addTearDown(c.dispose);
      c.read(sessionUnlockedProvider.notifier).state = true;
      expect(c.read(sessionUnlockedProvider), isTrue);
    });
  });
}
