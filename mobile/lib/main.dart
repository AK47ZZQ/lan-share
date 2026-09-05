import 'package:dynamic_color/dynamic_color.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'backup/backup_bridge.dart';
import 'backup/backup_config.dart';
import 'backup/backup_engine.dart';
import 'data/api/api_client.dart';
import 'data/local/verifier_store.dart';
import 'data/update_service.dart';
import 'state/session.dart';
import 'state/settings.dart';
import 'transfer/transfer_engine.dart';
import 'ui/home_shell.dart';
import 'ui/login_page.dart';
import 'ui/theme/app_theme.dart';

void main() {
  // 旧版 TOTP SP 明文副本启动即清（红队 P2）：不等验证器页/登录页触发，
  // 长期登录设备上明文不该残留（本地操作，静默失败无妨）
  // ignore: discarded_futures
  VerifierStore.purgeLegacySp();
  // 回调在 runApp 前注入：避免"启动期 401 拦截器无回调"的窗口期
  final container = ProviderContainer();
  onUnauthorized = () {
    if (container.read(sessionProvider).loggedIn) {
      container.read(sessionProvider.notifier).logout();
    }
  };
  // 登录态 ↔ 传输引擎联动：登录后恢复队列（含杀进程遗留的续传任务），登出全部挂起
  container.listen<SessionState>(sessionProvider, (prev, next) {
    final engine = TransferEngine.I;
    if (next.loggedIn && prev?.loggedIn != true) {
      // ignore: discarded_futures
      engine.onLogin();
      // 更新安装包清运（v0.16.0）：上一轮会话装完/放弃的 lanshare-*.apk
      // 残留在沙盒会永久堆积，登录后顺手清掉（本地操作，静默失败无妨）
      // ignore: discarded_futures
      UpdateService.purgeInstallers();
      // ignore: discarded_futures
      _recoverWipePause();
      // 自动备份：登录后确保周期任务就位（覆盖升级/重装后 JobScheduler 状态漂移），
      // 并顺手跑一轮（账本 diff 秒级，已备份过的照片直接跳过）
      // ignore: discarded_futures
      _rescheduleBackup();
    } else if (!next.loggedIn && prev?.loggedIn == true) {
      // ignore: discarded_futures
      engine.onLogout();
    }
  });
  runApp(UncontrolledProviderScope(
      container: container, child: const LanShareApp()));
}

Future<void> _rescheduleBackup() async {
  final cfg = await BackupConfig.load();
  if (!cfg.enabled) return;
  await BackupBridge.applySchedule(cfg);
  await BackupBridge.runOnce();
}

/// "清空服务端备份"中断恢复（v0.16.1）：清空期间会临时关停自动备份并落
/// prefs 标记；若恢复开关前进程被杀，标记残留——登录后把备份拉回开启，
/// 否则自动备份静默停摆（审查 P1）。标记只在清空流程内置位，原状态必为
/// 开启，直接恢复是安全的。
Future<void> _recoverWipePause() async {
  try {
    final sp = await SharedPreferences.getInstance();
    if (sp.getString('wipe_backup_paused') != '1') return;
    final cfg = await BackupConfig.load();
    final fixed = cfg.copyWith(enabled: true);
    await fixed.save();
    await BackupBridge.applySchedule(fixed);
    await sp.remove('wipe_backup_paused');
  } catch (_) {}
}

/// WorkManager Worker 的后台 isolate 入口（APP-DESIGN §4）：
/// 原生拉起独立 FlutterEngine → 等 ready 握手 → invoke "run" →
/// 返回结果 JSON → 原生销毁引擎。插件随引擎自动注册（sqflite/SharedPreferences/
/// flutter_secure_storage 在后台 isolate 均可用）。
@pragma('vm:entry-point')
void backupMain() {
  WidgetsFlutterBinding.ensureInitialized();
  const ch = MethodChannel('lanshare/backup');
  ch.setMethodCallHandler((call) async {
    // 触发来源（periodic/media_change/manual）贯穿进引擎：manual 需绕过
    // "仅充电"暂停——0.7.3 前手动轮被充电约束静默吞掉
    if (call.method == 'run') {
      return await BackupEngine.run((call.arguments as String?) ?? 'periodic');
    }
    return null;
  });
  // 就绪握手：引擎拉起到 handler 就绪有窗口期，Kotlin 等 ready 再下发 run
  // ignore: discarded_futures
  ch.invokeMethod('ready');
}

class LanShareApp extends ConsumerWidget {
  const LanShareApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    final settings = ref.watch(settingsProvider);
    // 主题引擎（0.8.5）：accent 指向预置色板，'auto' 跟随壁纸取色
    // （Android 12+ Material You；拿不到动态色时回落云朵紫蓝）。
    return DynamicColorBuilder(builder: (lightDynamic, darkDynamic) {
      final palette = settings.accent == Palettes.auto && lightDynamic != null
          ? Palette(
              id: Palettes.auto,
              name: '跟随壁纸',
              seed: lightDynamic.primary,
              gradient: [lightDynamic.primary, lightDynamic.tertiary],
              darkBg: darkDynamic?.surface ?? Palettes.violet.darkBg)
          : paletteById(settings.accent);
      return MaterialApp(
        title: 'LAN Share',
        debugShowCheckedModeBanner: false,
        themeMode: settings.themeMode,
        // 0.8.4 主题系统：brand tokens + 亮/暗/AMOLED 三套，0.8.5 挂色板。
        // AMOLED 开关生效于"暗色生效"的场合（dark 或 system 且系统为暗）。
        theme: AppTheme.light(palette),
        darkTheme:
            settings.amoledDark ? AppTheme.amoled(palette) : AppTheme.dark(palette),
        // 品牌头/云朵组件从作用域取当前色板的渐变
        builder: (context, child) =>
            PaletteScope(palette: palette, child: child ?? const SizedBox.shrink()),
        home: session.loggedIn ? const HomeShell() : const LoginPage(),
      );
    });
  }
}
