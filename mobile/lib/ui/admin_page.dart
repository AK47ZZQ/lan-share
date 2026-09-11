// 管理面板（0.8.0 对齐 Web AdminView）：成员 / 邀请 / 授权三 Tab +
// 公网地址 + 事件推送（webhook）。仅 admin 可见入口（我的页）。
// 视角预览（web setPreviewAs）暂不迁移：App 全局 dio 注入 header 改造大，
// 记录在 APP-DESIGN §9 遗留。
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/admin_api.dart';
import '../ui/widgets/prompt_dialog.dart';
import '../data/api/errors.dart';
import '../state/session.dart';
import 'theme/design_tokens.dart';
import 'widgets/kit.dart';

class AdminPage extends ConsumerStatefulWidget {
  const AdminPage({super.key});
  @override
  ConsumerState<AdminPage> createState() => _AdminPageState();
}

class _AdminPageState extends ConsumerState<AdminPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tab =
      TabController(length: 3, vsync: this);
  List<AdminUser>? _users;
  List<AdminInvite>? _invites;
  List<AdminGrant>? _grants;
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
      // 加载顺序保持原样：五个端点顺序拉取（users → invites → grants
      // → 公网入口 → webhook），任一失败整体进错误态可重试
      final users = await AdminApi.users();
      final invites = await AdminApi.invites();
      final grants = await AdminApi.grants();
      final pub = await AdminApi.publicUrl();
      final hookUrl = await AdminApi.webhookUrl();
      if (!mounted) return;
      setState(() {
        _users = users;
        _invites = invites;
        _grants = grants;
        _pubUrl.text = pub.base;
        _pubAlt.text = pub.alt;
        _hookUrl.text = hookUrl;
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
      out.addAll(await AdminApi.sharedTopDirs());
    } catch (_) {/* 无权目录跳过 */}
    if (!mounted) return;
    setState(() => _dirOptions = out..sort());
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
          orElse: () => AdminUser.fromJson({
                'id': uid,
                'username': '已删除(#$uid)',
                'role': 'member',
                'quota_human': '',
                'created_at': '',
              })).username ??
      '已删除(#$uid)';

  // ── 成员操作 ─────────────────────────────────────────────
  Future<void> _userSheet(AdminUser u) async {
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
                    if (role != null) {
                      await _run(() => AdminApi.setUserRole(u.id, role),
                          okMsg: '已更新');
                    }
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
                          await _run(() => AdminApi.disableTotp(u.id),
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
                      showToast('请输入正数 GB，或留空提交表示不限');
                      return;
                    }
                    await _run(
                        () => AdminApi.setUserQuota(
                            u.id, gb.isEmpty || n == 0 ? 0 : (n! * (1 << 30)).round()),
                        okMsg: '已更新');
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
                      showToast('密码至少 8 位（留空提交=清除）');
                      return;
                    }
                    await _run(() => AdminApi.setWebdavPassword(u.id, v),
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
                    await _run(() => AdminApi.setUserActive(u.id, !u.isActive),
                        okMsg: '已更新');
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
                      await _run(() => AdminApi.deleteUser(u.id),
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
  Future<void> _credFlow(AdminUser u) async {
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
      await _run(() => AdminApi.setUsername(u.id, newName),
          okMsg: '用户名已改为 $newName');
    } else if (action == 'reset') {
      final newPwd = await _prompt('重置密码 — ${u.username}', '新密码（至少 8 位）',
          obscure: true);
      if (newPwd == null) return;
      // 与改自己密码/WebDAV 密码同款下限（R1-P3）：短密码提交到服务端只会
      // 得到一条 400，当场反馈更直观
      if (newPwd.length < 8) {
        showToast('密码至少 8 位');
        return;
      }
      await _run(() => AdminApi.setPassword(u.id, newPwd),
          okMsg: '密码已重置（其两步验证同步解除、设备 Token 全部吊销）');
    }
  }

  /// "执行 → 提示 → 重载"统一薄壳：请求本体已下沉 AdminApi，这里只管
  /// 成功 toast（可选）与 _loadAll，失败统一 errDetail 弹 toast
  Future<void> _run(Future<void> Function() op, {String? okMsg}) async {
    try {
      await op();
      if (okMsg != null) showToast(okMsg);
      _loadAll();
    } catch (e) {
      showToast(errDetail(e));
    }
  }

  // ── 邀请 ────────────────────────────────────────────────
  Future<void> _createInvite(String role) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final code = await AdminApi.createInvite(role);
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
                    const SizedBox(height: Gap.xs),
                    const Text('72 小时内有效。新成员在登录页"邀请码建号"使用。',
                        style: TextStyle(fontSize: 12)),
                  ]),
                  actions: [
                    TextButton(
                        onPressed: () async {
                          await ClipboardSet.setText(code);
                          showToast('已复制');
                        },
                        child: const Text('复制')),
                    FilledButton(
                        onPressed: () => Navigator.pop(d),
                        child: const Text('关闭')),
                  ]));
      _loadAll();
    } catch (e) {
      showToast(errDetail(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ── 授权 ────────────────────────────────────────────────
  Future<void> _addGrantBatch() async {
    if (_busy) return;
    if (_gUsers.isEmpty || _gPaths.isEmpty) {
      showToast('先选成员和目录');
      return;
    }
    setState(() => _busy = true);
    var ok = 0, fail = 0;
    try {
      for (final uid in _gUsers) {
        for (final p in _gPaths) {
          try {
            await AdminApi.upsertGrant(
                userId: uid,
                path: p,
                canWrite: _gWrite,
                expiresHours: _gExpiry > 0 ? _gExpiry : null);
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
    showToast(fail == 0
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

  bool _grantExpired(AdminGrant g) =>
      g.expiresAt != null &&
      g.expiresAt! * 1000 < DateTime.now().millisecondsSinceEpoch;

  /// 修改授权（v0.20）：upsert 语义。续期=原剩余+延长时间；永久授权
  /// 续期无意义直接提示；读写切换保留原有效期
  Future<void> _updateGrant(AdminGrant g,
      {required bool canWrite, int extendHours = 0}) async {
    if (extendHours > 0 && g.expiresAt == null) {
      showToast('该授权已是永久，无需延长');
      return;
    }
    int? hours;
    if (g.expiresAt != null) {
      final left = g.expiresAt! - DateTime.now().millisecondsSinceEpoch ~/ 1000;
      if (left <= 0) {
        showToast('授权已过期，请删除后重新授权');
        return;
      }
      hours = (left / 3600).ceil() + extendHours;
    }
    try {
      await AdminApi.upsertGrant(
          userId: g.userId,
          path: g.path,
          canWrite: canWrite,
          expiresHours: hours);
      showToast(extendHours > 0
          ? '已延长 ${_memberName(g.userId)} 的「${g.path.split('/').last}」'
          : '已改为${canWrite ? '读写' : '只读'}');
      _loadAll();
    } catch (e) {
      showToast(errDetail(e));
    }
  }

  /// 单条授权操作菜单（v0.20）：续期/改读写/撤销收进一处，不再只有撤销
  void _grantSheet(AdminGrant g) {
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
                    _run(() => AdminApi.deleteGrant(g.id), okMsg: '已撤销');
                  }),
              const SizedBox(height: Gap.xs),
            ])));
  }

  // ── 设置卡 ──────────────────────────────────────────────
  Future<void> _savePubUrl() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await AdminApi.savePublicUrl(_pubUrl.text.trim(), _pubAlt.text.trim());
      showToast('公网地址已保存');
    } catch (e) {
      showToast(errDetail(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _saveHook() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await AdminApi.saveWebhook(_hookUrl.text.trim());
      showToast('推送地址已保存');
    } catch (e) {
      showToast(errDetail(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _testHook() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await AdminApi.testWebhook();
      showToast('测试消息已发送，去手机上看看');
    } catch (e) {
      showToast(errDetail(e));
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
                      const SizedBox(height: Gap.xs),
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
                              icon: const Icon(Icons.send_outlined, size: IconSizes.sm),
                              label: const Text('推送'))),
                    ])));
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      final r = await AdminApi.notify(
          title: titleCtrl.text.trim(),
          body: bodyCtrl.text.trim(),
          userIds: targets.isEmpty ? null : targets.toList());
      showToast('已推送给 ${r.sent} 人'
          '${r.webhook ? '，站外 webhook 同步发送' : ''}');
    } catch (e) {
      showToast(errDetail(e));
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
      margin: const EdgeInsets.fromLTRB(Gap.sm, Gap.xs, Gap.sm, Gap.xs),
      shape: RoundedRectangleBorder(
          borderRadius: Radii.bControl,
          side: BorderSide(
              color: Theme.of(context).dividerColor, width: 0.5)),
      child: Padding(padding: const EdgeInsets.all(Gap.sm), child: child));

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
                  const SizedBox(width: Gap.xxs),
                  _badge('2FA', Colors.green),
                ],
                if (!u.isActive) ...[
                  const SizedBox(width: Gap.xxs),
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
            const SizedBox(height: Gap.xs),
            Row(children: [
              Expanded(
                  child: TextField(
                      controller: _pubUrl,
                      decoration: const InputDecoration(
                          labelText: '主隧道',
                          isDense: true,
                          border: OutlineInputBorder(),
                          hintText: '如 https://your-frp-domain.com:xxxxx'))),
              const SizedBox(width: Gap.xs),
              FilledButton.tonal(
                  onPressed: _busy ? null : _savePubUrl,
                  child: const Text('保存')),
            ]),
            const SizedBox(height: Gap.xs),
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
            const SizedBox(height: Gap.xs),
            TextField(
                controller: _hookUrl,
                decoration: const InputDecoration(
                    isDense: true,
                    border: OutlineInputBorder(),
                    hintText: 'https://ntfy.sh/你的主题/{title}')),
            const SizedBox(height: Gap.xs),
            Row(children: [
              FilledButton.tonal(
                  onPressed: _busy ? null : _saveHook, child: const Text('保存')),
              const SizedBox(width: Gap.xs),
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
      const SizedBox(height: Gap.xl),
    ]);
  }

  Widget _badge(String text, Color color) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: Radii.bXs),
      child: Text(text, style: TextStyle(fontSize: 10, color: color)));

  // ── 邀请 Tab ──
  Widget _invitesTab(ColorScheme cs) {
    final invites = _invites!;
    return ListView(children: [
      Padding(
          padding: const EdgeInsets.fromLTRB(Gap.sm, Gap.xs, Gap.sm, 0),
          child: Row(children: [
            Expanded(
                child: FilledButton.icon(
                    onPressed: () => _createInvite('member'),
                    icon: const Icon(Icons.person_add_alt_1, size: 18),
                    label: const Text('邀请成员'))),
            const SizedBox(width: Gap.xs),
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
              icon: const Icon(Icons.copy_outlined, size: IconSizes.md),
              onPressed: () async {
                await ClipboardSet.setText(i.code);
                showToast('已复制');
              }),
          IconButton(
              tooltip: '删除记录',
              icon: Icon(Icons.close,
                  size: IconSizes.md, color: cs.error),
              onPressed: () =>
                  _run(() => AdminApi.deleteInvite(i.code), okMsg: '已删除')),
        ])),
      if (invites.isEmpty)
        const Padding(
            padding: EdgeInsets.all(40),
            child: Center(child: Text('还没有邀请记录',
                style: TextStyle(color: Colors.grey)))),
      Padding(
          padding: const EdgeInsets.symmetric(horizontal: Gap.md),
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
            const SizedBox(height: Gap.xs),
            const Text('成员（可多选）', style: TextStyle(fontSize: 12)),
            const SizedBox(height: Gap.xxs),
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
            const SizedBox(height: Gap.xs),
            Row(children: [
              const Text('共享一级目录（可多选，子目录自动继承）',
                  style: TextStyle(fontSize: 12)),
              const Spacer(),
              TextButton.icon(
                  onPressed: _loadDirs,
                  icon: const Icon(Icons.refresh, size: IconSizes.sm),
                  label: const Text('刷新目录',
                      style: TextStyle(fontSize: 12))),
            ]),
            const SizedBox(height: Gap.xxs),
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
            const SizedBox(height: Gap.xs),
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
            const SizedBox(height: Gap.xs),
            SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                    onPressed: _busy ? null : _addGrantBatch,
                    icon: const Icon(Icons.done_all, size: 18),
                    label: Text(
                        '保存授权（${_gUsers.length} 人 × ${_gPaths.length} 目录）'))),
          ])),
      Padding(
          padding: const EdgeInsets.fromLTRB(Gap.sm, Gap.xxs, Gap.sm, 0),
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
                  const SizedBox(width: Gap.xxs),
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
              icon: const Icon(Icons.more_vert, size: IconSizes.md),
              tooltip: '授权操作',
              onPressed: () => _grantSheet(g)),
        ]))),
      if (grants.isEmpty)
        const Padding(
            padding: EdgeInsets.all(40),
            child: Center(
                child: Text('暂无授权', style: TextStyle(color: Colors.grey)))),
      const SizedBox(height: Gap.xl),
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
