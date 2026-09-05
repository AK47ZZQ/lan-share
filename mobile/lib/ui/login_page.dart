// 登录页 v2（方向 A：品牌区 + 浮层卡片）。
// 功能：服务器条（历史/扫码/手动输入 + 连接预检）、账号密码 + TOTP 六格、
// 配对码免密登录、生物识别一键登录（凭据存档 + local_auth 门禁）。
// 服务端契约沿用 APP-DESIGN §3：/api/login →(totp_required)→ /api/login/totp
// → /api/auth/token 换长效设备 Token；配对码走 /api/pair/claim 直接换 Token。
import 'dart:async';
import 'dart:io';
import 'dart:math' as math;

import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show SystemUiOverlayStyle;
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/api_client.dart';
import '../data/auto_switch.dart';
import '../data/api/trust.dart';
import '../data/api/trust_flow.dart';
import '../data/local/biometric.dart';
import '../data/local/prefs.dart';
import '../data/local/saved_credential.dart';
import '../data/local/verifier_store.dart';
import '../data/totp.dart';
import '../state/session.dart';
import 'invite_redeem_page.dart';
import 'theme/app_theme.dart';
import 'widgets/brand.dart';
import 'widgets/server_picker.dart' show shortHost, showServerPicker;

enum _Probe { idle, probing, ok, fail }

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage>
    with SingleTickerProviderStateMixin {
  final _server = TextEditingController();
  final _username = TextEditingController();
  final _password = TextEditingController();

  bool _pairMode = false;        // 配对码免密登录
  bool _rcMode = false;          // 两步验证第二步：恢复码模式（认证器不可用时）
  bool _bioAvailable = false;
  SavedCredential? _saved;

  _Probe _probe = _Probe.idle;
  bool _obscured = true;
  String _lastCode = ''; // 六格验证码/配对码（填满时更新，按钮兜底提交用）
  final _codeKey = GlobalKey<_CodeFieldState>();
  final _rcCtrl = TextEditingController(); // 恢复码文本框
  List<TotpEntry> _verifierEntries = const []; // 本机验证器（needTotp 一键回填）

  late final AnimationController _shake = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 420));

  @override
  void initState() {
    super.initState();
    () async {
      _server.text = await Prefs.lastAddress();
      _username.text = await Prefs.lastLoginUser() ?? '';
      _saved = await SavedCredentialStore.load();
      _bioAvailable = await Biometric.available();
      _verifierEntries = await VerifierStore.load();
      if (mounted) {
        setState(() {});
        _runProbe(); // 初始地址也做一次连通性预检
      }
    }();
  }

  @override
  void dispose() {
    _server.dispose();
    _username.dispose();
    _password.dispose();
    _rcCtrl.dispose();
    _shake.dispose();
    super.dispose();
  }

  /// 本机验证器中与当前登录用户名匹配的条目（needTotp 一键回填）
  TotpEntry? get _verifierMatch {
    final u = _username.text.trim();
    for (final e in _verifierEntries) {
      if (e.label == u) return e;
    }
    return null;
  }

  /// 从 URL 提取短显示名：共用实现见 widgets/server_picker.dart
  static String _shortHost(String raw) => shortHost(raw);

  /// 连接预检：任何 HTTP 状态码（含 401/404）都证明服务器在线，
  /// 只有连接超时/失败才算不可达——无需服务端专门健康检查端点。
  /// 自签证书放行（badCertificateCallback）：预检只回答"在不在线"，
  /// 证书信不信任由正式登录的 TOFU 信任流（TrustStore）裁决。
  /// 独立 Dio 实例：不带本机凭据、不影响 ApiClient 的服务器切换状态。
  Future<void> _runProbe() async {
    final addr = _server.text.trim();
    if (!addr.startsWith('https://')) {
      // 只认 https（红队 P2-1）：http 地址在提交闸与探针两处都会被拒
      if (mounted) setState(() => _probe = _Probe.idle);
      return;
    }
    if (mounted) setState(() => _probe = _Probe.probing);
    bool ok;
    final dio = Dio(BaseOptions(
        receiveTimeout: const Duration(milliseconds: 2500),
        validateStatus: (_) => true))
      ..httpClientAdapter = IOHttpClientAdapter(createHttpClient: () {
        final hc = HttpClient()
          ..connectionTimeout = const Duration(milliseconds: 2500)
          ..badCertificateCallback = ((cert, host, port) => true);
        return hc;
      });
    try {
      await dio.get('$addr/api/auth/me');
      ok = true;
    } catch (_) {
      ok = false;
    } finally {
      dio.close(force: true); // 释放 HttpClient/Socket 池，频繁改地址不积压
    }
    if (!mounted || _server.text.trim() != addr) return; // 地址已被改，丢弃过期结果
    if (!ok) {
      // 公网兜底（v0.15）：局域网地址连不上时试缓存的公网入口——
      // "出门冷启动 App"是这个功能最典型场景，不能让用户自己想起来换地址
      final fallback = await AutoSwitch.cachedPublicFallback(exclude: addr);
      if (!mounted || _server.text.trim() != addr) return;
      if (fallback != null) {
        _server.text = fallback; // 只改输入框不落盘：用户仍可改回自己的地址
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(
              content: Text('局域网不可达，已自动换用公网入口 '
                  '${shortHost(fallback)}'),
              duration: const Duration(seconds: 3)));
        }
        return _runProbe();
      }
    }
    setState(() => _probe = ok ? _Probe.ok : _Probe.fail);
  }

  Future<void> _pickServer() async {
    await showServerPicker(context, current: _server.text.trim(),
        onPick: _applyAddress);
  }

  Future<void> _applyAddress(String url) async {
    _server.text = url;
    await Prefs.rememberAddress(url);
    if (!mounted) return;
    setState(() {});
    // 信任闸（红队 P1-1）：选择器里的"公网入口"可能被链路内攻击者注入。
    // 切换前对真实证书做同源裁决：已信任直通 / 同源指纹静默信任 /
    // 陌生指纹弹 TOFU。探测失败（不可达）不阻断——后续 _runProbe 与登录
    // 提交路径仍会如实呈现连接结果
    await ensureAddressTrusted(context, url);
    _runProbe();
  }

  Widget _probeDot(BuildContext context) {
    switch (_probe) {
      case _Probe.probing:
        return const SizedBox(
            width: 14, height: 14,
            child: CircularProgressIndicator(strokeWidth: 2));
      case _Probe.ok:
        return Container(width: 10, height: 10,
            decoration: const BoxDecoration(
                color: Color(0xFF34A853), shape: BoxShape.circle));
      case _Probe.fail:
        return Container(width: 10, height: 10,
            decoration: const BoxDecoration(
                color: Color(0xFFEA4335), shape: BoxShape.circle));
      case _Probe.idle:
        return Container(width: 10, height: 10,
            decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(
                    color: Theme.of(context).colorScheme.outline, width: 1.5)));
    }
  }

  String _probeLabel() {
    switch (_probe) {
      case _Probe.probing:
        return '正在检测连接…';
      case _Probe.ok:
        return '已连接';
      case _Probe.fail:
        return '无法连接（检查地址/网络）';
      case _Probe.idle:
        return '未检测';
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider);
    final cs = Theme.of(context).colorScheme;
    ref.listen(sessionProvider, (prev, next) async {
      final messenger = ScaffoldMessenger.of(context);
      // 登录成功：记住用户名 + 存凭据档（配对码登录没有密码，不存）
      if (next.loggedIn && (prev?.loggedIn != true)) {
        final user = _username.text.trim();
        if (user.isNotEmpty) Prefs.setLastLoginUser(user);
        if (!_pairMode && _password.text.isNotEmpty) {
          SavedCredentialStore.save(SavedCredential(
              server: _server.text.trim(),
              username: _username.text.trim(),
              password: _password.text));
        }
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('欢迎，${next.username ?? ''}')));
      }
      // 登录失败（新错误）：抖动卡片提示；验证码/配对码输错自动清空重输
      if (next.error != null && next.error != '__totp_required__' &&
          prev?.error != next.error) {
        _shake.forward(from: 0);
        if (next.needTotp || _pairMode) {
          _lastCode = '';
          _rcCtrl.clear();
          _codeKey.currentState?.clear();
        }
      }
      // 自签证书首次连接：先看新入口出示的指纹是否与本机任一已信任地址
      // 同源（主/备隧道共用同一副证书密钥）——同源则静默信任免弹框，
      // 陌生指纹才弹确认框（2026-09-03 备用隧道）
      if (next.trustIssue != null &&
          (prev?.trustIssue == null ||
              prev!.trustIssue?.fingerprint != next.trustIssue!.fingerprint)) {
        final issue = next.trustIssue!;
        if (TrustStore.I.knowsFingerprint(issue.fingerprint)) {
          await TrustStore.I.trust(issue.host, issue.port, issue.fingerprint);
          messenger.showSnackBar(const SnackBar(
              content: Text('指纹与已信任服务器一致，已自动信任此入口'),
              duration: Duration(seconds: 2)));
          _submit();
        } else {
          await _askTrust(issue);
        }
      }
    });

    // 0.8.12：状态栏白图标（顶部是渐变品牌头）；去掉 SafeArea 让渐变
    // 延伸进状态栏（品牌头 topInset 让位，内容不与状态栏重叠）
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light,
      child: Scaffold(
      // SingleChildScrollView 的子项高度无界：不可用 Spacer/minHeight 布局技巧
      // （unbounded 主轴上 flex child 直接布局异常→整页白屏）。
      body: SingleChildScrollView(
          child: Column(children: [
            _brandHeader(context, cs),
            const SizedBox(height: 28),
            AnimatedBuilder(
                animation: _shake,
                builder: (context, child) => Transform.translate(
                    offset: Offset(
                        math.sin(_shake.value * 3 * math.pi) *
                            10 *
                            (1 - _shake.value),
                        0),
                    child: child),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 20),
                  child: _card(context, session),
                )),
            const SizedBox(height: 24),
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text('登录后本机保存凭据用于指纹快速登录，退出登录时自动清除',
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(fontSize: 10.5)),
            ),
          ]),
        ),
      ),
    );
  }

  /// 上半屏品牌区：渐变背景 + 云朵 Logo + 产品名 + slogan（0.8.4 统一到 Brand 组件）
  Widget _brandHeader(BuildContext context, ColorScheme cs) {
    return BrandHeader(
      topInset: MediaQuery.of(context).padding.top,
      bottomRadius: Brand.headerRadius,
      child: Column(children: [
        const SizedBox(height: 16),
        const BrandCloud(size: 72),
        const SizedBox(height: 16),
        const Text('LAN Share',
            style: TextStyle(
                color: Colors.white, fontSize: 26,
                fontWeight: FontWeight.bold)),
        const SizedBox(height: 4),
        Text('家庭照片与文件，始终在手边',
            style: TextStyle(color: Colors.white.withValues(alpha: 0.85), fontSize: 13)),
      ]),
    );
  }

  /// 浮层卡片：服务器条 + 登录方式切换 + 表单/按钮/错误
  Widget _card(BuildContext context, SessionState session) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 20),
      decoration: BoxDecoration(
          color: cs.surface,
          borderRadius: BorderRadius.circular(24),
          boxShadow: [
            BoxShadow(
                color: Colors.black.withValues(alpha: 0.08),
                blurRadius: 24, offset: const Offset(0, 8)),
          ]),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        // 服务器条
        InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: _pickServer,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 10),
            child: Row(children: [
              _probeDot(context),
              const SizedBox(width: 10),
              Expanded(
                  child: Text(_shortHost(_server.text.trim()),
                      maxLines: 1, overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontSize: 14, fontWeight: FontWeight.w600))),
              Text(_probeLabel(),
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(fontSize: 11)),
              const SizedBox(width: 6),
              Icon(Icons.expand_more,
                  size: 18, color: cs.onSurfaceVariant),
            ]),
          ),
        ),
        const Divider(height: 1),
        const SizedBox(height: 16),
        // 生物识别一键登录（有存档且设备支持时显示在最顺手的顶部）
        if (_saved != null && _bioAvailable && !_pairMode) ...[
          SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              icon: const Icon(Icons.fingerprint, size: 22),
              label: Text('使用已保存的 ${_saved!.username} 登录',
                  overflow: TextOverflow.ellipsis),
              onPressed: session.checking ? null : _bioLogin,
            ),
          ),
          const SizedBox(height: 4),
          // 开两步验证的账号，生物识别只免密码，动态码仍要输（语义如实）
          Text('开启两步验证的账号仍需输入动态码',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 6),
          Row(children: [
            const Expanded(child: Divider()),
            Padding(
                padding: const EdgeInsets.symmetric(horizontal: 10),
                child: Text('或',
                    style: Theme.of(context).textTheme.bodySmall)),
            const Expanded(child: Divider()),
          ]),
          const SizedBox(height: 6),
        ],
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 240),
          child: _pairMode
              ? _pairBody(context, session)
              : _passwordBody(context, session),
        ),
        const SizedBox(height: 24),
        FilledButton(
          style: FilledButton.styleFrom(
              minimumSize: const Size.fromHeight(48)),
          onPressed: session.checking ? null : _submit,
          child: Text(session.checking
              ? '登录中…'
              : _pairMode
                  ? '配对登录'
                  : session.needTotp ? '验证并登录' : '登录'),
        ),
        if (session.error != null && session.error != '__totp_required__') ...[
          const SizedBox(height: 10),
          Text(session.error!,
              textAlign: TextAlign.center,
              style: TextStyle(color: cs.error, fontSize: 12.5)),
        ],
        const SizedBox(height: 10),
        TextButton(
          onPressed: session.checking
              ? null
              : () {
                  setState(() {
                    _pairMode = !_pairMode;
                    if (!_pairMode && session.needTotp) {
                      // 从配对码切回密码模式：重新走密码登录拿新的 TOTP 挑战
                      ref.read(sessionProvider.notifier).resetTotp();
                    }
                  });
                },
          child: Text(_pairMode ? '使用账号密码登录' : '无法输密码？使用配对码登录',
              style: const TextStyle(fontSize: 12.5)),
        ),
        // v0.15.2：与网页版登录页同源的邀请码建号入口。传入当前地址栏
        // 内容作提交底座（审查 P1：未登录时 ApiClient.dio() 会拿 prefs
        // 里的默认/旧地址懒建缓存，code+password 不能发错服务器）
        TextButton(
          onPressed: session.checking
              ? null
              : () => Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) =>
                          InviteRedeemPage(baseUrl: _server.text.trim()))),
          child: const Text('有邀请码？使用邀请码加入',
              style: TextStyle(fontSize: 12.5)),
        ),
      ]),
    );
  }

  Widget _passwordBody(BuildContext context, SessionState session) {
    return Column(key: const ValueKey('pw'), children: [
      if (!session.needTotp) ...[
        TextField(
          controller: _username,
          decoration: const InputDecoration(
              labelText: '用户名', prefixIcon: Icon(Icons.person_outline)),
          autocorrect: false,
          textInputAction: TextInputAction.next,
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _password,
          decoration: InputDecoration(
              labelText: '密码',
              prefixIcon: const Icon(Icons.lock_outline),
              suffixIcon: IconButton(
                  icon: Icon(_obscured
                      ? Icons.visibility_off_outlined
                      : Icons.visibility_outlined),
                  onPressed: () => setState(() => _obscured = !_obscured))),
          obscureText: _obscured,
          onSubmitted: (_) => _submit(),
        ),
      ] else ...[
        Text(_rcMode ? '输入开通时保存的恢复码' : '输入认证器动态码',
            style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 4),
        Text(_username.text.trim(),
            style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 12),
        // 本机验证器已绑定该账号：直接显示当前动态码，点按自动填入（v0.5.2）
        if (!_rcMode && _verifierMatch != null) ...[
          _VerifierFillCard(
              entry: _verifierMatch!,
              onFill: (code) {
                _lastCode = code;
                _codeKey.currentState?.fill(code); // 填满触发自动提交
              }),
          const SizedBox(height: 10),
        ],
        if (_rcMode)
          TextField(
              controller: _rcCtrl,
              decoration: const InputDecoration(
                  labelText: '恢复码（如 a1b2-c3d4）',
                  prefixIcon: Icon(Icons.vpn_key_outlined)),
              autocorrect: false,
              enableSuggestions: false,
              textInputAction: TextInputAction.done,
              onSubmitted: (_) => _submit())
        else
          _CodeField(key: _codeKey, onCompleted: _onCodeDone),
        const SizedBox(height: 4),
        TextButton(
          onPressed: session.checking
              ? null
              : () => setState(() {
                    _rcMode = !_rcMode;
                    _lastCode = '';
                    _rcCtrl.clear();
                    _codeKey.currentState?.clear();
                  }),
          child: Text(_rcMode ? '返回输入动态码' : '认证器不可用？使用恢复码登录',
              style: const TextStyle(fontSize: 12.5)),
        ),
      ],
    ]);
  }

  Widget _pairBody(BuildContext context, SessionState session) {
    return Column(key: const ValueKey('pair'), children: [
      Text('输入电脑端生成的 6 位配对码',
          style: Theme.of(context).textTheme.titleSmall),
      const SizedBox(height: 4),
      Text('3 分钟内有效 · 在「账号设置 → 手机 App 连接」生成',
          style: Theme.of(context).textTheme.bodySmall),
      const SizedBox(height: 12),
      _CodeField(key: _codeKey, onCompleted: _onCodeDone),
    ]);
  }

  void _onCodeDone(String code) {
    _lastCode = code;
    _submit(); // 填满即自动提交
  }

  Future<void> _bioLogin() async {
    final saved = _saved;
    if (saved == null) return;
    final ok = await Biometric.authenticate('验证身份以登录 LAN Share');
    if (!ok || !mounted) return;
    await _applyAddress(saved.server);
    _username.text = saved.username;
    _password.text = saved.password;
    ref.read(sessionProvider.notifier).login(
        server: saved.server,
        username: saved.username,
        password: saved.password);
  }

  void _submit() {
    // 明文 http 拒绝（红队 P2-1）：本部署无合法 http 入口（LAN= Caddy https），
    // 恶意二维码/诱导手输 http 地址会让账密全明文上送且无任何 TLS 层告警
    if (!_server.text.trim().startsWith('https://')) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('地址必须以 https:// 开头（明文 http 会泄露密码）')));
      return;
    }
    final s = ref.read(sessionProvider);
    if (_pairMode) {
      if (_lastCode.length == 6) {
        ref.read(sessionProvider.notifier)
            .loginByPair(_lastCode, server: _server.text.trim());
      }
      return;
    }
    if (s.needTotp) {
      if (_rcMode) {
        final rc = _rcCtrl.text.trim();
        if (rc.isNotEmpty) {
          ref.read(sessionProvider.notifier)
              .loginTotp(rc, server: _server.text.trim());
        }
      } else if (_lastCode.length == 6) {
        ref.read(sessionProvider.notifier)
            .loginTotp(_lastCode, server: _server.text.trim());
      }
    } else {
      ref.read(sessionProvider.notifier).login(
          server: _server.text.trim(),
          username: _username.text.trim(),
          password: _password.text);
    }
  }

  /// 证书信任确认：展示指纹，用户显式确认后重试登录
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

