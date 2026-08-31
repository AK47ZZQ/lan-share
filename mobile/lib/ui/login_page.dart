// 登录页：服务器地址 + 账号密码（+TOTP 动态码，按需出现）。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
      ref.read(sessionProvider.notifier).loginTotp(_totp.text.trim());
    } else {
      ref.read(sessionProvider.notifier).login(
          server: _server.text.trim(),
          username: _username.text.trim(),
          password: _password.text);
    }
  }
}
