// 创建分享（M-A4 / FR-7）：文件 → 有效期/提取码/下载上限/定向成员 →
// POST /api/share → 展示取件链接（保存了多个服务器地址时逐个给复制按钮）。
// 请求与错误文案解析都在 ShareService（data 层），本页不再 import dio。
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../data/local/prefs.dart';
import '../share/share_service.dart';
import 'theme/design_tokens.dart';

class ShareCreateSheet extends StatefulWidget {
  final String path; // 网盘内虚拟路径（/api/share 的 Form path）
  final String name;
  const ShareCreateSheet({super.key, required this.path, required this.name});

  @override
  State<ShareCreateSheet> createState() => _ShareCreateSheetState();
}

class _ShareCreateSheetState extends State<ShareCreateSheet> {
  static const _expiryChoices = [
    (24, '1 天'),
    (72, '3 天'),
    (168, '7 天'),
    (720, '30 天'),
  ];

  int _expires = 72;
  final _pwd = TextEditingController();
  final _maxDl = TextEditingController();
  List<String> _members = const [];
  String? _target; // null = 公开链接
  bool _busy = false;
  String? _error;
  String? _doneToken; // 创建成功后切成功视图

  @override
  void initState() {
    super.initState();
    // ignore: discarded_futures
    ShareService.members().then((m) {
      if (mounted) setState(() => _members = m);
    }).catchError((_) {});
  }

  @override
  void dispose() {
    _pwd.dispose();
    _maxDl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final max = int.tryParse(_maxDl.text.trim());
      final token = await ShareService.create(
          path: widget.path,
          expiresHours: _expires,
          password: _pwd.text.trim(),
          maxDownloads: (max != null && max >= 1) ? max : null,
          target: _target);
      if (!mounted) return;
      setState(() {
        _doneToken = token;
        _busy = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = ShareService.createErrorText(e);
      });
    }
  }

  /// 成功视图：各已存服务器地址各一行复制按钮（局域网/公网双地址）
  Widget _successView() {
    return FutureBuilder<List<String>>(
        future: Prefs.addresses(),
        builder: (bctx, snap) {
          final addrs = snap.data ?? const [];
          return Padding(
              padding: const EdgeInsets.all(Gap.lg),
              child: Column(mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Row(children: [
                  Icon(Icons.check_circle, color: Colors.green),
                  SizedBox(width: Gap.xs),
                  Text('分享已创建',
                      style:
                          TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                ]),
                const SizedBox(height: Gap.sm),
                Text(widget.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Colors.grey)),
                const SizedBox(height: Gap.sm),
                for (final a in addrs)
                  Padding(
                      padding: const EdgeInsets.only(bottom: Gap.xs),
                      child: Row(children: [
                        Expanded(
                            child: Text('$a/s/$_doneToken',
                                maxLines: 1, overflow: TextOverflow.ellipsis,
                                style: const TextStyle(fontSize: 12))),
                        TextButton(
                            onPressed: () async {
                              await Clipboard.setData(ClipboardData(
                                  text: await ShareService.linkOn(
                                      a, _doneToken!)));
                              if (mounted) {
                                Navigator.of(context, rootNavigator: true)
                                    .pop('copied');
                                ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                                    content: Text('已复制（${_addrTag(a)}）'),
                                    duration: const Duration(seconds: 2)));
                              }
                            },
                            child: Text('复制${_addrTag(a)}')),
                      ])),
                const Text('提示：对方在浏览器打开链接即可取件；可随时在「我的分享」里撤销',
                    style: TextStyle(fontSize: 11, color: Colors.grey)),
              ]));
        });
  }

  static String _addrTag(String a) {
    final host = a.replaceFirst(RegExp(r'^https?://'), '').split(':').first;
    // RFC1918/localhost 判局域网，与备份引擎 _isPublicHost 同思路
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
    return SafeArea(
        child: Padding(
            padding:
                EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
            child: _doneToken != null
                ? _successView()
                : SingleChildScrollView(
                    padding: const EdgeInsets.all(Gap.lg),
                    child: Column(mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                      Text('分享「${widget.name}」',
                          maxLines: 2, overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                              fontWeight: FontWeight.bold, fontSize: 16)),
                      const SizedBox(height: Gap.md),
                      const Text('有效期'),
                      const SizedBox(height: 6),
                      Wrap(spacing: 8, children: [
                        for (final (h, label) in _expiryChoices)
                          ChoiceChip(
                              label: Text(label),
                              selected: _expires == h,
                              onSelected: (_) => setState(() => _expires = h)),
                      ]),
                      const SizedBox(height: Gap.sm),
                      Row(children: [
                        Expanded(
                            child: TextField(
                                controller: _pwd,
                                obscureText: true,
                                decoration: const InputDecoration(
                                    labelText: '提取码（可空）',
                                    border: OutlineInputBorder(),
                                    isDense: true))),
                        const SizedBox(width: Gap.sm),
                        SizedBox(
                            width: 120,
                            child: TextField(
                                controller: _maxDl,
                                keyboardType: TextInputType.number,
                                decoration: const InputDecoration(
                                    labelText: '下载上限',
                                    border: OutlineInputBorder(),
                                    isDense: true))),
                      ]),
                      const SizedBox(height: Gap.sm),
                      if (_members.isNotEmpty)
                        DropdownButtonFormField<String?>(
                            initialValue: _target,
                            decoration: const InputDecoration(
                                labelText: '定向成员（可空=公开链接）',
                                border: OutlineInputBorder(),
                                isDense: true),
                            items: [
                              const DropdownMenuItem(
                                  value: null, child: Text('不限（公开链接）')),
                              for (final m in _members)
                                DropdownMenuItem(value: m, child: Text(m)),
                            ],
                            onChanged: (v) => setState(() => _target = v)),
                      if (_error != null)
                        Padding(
                            padding: const EdgeInsets.only(top: 10),
                            child: Text(_error!,
                                style: const TextStyle(
                                    color: Colors.red, fontSize: 12))),
                      const SizedBox(height: Gap.md),
                      SizedBox(
                          width: double.infinity,
                          child: FilledButton.icon(
                              onPressed: _busy ? null : _submit,
                              icon: _busy
                                  ? const SizedBox(
                                      width: 16,
                                      height: 16,
                                      child: CircularProgressIndicator(
                                          strokeWidth: 2))
                                  : const Icon(Icons.link),
                              label: Text(_busy ? '创建中…' : '创建分享链接'))),
                    ]))));
  }
}

/// 入口便捷函数：文件页/分享接收处调用。返回后由调用方决定是否提示。
Future<void> showShareCreate(BuildContext context,
    {required String path, required String name}) async {
  await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (_) => ShareCreateSheet(path: path, name: name));
}
