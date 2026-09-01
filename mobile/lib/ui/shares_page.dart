// 我的分享（M-A4 / FR-7）：列表（状态徽章/过期/下载次数/提取码）+
// 撤销 + 复制链接（双地址选择），能力与网页版分享管理页对齐。
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../data/local/prefs.dart';
import '../share/share_service.dart';

class SharesPage extends StatefulWidget {
  const SharesPage({super.key});

  @override
  State<SharesPage> createState() => _SharesPageState();
}

class _SharesPageState extends State<SharesPage> {
  List<ShareItem>? _items;
  String? _error;
  bool _revoking = false; // 撤销请求互斥（防连点重复 DELETE）

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final items = await ShareService.list();
      if (!mounted) return;
      setState(() => _items = items);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _items = [];
        _error = '分享列表加载失败';
      });
    }
  }

  Future<void> _revoke(ShareItem s) async {
    if (_revoking) return;
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
            title: Text('撤销「${s.name}」的分享？'),
            content: const Text('撤销后原链接立即失效，无法恢复。'),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(ctx, false),
                  child: const Text('取消')),
              FilledButton(
                  onPressed: () => Navigator.pop(ctx, true),
                  child: const Text('撤销')),
            ]));
    if (ok != true) return;
    _revoking = true;
    try {
      await ShareService.revoke(s.token);
      await _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('撤销失败')));
      }
    } finally {
      _revoking = false;
    }
  }

  /// 复制取件链接：单地址直接复制；多地址弹选择（局域网/公网）
  Future<void> _copyLink(ShareItem s) async {
    final addrs = await Prefs.addresses();
    if (!mounted) return;
    String? chosen = addrs.length <= 1 ? addrs.firstOrNull : null;
    chosen ??= await showModalBottomSheet<String>(
        context: context,
        builder: (ctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Padding(
                  padding: EdgeInsets.all(12),
                  child: Text('选择链接使用的服务器地址')),
              for (final a in addrs)
                ListTile(
                    leading: const Icon(Icons.link),
                    title: Text(a, maxLines: 1, overflow: TextOverflow.ellipsis),
                    subtitle: Text(_addrTag(a)),
                    onTap: () => Navigator.pop(ctx, a)),
            ])));
    if (chosen == null || !mounted) return;
    final url = await ShareService.linkOn(chosen, s.token);
    await Clipboard.setData(ClipboardData(text: url));
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('已复制（$_addrTag(chosen)）'),
          duration: const Duration(seconds: 2)));
    }
  }

  static String _addrTag(String a) {
    final host = a.replaceFirst(RegExp(r'^https?://'), '').split(':').first;
    final lan = host.startsWith('192.168.') ||
        host.startsWith('10.') ||
        RegExp(r'^172\.(1[6-9]|2\d|3[01])\.').hasMatch(host) ||
        host == 'localhost' ||
        host == '127.0.0.1' ||
        host.endsWith('.local') ||
        host.endsWith('.lan');
    return lan ? '局域网' : '公网';
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    return Scaffold(
        appBar: AppBar(title: const Text('我的分享'), actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _load),
        ]),
        body: items == null
            ? const Center(child: CircularProgressIndicator())
            : items.isEmpty
                ? ListView(children: [
                    const SizedBox(height: 120),
                    Center(
                        child: Text(_error ?? '还没有分享过文件',
                            style: const TextStyle(color: Colors.grey))),
                    const SizedBox(height: 8),
                    Center(
                        child: TextButton(onPressed: _load, child: const Text('刷新'))),
                  ])
                : RefreshIndicator(
                    onRefresh: _load,
                    child: ListView.builder(
                        itemCount: items.length,
                        itemBuilder: (context, i) {
                          final s = items[i];
                          final dlLeft = s.maxDownloads == null
                              ? '${s.downloads} 次'
                              : '${s.downloads}/${s.maxDownloads} 次';
                          return ListTile(
                            leading: Icon(s.target != null
                                ? Icons.forward_to_inbox
                                : Icons.link),
                            title: Text(s.name,
                                maxLines: 1, overflow: TextOverflow.ellipsis),
                            subtitle: Text(
                                '${s.sizeHuman} · 剩 ${s.expiresIn} · 已下载 $dlLeft'
                                '${s.hasPassword ? ' · 🔒${s.password ?? "有码"}' : ''}'
                                '${s.target != null ? ' · 定向→${s.target}' : ''}',
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(fontSize: 12)),
                            trailing: PopupMenuButton<String>(
                                onSelected: (v) {
                                  if (v == 'copy') _copyLink(s);
                                  if (v == 'revoke') _revoke(s);
                                },
                                itemBuilder: (_) => const [
                                      PopupMenuItem(
                                          value: 'copy',
                                          child: Text('复制链接')),
                                      PopupMenuItem(
                                          value: 'revoke',
                                          child: Text('撤销分享',
                                              style: TextStyle(
                                                  color: Colors.red))),
                                    ]),
                          );
                        })));
  }
}