/// 6 位分离式验证码（TOTP 动态码 / 配对码共用）：自动跳格、退格回移、
/// 首格粘贴自动分发；填满即回调（自动提交，无需再按按钮）。
class _CodeField extends StatefulWidget {
  final ValueChanged<String> onCompleted;
  const _CodeField({super.key, required this.onCompleted});

  @override
  State<_CodeField> createState() => _CodeFieldState();
}

class _CodeFieldState extends State<_CodeField> {
  static const _len = 6;
  final _nodes = List.generate(_len, (_) => FocusNode());
  final _values = List.filled(_len, '');
  late final _controllers =
      List.generate(_len, (_) => TextEditingController());

  @override
  void dispose() {
    for (final n in _nodes) {
      n.dispose();
    }
    for (final c in _controllers) {
      c.dispose();
    }
    super.dispose();
  }

  /// 输错清空重输（登录失败时由外层调用）
  void clear() {
    for (var i = 0; i < _len; i++) {
      _values[i] = '';
      _controllers[i].clear();
    }
    setState(() {});
    _nodes[0].requestFocus();
  }

  /// 自动填入（本机验证器一键回填 v0.5.2）：分发进各格并触发 onCompleted
  void fill(String code) {
    final digits = code.replaceAll(RegExp(r'\D'), '');
    if (digits.length < _len) return;
    for (var i = 0; i < _len; i++) {
      _values[i] = digits[i];
      _controllers[i].text = digits[i];
    }
    setState(() {});
    _nodes[_len - 1].requestFocus();
    _emit();
  }

