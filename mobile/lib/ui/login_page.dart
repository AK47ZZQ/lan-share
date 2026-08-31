// 登录页：服务器地址 + 账号密码（+TOTP 动态码，按需出现）。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/api_client.dart';
import '../data/api/trust.dart';
import '../data/local/prefs.dart';
import '../state/session.dart';

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _server = TextEditingController();
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _totp = TextEditingController();
  List<String> _addresses = const [];

  @override
  void dispose() {
    _server.dispose();
    _username.dispose();
    _password.dispose();
    _totp.dispose();
    super.dispose();
  }

  @override
  void initState() {
    super.initState();
    () async {
      _addresses = await Prefs.addresses();
      _server.text = await Prefs.lastAddress();
      if (mounted) setState(() {});
    }();
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider);
    ref.listen(sessionProvider, (prev, next) {
      if (next.loggedIn) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('欢迎，${next.username ?? ''}')));
      }
      // 自签证书首次连接：弹"信任此服务器"确认框（APP-DESIGN §5）
      if (next.trustIssue != null && (prev?.trustIssue == null ||
          prev!.trustIssue?.fingerprint != next.trustIssue!.fingerprint)) {
        _askTrust(next.trustIssue!);
      }
    });

    return Scaffold(
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            const SizedBox(height: 32),
            Text('LAN Share', style: Theme.of(context).textTheme.headlineMedium),
            const SizedBox(height: 4),
            Text('家庭网盘', style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 32),
            TextField(
              controller: _server,
              decoration: const InputDecoration(
                  labelText: '服务器地址', hintText: 'https://192.168.1.100:8765'),
              keyboardType: TextInputType.url,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _username,
              decoration: const InputDecoration(labelText: '用户名'),
              autocorrect: false,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _password,
              decoration: const InputDecoration(labelText: '密码'),
              obscureText: true,
              onSubmitted: (_) => _submit(),
            ),
            if (session.needTotp) ...[
              const SizedBox(height: 12),
              TextField(
                controller: _totp,
                decoration: const InputDecoration(
                    labelText: '两步验证动态码（6 位）'),
                keyboardType: TextInputType.number,
                maxLength: 6,
                onSubmitted: (_) => _submit(),
              ),
            ],
            const SizedBox(height: 24),
            FilledButton(
              onPressed: session.checking ? null : _submit,
              child: Text(session.checking
                  ? '登录中…'
                  : session.needTotp ? '验证并登录' : '登录'),
            ),
            if (session.error != null && session.error != '__totp_required__') ...[
              const SizedBox(height: 12),
              Text(session.error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
            const SizedBox(height: 24),
            if (_addresses.length > 1)
              Wrap(
                spacing: 8,
                children: _addresses
                    .map((a) => ActionChip(
                        label: Text(a, style: const TextStyle(fontSize: 12)),
                        onPressed: () => _server.text = a))
                    .toList(),
              ),
          ],
        ),
      ),
    );
  }

  void _submit() {
    final s = ref.read(sessionProvider);
    if (s.needTotp) {
      ref.read(sessionProvider.notifier)
          .loginTotp(_totp.text.trim(), server: _server.text.trim());
    } else {
      ref.read(sessionProvider.notifier).login(
          server: _server.text.trim(),
          username: _username.text.trim(),
          password: _password.text);
    }
  }

  /// 证书信任确认：展示指纹，用户显式勾选后重试登录
  Future<void> _askTrust(TrustRequiredError issue) async {
    final ok = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: const Text('信任此服务器？'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment:
            CrossAxisAlignment.start, children: [
          Text('该服务器的证书不是由公共 CA 签发的（家庭自建属正常）。',
              style: Theme.of(ctx).textTheme.bodySmall),
          const SizedBox(height: 10),
          const Text('证书指纹 (SHA256)：', style: TextStyle(fontSize: 12)),
          SelectableText(issue.pretty,
              style: const TextStyle(fontSize: 12, fontFamily: 'monospace')),
          const SizedBox(height: 10),
          const Text('请核对指纹与服务器管理员（你）记录的一致后再信任。',
              style: TextStyle(fontSize: 12)),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('信任此服务器')),
        ],
      ),
    );
    if (ok == true) {
      await TrustStore.I.trust(issue.host, issue.port, issue.fingerprint);
      _submit(); // 信任后自动重试
    }
  }
}
