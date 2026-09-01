// 我的：登录身份 + 设备管理（M-A0 三端点的 App 侧落地）+ 服务器地址 + 退出。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'backup_settings_page.dart';
import '../data/api/api_client.dart';
import '../data/local/prefs.dart';
import '../state/session.dart';
import 'shares_page.dart';

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

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider);
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
            subtitle: const Text('管理分享链接：复制/撤销'),
            onTap: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => const SharesPage())),
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
        const Center(child: Text('LAN Share App · M-A4',
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
