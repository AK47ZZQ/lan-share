import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'data/api/api_client.dart';
import 'state/session.dart';
import 'ui/home_shell.dart';
import 'ui/login_page.dart';

void main() {
  runApp(const ProviderScope(child: LanShareApp()));
}

class LanShareApp extends ConsumerWidget {
  const LanShareApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Token 失效（401 拦截器清掉后）→ 清会话回登录页
    onUnauthorized = () {
      if (ref.read(sessionProvider).loggedIn) {
        ref.read(sessionProvider.notifier).logout();
      }
    };
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