  void _emit() {
    if (_values.every((v) => v.isNotEmpty)) {
      widget.onCompleted(_values.join());
    }
  }

  void _onChanged(int i, String v) {
    v = v.replaceAll(RegExp(r'\D'), ''); // 只收数字
    if (v.length > 1) {
      // 粘贴/快速输入：从当前格开始依次分发
      for (var k = 0; k < v.length && i + k < _len; k++) {
        _values[i + k] = v[k];
      }
      setState(() {});
      final next = math.min(i + v.length, _len - 1);
      _nodes[next].requestFocus();
      _emit();
      return;
    }
    _values[i] = v;
    setState(() {});
    if (v.isNotEmpty && i < _len - 1) _nodes[i + 1].requestFocus();
    _emit();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: List.generate(_len, (i) {
          return SizedBox(
            width: 46,
            child: TextField(
              controller: _controllers[i],
              focusNode: _nodes[i],
              textAlign: TextAlign.center,
              keyboardType: TextInputType.number,
              maxLength: 1,
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
              decoration: InputDecoration(
                counterText: '',
                contentPadding:
                    const EdgeInsets.symmetric(vertical: 12),
                filled: _values[i].isNotEmpty,
                fillColor: cs.primary.withValues(alpha: 0.06),
                enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide(color: cs.outlineVariant)),
                focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide(color: cs.primary, width: 1.6)),
              ),
              onChanged: (v) => _onChanged(i, v),
            ),
          );
        }));
  }
}

