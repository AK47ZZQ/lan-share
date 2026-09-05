// 邀请码注册（v0.15.2）：凭管理员发放的邀请码自助建号。
// 服务端契约：POST /api/invites/redeem {code, username, password} →
// {ok, username, role}；匿名入口（登录态调用也无妨，Bearer 会被忽略）。
// 与网页版登录页「使用邀请码加入」同源；管理端建码在管理面板 → 邀请码。
// 双入口两种底座：登录页进入时无会话且可能刚改过地址——用调用方传入的
// baseUrl 建一次性 Dio（审查 P1：ApiClient.dio() 懒建缓存，可能把
// code+password 打到默认/旧地址）；主页（已登录）走 ApiClient 正常底座。
import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show Clipboard;

import '../data/api/api_client.dart';
import '../data/api/errors.dart';
import '../data/api/trust.dart';

class InviteRedeemPage extends StatefulWidget {
  const InviteRedeemPage({super.key, this.baseUrl});

  /// 登录页入口传入：用这个地址（而非 prefs 缓存地址）提交
  final String? baseUrl;

  /// 邀请码形状（token_urlsafe(12) → [A-Za-z0-9_-]{12,32}）。
  /// 公开供单测对齐（batch074），页面剪贴板回填与测试共用同一份判定。
  static final inviteCodeShape = RegExp(r'^[A-Za-z0-9_-]{12,32}$');

  @override
  State<InviteRedeemPage> createState() => _InviteRedeemPageState();
}

class _InviteRedeemPageState extends State<InviteRedeemPage> {
  final _code = TextEditingController();
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  final _formKey = GlobalKey<FormState>();

  bool _busy = false;
  bool _obscured = true;
  bool _clipped = false; // 邀请码是否从剪贴板自动填入（展示提示用）

  @override
  void initState() {
    super.initState();
    // 成员通常从聊天软件复制邀请码再打开本页：形如 token 的剪贴板内容
    // 直接回填，省一次粘贴。读不到/不像邀请码则静默跳过。
    () async {
      try {
        final text = await Clipboard.getData('text/plain');
        final v = text?.text?.trim() ?? '';
        if (InviteRedeemPage.inviteCodeShape.hasMatch(v) && mounted) {
          setState(() {
            _code.text = v;
            _clipped = true;
          });
        }
      } catch (_) {}
    }();
  }

  @override
  void dispose() {
    _code.dispose();
    _username.dispose();
    _password.dispose();
    _confirm.dispose();
    super.dispose();
  }

  Dio _loginFlowDio() {
    final d = Dio(BaseOptions(
        baseUrl: widget.baseUrl!,
        connectTimeout: const Duration(seconds: 8),
        receiveTimeout: const Duration(seconds: 30)));
    d.httpClientAdapter =
        IOHttpClientAdapter(createHttpClient: TrustStore.createHttpClient);
    return d;
  }

  Future<void> _submit() async {
    if (_busy) return;
    if (!(_formKey.currentState?.validate() ?? false)) return;
    setState(() => _busy = true);
    final loginFlow = widget.baseUrl != null;
    final own = loginFlow ? _loginFlowDio() : null;
    try {
      await TrustStore.I.warmup();
      // x-login-flow：匿名端点不吃 401 踢登逻辑，带上防万一
      final r = await (own ?? await ApiClient.I.dio()).post(
          '/api/invites/redeem',
          data: {
            'code': _code.text.trim(),
            'username': _username.text.trim(),
            'password': _password.text,
          },
          options: Options(headers: {'x-login-flow': '1'}));
      final username = r.data is Map ? '${r.data['username'] ?? ''}' : '';
      final role = r.data is Map ? '${r.data['role'] ?? 'member'}' : 'member';
      if (!mounted) return;
      await showDialog<void>(
          context: context,
          builder: (ctx) => AlertDialog(
                  title: const Text('账号已创建'),
                  content: Text(
                      '$username（${role == 'guest' ? '访客' : '成员'}）\n\n'
                      '退出登录后用新账号密码即可登录。'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(ctx),
                        child: const Text('知道了'))
                  ]));
      if (mounted) Navigator.pop(context);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(errDetail(e)), duration: const Duration(seconds: 3)));
      setState(() => _busy = false);
    } finally {
      own?.close(force: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
        appBar: AppBar(title: const Text('邀请码注册')),
        body: Form(
            key: _formKey,
            autovalidateMode: AutovalidateMode.onUserInteraction,
            child: ListView(padding: const EdgeInsets.all(20), children: [
              Text('输入管理员发放的邀请码，创建你自己的账号。',
                  style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)),
              const SizedBox(height: 16),
              TextFormField(
                  controller: _code,
                  decoration: InputDecoration(
                      labelText: '邀请码',
                      hintText: '如 4fK9...（向管理员索取）',
                      prefixIcon: const Icon(Icons.key_outlined),
                      isDense: true),
                  autocorrect: false,
                  enableSuggestions: false,
                  validator: (v) => (v == null || v.trim().isEmpty)
                      ? '请输入邀请码'
                      : null),
              if (_clipped)
                Padding(
                    padding: const EdgeInsets.only(top: 6, left: 4),
                    child: Text('已从剪贴板自动填入',
                        style: TextStyle(fontSize: 11.5, color: cs.primary))),
              const SizedBox(height: 12),
              TextFormField(
                  controller: _username,
                  decoration: const InputDecoration(
                      labelText: '用户名',
                      prefixIcon: Icon(Icons.person_outline),
                      isDense: true),
                  autocorrect: false,
                  enableSuggestions: false,
                  validator: (v) => (v == null || v.trim().length < 2)
                      ? '用户名至少 2 个字符'
                      : null),
              const SizedBox(height: 12),
              TextFormField(
                  controller: _password,
                  obscureText: _obscured,
                  decoration: InputDecoration(
                      labelText: '密码（至少 8 位）',
                      prefixIcon: const Icon(Icons.lock_outline),
                      isDense: true,
                      suffixIcon: IconButton(
                          icon: Icon(_obscured
                              ? Icons.visibility_outlined
                              : Icons.visibility_off_outlined),
                          onPressed: () =>
                              setState(() => _obscured = !_obscured))),
                  validator: (v) => (v == null || v.length < 8)
                      ? '密码至少 8 位'
                      : null),
              const SizedBox(height: 12),
              TextFormField(
                  controller: _confirm,
                  obscureText: true,
                  decoration: const InputDecoration(
                      labelText: '确认密码',
                      prefixIcon: Icon(Icons.lock_outline),
                      isDense: true),
                  validator: (v) =>
                      v != _password.text ? '两次输入的密码不一致' : null),
              const SizedBox(height: 20),
              FilledButton.icon(
                  onPressed: _busy ? null : _submit,
                  icon: _busy
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.how_to_reg_outlined, size: 18),
                  label: Text(_busy ? '创建中…' : '创建账号')),
              const SizedBox(height: 8),
              Text('没有邀请码？请联系管理员在「管理面板 → 邀请码」生成。',
                  style: TextStyle(
                      fontSize: 11.5, color: cs.onSurfaceVariant),
                  textAlign: TextAlign.center),
            ])));
  }
}
