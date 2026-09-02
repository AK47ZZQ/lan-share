// 关于页（App v0.6.0）：版本信息 + 检查更新（下载自家 APK 并拉起安装器）+
// 开源许可。发版约定：管理员把新 APK 与 version.json 放共享空间「App安装包/」。
import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../data/local/prefs.dart';
import '../data/update_service.dart';
import 'widgets/brand.dart';

class AboutPage extends StatefulWidget {
  const AboutPage({super.key});

  @override
  State<AboutPage> createState() => _AboutPageState();
}

class _AboutPageState extends State<AboutPage> {
  PackageInfo? _info;
  String? _server;
  UpdateInfo? _remote;
  String? _checkError;
  bool _checking = false;
  double? _progress; // null=未在下载

  @override
  void initState() {
    super.initState();
    () async {
      final info = await PackageInfo.fromPlatform();
      final server = await Prefs.lastAddress();
      if (!mounted) return;
      setState(() {
        _info = info;
        _server = server;
      });
    }();
  }

  Future<void> _check() async {
    setState(() {
      _checking = true;
      _checkError = null;
      _remote = null;
    });
    try {
      final remote = await UpdateService.check();
      if (!mounted) return;
      setState(() {
        _remote = remote;
        _checking = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _checkError = '检查失败：服务器不可达或未登录';
        _checking = false;
      });
    }
  }

  Future<void> _downloadAndInstall() async {
    final remote = _remote;
    if (remote == null || !remote.available) return;
    setState(() => _progress = 0);
    try {
      final path = await UpdateService.download(remote,
          onProgress: (received, total) {
        if (!mounted || total <= 0) return;
        setState(() => _progress = received / total);
      });
      if (!mounted) return;
      setState(() => _progress = null);
      await UpdateService.install(path);
    } catch (_) {
      if (!mounted) return;
      setState(() => _progress = null);
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('下载失败，请稍后重试')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final info = _info;
    final localBuild = int.tryParse(info?.buildNumber ?? '') ?? 0;
    final remote = _remote;
    final hasUpdate =
        remote != null && UpdateService.isNewer(remote, localBuild);
    return Scaffold(
        appBar: AppBar(title: const Text('关于')),
        body: ListView(padding: const EdgeInsets.all(16), children: [
          const SizedBox(height: 16),
          const Center(child: BrandCloud(size: 72, onGradient: false)),
          const SizedBox(height: 12),
          Center(child: Text('LAN Share',
              style: Theme.of(context).textTheme.titleLarge)),
          const SizedBox(height: 4),
          Center(child: Text(
              'v${info?.version ?? '…'} (${info?.buildNumber ?? "…"})',
              style: TextStyle(color: Theme.of(context).colorScheme.outline))),
          const SizedBox(height: 4),
          Center(child: Text('家庭自包含网盘 · ${_server ?? ''}',
              style: const TextStyle(fontSize: 12), textAlign: TextAlign.center)),
          const SizedBox(height: 24),
          Card(elevation: 0,
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                  side: BorderSide(
                      color: Theme.of(context).dividerColor, width: 0.5)),
              child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Column(children: [
                    if (!_checking && remote == null && _checkError == null)
                      FilledButton.tonalIcon(
                          onPressed: _check,
                          icon: const Icon(Icons.system_update_outlined),
                          label: const Text('检查更新')),
                    if (_checking) const CircularProgressIndicator(),
                    if (_checkError != null) ...[
                      Text(_checkError!,
                          style: TextStyle(
                              fontSize: 12,
                              color: Theme.of(context).colorScheme.error)),
                      const SizedBox(height: 8),
                      TextButton(onPressed: _check, child: const Text('重试')),
                    ],
                    if (remote != null && !hasUpdate) ...[
                      const Icon(Icons.check_circle, color: Colors.green),
                      const SizedBox(height: 4),
                      const Text('已是最新版本'),
                      const SizedBox(height: 4),
                      Text('服务端分发版本：v${remote.version} (${remote.build})'
                          '${remote.available ? "" : " · APK 未上传"}',
                          style: const TextStyle(fontSize: 12)),
                      TextButton(onPressed: _check, child: const Text('再查一次')),
                    ],
                    if (hasUpdate) ...[
                      Text('发现新版本 v${remote.version} (${remote.build})',
                          style: const TextStyle(fontWeight: FontWeight.bold)),
                      if (remote.notes.isNotEmpty)
                        Padding(
                            padding: const EdgeInsets.only(top: 4),
                            child: Text(remote.notes,
                                style: const TextStyle(fontSize: 12))),
                      const SizedBox(height: 10),
                      if (_progress != null) ...[
                        LinearProgressIndicator(value: _progress),
                        const SizedBox(height: 4),
                        Text('下载中 ${( _progress! * 100).toStringAsFixed(0)}%',
                            style: const TextStyle(fontSize: 12)),
                      ] else
                        FilledButton.icon(
                            onPressed: remote.available
                                ? _downloadAndInstall
                                : null,
                            icon: const Icon(Icons.download_outlined),
                            label: Text(remote.available
                                ? '下载并安装'
                                : '服务端 APK 未上传')),
                    ],
                  ]))),
          const SizedBox(height: 16),
          TextButton.icon(
              icon: const Icon(Icons.policy_outlined, size: 18),
              label: const Text('开源许可'),
              onPressed: () => showLicensePage(
                  context: context,
                  applicationName: 'LAN Share',
                  applicationVersion: info?.version)),
        ]));
  }
}
