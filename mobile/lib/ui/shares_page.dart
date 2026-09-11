// 我的分享（M-A4 / FR-7）：列表（状态徽章/过期/下载次数/提取码）+
// 撤销 + 复制链接 + 二维码 + 系统分享面板外发 + 编辑（0.6.0），
// 能力与网页版分享管理页对齐。
import 'package:flutter/material.dart';
import 'widgets/brand.dart';
import 'package:flutter/services.dart';
import 'package:share_plus/share_plus.dart';

import '../data/local/prefs.dart';
import '../share/share_service.dart';
import 'widgets/qr_dialog.dart';
import 'widgets/kit.dart';

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

  Future<void> _edit(ShareItem s) async {
    final hoursCtrl = TextEditingController(
        text: s.expiresIn.endsWith('h') ? s.expiresIn.substring(0, s.expiresIn.length - 1) : '');
    final maxCtrl = TextEditingController(text: s.maxDownloads?.toString() ?? '');
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
            title: Text('编辑「${s.name}」', style: const TextStyle(fontSize: 16)),
            content: Column(mainAxisSize: MainAxisSize.min, children: [
              TextField(
                  controller: hoursCtrl,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                      labelText: '剩余有效时间（小时，从现在重计）')),
              TextField(
                  controller: maxCtrl,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                      labelText: '下载上限（留空 = 不限）',
                      helperText: '改小已超限的分享会立即封口')),
            ]),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(ctx, false),
                  child: const Text('取消')),
              FilledButton(
                  onPressed: () => Navigator.pop(ctx, true),
                  child: const Text('保存')),
            ]));
    if (ok != true || !mounted) return;
    final hours = int.tryParse(hoursCtrl.text.trim());
    final maxText = maxCtrl.text.trim();
    try {
      await ShareService.edit(s.token,
          expiresHours: hours,
          maxDownloads: maxText.isEmpty ? null : int.parse(maxText));
      showToast('已保存');
      await _load();
    } catch (_) {
      showToast('保存失败（数值需为正整数）');
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
          content: Text('已复制（${_addrTag(chosen)}）'),
          duration: const Duration(seconds: 2)));
    }
  }

  /// 二维码：与复制链接同地址选择逻辑，本地渲染
  Future<void> _showQr(ShareItem s) async {
    final addrs = await Prefs.addresses();
    if (!mounted) return;
    String? chosen = addrs.length <= 1 ? addrs.firstOrNull : null;
    chosen ??= await showModalBottomSheet<String>(
        context: context,
        builder: (ctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Padding(
                  padding: EdgeInsets.all(12),
                  child: Text('选择二维码使用的服务器地址')),
              for (final a in addrs)
                ListTile(
                    leading: const Icon(Icons.qr_code),
                    title: Text(a, maxLines: 1, overflow: TextOverflow.ellipsis),
                    subtitle: Text(_addrTag(a)),
                    onTap: () => Navigator.pop(ctx, a)),
            ])));
    if (chosen == null || !mounted) return;
    final url = await ShareService.linkOn(chosen, s.token);
    if (!mounted) return;
    // ignore: discarded_futures
    showDialog<void>(
        context: context,
        builder: (_) => QrDialog(url: url, title: '「${s.name}」取件码'));
  }

  /// 系统分享面板外发：直发微信/QQ 等会话
  Future<void> _systemShare(ShareItem s) async {
    final url = await ShareService.linkOnCurrent(s.token);
    final text = s.hasPassword
        ? '【LAN Share】${s.name}\n取件链接：$url\n提取码：${s.password ?? "见分享管理"}'
        : '【LAN Share】${s.name}\n取件链接：$url';
    await Share.share(text, subject: s.name);
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
                    EmptyState(
                        title: _error ?? '还没有分享过文件',
                        hint: '在文件页选中文件 → ⋯ → 分享',
                        action: TextButton(onPressed: _load, child: const Text('刷新'))),
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
                                  if (v == 'qr') _showQr(s);
                                  if (v == 'send') _systemShare(s);
                                  if (v == 'edit') _edit(s);
                                  if (v == 'revoke') _revoke(s);
                                },
                                itemBuilder: (_) => const [
                                      PopupMenuItem(
                                          value: 'copy',
                                          child: Text('复制链接')),
                                      PopupMenuItem(
                                          value: 'qr',
                                          child: Text('二维码')),
                                      PopupMenuItem(
                                          value: 'send',
                                          child: Text('发给…（系统分享）')),
                                      PopupMenuItem(
                                          value: 'edit',
                                          child: Text('编辑有效期/上限')),
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
