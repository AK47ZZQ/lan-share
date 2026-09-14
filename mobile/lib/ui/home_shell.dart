// 主壳：底部导航（首页/照片/文件/笔记/我的）。0.4.2 起首页为综合面板，
// 打开 App 先看概览不再直落照片墙。
// v0.25：笔记升级为一级页签（知识库成为主功能），传输降级为"我的"页入口
//（传输是传输态而非目的地，底栏给更高频的笔记；传输页仍可从首页/文件页/我的进入）。
// M-A4：接管系统分享接收——冷启动/热分享统一在壳层消费（只有登录后才有壳）。
// 0.6.0：应用锁——冷启动/切回 App 需指纹或面部解锁；解不开可用"退出登录"
//        逃生（密码重登），不会永久锁死。
// 0.6.1 修复恶性锁死：锁判定收进 provider（appLock && !本进程已解锁），
//        只在"真正进过后台(paused/hidden)再回来"时落锁——指纹弹窗自身的
//        inactive→resumed 不算切后台；弹窗在屏期间挂起判定；登出重登不重锁。
// 1.0.2（20260914042923-b7c1）：接管系统返回手势——根层此前无 PopScope，
//        任何页签一拉返回直接退到桌面。现在：多选中→先退多选；非首页页签
//        →回首页；首页→2 秒内二次返回才退出（Toast 提示）。
import 'package:flutter/material.dart';
import 'package:flutter/services.dart'
    show SystemNavigator, SystemUiOverlayStyle;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';

import '../data/local/inbox_poll.dart';
import '../share/share_bridge.dart';
import '../state/session.dart';
import '../state/settings.dart';
import 'files_page.dart';
import 'home_page.dart';
import 'notes_page.dart';
import 'me_page.dart';
import 'photos_page.dart';
import 'transfers_page.dart';
import 'theme/design_tokens.dart';
import 'widgets/kit.dart';

class HomeShell extends ConsumerStatefulWidget {
  const HomeShell({super.key});

