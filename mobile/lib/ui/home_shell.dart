// 主壳：底部导航（照片/文件/传输/我的），M-A2 加入传输中心。
// M-A4：接管系统分享接收——冷启动/热分享统一在壳层消费（只有登录后才有壳）。
import 'package:flutter/material.dart';

import '../share/share_bridge.dart';
import 'files_page.dart';
import 'me_page.dart';
import 'photos_page.dart';
import 'transfers_page.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _tab = 0;
  bool _consuming = false; // 分享消费互斥（incoming 推送与 initState 拉取并发）

  @override
  void initState() {
    super.initState();
    ShareBridge.wire();
    ShareBridge.onIncoming = _consumeShares;
    // 冷启动经分享面板进入：登录壳就绪后拉一次（Kotlin 侧 pending 一直留着）
    _consumeShares();
  }

  @override
  void dispose() {
    if (ShareBridge.onIncoming == _consumeShares) ShareBridge.onIncoming = null;
    super.dispose();
  }

  /// 取走待收分享入上传队列；有新增则提示并跳传输页
  Future<void> _consumeShares() async {
    if (_consuming) return;
    _consuming = true;
    try {
      final n = await ShareBridge.consumePending();
      if (!mounted || n == 0) return;
      setState(() => _tab = 2);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(
              '已收到 $n 个分享文件，上传到「分享暂存」'),
          duration: const Duration(seconds: 3)));
    } finally {
      _consuming = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(index: _tab, children: [
        PhotosPage(onGoFiles: () => setState(() => _tab = 1)),
        FilesPage(onGoTransfers: () => setState(() => _tab = 2)),
        const TransfersPage(),
        MePage(onGoFiles: () => setState(() => _tab = 1)),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.photo_outlined),
              selectedIcon: Icon(Icons.photo), label: '照片'),
          NavigationDestination(icon: Icon(Icons.folder_outlined),
              selectedIcon: Icon(Icons.folder), label: '文件'),
          NavigationDestination(icon: Icon(Icons.swap_vert_circle_outlined),
              selectedIcon: Icon(Icons.swap_vert_circle), label: '传输'),
          NavigationDestination(icon: Icon(Icons.person_outline),
              selectedIcon: Icon(Icons.person), label: '我的'),
        ],
      ),
    );
  }
}
