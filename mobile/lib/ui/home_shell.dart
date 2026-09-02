// 主壳：底部导航（首页/照片/文件/传输/我的）。0.4.2 起首页为综合面板，
// 打开 App 先看概览不再直落照片墙。
// M-A4：接管系统分享接收——冷启动/热分享统一在壳层消费（只有登录后才有壳）。
// 0.6.0：应用锁——设置开启后，打开/切回 App 需指纹或面部解锁；
//        解不开可用"退出登录"逃生（密码重登），不会永久锁死。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';

import '../share/share_bridge.dart';
import '../state/session.dart';
import '../state/settings.dart';
import 'files_page.dart';
import 'home_page.dart';
import 'me_page.dart';
import 'photos_page.dart';
import 'transfers_page.dart';

class HomeShell extends ConsumerStatefulWidget {
  const HomeShell({super.key});

  @override
  ConsumerState<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends ConsumerState<HomeShell> with WidgetsBindingObserver {
  int _tab = 0;
  bool _consuming = false; // 分享消费互斥（incoming 推送与 initState 拉取并发）
  bool _locked = false;
  bool _authenticating = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    ShareBridge.wire();
    ShareBridge.onIncoming = _consumeShares;
    // 冷启动经分享面板进入：登录壳就绪后拉一次（Kotlin 侧 pending 一直留着）
    _consumeShares();
    _locked = ref.read(settingsProvider).appLock;
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    if (ShareBridge.onIncoming == _consumeShares) ShareBridge.onIncoming = null;
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed &&
        ref.read(settingsProvider).appLock) {
      setState(() => _locked = true);
    }
  }

  Future<void> _unlock() async {
    if (_authenticating) return;
    _authenticating = true;
    try {
      final la = LocalAuthentication();
      final passed = await la.authenticate(
          localizedReason: '解锁 LAN Share',
          options: const AuthenticationOptions(biometricOnly: true));
      if (passed && mounted) setState(() => _locked = false);
    } catch (_) {
      // 生物识别不可用（硬件/系统态变化）：提示后仍留在锁屏，走退出登录逃生
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('生物识别不可用，可用下方"退出登录"后重新登录')));
      }
    } finally {
      _authenticating = false;
      if (mounted) setState(() {});
    }
  }

  /// 取走待收分享入上传队列；有新增则提示并跳传输页
  Future<void> _consumeShares() async {
    if (_consuming) return;
    _consuming = true;
    try {
      final n = await ShareBridge.consumePending();
      if (!mounted || n == 0) return;
      setState(() => _tab = 3);
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
    final lockOn = ref.watch(settingsProvider).appLock;
    if (lockOn && _locked) {
      // 锁屏：内容完全不上屏（隐私遮罩即锁屏本身）
      return Scaffold(
        body: Center(
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(Icons.lock_outline,
                size: 56, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: 12),
            const Text('LAN Share 已锁定'),
            const SizedBox(height: 16),
            FilledButton.icon(
                onPressed: _unlock,
                icon: const Icon(Icons.fingerprint),
                label: const Text('解锁')),
            const SizedBox(height: 8),
            TextButton(
                onPressed: () {
                  // 逃生口：登出回登录页（凭据清空，密码重登）
                  setState(() => _locked = false);
                  ref.read(sessionProvider.notifier).logout();
                },
                child: const Text('退出登录')),
          ]),
        ),
      );
    }
    return Scaffold(
      body: IndexedStack(index: _tab, children: [
        HomePage(
            onGoPhotos: () => setState(() => _tab = 1),
            onGoFiles: () => setState(() => _tab = 2),
            onGoTransfers: () => setState(() => _tab = 3),
            onGoMe: () => setState(() => _tab = 4)),
        PhotosPage(onGoFiles: () => setState(() => _tab = 2)),
        FilesPage(onGoTransfers: () => setState(() => _tab = 3)),
        const TransfersPage(),
        MePage(onGoFiles: () => setState(() => _tab = 2)),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined),
              selectedIcon: Icon(Icons.home), label: '首页'),
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