  @override
  ConsumerState<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends ConsumerState<HomeShell> with WidgetsBindingObserver {
  int _tab = 0;
  bool _consuming = false; // 分享消费互斥（incoming 推送与 initState 拉取并发）
  // 双击返回退出（20260914042923-b7c1）：首页 2 秒内二次返回才真退出
  DateTime _lastBackAt = DateTime.fromMillisecondsSinceEpoch(0);

  /// 传输页（v0.25 起不再是底栏页签）：统一从这里 push，
  /// 首页传输卡/文件页传输入口/"我的"页宫格三处共用。
  void _openTransfers() {
    Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => const TransfersPage()));
  }
  final LockGate _lockGate = LockGate(); // 生命周期→落锁判定（单测覆盖）

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    ShareBridge.wire();
    ShareBridge.onIncoming = _consumeShares;
    // 冷启动经分享面板进入：登录壳就绪后拉一次（Kotlin 侧 pending 一直留着）
    _consumeShares();
    // 不再在 initState 读 appLock 置锁：冷启动锁由 build 门
    // (appLock && !sessionUnlocked) 覆盖，且不受 settings 异步加载时序影响；
    // initState 无条件置锁曾导致登出重登立刻再锁（v0.6.0 锁死第 3 环）。
    // 站内通知前台轮询（v0.11）：未登录时静默失败，登录后自然生效。
    InboxPoll.I.start();
  }

  @override
  void dispose() {
    InboxPoll.I.stop();
    WidgetsBinding.instance.removeObserver(this);
    if (ShareBridge.onIncoming == _consumeShares) ShareBridge.onIncoming = null;
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _lockGate.onState(state);
    // 前台才轮询（后台推送属厂商通道，阶段 2）；回前台立即对齐一次
    if (state == AppLifecycleState.resumed) {
      InboxPoll.I.start();
    } else if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden) {
      InboxPoll.I.stop();
    }
    if (state == AppLifecycleState.resumed &&
        _lockGate.shouldLockOnResume(
            biometricBusy: ref.read(biometricBusyProvider)) &&
        ref.read(settingsProvider).appLock) {
      // 真正从后台回来才落锁；指纹弹窗自身的 resumed 被闸门挡掉
      // （0.6.0 锁死第 1/2 环）
      ref.read(sessionUnlockedProvider.notifier).state = false;
    }
  }

  Future<void> _unlock() async {
    if (ref.read(biometricBusyProvider)) return;
    ref.read(biometricBusyProvider.notifier).state = true;
    try {
      final la = LocalAuthentication();
      final passed = await la.authenticate(
          localizedReason: '解锁 LAN Share',
          options: const AuthenticationOptions(
              biometricOnly: true, stickyAuth: true));
      if (passed && mounted) {
        ref.read(sessionUnlockedProvider.notifier).state = true;
      }
    } catch (_) {
      // 生物识别不可用（硬件/系统态变化）：提示后仍留在锁屏，走退出登录逃生
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('生物识别不可用，可用下方"退出登录"后用密码重新进入')));
      }
    } finally {
      ref.read(biometricBusyProvider.notifier).state = false;
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
    final unlocked = ref.watch(sessionUnlockedProvider);
    if (lockOn && !unlocked) {
      // 锁屏：内容完全不上屏（隐私遮罩即锁屏本身）
      return Scaffold(
        body: Center(
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(Icons.lock_outline,
                size: 56, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: Gap.sm),
            const Text('LAN Share 已锁定'),
            const SizedBox(height: Gap.xxs),
            const Text('指纹/面部解不开？可退出登录后用密码重新进入',
                style: TextStyle(fontSize: 12, color: Colors.grey)),
            const SizedBox(height: Gap.md),
            FilledButton.icon(
                onPressed: _unlock,
                icon: const Icon(Icons.fingerprint),
                label: const Text('解锁')),
            const SizedBox(height: Gap.xs),
            TextButton(
                onPressed: () {
                  // 逃生口：登出回登录页（凭据清空，密码重登）。
                  // 置解锁宽限——同进程重登不再立刻上锁，否则逃生口形同虚设
                  ref.read(sessionUnlockedProvider.notifier).state = true;
                  ref.read(sessionProvider.notifier).logout();
                },
                child: const Text('退出登录')),
          ]),
        ),
      );
    }
    // 状态栏图标亮度随页签：首页顶部是渐变品牌头（白图标），其余浅底（深图标）
    // 返回手势接管（20260914042923-b7c1）：见文件头注释。
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, result) async {
        if (didPop) return;
        if (_tab == 1 && PhotosPage.selectionActive.value) {
          PhotosPage.backExitSelection?.call();
          return;
        }
        if (_tab != 0) {
          setState(() => _tab = 0);
          return;
        }
        final now = DateTime.now();
        if (now.difference(_lastBackAt) < const Duration(seconds: 2)) {
          await SystemNavigator.pop();
        } else {
          _lastBackAt = now;
          showToast('再返回一次退出 LAN Share');
        }
      },
      child: AnnotatedRegion<SystemUiOverlayStyle>(
      value: _tab == 0 ? SystemUiOverlayStyle.light : SystemUiOverlayStyle.dark,
      child: Scaffold(
      // 传输页改走 Navigator.push（不再占底栏页签）——保留原页面与全部功能
      body: IndexedStack(index: _tab, children: [
        HomePage(
            onGoPhotos: () => setState(() => _tab = 1),
            onGoFiles: () => setState(() => _tab = 2),
            onGoTransfers: _openTransfers,
            onGoMe: () => setState(() => _tab = 4)),
        PhotosPage(onGoFiles: () => setState(() => _tab = 2)),
        FilesPage(onGoTransfers: _openTransfers),
        const NotesPage(),
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
          NavigationDestination(icon: Icon(Icons.edit_note_outlined),
              selectedIcon: Icon(Icons.edit_note), label: '笔记'),
          NavigationDestination(icon: Icon(Icons.person_outline),
              selectedIcon: Icon(Icons.person), label: '我的'),
        ],
      ),
      ),
      ),
    );
  }
}