/// 本机验证器动态码卡片（needTotp 时显示）：自走秒针刷新码与倒计时圈，
/// 点按整卡即把当前码填进六格并自动提交。
class _VerifierFillCard extends StatefulWidget {
  const _VerifierFillCard({required this.entry, required this.onFill});

  final TotpEntry entry;
  final ValueChanged<String> onFill;

  @override
  State<_VerifierFillCard> createState() => _VerifierFillCardState();
}

class _VerifierFillCardState extends State<_VerifierFillCard> {
  late Timer _ticker;
  int _now = DateTime.now().millisecondsSinceEpoch ~/ 1000;

  @override
  void initState() {
    super.initState();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) {
        setState(() => _now = DateTime.now().millisecondsSinceEpoch ~/ 1000);
      }
    });
  }

  @override
  void dispose() {
    _ticker.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final e = widget.entry;
    final remain = e.period - (_now % e.period);
    final code = e.code(epoch: _now);
    final shown =
        '${code.substring(0, e.digits ~/ 2)} ${code.substring(e.digits ~/ 2)}';
    final cs = Theme.of(context).colorScheme;
    return Card(
      margin: EdgeInsets.zero,
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: () => widget.onFill(code),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          child: Row(children: [
            SizedBox(
              width: 38,
              height: 38,
              child: Stack(alignment: Alignment.center, children: [
                CircularProgressIndicator(
                  value: remain / e.period,
                  strokeWidth: 3,
                  color: remain <= 5 ? Colors.redAccent : null,
                ),
                Text('$remain', style: const TextStyle(fontSize: 12)),
              ]),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text('本机验证器',
                    style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                Text(shown,
                    style: const TextStyle(
                        fontSize: 26,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 5)),
              ]),
            ),
            Icon(Icons.touch_app_outlined, size: 20, color: cs.onSurfaceVariant),
          ]),
        ),
      ),
    );
  }
}
