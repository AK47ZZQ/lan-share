// 管理面板（0.8.0 对齐 Web AdminView）：成员 / 邀请 / 授权三 Tab +
// 公网地址 + 事件推送（webhook）。仅 admin 可见入口（我的页）。
// 视角预览（web setPreviewAs）暂不迁移：App 全局 dio 注入 header 改造大，
// 记录在 APP-DESIGN §9 遗留。
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/api_client.dart';
import '../ui/widgets/prompt_dialog.dart';
import '../data/api/errors.dart';
import '../state/session.dart';

class _AdminUser {
  final int id;
  final String username;
  final String role;
  final bool isActive;
  final int? quotaBytes;
  final String quotaHuman;
  final String createdAt;
  final bool totpEnabled;
  final bool webdavSet;
  _AdminUser.fromJson(Map<String, dynamic> j)
      : id = (j['id'] as num?)?.toInt() ?? 0,
        username = j['username'] as String? ?? '',
        role = j['role'] as String? ?? 'member',
        isActive = j['is_active'] as bool? ?? true,
        quotaBytes = (j['quota_bytes'] as num?)?.toInt(),
        quotaHuman = j['quota_human'] as String? ?? '',
        createdAt = j['created_at'] as String? ?? '',
        totpEnabled = j['totp_enabled'] as bool? ?? false,
        webdavSet = j['webdav_set'] as bool? ?? false;
}

class _Grant {
  final int id;
  final int userId;
  final String path;
  final bool canWrite;
  final int? expiresAt;
  _Grant.fromJson(Map<String, dynamic> j)
      : id = (j['id'] as num?)?.toInt() ?? 0,
        userId = (j['user_id'] as num?)?.toInt() ?? 0,
        path = j['path'] as String? ?? '',
        canWrite = j['can_write'] as bool? ?? false,
        expiresAt = (j['expires_at'] as num?)?.toInt();
}

class _Invite {
  final String code;
  final String role;
  final bool expired;
  final bool used;
  _Invite.fromJson(Map<String, dynamic> j)
      : code = j['code'] as String? ?? '',
        role = j['role'] as String? ?? '',
        expired = j['expired'] as bool? ?? false,
        used = j['used'] as bool? ?? false;
}

class AdminPage extends ConsumerStatefulWidget {
  const AdminPage({super.key});
  @override
  ConsumerState<AdminPage> createState() => _AdminPageState();
}

