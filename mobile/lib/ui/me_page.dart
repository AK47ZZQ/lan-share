// 我的：登录身份 + 设备管理 + 设置区（0.6.0：主题三档/修改密码/通知/应用锁/
// 缓存管理/关于与检查更新）+ 服务器地址 + 退出。
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';
import 'package:path_provider/path_provider.dart';

import 'about_page.dart';
import 'backup_settings_page.dart';
import '../data/api/api_client.dart';
import '../data/api/auth_cache.dart';
import '../data/local/prefs.dart';
import '../state/session.dart';
import '../state/settings.dart';
import '../transfer/transfer_task.dart' show humanSize;
import 'shares_page.dart';
import 'trash_page.dart';
import 'verifier_page.dart';

class MePage extends ConsumerStatefulWidget {
  final VoidCallback? onGoFiles;
  const MePage({super.key, this.onGoFiles});

  @override
  ConsumerState<MePage> createState() => _MePageState();
}

class _MePageState extends ConsumerState<MePage> {
  List<_Device> _devices = [];
  bool _loadingDevices = true;
  String? _server;
  String _cacheSize = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    _server = await Prefs.lastAddress();
    if (mounted) setState(() {});
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/auth/devices');
      final list = (r.data['devices'] as List)
          .map((e) => _Device(
              id: (e['id'] as num).toInt(),
              name: e['name'] as String,
              createdAt: (e['created_at'] as num).toDouble(),
              lastSeen: e['last_seen'] as num?,
              current: e['current'] as bool? ?? false))
          .toList();
      if (!mounted) return;
      setState(() {
        _devices = list;
        _loadingDevices = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingDevices = false);
    }
    await _loadCacheSize();
  }

  /// 缩略图缓存占用（flutter_cache_manager 自定义 key 落在临时目录同名子目录）
  Future<void> _loadCacheSize() async {
    try {
      final temp = await getTemporaryDirectory();
      final dir = Directory('${temp.path}/authCachedImageData');
      if (!dir.existsSync()) {
        if (mounted) setState(() => _cacheSize = '0 B');
        return;
      }
      var total = 0;
      await for (final f in dir.list(recursive: true, followLinks: false)) {
        if (f is File) {
          try {
            total += f.lengthSync();
          } catch (_) {}
        }
      }
      if (mounted) setState(() => _cacheSize = humanSize(total));
    } catch (_) {
      if (mounted) setState(() => _cacheSize = '');
    }
  }

  Future<void> _revoke(_Device d) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('吊销「${d.name}」？'),
        content: const Text('该设备下次请求将需要重新登录。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('吊销')),
        ],
      ),
    );
    if (ok != true) return;
    final dio = await ApiClient.I.dio();
    try {
      await dio.delete('/api/auth/devices/${d.id}');
      if (d.current) {
        // 吊销的是本机：清 Token 回登录页
        if (mounted) ref.read(sessionProvider.notifier).logout();
        return;
      }
      await _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('吊销失败')));
      }
    }
  }

  // ── 设置区动作 ───────────────────────────────────────────

  Future<void> _pickThemeMode() async {
    final cur = ref.read(settingsProvider).themeMode;
    final sel = await showDialog<ThemeMode>(
        context: context,
        builder: (ctx) => SimpleDialog(
                title: const Text('深色模式'),
                children: <Widget>[
                  for (final (m, label) in [
                    (ThemeMode.system, '跟随系统'),
                    (ThemeMode.light, '浅色'),
                    (ThemeMode.dark, '深色'),
                  ])
                    ListTile(
                        title: Text(label),
                        trailing: cur == m
                            ? const Icon(Icons.check)
                            : null,
                        onTap: () => Navigator.pop(ctx, m)),
                ]));
    if (sel != null) {
      await ref.read(settingsProvider.notifier).setThemeMode(sel);
    }
  }

  Future<void> _changePassword() async {
    final oldCtrl = TextEditingController();
    final newCtrl = TextEditingController();
    final againCtrl = TextEditingController();
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('修改密码'),
                content: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min,
                    children: [
                  TextField(controller: oldCtrl,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '原密码')),
                  TextField(controller: newCtrl,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '新密码（至少 8 位）')),
                  TextField(controller: againCtrl,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '再输一遍新密码')),
                ])),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('修改')),
                ]));
    if (ok != true || !mounted) {
      oldCtrl.dispose();
      newCtrl.dispose();
      againCtrl.dispose();
      return;
    }
    if (newCtrl.text.length < 8) {
      _toast('新密码至少 8 位');
      return;
    }
    if (newCtrl.text != againCtrl.text) {
      _toast('两次输入的新密码不一致');
      return;
    }
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/auth/password', data: {
        'old_password': oldCtrl.text,
        'new_password': newCtrl.text,
      });
      _toast('密码已修改（网页端会话需重新登录）');
    } catch (e) {
      _toast('修改失败：${_detail(e)}');
    } finally {
      oldCtrl.dispose();
      newCtrl.dispose();
      againCtrl.dispose();
    }
  }

  Future<void> _toggleAppLock(bool v) async {
    if (v) {
      final la = LocalAuthentication();
      final can = await la.canCheckBiometrics;
      if (!can) {
        if (!mounted) return;
        _toast('本机未录入指纹/面部，无法开启应用锁');
        return;
      }
      // 开启前先验证一次：防止他人借机开启
      final passed = await la.authenticate(
          localizedReason: '验证以开启应用锁',
          options: const AuthenticationOptions(biometricOnly: true));
      if (!passed) return;
    }
    await ref.read(settingsProvider.notifier).setAppLock(v);
  }

  Future<void> _clearCache() async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('清理缩略图缓存？'),
                content: Text('当前占用 $_cacheSize。清理后浏览照片会重新下载缩略图。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('清理')),
                ]));
    if (ok != true) return;
    await AuthCacheManager.I.emptyCache();
    await _loadCacheSize();
    _toast('已清理');
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg), duration: const Duration(seconds: 2)));
  }

  static String _detail(Object e) {
    final s = e.toString();
    return s.length > 80 ? s.substring(0, 80) : s;
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider);
    final settings = ref.watch(settingsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('我的')),
      body: ListView(children: [
        const SizedBox(height: 8),
        ListTile(
          leading: CircleAvatar(child: Text((session.username ?? '?').isNotEmpty
              ? session.username![0].toUpperCase() : '?')),
          title: Text(session.username ?? ''),
          subtitle: Text(session.role == 'admin' ? '管理员'
              : session.role == 'guest' ? '访客' : '成员'),
        ),
        ListTile(
          leading: const Icon(Icons.dns_outlined),
          title: Text(_server ?? ''),
          subtitle: const Text('当前服务器'),
        ),
        const Divider(),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 16, vertical: 6),
          child: Text('设置', style: TextStyle(fontWeight: FontWeight.bold)),
        ),
        ListTile(
          leading: const Icon(Icons.dark_mode_outlined),
          title: const Text('深色模式'),
          subtitle: Text(switch (settings.themeMode) {
            ThemeMode.system => '跟随系统',
            ThemeMode.light => '浅色',
            ThemeMode.dark => '深色',
          }),
          onTap: _pickThemeMode,
        ),
        ListTile(
          leading: const Icon(Icons.lock_outline),
          title: const Text('修改密码'),
          onTap: _changePassword,
        ),
        SwitchListTile(
          secondary: const Icon(Icons.notifications_outlined),
          title: const Text('传输完成通知'),
          subtitle: const Text('上传/下载完成时系统提醒'),
          value: settings.notifyTransferDone,
          onChanged: (v) =>
              ref.read(settingsProvider.notifier).setNotifyTransferDone(v),
        ),
        SwitchListTile(
          secondary: const Icon(Icons.fingerprint),
          title: const Text('应用锁'),
          subtitle: const Text('打开或切回 App 需指纹/面部解锁'),
          value: settings.appLock,
          onChanged: _toggleAppLock,
        ),
        ListTile(
          leading: const Icon(Icons.image_outlined),
          title: const Text('清理缩略图缓存'),
          subtitle: Text(_cacheSize.isEmpty ? '统计中…' : '占用 $_cacheSize'),
          onTap: _clearCache,
        ),
        ListTile(
          leading: const Icon(Icons.info_outline),
          title: const Text('关于与检查更新'),
          onTap: () => Navigator.push(context,
              MaterialPageRoute(builder: (_) => const AboutPage())),
        ),
        // 访客只读：无备份（APP-REQUIREMENTS §2）
        if (session.role != 'guest')
          ListTile(
            leading: const Icon(Icons.backup_outlined),
            title: const Text('自动备份'),
            subtitle: const Text('手机相册自动存进网盘'),
            onTap: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => const BackupSettingsPage())),
          ),
        // 分享管理（M-A4 FR-7）：访客无分享创建，入口一并隐藏
        if (session.role != 'guest')
          ListTile(
            leading: const Icon(Icons.share_outlined),
            title: const Text('我的分享'),
            subtitle: const Text('管理分享链接：复制/二维码/编辑/撤销'),
            onTap: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => const SharesPage())),
          ),
        ListTile(
          leading: const Icon(Icons.delete_outline),
          title: const Text('回收站'),
          subtitle: const Text('恢复或彻底删除已删文件'),
          onTap: () => Navigator.push(context,
              MaterialPageRoute(builder: (_) => const TrashPage())),
        ),
        // App 内置验证器（v0.5.2）：两步验证动态码本机生成，免装第三方验证器
        ListTile(
          leading: const Icon(Icons.phonelink_lock_outlined),
          title: const Text('验证器'),
          subtitle: const Text('两步验证动态码，扫码绑定后本机直接显示'),
          onTap: () => Navigator.push(context,
              MaterialPageRoute(builder: (_) => const VerifierPage())),
        ),
        const Divider(),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 16, vertical: 6),
          child: Text('已登录设备', style: TextStyle(fontWeight: FontWeight.bold)),
        ),
        if (_loadingDevices)
          const Padding(padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator())),
        if (!_loadingDevices)
          for (final d in _devices)
            ListTile(
              leading: Icon(d.current ? Icons.phone_android : Icons.devices_other),
              title: Text(d.name),
              subtitle: Text(d.current
                  ? '本设备（当前登录）'
                  : '最近活跃：${d.lastSeen == null ? '-' : DateTime.fromMillisecondsSinceEpoch((d.lastSeen! * 1000).toInt()).toString().split('.').first}'),
              trailing: d.current
                  ? null
                  : TextButton(onPressed: () => _revoke(d), child: const Text('吊销')),
              onTap: d.current ? null : () => _revoke(d),
            ),
        const Divider(),
        ListTile(
          leading: const Icon(Icons.logout),
          title: const Text('退出登录'),
          onTap: () => ref.read(sessionProvider.notifier).logout(),
        ),
        const SizedBox(height: 12),
        const Center(child: Text('LAN Share App · v0.6.0',
            style: TextStyle(fontSize: 11, color: Colors.grey))),
        const SizedBox(height: 24),
      ]),
    );
  }
}

class _Device {
  final int id;
  final String name;
  final double createdAt;
  final num? lastSeen;
  final bool current;
  _Device({required this.id, required this.name, required this.createdAt,
      required this.lastSeen, required this.current});
}
