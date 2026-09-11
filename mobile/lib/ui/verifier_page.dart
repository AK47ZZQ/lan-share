// App 内置验证器（v0.5.2）：绑定 LAN Share 两步验证后本机直接显示动态码，
// 家庭成员不必再装 Microsoft/Google 验证器。入口在「我的」。
// 绑定 = 扫电脑端开通两步验证时展示的 otpauth 二维码（或手输密钥兜底）。
import 'dart:async';

import 'package:flutter/material.dart';

import '../data/local/verifier_store.dart';
import '../data/totp.dart';
import 'theme/design_tokens.dart';
import 'scan_page.dart';

class VerifierPage extends StatefulWidget {
  const VerifierPage({super.key});

  @override
  State<VerifierPage> createState() => _VerifierPageState();
}

class _VerifierPageState extends State<VerifierPage> {
  List<TotpEntry> _entries = [];
  bool _loading = true;
  Timer? _ticker;
  int _now = DateTime.now().millisecondsSinceEpoch ~/ 1000;

  @override
  void initState() {
    super.initState();
    _reload();
    // 每秒走针：码的剩余窗口倒计时驱动圆环刷新
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      setState(() => _now = DateTime.now().millisecondsSinceEpoch ~/ 1000);
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  Future<void> _reload() async {
    final list = await VerifierStore.load();
    if (!mounted) return;
    setState(() {
      _entries = list;
      _loading = false;
    });
  }

  Future<void> _scanBind() async {
    final raw = await Navigator.push<String>(
        context, MaterialPageRoute(builder: (_) => const ScanPage(mode: ScanMode.otpauth)));
    if (raw == null) return;
    final entry = parseOtpauthUri(raw);
    if (entry == null) {
      _snack('这不是两步验证二维码', err: true);
      return;
    }
    try {
      await VerifierStore.upsert(entry);
    } on StateError {
      _snack('安全存储不可用，绑定失败', err: true);
      return;
    }
    await _reload();
    _snack('已绑定 ${entry.label.isEmpty ? '账号' : entry.label}');
  }

  Future<void> _manualBind() async {
    final secret = await showDialog<String>(
      context: context,
      builder: (ctx) {
        final ctrl = TextEditingController();
        return AlertDialog(
          title: const Text('手动输入密钥'),
          content: TextField(
            controller: ctrl,
            autofocus: true,
            decoration: const InputDecoration(
                hintText: 'Base32 密钥，如 JBSWY3DPEHPK3PXP',
                labelText: '密钥'),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
            FilledButton(onPressed: () => Navigator.pop(ctx, ctrl.text.trim()), child: const Text('绑定')),
          ],
        );
      },
    );
    if (secret == null || secret.isEmpty) return;
    try {
      // 试算一次验证密钥合法（非法 Base32 此处抛异常）
      TotpEntry(secret: secret.toUpperCase()).code();
      await VerifierStore.upsert(
          TotpEntry(secret: secret.toUpperCase(), label: '手动绑定', issuer: 'LAN Share'));
      await _reload();
      if (mounted) _snack('已绑定');
    } on FormatException {
      _snack('密钥格式不对（应为 Base32）', err: true);
    } on StateError {
      _snack('安全存储不可用，绑定失败', err: true);
    }
  }

  Future<void> _remove(TotpEntry e) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('解除绑定'),
        content: Text('删除「${e.label.isEmpty ? '手动绑定' : e.label}」的本机验证器？'
            '\n不影响服务器上的两步验证状态，可随时重新扫码绑定。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(
              style: FilledButton.styleFrom(
                  backgroundColor: Theme.of(context).colorScheme.error),
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('删除')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await VerifierStore.remove(e.label);
    } on StateError {
      _snack('安全存储不可用，删除失败', err: true);
      return;
    }
    await _reload();
  }

  void _snack(String msg, {bool err = false}) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(msg),
        backgroundColor: err ? Theme.of(context).colorScheme.error : null));
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('验证器')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _entries.isEmpty
              ? Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(Icons.phonelink_lock_outlined,
                          size: 64, color: cs.onSurfaceVariant),
                      const SizedBox(height: Gap.sm),
                      Text('还没绑定两步验证',
                          style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: Gap.xxs),
                      const Padding(
                        padding: EdgeInsets.symmetric(horizontal: Gap.xxl),
                        child: Text(
                            '在电脑端「账号设置 → 两步验证 → 启用」出示二维码，\n用下面的按钮扫码即可绑定，之后登录无需装其他验证器',
                            textAlign: TextAlign.center,
                            style: TextStyle(fontSize: 13, color: Colors.grey)),
                      ),
                    ],
                  ),
                )
              : ListView(padding: const EdgeInsets.all(Gap.sm), children: [
                  for (final e in _entries)
                    _codeCard(context, e),
                  const Padding(
                    padding: EdgeInsets.all(Gap.xs),
                    child: Text(
                        '动态码 30 秒更换一次。登录第二步把这里显示的数字填入即可；'
                        '本机 App 登录时会自动帮你填。',
                        style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                ]),
      floatingActionButton: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // 手输兜底常驻：相机故障/二维码截屏后扫不了时仍可绑定
          FloatingActionButton.small(
            heroTag: 'manual',
            onPressed: _manualBind,
            child: const Icon(Icons.keyboard_alt_outlined),
          ),
          const SizedBox(height: 10),
          FloatingActionButton(
            heroTag: 'scan',
            onPressed: _scanBind,
            child: const Icon(Icons.qr_code_scanner),
          ),
        ],
      ),
    );
  }

  Widget _codeCard(BuildContext context, TotpEntry e) {
    final remain = e.period - (_now % e.period);
    final code = e.code(epoch: _now);
    final shown =
        '${code.substring(0, e.digits ~/ 2)} ${code.substring(e.digits ~/ 2)}';
    final progress = remain / e.period;
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: Gap.md, vertical: Gap.sm),
        child: Row(children: [
          SizedBox(
            width: 44,
            height: 44,
            child: Stack(alignment: Alignment.center, children: [
              CircularProgressIndicator(
                value: progress,
                strokeWidth: 3,
                color: remain <= 5 ? Colors.redAccent : null,
              ),
              Text('$remain', style: const TextStyle(fontSize: 13)),
            ]),
          ),
          const SizedBox(width: Gap.md),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(e.label.isEmpty ? '手动绑定' : e.label,
                  style: Theme.of(context).textTheme.titleSmall),
              Text(e.issuer, style: const TextStyle(fontSize: 11, color: Colors.grey)),
              const SizedBox(height: 2),
              Text(shown,
                  style: const TextStyle(
                      fontSize: 30,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 6,
                      fontFeatures: [FontFeature.tabularFigures()])),
            ]),
          ),
          IconButton(
              icon: const Icon(Icons.delete_outline),
              tooltip: '解除绑定',
              onPressed: () => _remove(e)),
        ]),
      ),
    );
  }
}