class _AdminPageState extends ConsumerState<AdminPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tab =
      TabController(length: 3, vsync: this);
  List<_AdminUser>? _users;
  List<_Invite>? _invites;
  List<_Grant>? _grants;
  String? _error;
  // 授权表单
  final _gUsers = <int>{};
  final _gPaths = <String>{};
  bool _gWrite = false;
  int _gExpiry = 0; // 0=永久
  List<String> _dirOptions = const [];
  int _grantFilter = -1; // -1=全部
  // 设置卡
  final _pubUrl = TextEditingController();
  final _pubAlt = TextEditingController();
  final _hookUrl = TextEditingController();
  bool _busy = false; // 保存/创建类按钮防连点（0.8.1 审查修复）

  @override
  void initState() {
    super.initState();
    _loadAll();
  }

  @override
  void dispose() {
    _tab.dispose();
    _pubUrl.dispose();
    _pubAlt.dispose();
    _hookUrl.dispose();
    super.dispose();
  }

  Future<void> _loadAll() async {
    try {
      final dio = await ApiClient.I.dio();
      final users = (await dio.get('/api/admin/users'))
          .data as List;
      final invites = (await dio.get('/api/admin/invites')).data as List;
      final grants = (await dio.get('/api/admin/grants')).data as List;
      final pub = await dio.get('/api/public-url');
      final hook = await dio.get('/api/admin/webhook');
      if (!mounted) return;
      setState(() {
        _users = users.map((e) => _AdminUser.fromJson(e)).toList();
        _invites = invites.map((e) => _Invite.fromJson(e)).toList();
        _grants = grants.map((e) => _Grant.fromJson(e)).toList();
        _pubUrl.text = pub.data['public_base_url'] as String? ?? '';
        _pubAlt.text = pub.data['public_base_url_alt'] as String? ?? '';
        _hookUrl.text = hook.data['url'] as String? ?? '';
        _error = null;
      });
      _loadDirs();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = errDetail(e, max: 160));
    }
  }

  /// 共享一级目录清单（建授权用，v0.20 重构）：授权只收一级目录，
  /// 子目录随父级授权自动继承（服务端 spaces._grant_hit 前缀匹配语义）
  Future<void> _loadDirs() async {
    final out = <String>[];
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/files');
      for (final e in (r.data['items'] as List)) {
        final m = e as Map;
        if (m['is_dir'] != true) continue;
        final name = m['name'] as String;
        if (name == 'private' || name == 'incoming') continue;
        out.add(m['path'] as String);
      }
    } catch (_) {/* 无权目录跳过 */}
    if (!mounted) return;
    setState(() => _dirOptions = out..sort());
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg), width: 360));
  }

  Future<bool> _confirm(String title, String content,
      {String okText = '确定'}) async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (d) => AlertDialog(
                title: Text(title),
                content: Text(content),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(d, false),
                      child: const Text('取消')),
                  FilledButton(
                      style: FilledButton.styleFrom(
                          backgroundColor:
                              Theme.of(d).colorScheme.error),
                      onPressed: () => Navigator.pop(d, true),
                      child: Text(okText)),
                ]));
    return ok == true;
  }

  /// allowEmpty=true 时"留空提交"返回 ''（区别于取消的 null）——
  /// 清配额/清 WebDAV 密码等"空值有语义"的流程用（0.8.1 审查修复）
  Future<String?> _prompt(String title, String label,
      {String initial = '',
      bool obscure = false,
      String? hint,
      String okText = '保存',
      bool allowEmpty = false}) async {
    // controller 生命周期由 PromptDialog 自持（v0.13：dialog 退场动画中
    // dispose 会触发 framework 断言红屏，与 photos/files 批量收口同款）
    final v = await showDialog<String>(
        context: context,
        builder: (d) => PromptDialog(
            title: title,
            label: label,
            hint: hint ?? '',
            initial: initial,
            obscure: obscure,
            okText: okText,
            trimOnSubmit: true));
    if (v == null) return null; // 取消
    if (v.isEmpty) return allowEmpty ? '' : null; // 留空提交
    return v;
  }

  /// 配额展示：<10GB 保留一位小数（防 500MB 显示成 0 被误存为清空）
  String _gbText(int bytes) {
    final gb = bytes / (1 << 30);
    return gb < 10 ? gb.toStringAsFixed(1) : gb.round().toString();
  }

  String _memberName(int uid) =>
      _users?.firstWhere((u) => u.id == uid,
          orElse: () => _AdminUser.fromJson({
                'id': uid,
                'username': '已删除(#$uid)',
                'role': 'member',
                'quota_human': '',
                'created_at': '',
              })).username ??
      '已删除(#$uid)';

  // ── 成员操作 ─────────────────────────────────────────────
  Future<void> _userSheet(_AdminUser u) async {
    final me = ref.read(sessionProvider).username;
    await showModalBottomSheet<void>(
        context: context,
        builder: (ctx) => SafeArea(
                child: ListView(shrinkWrap: true, children: [
              ListTile(title: Text(u.username),
                  subtitle: Text('${u.role} · 配额 ${u.quotaHuman}'),
                  leading: const Icon(Icons.person_outline)),
              ListTile(
                  leading: const Icon(Icons.badge_outlined),
                  title: const Text('修改角色'),
                  onTap: () async {
                    Navigator.pop(ctx);
                    final role = await _pickRole(u.role);
                    if (role != null) await _patchUser(u.id, {'role': role});
                  }),
              ListTile(
                  leading: const Icon(Icons.edit_outlined),
                  title: const Text('改用户名 / 重置密码'),
                  onTap: () async {
                    Navigator.pop(ctx);
                    await _credFlow(u);
                  }),
              ListTile(
                  leading: const Icon(Icons.shield_outlined),
                  title: Text(u.totpEnabled ? '解除两步验证' : '两步验证未开启'),
                  onTap: u.totpEnabled
                      ? () async {
                          Navigator.pop(ctx);
                          if (!await _confirm('解除两步验证',
                              '将解除 ${u.username} 的两步验证（含恢复码），不动密码。')) {
                            return;
                          }
                          await _post('/api/admin/users/${u.id}/totp-disable',
                              {},
                              okMsg: '已解除 ${u.username} 的两步验证');
                        }
                      : null),
              ListTile(
                  leading: const Icon(Icons.storage_outlined),
                  title: const Text('设置配额'),
                  onTap: () async {
                    Navigator.pop(ctx);
                    final gb = await _prompt('私人空间配额',
                        'GB（留空或 0 提交=不限）',
                        initial: u.quotaBytes == null
                            ? ''
                            : _gbText(u.quotaBytes!),
                        hint: '不限',
                        allowEmpty: true);
                    if (gb == null) return;
                    // 留空或 0=清成不限（服务端 0 → NULL）；非数字/负数不发
                    // 请求（0.8.1 审查修复；二次审查补 0 语义与小数展示）
                    final n = double.tryParse(gb);
                    if (gb.isNotEmpty && (n == null || n < 0)) {
                      _toast('请输入正数 GB，或留空提交表示不限');
                      return;
                    }
                    await _patchUser(u.id, {
                      'quota_bytes':
                          gb.isEmpty || n == 0 ? 0 : (n! * (1 << 30)).round()
                    });
                  }),
              ListTile(
                  leading: const Icon(Icons.key_outlined),
                  title: Text(u.webdavSet ? '重置 WebDAV 密码' : '设置 WebDAV 密码'),
                  onTap: () async {
                    Navigator.pop(ctx);
                    final v = await _prompt('WebDAV 独立密码',
                        '至少 8 位，留空提交=清除',
                        obscure: true, okText: '保存', allowEmpty: true);
                    if (v == null) return;
                    if (v.isNotEmpty && v.length < 8) {
                      _toast('密码至少 8 位（留空提交=清除）');
                      return;
                    }
                    await _post(
                        '/api/admin/users/${u.id}/webdav-password',
                        {'value': v},
                        okMsg: v.isEmpty
                            ? '已清除 WebDAV 独立密码'
                            : 'WebDAV 密码已更新');
                  }),
              ListTile(
                  leading: Icon(u.isActive
                      ? Icons.block_outlined
                      : Icons.check_circle_outline),
                  title: Text(u.isActive ? '停用账号' : '启用账号'),
                  onTap: () async {
                    Navigator.pop(ctx);
                    await _patchUser(u.id, {'is_active': !u.isActive});
                  }),
              if (u.username != me)
                ListTile(
                    leading: Icon(Icons.delete_outline,
                        color: Theme.of(ctx).colorScheme.error),
                    title: Text('删除成员',
                        style: TextStyle(
                            color: Theme.of(ctx).colorScheme.error)),
                    onTap: () async {
                      Navigator.pop(ctx);
                      if (!await _confirm('删除成员「${u.username}」？',
                          '其私人目录将移入 .orphaned/ 保留。',
                          okText: '删除')) {
                        return;
                      }
                      await _del('/api/admin/users/${u.id}',
                          okMsg: '已删除（其授权/会话一并清除）');
                      _gUsers.remove(u.id);
                    }),
            ])));
  }

  Future<String?> _pickRole(String current) async {
    String? picked;
    await showModalBottomSheet<String>(
        context: context,
        builder: (ctx) => SafeArea(
                child: ListView(shrinkWrap: true, children: [
              const ListTile(title: Text('选择角色')),
              for (final r in const ['admin', 'member', 'guest'])
                ListTile(
                    leading: Icon(r == current
                        ? Icons.radio_button_checked
                        : Icons.radio_button_off),
                    title: Text(r),
                    onTap: () {
                      picked = r;
                      Navigator.pop(ctx);
                    }),
            ])));
    return picked;
  }

  /// 改用户名（管理员直改，私人目录/incoming 同步改名）→ 重置密码二选一流
  Future<void> _credFlow(_AdminUser u) async {
    final action = await showModalBottomSheet<String>(
        context: context,
        builder: (ctx) => SafeArea(
                child: ListView(shrinkWrap: true, children: [
              ListTile(
                  leading: const Icon(Icons.edit_outlined),
                  title: const Text('改用户名'),
                  onTap: () => Navigator.pop(ctx, 'rename')),
              ListTile(
                  leading: const Icon(Icons.lock_reset_outlined),
                  title: const Text('重置密码'),
                  onTap: () => Navigator.pop(ctx, 'reset')),
            ])));
    if (action == 'rename') {
      final newName = await _prompt('改用户名 — ${u.username}', '新用户名');
      if (newName == null || newName == u.username) return;
      await _post('/api/admin/users/${u.id}/username', {'value': newName},
          okMsg: '用户名已改为 $newName');
    } else if (action == 'reset') {
      final newPwd = await _prompt('重置密码 — ${u.username}', '新密码（至少 8 位）',
          obscure: true);
      if (newPwd == null) return;
      // 与改自己密码/WebDAV 密码同款下限（R1-P3）：短密码提交到服务端只会
      // 得到一条 400，当场反馈更直观
      if (newPwd.length < 8) {
        _toast('密码至少 8 位');
        return;
      }
      await _post('/api/admin/users/${u.id}/password', {'value': newPwd},
          okMsg: '密码已重置（其两步验证同步解除、设备 Token 全部吊销）');
    }
  }

  Future<void> _patchUser(int uid, Map<String, Object?> body) async {
    try {
      final dio = await ApiClient.I.dio();
      await dio.patch('/api/admin/users/$uid', data: body);
      _toast('已更新');
      _loadAll();
    } catch (e) {
      _toast(errDetail(e));
    }
  }

  Future<void> _post(String path, Map<String, Object?> body,
      {String? okMsg}) async {
    try {
      final dio = await ApiClient.I.dio();
      await dio.post(path, data: body);
      if (okMsg != null) _toast(okMsg);
      _loadAll();
    } catch (e) {
      _toast(errDetail(e));
    }
  }

  /// DELETE 类操作（邀请撤销/授权撤销/删成员）：0.8.0 误用 POST 全部 405，
  /// 删除按钮失效的根因（0.8.1 审查修复）
  Future<void> _del(String path, {String? okMsg}) async {
    try {
      final dio = await ApiClient.I.dio();
      await dio.delete(path);
      if (okMsg != null) _toast(okMsg);
      _loadAll();
    } catch (e) {
      _toast(errDetail(e));
    }
  }

  // ── 邀请 ────────────────────────────────────────────────
  Future<void> _createInvite(String role) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio
          .post('/api/admin/invites', data: {'role': role, 'hours': 72});
      final code = r.data['code'] as String? ?? '';
      if (!mounted || code.isEmpty) return;
      await showDialog<void>(
          context: context,
          builder: (d) => AlertDialog(
                  title: Text(role == 'member' ? '成员邀请码' : '教访邀请码'),
                  content: Column(mainAxisSize: MainAxisSize.min, children: [
                    SelectableText(code,
                        style: const TextStyle(
                            fontSize: 24,
                            fontFamily: 'monospace',
                            fontWeight: FontWeight.bold)),
                    const SizedBox(height: 8),
                    const Text('72 小时内有效。新成员在登录页"邀请码建号"使用。',
                        style: TextStyle(fontSize: 12)),
                  ]),
                  actions: [
                    TextButton(
                        onPressed: () async {
                          await ClipboardSet.setText(code);
                          _toast('已复制');
                        },
                        child: const Text('复制')),
                    FilledButton(
                        onPressed: () => Navigator.pop(d),
                        child: const Text('关闭')),
                  ]));
      _loadAll();
    } catch (e) {
      _toast(errDetail(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ── 授权 ────────────────────────────────────────────────
  Future<void> _addGrantBatch() async {
    if (_busy) return;
    if (_gUsers.isEmpty || _gPaths.isEmpty) {
      _toast('先选成员和目录');
      return;
    }
    setState(() => _busy = true);
    var ok = 0, fail = 0;
    try {
      final dio = await ApiClient.I.dio();
      for (final uid in _gUsers) {
        for (final p in _gPaths) {
          try {
            await dio.post('/api/admin/grants', data: {
              'user_id': uid,
              'path': p,
              'can_read': true,
              'can_write': _gWrite,
              if (_gExpiry > 0) 'expires_hours': _gExpiry,
            });
            ok++;
          } catch (_) {
            fail++;
          }
        }
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    if (!mounted) return;
    _toast(fail == 0
        ? '已创建 $ok 条授权（${_gUsers.length} 位成员 × ${_gPaths.length} 个目录）'
        : '已创建 $ok 条，失败 $fail 条');
    setState(() {
      _gUsers.clear();
      _gPaths.clear();
    });
    _loadAll();
  }

  /// 授权有效期展示（v0.20）：倒计时/过期一目了然
  String _expiryText(int? exp) {
    if (exp == null) return '永久';
    final left = DateTime.fromMillisecondsSinceEpoch(exp * 1000)
        .difference(DateTime.now());
    if (left.isNegative) return '已过期';
    if (left.inHours < 24) return '${left.inHours.clamp(1, 23)} 小时内到期';
    return '剩 ${left.inDays} 天';
  }

  bool _grantExpired(_Grant g) =>
      g.expiresAt != null &&
      g.expiresAt! * 1000 < DateTime.now().millisecondsSinceEpoch;

  /// 修改授权（v0.20）：upsert 语义。续期=原剩余+延长时间；永久授权
  /// 续期无意义直接提示；读写切换保留原有效期
  Future<void> _updateGrant(_Grant g,
      {required bool canWrite, int extendHours = 0}) async {
    if (extendHours > 0 && g.expiresAt == null) {
      _toast('该授权已是永久，无需延长');
      return;
    }
    int? hours;
    if (g.expiresAt != null) {
      final left = g.expiresAt! - DateTime.now().millisecondsSinceEpoch ~/ 1000;
      if (left <= 0) {
        _toast('授权已过期，请删除后重新授权');
        return;
      }
      hours = (left / 3600).ceil() + extendHours;
    }
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/admin/grants', data: {
        'user_id': g.userId,
        'path': g.path,
        'can_read': true,
        'can_write': canWrite,
        'expires_hours': ?hours,
      });
      _toast(extendHours > 0
          ? '已延长 ${_memberName(g.userId)} 的「${g.path.split('/').last}」'
          : '已改为${canWrite ? '读写' : '只读'}');
      _loadAll();
    } catch (e) {
      _toast(errDetail(e));
    }
  }

  /// 单条授权操作菜单（v0.20）：续期/改读写/撤销收进一处，不再只有撤销
  void _grantSheet(_Grant g) {
    final expired = _grantExpired(g);
    showModalBottomSheet(
        context: context,
        builder: (ctx) => SafeArea(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              ListTile(
                  leading: const Icon(Icons.folder_outlined),
                  title: Text('${_memberName(g.userId)} · ${g.path}'),
                  subtitle: Text(
                      '${_expiryText(g.expiresAt)} · ${g.canWrite ? '读写' : '只读'}')),
              if (g.expiresAt != null && !expired) ...[
                ListTile(
                    leading: const Icon(Icons.update),
                    title: const Text('延长 7 天'),
                    onTap: () {
                      Navigator.pop(ctx);
                      _updateGrant(g, canWrite: g.canWrite, extendHours: 168);
                    }),
                ListTile(
                    leading: const Icon(Icons.update),
                    title: const Text('延长 30 天'),
                    onTap: () {
                      Navigator.pop(ctx);
                      _updateGrant(g, canWrite: g.canWrite, extendHours: 720);
                    }),
              ],
              ListTile(
                  leading: Icon(g.canWrite
                      ? Icons.lock_outline
                      : Icons.lock_open_outlined),
                  title: Text(g.canWrite ? '转为只读' : '转为可写入'),
                  onTap: g.canWrite
                      ? () {
                          Navigator.pop(ctx);
                          _updateGrant(g, canWrite: false);
                        }
                      : () {
                          Navigator.pop(ctx);
                          _updateGrant(g, canWrite: true);
                        }),
              ListTile(
                  leading: Icon(Icons.delete_outline,
                      color: Theme.of(ctx).colorScheme.error),
                  title: Text('撤销授权',
                      style: TextStyle(
                          color: Theme.of(ctx).colorScheme.error)),
                  onTap: () {
                    Navigator.pop(ctx);
                    _del('/api/admin/grants/${g.id}', okMsg: '已撤销');
                  }),
              const SizedBox(height: 8),
            ])));
  }

  // ── 设置卡 ──────────────────────────────────────────────
  Future<void> _savePubUrl() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/admin/public-url',
          data: {'url': _pubUrl.text.trim(), 'url_alt': _pubAlt.text.trim()});
      _toast('公网地址已保存');
    } catch (e) {
      _toast(errDetail(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _saveHook() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/admin/webhook',
          data: {'url': _hookUrl.text.trim()});
      _toast('推送地址已保存');
    } catch (e) {
      _toast(errDetail(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _testHook() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/admin/webhook/test');
      _toast('测试消息已发送，去手机上看看');
    } catch (e) {
      _toast(errDetail(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 手动广播（v0.20）：对话框里输标题/正文点推送即达——站内通知人手一条 +
  /// 已配置的站外 webhook 一起推。默认全体，可勾选部分成员。
  Future<void> _showNotifyDialog() async {
    final titleCtrl = TextEditingController();
    final bodyCtrl = TextEditingController();
    final targets = <int>{}; // 空 = 全体
    final ok = await showDialog<bool>(
        context: context,
        builder: (dctx) => StatefulBuilder(
            builder: (dctx, setD) => AlertDialog(
                    title: const Text('发送通知'),
                    content: SingleChildScrollView(
                        child: Column(mainAxisSize: MainAxisSize.min,
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                      TextField(
                          controller: titleCtrl,
                          autofocus: true,
                          maxLength: 80,
                          decoration: const InputDecoration(
                              labelText: '标题', border: OutlineInputBorder())),
                      const SizedBox(height: 10),
                      TextField(
                          controller: bodyCtrl,
                          maxLines: 3,
                          maxLength: 500,
                          decoration: const InputDecoration(
                              labelText: '正文（可留空）',
                              alignLabelWithHint: true,
                              border: OutlineInputBorder())),
                      const SizedBox(height: 8),
                      Wrap(spacing: 6, runSpacing: 4, children: [
                        FilterChip(
                            label: const Text('全体',
                                style: TextStyle(fontSize: 12)),
                            selected: targets.isEmpty,
                            onSelected: (_) => setD(() => targets.clear())),
                        for (final u in _users!
                            .where((u) => u.role != 'admin' && u.isActive))
                          FilterChip(
                              label: Text(u.username,
                                  style: const TextStyle(fontSize: 12)),
                              selected: targets.contains(u.id),
                              onSelected: (v) => setD(() =>
                                  v ? targets.add(u.id) : targets.remove(u.id))),
                      ]),
                    ])),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(dctx, false),
                          child: const Text('取消')),
                      ValueListenableBuilder<TextEditingValue>(
                          valueListenable: titleCtrl,
                          builder: (_, v, _) => FilledButton.icon(
                              onPressed: v.text.trim().isEmpty
                                  ? null
                                  : () => Navigator.pop(dctx, true),
                              icon: const Icon(Icons.send_outlined, size: 16),
                              label: const Text('推送'))),
                    ])));
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.post('/api/admin/notify', data: {
        'title': titleCtrl.text.trim(),
        'body': bodyCtrl.text.trim(),
        if (targets.isNotEmpty) 'user_ids': targets.toList(),
      });
      final n = (r.data['sent'] as num?)?.toInt() ?? 0;
      _toast('已推送给 $n 人'
          '${(r.data['webhook'] as bool? ?? false) ? '，站外 webhook 同步发送' : ''}');
    } catch (e) {
      _toast(errDetail(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ── UI ──────────────────────────────────────────────────
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
        appBar: AppBar(
            title: const Text('管理'),
            bottom: TabBar(controller: _tab, tabs: const [
              Tab(text: '成员'),
              Tab(text: '邀请'),
              Tab(text: '授权'),
            ])),
        body: _error != null
            ? ListView(children: [
                const SizedBox(height: 80),
                Center(
                    child: Text(_error!,
                        textAlign: TextAlign.center,
                        style:
                            const TextStyle(fontSize: 12, color: Colors.grey))),
                Center(
                    child: TextButton(
                        onPressed: _loadAll, child: const Text('重试'))),
              ])
            : _users == null
                ? const Center(child: CircularProgressIndicator())
                : TabBarView(controller: _tab, children: [
                    _usersTab(cs),
                    _invitesTab(cs),
                    _grantsTab(cs),
                  ]));
  }

  Widget _card({required Widget child}) => Card(
      elevation: 0,
      margin: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(
              color: Theme.of(context).dividerColor, width: 0.5)),
      child: Padding(padding: const EdgeInsets.all(12), child: child));

  // ── 成员 Tab ──
  Widget _usersTab(ColorScheme cs) {
    final users = _users!;
    return ListView(children: [
      for (final u in users)
        _card(
            child: Row(children: [
          CircleAvatar(
              radius: 17,
              backgroundColor: cs.primaryContainer,
              child: Text(u.username.isEmpty
                  ? '?'
                  : u.username[0].toUpperCase(),
                  style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.bold,
                      color: cs.onPrimaryContainer))),
          const SizedBox(width: 10),
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
              Row(children: [
                Flexible(
                    child: Text(u.username,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontWeight: FontWeight.w600))),
                const SizedBox(width: 6),
                _badge(u.role, u.role == 'admin' ? cs.primary : cs.outline),
                if (u.totpEnabled) ...[
                  const SizedBox(width: 4),
                  _badge('2FA', Colors.green),
                ],
                if (!u.isActive) ...[
                  const SizedBox(width: 4),
                  _badge('已停用', cs.error),
                ],
              ]),
              const SizedBox(height: 2),
              Text('加入于 ${u.createdAt} · 配额 ${u.quotaHuman}',
                  style: const TextStyle(fontSize: 11, color: Colors.grey)),
            ])),
          IconButton(
              icon: const Icon(Icons.more_vert),
              onPressed: () => _userSheet(u)),
        ])),
      _card(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
            const Text('🌐 公网访问地址',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
            const SizedBox(height: 2),
            const Text('主/备公网入口（备用隧道同一服务器即可填）；保存后分享卡片和登录页会一并提供。',
                style: TextStyle(fontSize: 11, color: Colors.grey)),
            const SizedBox(height: 8),
            Row(children: [
              Expanded(
                  child: TextField(
                      controller: _pubUrl,
                      decoration: const InputDecoration(
                          labelText: '主隧道',
                          isDense: true,
                          border: OutlineInputBorder(),
                          hintText: '如 https://your-frp-domain.com:xxxxx'))),
              const SizedBox(width: 8),
              FilledButton.tonal(
                  onPressed: _busy ? null : _savePubUrl,
                  child: const Text('保存')),
            ]),
            const SizedBox(height: 8),
            TextField(
                controller: _pubAlt,
                decoration: const InputDecoration(
                    labelText: '备用隧道',
                    isDense: true,
                    border: OutlineInputBorder(),
                    hintText: '如 https://your-frp-domain.com:xxxxx')),
          ])),
      _card(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
            const Text('📮 事件推送（通知 bot）',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
            const SizedBox(height: 2),
            const Text('GET 模板 URL，需含 {title}/{body} 占位符（ntfy/Bark/Server酱）。',
                style: TextStyle(fontSize: 11, color: Colors.grey)),
            const SizedBox(height: 8),
            TextField(
                controller: _hookUrl,
                decoration: const InputDecoration(
                    isDense: true,
                    border: OutlineInputBorder(),
                    hintText: 'https://ntfy.sh/你的主题/{title}')),
            const SizedBox(height: 8),
            Row(children: [
              FilledButton.tonal(
                  onPressed: _busy ? null : _saveHook, child: const Text('保存')),
              const SizedBox(width: 8),
              // controller 输入不触发整页重建：ValueListenableBuilder 跟随
              // 文本实时启用"发送测试"（二次审查修复）
              ValueListenableBuilder<TextEditingValue>(
                  valueListenable: _hookUrl,
                  builder: (_, v, _) => OutlinedButton(
                      onPressed:
                          (_busy || v.text.isEmpty) ? null : _testHook,
                      child: const Text('发送测试'))),
            ]),
            const Divider(height: 20),
            // v0.20 手动广播：填标题点推送即达（站内+站外）
            Row(children: [
              Expanded(
                  child: Text('群发站内通知（成员的消息中心即时可见，'
                      '已配置 webhook 时同步推送到手机）',
                      style: TextStyle(
                          fontSize: 11, color: Colors.grey))),
              FilledButton.icon(
                  onPressed: _busy ? null : _showNotifyDialog,
                  icon: const Icon(Icons.campaign_outlined, size: 18),
                  label: const Text('发送通知')),
            ]),
          ])),
      const SizedBox(height: 24),
    ]);
  }

  Widget _badge(String text, Color color) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(8)),
      child: Text(text, style: TextStyle(fontSize: 10, color: color)));

  // ── 邀请 Tab ──
  Widget _invitesTab(ColorScheme cs) {
    final invites = _invites!;
    return ListView(children: [
      Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: Row(children: [
            Expanded(
                child: FilledButton.icon(
                    onPressed: () => _createInvite('member'),
                    icon: const Icon(Icons.person_add_alt_1, size: 18),
                    label: const Text('邀请成员'))),
            const SizedBox(width: 8),
            Expanded(
                child: OutlinedButton.icon(
                    onPressed: () => _createInvite('guest'),
                    icon: const Icon(Icons.school_outlined, size: 18),
                    label: const Text('邀请教访'))),
          ])),
      for (final i in invites)
        _card(
            child: Row(children: [
          const Icon(Icons.confirmation_number_outlined),
          const SizedBox(width: 10),
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
              SelectableText(i.code,
                  style: const TextStyle(
                      fontFamily: 'monospace',
                      fontWeight: FontWeight.w600)),
              Text('${i.role} · ${i.used ? '已使用' : i.expired ? '已过期' : '有效'}',
                  style: TextStyle(
                      fontSize: 11,
                      color: i.used || i.expired
                          ? Colors.grey
                          : Colors.green)),
            ])),
          IconButton(
              tooltip: '复制',
              icon: const Icon(Icons.copy_outlined, size: 20),
              onPressed: () async {
                await ClipboardSet.setText(i.code);
                _toast('已复制');
              }),
          IconButton(
              tooltip: '删除记录',
              icon: Icon(Icons.close,
                  size: 20, color: cs.error),
              onPressed: () => _del(
                  '/api/admin/invites/${Uri.encodeComponent(i.code)}',
                  okMsg: '已删除')),
        ])),
      if (invites.isEmpty)
        const Padding(
            padding: EdgeInsets.all(40),
            child: Center(child: Text('还没有邀请记录',
                style: TextStyle(color: Colors.grey)))),
      Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Text('成员在登录页使用邀请码自助建号（72 小时有效）。',
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant))),
    ]);
  }

  // ── 授权 Tab ──
  Widget _grantsTab(ColorScheme cs) {
    final members = _users!.where((u) => u.role != 'admin').toList();
    final grants = _grantFilter == -1
        ? _grants!
        : _grants!.where((g) => g.userId == _grantFilter).toList();
    return ListView(children: [
      _card(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
            const Text('➕ 新建授权（可批量）',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
            const SizedBox(height: 8),
            const Text('成员（可多选）', style: TextStyle(fontSize: 12)),
            const SizedBox(height: 4),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final u in members)
                FilterChip(
                    label: Text(u.username, style: const TextStyle(fontSize: 12)),
                    selected: _gUsers.contains(u.id),
                    onSelected: (v) => setState(() =>
                        v ? _gUsers.add(u.id) : _gUsers.remove(u.id))),
              if (members.isEmpty)
                const Text('暂无成员',
                    style: TextStyle(fontSize: 12, color: Colors.grey)),
            ]),
            const SizedBox(height: 8),
            Row(children: [
              const Text('共享一级目录（可多选，子目录自动继承）',
                  style: TextStyle(fontSize: 12)),
              const Spacer(),
              TextButton.icon(
                  onPressed: _loadDirs,
                  icon: const Icon(Icons.refresh, size: 16),
                  label: const Text('刷新目录',
                      style: TextStyle(fontSize: 12))),
            ]),
            const SizedBox(height: 4),
            if (_dirOptions.isEmpty)
              const Text('共享空间还没有目录，点"刷新目录"重试',
                  style: TextStyle(fontSize: 12, color: Colors.grey))
            else
              SizedBox(
                  height: 120,
                  child: SingleChildScrollView(
                      child: Wrap(spacing: 6, runSpacing: 6, children: [
            for (final d in _dirOptions)
              FilterChip(
                  label: ConstrainedBox(
                      constraints: BoxConstraints(
                          maxWidth: MediaQuery.of(context).size.width * 0.62),
                      child: Text('📁 $d',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 11))),
                  selected: _gPaths.contains(d),
                  onSelected: (v) => setState(
                      () => v ? _gPaths.add(d) : _gPaths.remove(d))),
                  ]))),
            const SizedBox(height: 8),
            Row(children: [
              Switch(
                  value: _gWrite,
                  onChanged: (v) => setState(() => _gWrite = v)),
              const Text('可写入（默认只读）', style: TextStyle(fontSize: 13)),
              const Spacer(),
              DropdownButton<int>(
                  value: _gExpiry,
                  items: const [
                    DropdownMenuItem(value: 0, child: Text('永久')),
                    DropdownMenuItem(value: 24, child: Text('1 天')),
                    DropdownMenuItem(value: 168, child: Text('7 天')),
                    DropdownMenuItem(value: 720, child: Text('30 天')),
                  ],
                  onChanged: (v) => setState(() => _gExpiry = v ?? 0)),
            ]),
            const SizedBox(height: 8),
            SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                    onPressed: _busy ? null : _addGrantBatch,
                    icon: const Icon(Icons.done_all, size: 18),
                    label: Text(
                        '保存授权（${_gUsers.length} 人 × ${_gPaths.length} 目录）'))),
          ])),
      Padding(
          padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
          child: Wrap(spacing: 6, children: [
            FilterChip(
                label: const Text('全部成员', style: TextStyle(fontSize: 12)),
                selected: _grantFilter == -1,
                onSelected: (_) => setState(() => _grantFilter = -1)),
            for (final u in members)
              FilterChip(
                  label: Text('${u.username}（${_grants!.where((g) => g.userId == u.id).length}）',
                      style: const TextStyle(fontSize: 12)),
                  selected: _grantFilter == u.id,
                  onSelected: (_) => setState(() => _grantFilter = u.id)),
          ])),
      for (final g in grants)
        _card(
            // v0.20：整卡点按/长按进操作菜单（续期/改读写/撤销），
            // 过期授权红牌一眼可见
            child: InkWell(
                onTap: () => _grantSheet(g),
                child: Row(children: [
          Icon(
              _grantExpired(g) ? Icons.key_off_outlined : Icons.key_outlined,
              color: _grantExpired(g) ? cs.error : null),
          const SizedBox(width: 10),
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
              Row(children: [
                Flexible(
                    child: Text(g.path.split('/').last,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontWeight: FontWeight.w600, fontSize: 13))),
                const SizedBox(width: 6),
                _badge(g.canWrite ? '读写' : '只读',
                    g.canWrite ? cs.primary : cs.outline),
                if (g.expiresAt != null) ...[
                  const SizedBox(width: 4),
                  _badge(_expiryText(g.expiresAt),
                      _grantExpired(g) ? cs.error : Colors.orange),
                ],
              ]),
              Text(
                  '${_memberName(g.userId)} · ${g.path}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11, color: Colors.grey)),
            ])),
          IconButton(
              icon: const Icon(Icons.more_vert, size: 20),
              tooltip: '授权操作',
              onPressed: () => _grantSheet(g)),
        ]))),
      if (grants.isEmpty)
        const Padding(
            padding: EdgeInsets.all(40),
            child: Center(
                child: Text('暂无授权', style: TextStyle(color: Colors.grey)))),
      const SizedBox(height: 24),
    ]);
  }
}

/// 剪贴板薄封装（单测可注入 hook 断言内容）
class ClipboardSet {
  static Future<void> Function(String)? hook;
  static Future<void> setText(String s) async {
    if (hook != null) return hook!(s);
    await Clipboard.setData(ClipboardData(text: s));
  }
}
