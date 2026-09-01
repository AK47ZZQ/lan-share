import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
    } else if (!next.loggedIn && prev?.loggedIn == true) {
      // ignore: discarded_futures
      engine.onLogout();
    }
  });
  runApp(UncontrolledProviderScope(
      container: container, child: const LanShareApp()));
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
