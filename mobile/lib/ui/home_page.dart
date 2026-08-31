// 主页（M-A0 骨架）：确认登录身份 + 退出。照片墙/文件浏览 M-A1 接入。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/session.dart';

class HomePage extends ConsumerWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('LAN Share'),
        actions: [
          IconButton(
            tooltip: '退出登录',
            icon: const Icon(Icons.logout),
            onPressed: () =>
                ref.read(sessionProvider.notifier).logout(),
          ),
        ],
      ),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Text('✅ 已登录（M-A0 骨架）', style: TextStyle(fontSize: 18)),
            const SizedBox(height: 8),
            Text('当前账号：${session.username ?? '?'}'
                '（${session.role == 'admin' ? '管理员' : session.role == 'guest' ? '访客' : '成员'}）'),
            const SizedBox(height: 24),
            const Text('照片墙 / 文件浏览 / 传输中心\n将在 M-A1 起逐里程碑接入',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.grey)),
          ],
        ),
      ),
    );
  }
}
