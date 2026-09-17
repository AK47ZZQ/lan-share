// 我的：登录身份 + 设备管理 + 设置区（0.6.0：主题三档/修改密码/通知/应用锁/
// 缓存管理/关于与检查更新）+ 服务器地址 + 退出。
import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';

import 'about_page.dart';
import 'feedback_page.dart';
import 'files_page.dart';
import 'transfers_page.dart';
import 'admin_page.dart';
import 'backup_settings_page.dart';
import 'package:file_picker/file_picker.dart';

import '../data/api/account_api.dart';
import '../data/api/api_client.dart';
import '../data/api/auth_cache.dart';
import '../data/api/errors.dart';
import '../data/api/trust_flow.dart';
import '../data/auto_switch.dart';
import '../data/local/prefs.dart';
import '../data/net.dart';
import '../state/session.dart';
import '../state/settings.dart';
import 'theme/app_theme.dart';
import 'theme/design_tokens.dart';
import 'theme/responsive.dart';
import '../transfer/transfer_task.dart' show humanSize;
import 'shares_page.dart';
import 'trash_page.dart';
import 'verifier_page.dart';
import 'widgets/auth_image.dart';
import 'widgets/server_picker.dart' show shortHost, showServerPicker;
import 'widgets/kit.dart';

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
  String _cacheSize = '';
  String _appCacheSize = '';
  String _version = '';
  bool? _webdavSet; // WebDAV 独立密码是否已设置（0.8.0）

  @override
  void initState() {
    super.initState();
    _load();
    // 自动切换会改地址（红队 P2-4）：当前入口卡不跟随就会显示陈旧入口
    Prefs.addressNotifier.addListener(_onAddressChanged);
  }

  void _onAddressChanged() {
    final b = Prefs.addressNotifier.value;
    if (mounted && b.isNotEmpty && b != _server) {
      setState(() => _server = b);
    }
  }

  @override
  void dispose() {
    Prefs.addressNotifier.removeListener(_onAddressChanged);
    super.dispose();
  }

  Future<void> _load() async {
    _server = await Prefs.lastAddress();
    if (mounted) setState(() {});
    try {
      final webdavSet = await AccountApi.webdavPwdStatus();
      if (mounted) setState(() => _webdavSet = webdavSet);
    } catch (_) {/* 端点不可用保持未知 */}
    try {
      final pi = await PackageInfo.fromPlatform();
      if (mounted) setState(() => _version = pi.version);
    } catch (_) {}
    try {
      final list = (await AccountApi.listDevices()).map((e) {
        final m = e as Map;
        return _Device(
            id: (m['id'] as num?)?.toInt() ?? 0,
            name: m['name'] as String? ?? '未知设备',
            createdAt: (m['created_at'] as num?)?.toDouble() ?? 0,
            lastSeen: m['last_seen'] as num?,
            current: m['current'] as bool? ?? false);
      }).toList();
      if (!mounted) return;
      setState(() {
        _devices = list;
        _loadingDevices = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingDevices = false);
    }
    await _loadCacheSize();
    // 独立调用而非串在 _loadCacheSize 末尾：缩略图目录不存在的早退路径
    // 会把它吞掉（新装/清完缓存后"统计中…"永不消失，二轮审查 P1）
    _loadAppCacheSize();
  }

  /// 缩略图缓存占用（flutter_cache_manager 自定义 key 落在临时目录同名子目录）
  Future<void> _loadCacheSize() async {
    try {
      final temp = await getTemporaryDirectory();
      final dir = Directory('${temp.path}/authCachedImageData');
      if (!dir.existsSync()) {
        if (mounted) setState(() => _cacheSize = '0 B');
        return;
      }
      var total = 0;
      await for (final f in dir.list(recursive: true, followLinks: false)) {
        if (f is File) {
          try {
            total += f.lengthSync();
          } catch (_) {}
        }
      }
      if (mounted) setState(() => _cacheSize = humanSize(total));
    } catch (_) {
      if (mounted) setState(() => _cacheSize = '');
    }
  }

  /// 应用缓存目录（v0.16.0）：临时目录 + 系统 cache 目录（Android 上两者
  /// 常是同一路径，按路径去重）。下载文件（documents/Downloads）、账本
  /// （databases 目录）、队列 JSON 都不在 temp/cache，不受影响。
  Future<List<Directory>> _appCacheDirs() async {
    final found = <String, Directory>{};
    for (final get in [getTemporaryDirectory, getApplicationCacheDirectory]) {
      try {
        final d = await get();
        found[d.path] = d;
      } catch (_) {}
    }
    return found.values.toList();
  }

  Future<void> _loadAppCacheSize() async {
    try {
      var total = 0;
      for (final d in await _appCacheDirs()) {
        if (!d.existsSync()) continue;
        await for (final e in d.list(recursive: true, followLinks: false)) {
          if (e is File) {
            try {
              total += e.lengthSync();
            } catch (_) {}
          }
        }
      }
      if (mounted) setState(() => _appCacheSize = humanSize(total));
    } catch (_) {
      if (mounted) setState(() => _appCacheSize = '');
    }
  }

  /// 清理应用缓存（v0.16.0，位于"清理缩略图缓存"下方）：清空临时/cache
  /// 目录全部内容。缩略图缓存本就落在临时目录里，这里一并带走；清完刷新
  /// 两项统计。
  Future<void> _clearAppCache() async {
    final size = _appCacheSize;
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('清理应用缓存？'),
                content: Text(
                    '当前占用 ${size.isEmpty ? "统计中" : size}。将清空临时文件、'
                    '网络与图片缓存（含缩略图缓存），不影响下载的文件和账号数据。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('清理')),
                ]));
    if (ok != true) return;
    for (final d in await _appCacheDirs()) {
      if (!d.existsSync()) continue;
      try {
        await for (final e in d.list(followLinks: false)) {
          try {
            await e.delete(recursive: true);
          } catch (_) {}
        }
      } catch (_) {
        // 目录流中途被并发删改：吞掉，能清多少清多少
      }
    }
    AuthCacheManager.I.clearFailureCache();
    // 两个统计都刷：清理应用缓存把缩略图缓存一并带走（同在临时目录），
    // 只刷缩略图一项会让本项显示清理前的陈旧占用（E2E 实测踩中）
    await _loadCacheSize();
    _loadAppCacheSize();
    showToast('已清理');
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
    try {
      await AccountApi.revokeDevice(d.id);
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

  // ── 设置区动作 ───────────────────────────────────────────

  Future<void> _pickThemeMode() async {
    final cur = ref.read(settingsProvider).themeMode;
    final sel = await showDialog<ThemeMode>(
        context: context,
        builder: (ctx) => SimpleDialog(
                title: const Text('深色模式'),
                children: <Widget>[
                  for (final (m, label) in [
                    (ThemeMode.system, '跟随系统'),
                    (ThemeMode.light, '浅色'),
                    (ThemeMode.dark, '深色'),
                  ])
                    ListTile(
                        title: Text(label),
                        trailing: cur == m
                            ? const Icon(Icons.check)
                            : null,
                        onTap: () => Navigator.pop(ctx, m)),
                ]));
    if (sel != null) {
      await ref.read(settingsProvider.notifier).setThemeMode(sel);
    }
  }

  // 主题引擎（0.8.5）：6 套预置色板 + 跟随壁纸，点色板即换全 App 配色
  Future<void> _pickAccent() async {
    final cur = ref.read(settingsProvider).accent;
    final sel = await showDialog<String>(
        context: context,
        builder: (ctx) => SimpleDialog(
                title: const Text('主题色'),
                children: [
                  Padding(
                      padding: const EdgeInsets.fromLTRB(Gap.lg, Gap.xxs, Gap.lg, Gap.sm),
                      child: Wrap(
                          spacing: 10,
                          runSpacing: 14,
                          children: [
                            for (final p in Palettes.all)
                              _accentSwatch(ctx, cur, p.id, p.name,
                                  BoxDecoration(
                                      gradient: LinearGradient(
                                          begin: Alignment.topLeft,
                                          end: Alignment.bottomRight,
                                          colors: p.gradient))),
                            _accentSwatch(
                                ctx,
                                cur,
                                Palettes.auto,
                                '跟随壁纸',
                                BoxDecoration(
                                    color: Theme.of(ctx)
                                        .colorScheme
                                        .surfaceContainerHighest),
                                icon: Icons.wallpaper),
                          ])),
                ]));
    if (sel != null) {
      await ref.read(settingsProvider.notifier).setAccent(sel);
    }
  }

  Widget _accentSwatch(BuildContext ctx, String cur, String id, String label,
      BoxDecoration deco, {IconData? icon}) {
    final cs = Theme.of(ctx).colorScheme;
    final selected = cur == id;
    return InkWell(
        borderRadius: Radii.bControl,
        onTap: () => Navigator.pop(ctx, id),
        child: SizedBox(
            width: 78,
            child: Column(children: [
              Container(
                  width: 48,
                  height: 48,
                  decoration: deco.copyWith(
                      shape: BoxShape.circle,
                      border: Border.all(
                          color: selected ? cs.primary : cs.outlineVariant,
                          width: selected ? 3 : 1)),
                  child: icon == null
                      ? null
                      : Icon(icon, size: 22, color: cs.onSurfaceVariant)),
              const SizedBox(height: 5),
              Text(label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style:
                      TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
            ])));
  }

  Future<void> _changePassword() async {
    final oldCtrl = TextEditingController();
    final newCtrl = TextEditingController();
    final againCtrl = TextEditingController();
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('修改密码'),
                content: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min,
                    children: [
                  TextField(controller: oldCtrl,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '原密码')),
                  TextField(controller: newCtrl,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '新密码（至少 8 位）')),
                  TextField(controller: againCtrl,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '再输一遍新密码')),
                ])),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('修改')),
                ]));
    // 控制器不 dispose 交给 GC（backup_settings 同款方案）：await 返回时
    // 对话框退场动画未结束、TextField 仍挂在树上，此刻 dispose 必崩红屏
    //（v0.13 E2E 实测机制，见 prompt_dialog 头注释）
    if (ok != true || !mounted) return;
    if (newCtrl.text.length < 8) {
      showToast('新密码至少 8 位');
      return;
    }
    if (newCtrl.text != againCtrl.text) {
      showToast('两次输入的新密码不一致');
      return;
    }
    try {
      await AccountApi.changePassword(oldCtrl.text, newCtrl.text);
      showToast('密码已修改（网页端会话需重新登录）');
    } catch (e) {
      showToast('修改失败：${_detail(e)}');
    } finally {
      oldCtrl.dispose();
      newCtrl.dispose();
      againCtrl.dispose();
    }
  }

  // ── 账号与安全（0.8.0 对齐 Web AccountModal）─────────────────

  /// 改用户名：私人目录/incoming 同步改名；设备 Token 保持有效。
  /// 改名后备份账本按账号隔离自动清空重对账（秒传迁移，v0.7.0 链路）。
  Future<void> _changeUsername() async {
    final session = ref.read(sessionProvider);
    final nameCtrl = TextEditingController(text: session.username ?? '');
    final pwdCtrl = TextEditingController();
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('修改用户名'),
                content: SingleChildScrollView(child: Column(
                    mainAxisSize: MainAxisSize.min, children: [
                  TextField(controller: nameCtrl,
                      decoration: const InputDecoration(labelText: '新用户名')),
                  TextField(controller: pwdCtrl,
                      obscureText: true,
                      decoration:
                          const InputDecoration(labelText: '当前密码')),
                ])),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('修改')),
                ]));
    if (ok != true || !mounted) return;
    final newName = nameCtrl.text.trim();
    if (newName.isEmpty || newName == session.username) return;
    try {
      await AccountApi.changeUsername(newName, pwdCtrl.text);
      if (!mounted) return;
      await Prefs.setUsername(newName);
      if (!mounted) return;
      ref.read(sessionProvider.notifier).applyRename(newName);
      // 缩略图 key 含旧用户名路径：清掉避免旧目录名驻留本地（0.8.1 审查修复）
      unawaited(AuthCacheManager.I.emptyCache());
      showToast('用户名已改为 $newName（私人目录已同步改名）');
      setState(() {});
    } catch (e) {
      showToast('修改失败：${_detail(e)}');
    }
  }

  /// WebDAV 独立密码：设置/轮换/清除。独立于登录密码，客户端挂载用。
  Future<void> _webdavDialog() async {
    final curCtrl = TextEditingController();
    final newCtrl = TextEditingController();
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('WebDAV 独立密码'),
                content: SingleChildScrollView(child: Column(
                    mainAxisSize: MainAxisSize.min, children: [
                  Text(_webdavSet == true
                      ? '已启用：WebDAV 挂载只认独立密码，登录密码对 /dav/ 无效。'
                      : '未设置：WebDAV 挂载目前使用登录密码。',
                      style: const TextStyle(fontSize: 12)),
                  const SizedBox(height: Gap.xs),
                  TextField(controller: curCtrl,
                      obscureText: true,
                      decoration:
                          const InputDecoration(labelText: '当前登录密码')),
                  TextField(controller: newCtrl,
                      obscureText: true,
                      decoration: const InputDecoration(
                          labelText: 'WebDAV 新密码（至少 8 位，留空=清除）')),
                ])),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('保存')),
                ]));
    if (ok != true || !mounted) return;
    final newPwd = newCtrl.text;
    if (newPwd.isNotEmpty && newPwd.length < 8) {
      showToast('WebDAV 密码至少 8 位（留空=清除）');
      return;
    }
    try {
      final setNow = await AccountApi.setWebdavPwd(curCtrl.text, newPwd);
      if (!mounted) return;
      setState(() => _webdavSet = setNow);
      showToast(setNow
          ? 'WebDAV 独立密码已生效（挂载客户端需改用新密码）'
          : '已清除：WebDAV 回退用登录密码挂载');
    } catch (e) {
      showToast('保存失败：${_detail(e)}');
    }
  }

  /// 生成一次性配对码：给另一台设备免密登录本账号（登录页"配对码登录"）。
  Future<void> _showPairCode() async {
    String? code;
    int expires = 0;
    try {
      final pc = await AccountApi.pairCode();
      code = pc.code;
      expires = pc.expires;
    } catch (e) {
      showToast('生成失败：${_detail(e)}');
      return;
    }
    if (!mounted) return;
    if (code == null || code.isEmpty) {
      showToast('生成失败：服务端未返回配对码');
      return;
    }
    await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('配对码'),
                content: Column(mainAxisSize: MainAxisSize.min, children: [
                  FittedBox(
                      fit: BoxFit.scaleDown,
                      child: Text(code!,
                          style: const TextStyle(
                              fontSize: 36,
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.bold,
                              letterSpacing: 6))),
                  const SizedBox(height: Gap.xs),
                  Text('在另一台设备的登录页选择"配对码登录"，输入此码即可免密登录本账号。$expires 秒内有效，一码一用。',
                      style: const TextStyle(fontSize: 12)),
                ]),
                actions: [
                  TextButton(
                      onPressed: () =>
                          Clipboard.setData(ClipboardData(text: code!)),
                      child: const Text('复制')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx),
                      child: const Text('完成')),
                ]));
  }

  /// 重新生成两步验证恢复码：旧码全部作废，新码仅本次明文展示。
  Future<void> _regenRecoveryCodes() async {
    final pwdCtrl = TextEditingController();
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('重新生成恢复码'),
                content: TextField(controller: pwdCtrl,
                    obscureText: true,
                    autofocus: true,
                    decoration:
                        const InputDecoration(labelText: '当前密码')),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('生成')),
                ]));
    if (ok != true || !mounted) return;
    List codes;
    try {
      codes = await AccountApi.regenRecoveryCodes(pwdCtrl.text);
    } catch (e) {
      showToast('生成失败：${_detail(e)}');
      return;
    }
    if (!mounted) return;
    if (codes.isEmpty) {
      showToast('生成失败：服务端未返回恢复码');
      return;
    }
    await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('新的恢复码（仅显示一次）'),
                content: Column(mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Text('旧恢复码已全部作废。请把新码抄到安全的地方：',
                      style: TextStyle(fontSize: 12)),
                  const SizedBox(height: Gap.xs),
                  for (final c in codes)
                    Text(c as String,
                        style: const TextStyle(
                            fontFamily: 'monospace', fontSize: 15)),
                ]),
                actions: [
                  TextButton(
                      onPressed: () => Clipboard.setData(ClipboardData(
                          text: codes.join('\n'))),
                      child: const Text('复制全部')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx),
                      child: const Text('我已保存')),
                ]));
  }

  Future<void> _toggleAppLock(bool v) async {
    if (!v) {
      await ref.read(settingsProvider.notifier).setAppLock(false);
      return;
    }
    final la = LocalAuthentication();
    try {
      if (!await la.canCheckBiometrics) {
        showToast('本机未录入指纹/面部，无法开启应用锁');
        return;
      }
      // 开启验证本身记为"本进程已解锁"：开关验证弹窗关闭的 resumed 竞态
      // 不能把刚打开的锁立刻拉上（v0.6.0 锁死第 1 环）
      ref.read(sessionUnlockedProvider.notifier).state = true;
      ref.read(biometricBusyProvider.notifier).state = true;
      // 开启前先验证一次：防止他人借机开启
      final passed = await la.authenticate(
          localizedReason: '验证以开启应用锁',
          options: const AuthenticationOptions(
              biometricOnly: true, stickyAuth: true));
      if (!passed) return;
      await ref.read(settingsProvider.notifier).setAppLock(true);
    } catch (_) {
      showToast('生物识别不可用，无法开启应用锁');
      ref.read(sessionUnlockedProvider.notifier).state = false;
    } finally {
      ref.read(biometricBusyProvider.notifier).state = false;
    }
  }

  Future<void> _clearCache() async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('清理缩略图缓存？'),
                content: Text('当前占用 $_cacheSize。清理后浏览照片会重新下载缩略图。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('清理')),
                ]));
    if (ok != true) return;
    await AuthCacheManager.I.emptyCache();
    // 负缓存一并放行：否则已记失败的缩略图 10 分钟内仍是坏图图标，
    // 与"清理后重新下载缩略图"的承诺不符（review 0.12.0 P3）
    AuthCacheManager.I.clearFailureCache();
    // 缩略图也占着临时目录：应用缓存统计连带刷新（E2E 实测踩中陈旧值）
    await _loadCacheSize();
    _loadAppCacheSize();
    showToast('已清理');
  }

  static String _detail(Object e) => errDetail(e);

  // ── 服务器入口切换（2026-09-03 主/备隧道）────────────────
  // 设备 Token 与服务器绑定而非与地址绑定：主/备/局域网同一后端，
  // 切换地址后 Bearer 依然有效，业务数据随各自页面的轮询/刷新自愈。
  Future<void> _pickServer() async {
    final current = _server;
    if (current == null) return;
    await showServerPicker(context, current: current, onPick: (url) async {
      final ok = await ensureAddressTrusted(context, url,
          onAutoTrusted: () => showToast('指纹与已信任服务器一致，已自动信任此入口'));
      if (!ok || !mounted) return;
      await ApiClient.I.switchServer(url);
      await Prefs.rememberAddress(url);
      AutoSwitch.I.noteManualPick(); // 手动选择后 10 分钟内自动切换不干预
      if (!mounted) return;
      setState(() => _server = url);
      showToast('已切换到 ${shortHost(url)}');
    });
  }

  /// 公网入口徽标
  Widget _netBadge(BuildContext context) => Container(
      padding: const EdgeInsets.symmetric(horizontal: Gap.xs, vertical: 3),
      decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.tertiaryContainer,
          borderRadius: BorderRadius.circular(Radii.pill)),
      child: Text('公网',
          style: TextStyle(
              fontSize: 11,
              color: Theme.of(context).colorScheme.onTertiaryContainer)));

  int _avatarBust = 0; // 上传/替换头像后 +1，绕过 AuthCacheManager 磁盘缓存

  Widget _avatarWidget(SessionState session, {double radius = 20}) {
    final path = session.avatarPath ?? '';
    // 头像统一走 /api/auth/avatar/<username>（v0.17.2）：DB 里的
    // .avatars/<uid>.jpg 是服务端内部路径，不可路由——直接拼它 404，
    // 头像永远显示不出来。auth 端点按 username 查库取最新头像
    final name = session.username ?? '';
    if (path.isNotEmpty && _server != null && name.isNotEmpty) {
      return CircleAvatar(
          radius: radius,
          child: ClipOval(
              child: AuthImage(
                  url: '$_server/api/auth/avatar/'
                      '${Uri.encodeComponent(name)}?t=$_avatarBust',
                  semanticLabel: '$name 的头像',
                  fit: BoxFit.cover,
                  placeholder: (_) => const ColoredBox(
                      color: Color(0x22000000),
                      child: Center(
                          child: SizedBox(
                              width: 12, height: 12,
                              child: CircularProgressIndicator(strokeWidth: 2)))),
                  errorBuilder: (_, _) => Text(
                      (session.username ?? '?').isNotEmpty
                          ? session.username![0].toUpperCase()
                          : '?',
                      style: const TextStyle(fontSize: 16)))));
    }
    return CircleAvatar(
        radius: radius,
        child: Text((session.username ?? '?').isNotEmpty
            ? session.username![0].toUpperCase()
            : '?'));
  }

  /// 资料编辑（v0.17）：换头像（file_picker 选图 → POST multipart）+ 改昵称
  Future<void> _openProfileEditor() async {
    final session = ref.read(sessionProvider);
    final nickCtrl = TextEditingController(text: (session.nickname ?? '').trim());
    File? newAvatar;
    var busy = false;
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      enableDrag: false,   // busy 期间不可拖拽关闭（review P2-5）
      builder: (bctx) => StatefulBuilder(
        builder: (bctx, setD) => Padding(
          padding: EdgeInsets.only(
              left: Gap.lg, right: Gap.lg, top: 18,
              bottom: 18 + MediaQuery.of(bctx).viewInsets.bottom),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text('编辑资料',
                style: Theme.of(bctx).textTheme.titleMedium),
            const SizedBox(height: Gap.md),
            GestureDetector(
              onTap: busy
                  ? null
                  : () async {
                      final r = await FilePicker.platform
                          .pickFiles(type: FileType.image);
                      if (r != null && r.files.single.path != null) {
                        setD(() => newAvatar = File(r.files.single.path!));
                      }
                    },
              child: Stack(alignment: Alignment.bottomRight, children: [
                if (newAvatar != null)
                  CircleAvatar(
                      radius: 36, backgroundImage: FileImage(newAvatar!))
                else if ((session.avatarPath ?? '').isNotEmpty &&
                    _server != null &&
                    (session.username ?? '').isNotEmpty)
                  CircleAvatar(
                      radius: 36,
                      child: ClipOval(
                          child: AuthImage(
                              url: '$_server/api/auth/avatar/'
                                  '${Uri.encodeComponent(session.username!)}'
                                  '?t=$_avatarBust',
                              fit: BoxFit.cover,
                              semanticLabel:
                                  '${session.username!} 的头像')))
                else
                  CircleAvatar(
                      radius: 36,
                      child: Text((session.username ?? '?').isNotEmpty
                          ? session.username![0].toUpperCase()
                          : '?',
                          style: const TextStyle(fontSize: 26))),
                const CircleAvatar(
                    radius: 12, child: Icon(Icons.camera_alt, size: 13)),
              ]),
            ),
            const SizedBox(height: Gap.xxs),
            Text('点头像更换（jpg/png，8MB 内）',
                style: TextStyle(fontSize: 11.5, color: Colors.grey)),
            const SizedBox(height: 14),
            TextField(
                controller: nickCtrl,
                maxLength: 32,
                decoration: const InputDecoration(
                    labelText: '昵称',
                    hintText: '留空恢复显示用户名；登录仍用用户名')),
            const SizedBox(height: 14),
            Row(mainAxisAlignment: MainAxisAlignment.end, children: [
              TextButton(
                  onPressed: busy ? null : () => Navigator.pop(bctx, false),
                  child: const Text('取消')),
              const SizedBox(width: Gap.xs),
              FilledButton(
                  onPressed: busy
                      ? null
                      : () async {
                          setD(() => busy = true);
                          try {
                            if (newAvatar != null) {
                              await AccountApi.uploadAvatar(newAvatar!.path);
                              await ref
                                  .read(sessionProvider.notifier)
                                  .refreshFromMe();
                              if (mounted) setState(() => _avatarBust++);
                            }
                            final nick = nickCtrl.text.trim();
                            if (nick != (session.nickname ?? '').trim()) {
                              await ref
                                  .read(sessionProvider.notifier)
                                  .updateProfile(nick);
                            }
                            if (bctx.mounted) Navigator.pop(bctx, true);
                          } catch (e) {
                            // 服务端 detail 优先（errors.dart 统一文案）
                            if (bctx.mounted) setD(() => busy = false);
                            if (mounted) {
                              final msg = errDetail(e, max: 120);
                              ScaffoldMessenger.of(context).showSnackBar(
                                  SnackBar(content: Text(
                                      msg.isEmpty ? '保存失败' : msg)));
                            }
                          }
                        },
                  child: busy
                      ? const SizedBox(
                          width: 16, height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('保存')),
            ]),
          ]),
        ),
      ),
    );
    if (saved == true && mounted) {
      showToast('资料已更新');
      setState(() {});
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider);
    final settings = ref.watch(settingsProvider);
    final cs = Theme.of(context).colorScheme;
    final isGuest = session.role == 'guest';
    return Scaffold(
      appBar: AppBar(title: const Text('我的')),
      // v1.0 内容限宽：宽屏（平板/横屏）下居中且不超过 720 逻辑像素
      // （含两侧 12 边距）——设置项文字不再横跨整屏；手机竖屏零变化
      body: AdaptiveContent(
          maxWidth: Breakpoints.contentMaxWidth + 24,
          // padding 必须为零：各分组自带 12 边距（改造前就是它承担页边距），
          // 这里再叠一层会变成 24——本组件只负责"宽屏居中限宽"这一件事
          padding: EdgeInsets.zero,
          child: ListView(
              padding: const EdgeInsets.only(bottom: Gap.xl), children: [
        // ── 身份 + 连接（重点置顶）────────────────────────────
        Padding(
          padding: const EdgeInsets.fromLTRB(Gap.sm, Gap.xs, Gap.sm, 0),
          child: Card(
              margin: EdgeInsets.zero,
              child: Padding(
                  padding: const EdgeInsets.all(6),
                  child: Column(children: [
                    ListTile(
                      leading: _avatarWidget(session),
                      title: Text(session.displayName ?? ''),
                      subtitle: Text(session.role == 'admin'
                          ? '管理员 · 点按编辑头像/昵称'
                          : isGuest
                              ? '访客（只读）· 点按编辑头像/昵称'
                              : '成员 · 点按编辑头像/昵称'),
                      trailing: const Icon(Icons.edit_outlined, size: 18),
                      onTap: _openProfileEditor,
                    ),
                    const Divider(height: 1),
                    ListTile(
                      leading: const Icon(Icons.dns_outlined),
                      title: Text(_server != null ? shortHost(_server!) : ''),
                      subtitle: Text(_server != null && isPublicAddress(_server!)
                          ? '当前入口 · 公网（点按切换 主/备隧道/局域网）'
                          : '当前入口 · 局域网（点按切换 公网隧道）'),
                      trailing: _server != null && isPublicAddress(_server!)
                          ? _netBadge(context)
                          : null,
                      onTap: _pickServer,
                    ),
                    // 自动切换（v0.15）：在家直连、出门走隧道的自动化
                    SwitchListTile(
                      secondary: const Icon(Icons.swap_horiz_outlined),
                      title: const Text('自动切换局域网/公网'),
                      subtitle: Text(settings.autoSwitch
                          ? '开启：局域网不可达自动走公网，回家自动切回直连'
                          : '关闭：始终使用上面手动选择的入口'),
                      value: settings.autoSwitch,
                      onChanged: (v) async {
                        await ref
                            .read(settingsProvider.notifier)
                            .setAutoSwitch(v);
                        if (mounted) showToast(v ? '已开启自动切换' : '已关闭自动切换');
                      },
                    ),
                  ]))),
        ),
        // ── 功能宫格（高频重点，大图标）──────────────────────
        Padding(
          padding: const EdgeInsets.fromLTRB(Gap.sm, 10, Gap.sm, 0),
          child: Card(
              margin: EdgeInsets.zero,
              child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 10),
                  // Wrap 而非 Row：5 项一行正好放下；将来再加入口也只会
                  // 自动换行而不是溢出屏幕，行内仍按 spaceAround 分布
                  child: Wrap(
                      alignment: WrapAlignment.spaceAround,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      runSpacing: 4,
                      children: [
                        if (!isGuest)
                          _quickTile(Icons.backup_outlined, '自动备份', () =>
                              Navigator.push(context, MaterialPageRoute(
                                  builder: (_) => const BackupSettingsPage()))),
                        if (!isGuest)
                          _quickTile(Icons.swap_vert_circle_outlined, '传输', () =>
                              Navigator.push(context, MaterialPageRoute(
                                  builder: (_) => const TransfersPage()))),
                        if (!isGuest)
                          _quickTile(Icons.share_outlined, '我的分享', () =>
                              Navigator.push(context, MaterialPageRoute(
                                  builder: (_) => const SharesPage()))),
                        // v1.1 效率舱 IA 重构：底栏已无"文件"页签，宫格里的
                        // "笔记"与底栏页签完全重复 → 用"文件"替位（仍 5 项
                        // 不换行；文件页本体从底栏降为 push 路由，见 home_shell）
                        _quickTile(Icons.folder_outlined, '文件', () =>
                            Navigator.push(context, MaterialPageRoute(
                                builder: (_) => FilesPage(
                                    onGoTransfers: () => Navigator.push(
                                        context, MaterialPageRoute(
                                        builder: (_) =>
                                            const TransfersPage())))))),
                        _quickTile(Icons.delete_outline, '回收站', () =>
                            Navigator.push(context, MaterialPageRoute(
                                builder: (_) => const TrashPage()))),
                      ]))),
        ),
        // ── 反馈通道（v0.28）：想法/Bug 直达开发端，状态可追 ────
        Padding(
          padding: const EdgeInsets.fromLTRB(Gap.sm, 10, Gap.sm, 0),
          child: Card(
              margin: EdgeInsets.zero,
              child: ListTile(
                  leading: Icon(Icons.forum_outlined, color: cs.primary),
                  title: const Text('反馈通道'),
                  subtitle: const Text('有想法或发现 Bug？一键发给开发端'),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.push(context,
                      MaterialPageRoute(builder: (_) => const FeedbackPage())),
                  ))),
        // ── 管理（仅管理员）：提前到普通功能之后（9-11 反馈 #b304）——
        if (session.role == 'admin')
          _group(context, '管理', [
            ListTile(
              leading: const Icon(Icons.admin_panel_settings_outlined),
              title: const Text('管理面板'),
              subtitle: const Text('成员/邀请/授权矩阵/系统设置'),
              onTap: () => Navigator.push(context,
                  MaterialPageRoute(builder: (_) => const AdminPage())),
            ),
          ]),
        // ── 外观与通知 ────────────────────────────────────────
        _group(context, '外观与通知', [
          ListTile(
            leading: const Icon(Icons.dark_mode_outlined),
            title: const Text('深色模式'),
            subtitle: Text(switch (settings.themeMode) {
              ThemeMode.system => '跟随系统',
              ThemeMode.light => '浅色',
              ThemeMode.dark => '深色',
            }),
            onTap: _pickThemeMode,
          ),
          // 0.8.5 主题引擎：预置色板 / 跟随壁纸（Material You）
          Builder(builder: (context) {
            final isAuto = settings.accent == Palettes.auto;
            final cur = paletteById(settings.accent);
            return ListTile(
              leading: Container(
                  width: 30,
                  height: 30,
                  decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      gradient:
                          isAuto ? null : LinearGradient(colors: cur.gradient),
                      color: isAuto ? cs.surfaceContainerHighest : null),
                  child:
                      isAuto ? const Icon(Icons.wallpaper, size: IconSizes.sm) : null),
              title: const Text('主题色'),
              subtitle: Text(isAuto ? '跟随壁纸' : cur.name),
              onTap: _pickAccent,
            );
          }),
          SwitchListTile(
            secondary: const Icon(Icons.nights_stay_outlined),
            title: const Text('AMOLED 纯黑暗色'),
            subtitle: const Text('暗色模式下使用纯黑背景（OLED 屏更省电）'),
            value: settings.amoledDark,
            onChanged: (v) =>
                ref.read(settingsProvider.notifier).setAmoledDark(v),
          ),
          SwitchListTile(
            secondary: const Icon(Icons.notifications_outlined),
            title: const Text('传输完成通知'),
            subtitle: const Text('上传/下载完成时系统提醒'),
            value: settings.notifyTransferDone,
            onChanged: (v) =>
                ref.read(settingsProvider.notifier).setNotifyTransferDone(v),
          ),
        ]),
        // ── 账号与安全（低频但重要，收进一组）─────────────────
        _group(context, '账号与安全', [
          ListTile(
            leading: const Icon(Icons.lock_outline),
            title: const Text('修改密码'),
            onTap: _changePassword,
          ),
          ListTile(
            leading: const Icon(Icons.badge_outlined),
            title: const Text('修改用户名'),
            subtitle: const Text('私人目录同步改名，需密码确认'),
            onTap: _changeUsername,
          ),
          ListTile(
            leading: const Icon(Icons.storage_outlined),
            title: const Text('WebDAV 独立密码'),
            subtitle: Text(_webdavSet == null
                ? '读取中…'
                : _webdavSet!
                    ? '已启用：挂载只认独立密码'
                    : '未设置：挂载目前用登录密码'),
            onTap: _webdavDialog,
          ),
          ListTile(
            leading: const Icon(Icons.qr_code_2),
            title: const Text('生成配对码'),
            subtitle: const Text('另一台设备免密登录本账号（3 分钟有效）'),
            onTap: _showPairCode,
          ),
          ListTile(
            leading: const Icon(Icons.restore),
            title: const Text('重新生成恢复码'),
            subtitle: const Text('两步验证丢失认证器时的备用码，需密码确认'),
            onTap: _regenRecoveryCodes,
          ),
          ListTile(
            leading: const Icon(Icons.phonelink_lock_outlined),
            title: const Text('验证器'),
            subtitle: const Text('两步验证动态码，扫码绑定后本机直接显示'),
            onTap: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => const VerifierPage())),
          ),
          SwitchListTile(
            secondary: const Icon(Icons.fingerprint),
            title: const Text('应用锁'),
            subtitle: const Text('打开或切回 App 需指纹/面部解锁'),
            value: settings.appLock,
            onChanged: _toggleAppLock,
          ),
          // 已登录设备收进安全组（吊销入口保留）
          _devicesSection(),
        ]),
        // ── 通用 ──────────────────────────────────────────────
        _group(context, '通用', [
          ListTile(
            leading: const Icon(Icons.image_outlined),
            title: const Text('清理缩略图缓存'),
            subtitle: Text(_cacheSize.isEmpty ? '统计中…' : '占用 $_cacheSize'),
            onTap: _clearCache,
          ),
          ListTile(
            leading: const Icon(Icons.cleaning_services_outlined),
            title: const Text('清理应用缓存'),
            subtitle: Text(_appCacheSize.isEmpty
                ? '统计中…'
                : '临时文件与网络缓存，占用 $_appCacheSize'),
            onTap: _clearAppCache,
          ),
          ListTile(
            leading: const Icon(Icons.info_outline),
            title: const Text('关于与检查更新'),
            onTap: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => const AboutPage())),
          ),
        ]),
        const Divider(),
        ListTile(
          leading: Icon(Icons.logout, color: cs.error),
          title: Text('退出登录',
              style: TextStyle(color: cs.error)),
          // 二次确认（R1-P2-5）：全 App 唯一无确认的高危操作，误触即清凭据
          onTap: () async {
            final ok = await showDialog<bool>(
              context: context,
              builder: (ctx) => AlertDialog(
                title: const Text('退出登录？'),
                content: const Text('将清除本机保存的登录凭据与传输记录状态，'
                    '下次需要重新输入密码登录。'),
                actions: [
                  TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
                  FilledButton(
                      style: FilledButton.styleFrom(
                          backgroundColor: Theme.of(context).colorScheme.error),
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('退出')),
                ],
              ),
            );
            if (ok == true) {
              // ignore: discarded_futures
              ref.read(sessionProvider.notifier).logout();
            }
          },
        ),
        const SizedBox(height: Gap.sm),
        Center(child: Text('LAN Share App · v$_version',
            style: const TextStyle(fontSize: 11, color: Colors.grey))),
        const SizedBox(height: Gap.xl),
      ])),
    );
  }

  /// 分组卡：小标题 + 圆角卡包裹的条目列表（v0.15 重组）
  Widget _group(BuildContext context, String title, List<Widget> children) =>
      Padding(
          padding: const EdgeInsets.fromLTRB(Gap.sm, 10, Gap.sm, 0),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start,
              children: [
            Padding(
                padding: const EdgeInsets.fromLTRB(Gap.xxs, 0, 0, 6),
                child: Text(title,
                    style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600,
                        color: Theme.of(context).colorScheme.onSurfaceVariant))),
            Card(
                margin: EdgeInsets.zero,
                child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: Gap.xxs),
                    child: Column(children: children))),
          ]));

  /// 功能宫格单元（大图标 + 标签，与首页快捷区同风格）
  Widget _quickTile(IconData icon, String label, VoidCallback onTap) =>
      InkWell(
          onTap: onTap,
          borderRadius: Radii.bMd,
          child: Padding(
              padding:
                  const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Container(
                    width: 46,
                    height: 46,
                    decoration: BoxDecoration(
                        color: Theme.of(context)
                            .colorScheme
                            .primary
                            .withValues(alpha: 0.12),
                        borderRadius: Radii.bMd),
                    child: Icon(icon,
                        size: 22,
                        color: Theme.of(context).colorScheme.primary)),
                const SizedBox(height: 5),
                Text(label, style: const TextStyle(fontSize: 12)),
              ])));

  /// 已登录设备（收进账号与安全组；原独立分区内容不变）
  Widget _devicesSection() {
    if (_loadingDevices) {
      return const Padding(
          padding: EdgeInsets.all(Gap.xl),
          child: Center(child: CircularProgressIndicator()));
    }
    return Column(children: [
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
    ]);
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
