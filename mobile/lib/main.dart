import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'backup/backup_bridge.dart';
import 'backup/backup_config.dart';
import 'backup/backup_engine.dart';
import 'data/api/api_client.dart';
import 'state/session.dart';
import 'transfer/transfer_engine.dart';
import 'ui/home_shell.dart';
import 'ui/login_page.dart';

void main() {
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

/// WorkManager Worker 的后台 isolate 入口（APP-DESIGN §4）：
/// 原生拉起独立 FlutterEngine → 等 ready 握手 → invoke "run" →
/// 返回结果 JSON → 原生销毁引擎。插件随引擎自动注册（sqflite/SharedPreferences/
/// flutter_secure_storage 在后台 isolate 均可用）。
@pragma('vm:entry-point')
void backupMain() {
  WidgetsFlutterBinding.ensureInitialized();
  const ch = MethodChannel('lanshare/backup');
  ch.setMethodCallHandler((call) async {
    if (call.method == 'run') return await BackupEngine.run();
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
    return MaterialApp(
      title: 'LAN Share',
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF5B6CFF)),
      ),
      darkTheme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
            seedColor: const Color(0xFF5B6CFF), brightness: Brightness.dark),
      ),
      home: session.loggedIn ? const HomeShell() : const LoginPage(),
    );
  }
}
