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
import 'admin_page.dart';
import 'backup_settings_page.dart';
import '../data/api/api_client.dart';
import '../data/api/auth_cache.dart';
import '../data/api/errors.dart';
import '../data/api/trust_flow.dart';
import '../data/local/prefs.dart';
import '../data/net.dart';
import '../state/session.dart';
import '../state/settings.dart';
import 'theme/app_theme.dart';
import '../transfer/transfer_task.dart' show humanSize;
import 'shares_page.dart';
import 'trash_page.dart';
import 'verifier_page.dart';
import 'widgets/server_picker.dart' show shortHost, showServerPicker;

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
  String _version = '';
  bool? _webdavSet; // WebDAV 独立密码是否已设置（0.8.0）

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    _server = await Prefs.lastAddress();
    if (mounted) setState(() {});
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/auth/webdav-password');
      if (mounted) setState(() => _webdavSet = r.data['set'] as bool? ?? false);
    } catch (_) {/* 端点不可用保持未知 */}
    try {
      final pi = await PackageInfo.fromPlatform();
      if (mounted) setState(() => _version = pi.version);
    } catch (_) {}
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/auth/devices');
      final list = ((r.data['devices'] as List?) ?? const []).map((e) {
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
    final dio = await ApiClient.I.dio();
    try {
      await dio.delete('/api/auth/devices/${d.id}');
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
                      padding: const EdgeInsets.fromLTRB(20, 4, 20, 12),
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
        borderRadius: BorderRadius.circular(12),
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
    if (ok != true || !mounted) {
      oldCtrl.dispose();
      newCtrl.dispose();
      againCtrl.dispose();
      return;
    }
    if (newCtrl.text.length < 8) {
      _toast('新密码至少 8 位');
      return;
    }
    if (newCtrl.text != againCtrl.text) {
      _toast('两次输入的新密码不一致');
      return;
    }
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/auth/password', data: {
        'old_password': oldCtrl.text,
        'new_password': newCtrl.text,
      });
      _toast('密码已修改（网页端会话需重新登录）');
    } catch (e) {
      _toast('修改失败：${_detail(e)}');
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
    if (ok != true || !mounted) {
      nameCtrl.dispose();
      pwdCtrl.dispose();
      return;
    }
    final newName = nameCtrl.text.trim();
    if (newName.isEmpty || newName == session.username) {
      nameCtrl.dispose();
      pwdCtrl.dispose();
      return;
    }
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/auth/username',
          data: {'new_username': newName, 'password': pwdCtrl.text});
      if (!mounted) return;
      await Prefs.setUsername(newName);
      if (!mounted) return;
      ref.read(sessionProvider.notifier).applyRename(newName);
      // 缩略图 key 含旧用户名路径：清掉避免旧目录名驻留本地（0.8.1 审查修复）
      unawaited(AuthCacheManager.I.emptyCache());
      _toast('用户名已改为 $newName（私人目录已同步改名）');
      setState(() {});
    } catch (e) {
      _toast('修改失败：${_detail(e)}');
    } finally {
      nameCtrl.dispose();
      pwdCtrl.dispose();
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
                  const SizedBox(height: 8),
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
    if (ok != true || !mounted) {
      curCtrl.dispose();
      newCtrl.dispose();
      return;
    }
    final newPwd = newCtrl.text;
    if (newPwd.isNotEmpty && newPwd.length < 8) {
      _toast('WebDAV 密码至少 8 位（留空=清除）');
      newCtrl.dispose();
      curCtrl.dispose();
      return;
    }
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.post('/api/auth/webdav-password', data: {
        'current_password': curCtrl.text,
        'webdav_password': newPwd,
      });
      if (!mounted) return;
      setState(() => _webdavSet = r.data['set'] as bool? ?? false);
      _toast(r.data['set'] == true
          ? 'WebDAV 独立密码已生效（挂载客户端需改用新密码）'
          : '已清除：WebDAV 回退用登录密码挂载');
    } catch (e) {
      _toast('保存失败：${_detail(e)}');
    } finally {
      curCtrl.dispose();
      newCtrl.dispose();
    }
  }

  /// 生成一次性配对码：给另一台设备免密登录本账号（登录页"配对码登录"）。
  Future<void> _showPairCode() async {
    String? code;
    int expires = 0;
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.post('/api/pair/code');
      code = r.data['code'] as String?;
      expires = (r.data['expires_in'] as num?)?.toInt() ?? 180;
    } catch (e) {
      _toast('生成失败：${_detail(e)}');
      return;
    }
    if (!mounted) return;
    if (code == null || code.isEmpty) {
      _toast('生成失败：服务端未返回配对码');
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
                  const SizedBox(height: 8),
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
    if (ok != true || !mounted) {
      pwdCtrl.dispose();
      return;
    }
    List codes;
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio
          .post('/api/auth/totp/rc', data: {'old_password': pwdCtrl.text});
      codes = ((r.data['recovery_codes'] as List?) ?? const [])
          .whereType<String>()
          .toList();
    } catch (e) {
      _toast('生成失败：${_detail(e)}');
      return;
    } finally {
      pwdCtrl.dispose();
    }
    if (!mounted) return;
    if (codes.isEmpty) {
      _toast('生成失败：服务端未返回恢复码');
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
                  const SizedBox(height: 8),
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
        _toast('本机未录入指纹/面部，无法开启应用锁');
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
      _toast('生物识别不可用，无法开启应用锁');
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
    await _loadCacheSize();
    _toast('已清理');
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg), duration: const Duration(seconds: 2)));
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
          onAutoTrusted: () => _toast('指纹与已信任服务器一致，已自动信任此入口'));
      if (!ok || !mounted) return;
      await ApiClient.I.switchServer(url);
      await Prefs.rememberAddress(url);
      if (!mounted) return;
      setState(() => _server = url);
      _toast('已切换到 ${shortHost(url)}');
    });
  }

  /// 公网入口徽标
  Widget _netBadge(BuildContext context) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.tertiaryContainer,
          borderRadius: BorderRadius.circular(999)),
      child: Text('公网',
          style: TextStyle(
              fontSize: 11,
              color: Theme.of(context).colorScheme.onTertiaryContainer)));

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider);
    final settings = ref.watch(settingsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('我的')),
      body: ListView(children: [
        const SizedBox(height: 8),
        ListTile(
          leading: CircleAvatar(child: Text((session.username ?? '?').isNotEmpty
              ? session.username![0].toUpperCase() : '?')),
          title: Text(session.username ?? ''),
          subtitle: Text(session.role == 'admin' ? '管理员'
              : session.role == 'guest' ? '访客' : '成员'),
        ),
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
        const Divider(),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 16, vertical: 6),
          child: Text('设置', style: TextStyle(fontWeight: FontWeight.bold)),
        ),
        // 管理面板（0.8.0 对齐 Web AdminView）：仅管理员可见
        if (session.role == 'admin')
          ListTile(
            leading: const Icon(Icons.admin_panel_settings_outlined),
            title: const Text('管理面板'),
            subtitle: const Text('成员 / 邀请码 / 授权 / 事件推送 / 公网地址'),
            onTap: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => const AdminPage())),
          ),
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
                    color: isAuto
                        ? Theme.of(context).colorScheme.surfaceContainerHighest
                        : null),
                child:
                    isAuto ? const Icon(Icons.wallpaper, size: 16) : null),
            title: const Text('主题色'),
            subtitle: Text(isAuto ? '跟随壁纸' : cur.name),
            onTap: _pickAccent,
          );
        }),
        // 0.8.4 暗色增强：AMOLED 纯黑底（OLED 屏省电 + 夜间观影友好）
        SwitchListTile(
          secondary: const Icon(Icons.nights_stay_outlined),
          title: const Text('AMOLED 纯黑暗色'),
          subtitle: const Text('暗色模式下使用纯黑背景（OLED 屏更省电）'),
          value: settings.amoledDark,
          onChanged: (v) =>
              ref.read(settingsProvider.notifier).setAmoledDark(v),
        ),
        const Divider(),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 16, vertical: 6),
          child: Text('账号与安全',
              style: TextStyle(fontWeight: FontWeight.bold)),
        ),
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
        SwitchListTile(
          secondary: const Icon(Icons.notifications_outlined),
          title: const Text('传输完成通知'),
          subtitle: const Text('上传/下载完成时系统提醒'),
          value: settings.notifyTransferDone,
          onChanged: (v) =>
              ref.read(settingsProvider.notifier).setNotifyTransferDone(v),
        ),
        SwitchListTile(
          secondary: const Icon(Icons.fingerprint),
          title: const Text('应用锁'),
          subtitle: const Text('打开或切回 App 需指纹/面部解锁'),
          value: settings.appLock,
          onChanged: _toggleAppLock,
        ),
        ListTile(
          leading: const Icon(Icons.image_outlined),
          title: const Text('清理缩略图缓存'),
          subtitle: Text(_cacheSize.isEmpty ? '统计中…' : '占用 $_cacheSize'),
          onTap: _clearCache,
        ),
        ListTile(
          leading: const Icon(Icons.info_outline),
          title: const Text('关于与检查更新'),
          onTap: () => Navigator.push(context,
              MaterialPageRoute(builder: (_) => const AboutPage())),
        ),
        // 访客只读：无备份（APP-REQUIREMENTS §2）
        if (session.role != 'guest')
          ListTile(
            leading: const Icon(Icons.backup_outlined),
            title: const Text('自动备份'),
            subtitle: const Text('手机相册自动存进网盘'),
            onTap: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => const BackupSettingsPage())),
          ),
        // 分享管理（M-A4 FR-7）：访客无分享创建，入口一并隐藏
        if (session.role != 'guest')
          ListTile(
            leading: const Icon(Icons.share_outlined),
            title: const Text('我的分享'),
            subtitle: const Text('管理分享链接：复制/二维码/编辑/撤销'),
            onTap: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => const SharesPage())),
          ),
        ListTile(
          leading: const Icon(Icons.delete_outline),
          title: const Text('回收站'),
          subtitle: const Text('恢复或彻底删除已删文件'),
          onTap: () => Navigator.push(context,
              MaterialPageRoute(builder: (_) => const TrashPage())),
        ),
        // App 内置验证器（v0.5.2）：两步验证动态码本机生成，免装第三方验证器
        ListTile(
          leading: const Icon(Icons.phonelink_lock_outlined),
          title: const Text('验证器'),
          subtitle: const Text('两步验证动态码，扫码绑定后本机直接显示'),
          onTap: () => Navigator.push(context,
              MaterialPageRoute(builder: (_) => const VerifierPage())),
        ),
        const Divider(),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 16, vertical: 6),
          child: Text('已登录设备', style: TextStyle(fontWeight: FontWeight.bold)),
        ),
        if (_loadingDevices)
          const Padding(padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator())),
        if (!_loadingDevices)
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
        const Divider(),
        ListTile(
          leading: const Icon(Icons.logout),
          title: const Text('退出登录'),
          onTap: () => ref.read(sessionProvider.notifier).logout(),
        ),
        const SizedBox(height: 12),
        Center(child: Text('LAN Share App · v$_version',
            style: const TextStyle(fontSize: 11, color: Colors.grey))),
        const SizedBox(height: 24),
      ]),
    );
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
