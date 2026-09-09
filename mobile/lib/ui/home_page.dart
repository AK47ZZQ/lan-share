// 首页综合面板（0.4.2）：App 打开不再直落照片墙，先给一块"家里的盘还好吗"
// 的概览——备份状态、传输动态、服务端存储、最近上传 + 快捷入口。
// 只读概览加少量动作（立即备份/跳转）；细节操作仍归各职能页。
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show SystemUiOverlayStyle;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../backup/backup_bridge.dart';
import '../backup/backup_round.dart';
import '../data/activity.dart';
import '../data/api/api_client.dart';
import '../data/auto_switch.dart';
import '../data/local/prefs.dart';
import '../state/session.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'activity_page.dart';
import 'backup_settings_page.dart';
import 'invite_redeem_page.dart';
import 'notifications_page.dart';
import 'recent_page.dart';
import 'shares_page.dart';
import 'storage_detail_page.dart';
import 'notes_page.dart';
import 'trash_page.dart';
import 'widgets/auth_image.dart';
import 'widgets/brand.dart';
import 'theme/app_theme.dart';

class HomePage extends ConsumerStatefulWidget {
  final VoidCallback onGoPhotos, onGoFiles, onGoTransfers, onGoMe;
  const HomePage(
      {super.key,
      required this.onGoPhotos,
      required this.onGoFiles,
      required this.onGoTransfers,
      required this.onGoMe});

  @override
  ConsumerState<HomePage> createState() => _HomePageState();
}

class _HomePageState extends ConsumerState<HomePage> {
  bool _backupOn = false;
  int _pending = -1; // -1=待统计（与引擎约定一致）
  int? _pendingPhotos, _pendingVideos; // 分类型待备数（0.7.1，引擎每轮回写）
  int _pendingBytes = 0;
  int? _lastDoneAt;
  int _failed = 0; // 账本失败数（引擎每轮回写）
  int _progressDone = 0, _progressTotal = 0; // 本轮实时进度（引擎逐张回写）
  String? _lastRound;    // 引擎最近一轮结果 JSON（0.7.3 可观测性）
  String? _workerState;  // Worker/队列状态 JSON（排队等待充电/Wi-Fi 也可见）
  int _diskUsed = 0, _diskTotal = 0, _diskFree = 0;
  String _shareSize = '';
  List<_RecentUpload>? _recent;
  List<ActivityItem>? _activity;
  int _notifUnread = 0; // 站内通知未读数（0.8.0，30s 一刷）
  Timer? _tick, _diskTick;

  // ── 首页卡片布局自定义（v0.18）────────────────────────────
  // 长按卡片拖动排序；「管理卡片」面板控制显隐；顺序/显隐存 prefs
  // （本机偏好，不进服务端）。新版本新增的卡片自动补到末尾，下线的
  // 卡片剔除（脏数据自愈）。
  static const _cardIds = [
    'recent', 'activity', 'backup', 'transfers', 'storage', 'quick', 'invite',
  ];
  static const _cardLabels = {
    'recent': '最近上传',
    'activity': '动态',
    'backup': '自动备份',
    'transfers': '传输',
    'storage': '服务端存储',
    'quick': '快捷入口',
    'invite': '邀请码注册',
  };
  List<String> _order = _cardIds;
  Set<String> _hidden = {};
  bool _hintShown = false; // 拖动提示只弹一次

  Future<void> _loadLayout() async {
    final sp = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      final saved = sp.getStringList('home_cards_order');
      final valid = saved?.where(_cardIds.contains).toList() ?? const [];
      final missing = _cardIds.where((c) => !valid.contains(c)).toList();
      _order = [...valid, ...missing];
      _hidden = (sp.getStringList('home_cards_hidden') ?? const []).toSet();
      _hintShown = sp.getBool('home_cards_hint') ?? false;
    });
  }

  Future<void> _persistLayout() async {
    final sp = await SharedPreferences.getInstance();
    await sp.setStringList('home_cards_order', _order);
    await sp.setStringList('home_cards_hidden', _hidden.toList());
  }

  void _onReorder(int oldI, int newI, int visibleCount) {
    if (oldI < 0 || oldI >= visibleCount) return; // 管理入口等不可拖
    if (newI >= visibleCount) newI = visibleCount - 1;
    if (newI == oldI) return;
    setState(() {
      final visible = _order.where((c) => !_hidden.contains(c)).toList();
      final moved = visible.removeAt(oldI);
      visible.insert(newI, moved);
      _order = [...visible, ..._order.where(_hidden.contains)];
    });
    _persistLayout();
  }

  Future<void> _dragHintOnce() async {
    if (_hintShown) return;
    _hintShown = true;
    final sp = await SharedPreferences.getInstance();
    if (sp.getBool('home_cards_hint') ?? false) return;
    await sp.setBool('home_cards_hint', true);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('长按卡片拖动即可排序，底部「管理卡片」可隐藏/恢复'),
        duration: Duration(seconds: 3)));
  }

  /// 管理面板（v0.18）：逐卡显隐开关 + 恢复默认排序
  void _manageCards() {
    final cs = Theme.of(context).colorScheme;
    showModalBottomSheet(
        context: context,
        showDragHandle: true,
        builder: (bctx) => StatefulBuilder(
            builder: (bctx, setSheet) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Padding(
                      padding: const EdgeInsets.fromLTRB(16, 0, 16, 6),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('管理首页卡片',
                                style: TextStyle(
                                    fontSize: 16,
                                    fontWeight: FontWeight.w700)),
                            const SizedBox(height: 4),
                            Text('关闭的卡片随时可从这里重新打开；'
                                '长按卡片拖动可排序。',
                                style: TextStyle(
                                    fontSize: 12,
                                    color: cs.onSurfaceVariant)),
                          ])),
                  Flexible(
                      child: ListView(shrinkWrap: true, children: [
                    for (final id in _order)
                      SwitchListTile(
                          value: !_hidden.contains(id),
                          title: Text(_cardLabels[id] ?? id),
                          onChanged: (on) {
                            setSheet(() {
                              on
                                  ? _hidden.remove(id)
                                  : _hidden.add(id);
                            });
                            setState(() {});
                            _persistLayout();
                          }),
                  ])),
                  Padding(
                      padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
                      child: Row(children: [
                        TextButton.icon(
                            onPressed: () {
                              setSheet(() {
                                _order = List.of(_cardIds);
                                _hidden.clear();
                              });
                              setState(() {});
                              _persistLayout();
                            },
                            icon: const Icon(Icons.restart_alt, size: 18),
                            label: const Text('恢复默认')),
                        const Spacer(),
                        FilledButton(
                            onPressed: () => Navigator.pop(bctx),
                            child: const Text('完成')),
                      ])),
                ]))));
  }

  @override
  void initState() {
    super.initState();
    () async {
      final b = await Prefs.lastAddress();
      if (mounted) setState(() => _base = b);
    }();
    // 自动切换（v0.15）会改地址：徽标与最近上传缩略图必须跟随，
    // 否则切换后整页仍打死地址（红队 P2-4）
    Prefs.addressNotifier.addListener(_onAddressChanged);
    // 自动切换服务（v0.15）：HomePage 在 IndexedStack 常驻=登录态全周期，
    // 在这里启停最稳（幂等；登出时由服务 stop）
    AutoSwitch.I.start();
    _loadLocal();
    _loadDisk();
    _loadRecent();
    _loadActivity();
    _loadLayout();
    // 本地状态（备份开关/待备数/传输动态）轻，2s 一刷保证跨页回来也是新的；
    // 磁盘要服务端 rglob 统计，30s 一刷 + 下拉手动刷
    _tick = Timer.periodic(const Duration(seconds: 2), (_) => _loadLocal());
    // ⚠️ 必须持有并在 dispose 取消：匿名 Timer 会登出后继续空转（泄漏）
    _diskTick = Timer.periodic(const Duration(seconds: 30), (_) {
      // 生命周期闸门（R1-P2-2）：退后台不再打服务端三连请求
      if (WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) return;
      _loadDisk();
      _loadRecent();
      _loadActivity();
    });
  }

  Future<void> _refreshAll() async {
    await _loadDisk();
    await _loadLocal();
    await _loadRecent();
    await _loadActivity();
  }

  void _onAddressChanged() {
    final b = Prefs.addressNotifier.value;
    if (mounted && b.isNotEmpty && b != _base) setState(() => _base = b);
  }

  @override
  void dispose() {
    Prefs.addressNotifier.removeListener(_onAddressChanged);
    _tick?.cancel();
    _diskTick?.cancel();
    AutoSwitch.I.stop(); // HomePage 销毁=登出/关 App：探测定停
    super.dispose();
  }

  Future<void> _loadLocal() async {
    // 生命周期闸门（R1-P2-2）：退后台 2s 轮询只读本地 prefs 开销小但
    // setState 无意义，直接跳过
    if (WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) return;
    final sp = await SharedPreferences.getInstance();
    // 引擎在独立 isolate 写 prefs，不 reload 就永远是首帧缓存（备份卡假死）
    await sp.reload();
    if (!mounted) return;
    // 2s 周期定时器：值全部未变就跳过 setState（R1-P2）——HomePage 常驻
    // IndexedStack，无条件重建等于 App 全生命周期每 2s 重建整页
    final backupOn = sp.getBool('backup_enabled') ?? false;
    final pending = sp.getInt('backup_pending_count') ?? -1;
    final pendingPhotos = sp.getInt('backup_pending_photos');
    final pendingVideos = sp.getInt('backup_pending_videos');
    final pendingBytes = sp.getInt('backup_pending_bytes') ?? 0;
    final lastDoneAt = sp.getInt('backup_last_done_at');
    final failed = sp.getInt('backup_failed_count') ?? 0;
    final progressDone = sp.getInt('backup_progress_done') ?? 0;
    final progressTotal = sp.getInt('backup_progress_total') ?? 0;
    final lastRound = sp.getString('backup_last_round');
    final workerState = sp.getString('backup_worker_state');
    final unchanged = backupOn == _backupOn &&
        pending == _pending &&
        pendingPhotos == _pendingPhotos &&
        pendingVideos == _pendingVideos &&
        pendingBytes == _pendingBytes &&
        lastDoneAt == _lastDoneAt &&
        failed == _failed &&
        progressDone == _progressDone &&
        progressTotal == _progressTotal &&
        lastRound == _lastRound &&
        workerState == _workerState;
    if (unchanged) return;
    setState(() {
      _backupOn = backupOn;
      _pending = pending;
      _pendingPhotos = pendingPhotos;
      _pendingVideos = pendingVideos;
      _pendingBytes = pendingBytes;
      _lastDoneAt = lastDoneAt;
      _failed = failed;
      _progressDone = progressDone;
      _progressTotal = progressTotal;
      _lastRound = lastRound;
      _workerState = workerState;
    });
  }

  Future<void> _loadDisk() async {
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/disk');
      if (!mounted) return;
      setState(() {
        _diskUsed = (r.data['used_bytes'] as num?)?.toInt() ?? 0;
        _diskTotal = (r.data['total_bytes'] as num?)?.toInt() ?? 0;
        _diskFree = (r.data['free_bytes'] as num?)?.toInt() ?? 0;
        _shareSize = r.data['share_size'] as String? ?? '';
      });
    } catch (_) {}
  }

  /// 最近上传横滑条：全库最近修改的文件（跨上传入口，30s 一刷）。
  /// v0.20 扩面：不再只留图片/视频——文档/音频/压缩包等以类型色块显示
  Future<void> _loadRecent() async {
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/recent', queryParameters: {'limit': 100});
      final items = (r.data as List)
          .whereType<Map>()
          .map((e) => _RecentUpload(
              path: (e['path'] as String?) ?? '',
              name: (e['name'] as String?) ?? '',
              ext: ((e['ext'] as String?) ?? '').toLowerCase()))
          .take(12)
          .toList();
      if (!mounted) return;
      setState(() => _recent = items);
    } catch (_) {}
  }

  /// 家里动态（0.6.2）：/api/activity 与 Web 同源，隐私过滤/聚合在服务端
  Future<void> _loadActivity() async {
    // dio() 内部有 await（prefs/secure store）且本方法被 fire-and-forget
    // 调用：包进 try 防未捕获异步异常（二次审查修复）
    try {
      final dio = await ApiClient.I.dio();
      try {
        final r =
            await dio.get('/api/activity', queryParameters: {'limit': 30});
        final items = ActivityItem.listFrom(r.data);
        if (!mounted) return;
        setState(() => _activity = items);
      } catch (_) {}
      // 通知未读数顺带刷新（0.8.0）：铃铛徽标用（复用同一 dio，0.8.1 审查修复）
      try {
        final r2 = await dio.get('/api/notifications');
        if (!mounted) return;
        setState(() =>
            _notifUnread = (r2.data['unread'] as num?)?.toInt() ?? 0);
      } catch (_) {}
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    // 昵称优先（v0.17）：问候语显示昵称，没有昵称回落用户名
    final username = ref.watch(sessionProvider).displayName ?? '';
    final engine = ref.watch(transfersProvider);
    final tasks = engine.tasks;
    final active = tasks.where((t) => t.isActive).length;
    final speed = tasks.fold<int>(0, (s, t) => s + engine.speedOf(t));
    final recent = tasks
        .where((t) => t.status == TransferStatus.done)
        .toList()
        .reversed
        .take(3)
        .toList();
    final cs = Theme.of(context).colorScheme;

    // 首页卡片布局（v0.18）：按 _order 过滤隐藏项组装；数据未就绪的
    // 卡片（最近上传/动态为空）跳过——onReorder 只在这份可见清单内工作
    final layout = <_CardSpec>[];
    for (final id in _order) {
      if (_hidden.contains(id)) continue;
      final w = _cardFor(id, cs,
          tasks: tasks, active: active, speed: speed, recent: recent);
      if (w != null) layout.add(_CardSpec(id, w));
    }

    // 沉浸渐变头（9.6-P2-2 方案 A）：SliverAppBar 展开=品牌渐变头
    // （问候+头像+微数据，从状态栏铺满），滚动收窄为纯色工具条
    final topInset = MediaQuery.of(context).padding.top;
    final session = ref.watch(sessionProvider);
    return AnnotatedRegion<SystemUiOverlayStyle>(
        value: SystemUiOverlayStyle.light,
        child: RefreshIndicator(
        onRefresh: _refreshAll,
        child: CustomScrollView(slivers: [
          // v0.21.3 自绘欢迎头（SliverPersistentHeader 替代 SliverAppBar+
          // FlexibleSpaceBar）：渐变永远铺满当前实际高度——此前 FlexibleSpace
          // 的 background 按展开高绘制、SliverAppBar.backgroundColor 按收起高
          // 露底，两者高度差在下拉回弹/收缩中露出一条纯色色带；自绘后交叉
          // 淡入淡出也完全由 shrinkOffset 驱动，无残影无遮挡
          SliverPersistentHeader(
              pinned: true,
              delegate: _WelcomeHeaderDelegate(
                topInset: topInset,
                greeting: _greeting(username),
                avatar: _headAvatar(session),
                gradient: PaletteScope.of(context).gradient,
              )),
          SliverPadding(
              padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
              sliver: SliverReorderableList(
              onReorderStart: (_) => _dragHintOnce(),
              // v3.41+：onReorderItem 的 newIndex 已自适应移除项，无需 -1 修正
              onReorderItem: (oldI, newI) => _onReorder(oldI, newI, layout.length),
              proxyDecorator: (child, i, anim) => AnimatedBuilder(
                  animation: anim,
                  builder: (_, _) => Material(
                      elevation: 6,
                      color: Colors.transparent,
                      borderRadius: BorderRadius.circular(12),
                      child: child)),
              itemCount: layout.length + 1,
              itemBuilder: (ctx, i) {
                // 管理入口（常驻、不可拖动）：隐藏的卡片从这里加回
                if (i == layout.length) {
                  return KeyedSubtree(
                      key: const ValueKey('__manage__'),
                      child: Center(
                          child: TextButton.icon(
                              style: TextButton.styleFrom(
                                  visualDensity: VisualDensity.compact),
                              onPressed: _manageCards,
                              icon: const Icon(Icons.tune, size: 18),
                              label: const Text('管理卡片'))));
                }
                return ReorderableDelayedDragStartListener(
                    key: ValueKey(layout[i].id),
                    index: i,
                    child: layout[i].widget);
              }))])));
  }

  /// 渐变头右上角头像：有头像走 AuthImage（服务端 /api/auth/avatar），无头像
  /// 取显示名首字；点按进"我的"tab（9.6-P2-2 快捷入口）
  Widget _headAvatar(SessionState session) {
    final name = session.username ?? '';
    final hasAvatar = (session.avatarPath ?? '').isNotEmpty && _base.isNotEmpty;
    return GestureDetector(
        onTap: widget.onGoMe,
        child: CircleAvatar(
            radius: 20,
            backgroundColor: Colors.white24,
            backgroundImage: null,
            child: hasAvatar
                ? ClipOval(
                    child: SizedBox(
                        width: 40,
                        height: 40,
                        child: AuthImage(
                            url: '$_base/api/auth/avatar/'
                                '${Uri.encodeComponent(name)}')))
                : Text(
                    name.isNotEmpty ? name.characters.first.toUpperCase() : '?',
                    style: const TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.w700))));
  }

  /// 卡片 id → 内容。数据未就绪（最近上传/动态为空）返回 null 跳过整卡。
  Widget? _cardFor(String id, ColorScheme cs,
      {required List<TransferTask> tasks, required int active,
      required int speed, required List<TransferTask> recent}) {
    switch (id) {
      case 'recent':
        if (_recent == null || _recent!.isEmpty) return null;
        return _card(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                children: [
              _cardTitle('最近上传',
                  icon: Icons.history,
                  trailing: TextButton(
                      style: TextButton.styleFrom(
                          visualDensity: VisualDensity.compact,
                          padding: const EdgeInsets.symmetric(horizontal: 4)),
                      onPressed: () => Navigator.push(context,
                          MaterialPageRoute(
                              builder: (_) => const RecentPage())),
                      child: const Text('全部 ›',
                          style: TextStyle(fontSize: 12)))),
              const SizedBox(height: 6),
              SizedBox(
                  height: 92,
                  child: ListView(scrollDirection: Axis.horizontal,
                      children: [
                        for (final it in _recent!)
                          GestureDetector(
                              onTap: widget.onGoPhotos,
                              child: Padding(
                                  padding: const EdgeInsets.only(right: 6),
                                  child: Stack(children: [
                                    ClipRRect(
                                        borderRadius:
                                            BorderRadius.circular(8),
                                        child: SizedBox(
                                            width: 92,
                                            height: 92,
                                            // 图片/视频走缩略图；其余类型
                                            // 用品牌类型色块+图标（v0.20）
                                            child: it.isImage || it.isVideo
                                                ? AuthImage(
                                                    url: '$_base/thumb/'
                                                        '${it.path.split("/").map(Uri.encodeComponent).join("/")}')
                                                : Container(
                                                    color: cs.surfaceContainerHighest,
                                                    child: Column(
                                                        mainAxisAlignment:
                                                            MainAxisAlignment
                                                                .center,
                                                        children: [
                                                          FileTypeTile(
                                                              ext: it.ext,
                                                              icon: it.typeIcon,
                                                              size: 40),
                                                          const SizedBox(
                                                              height: 4),
                                                          Text(
                                                              it.ext.isEmpty
                                                                  ? '文件'
                                                                  : it.ext
                                                                      .substring(1)
                                                                      .toUpperCase(),
                                                              maxLines: 1,
                                                              overflow: TextOverflow
                                                                  .ellipsis,
                                                              style: const TextStyle(
                                                                  fontSize: 10,
                                                                  color: Colors
                                                                      .grey)),
                                                        ])))),
                                    if (it.isVideo)
                                      const Positioned(
                                          right: 4,
                                          bottom: 4,
                                          child: Icon(Icons.play_circle_fill,
                                              size: 18,
                                              color: Colors.white70)),
                                  ]))),
                      ])),
            ]));
      case 'activity':
        if (_activity == null || _activity!.isEmpty) return null;
        return _card(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                children: [
              _cardTitle('动态',
                  icon: Icons.newspaper_outlined,
                  trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                    // 站内通知（0.8.0）：定向分享被下载/新成员等，带未读徽标
                    Stack(children: [
                      IconButton(
                          visualDensity: VisualDensity.compact,
                          icon: const Icon(Icons.notifications_outlined,
                              size: 20),
                          onPressed: () async {
                            await Navigator.push(
                                context,
                                MaterialPageRoute(
                                    builder: (_) =>
                                        const NotificationsPage()));
                            _loadActivity();
                          }),
                      if (_notifUnread > 0)
                        Positioned(
                            right: 6,
                            top: 6,
                            child: Container(
                                padding: const EdgeInsets.all(3),
                                decoration: const BoxDecoration(
                                    color: Colors.red,
                                    shape: BoxShape.circle),
                                child: Text(
                                    _notifUnread > 99
                                        ? '99+'
                                        : '$_notifUnread',
                                    style: const TextStyle(
                                        fontSize: 9,
                                        color: Colors.white)))),
                    ]),
                    TextButton(
                        style: TextButton.styleFrom(
                            visualDensity: VisualDensity.compact,
                            padding:
                                const EdgeInsets.symmetric(horizontal: 4)),
                        onPressed: () => Navigator.push(
                            context,
                            MaterialPageRoute(
                                builder: (_) => const ActivityPage())),
                        child: const Text('全部 ›',
                            style: TextStyle(fontSize: 12))),
                  ])),
              const SizedBox(height: 2),
              for (final n in _activity!.take(5))
                Padding(
                    padding: const EdgeInsets.symmetric(vertical: 3),
                    child: Row(children: [
                      Icon(_activityIcon(n.action),
                          size: 15, color: cs.onSurfaceVariant),
                      const SizedBox(width: 8),
                      Expanded(
                          child: Text(activityText(n),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontSize: 13))),
                      const SizedBox(width: 6),
                      Text(activityRelTime(n.ts),
                          style: TextStyle(
                              fontSize: 11, color: cs.onSurfaceVariant)),
                    ])),
            ]));
      case 'backup':
        // 整卡可点（改进建议 9.6-P0-9）：点卡片主体进备份管理页；
        // 卡内"立即备份/备份设置"等按钮仍各自响应
        return _card(
            child: InkWell(
                onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                        builder: (_) => const BackupSettingsPage())),
                borderRadius: BorderRadius.circular(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                  _cardTitle('自动备份',
                      icon: Icons.backup_outlined,
                      trailing: _badge(_backupOn ? '已开启' : '已关闭',
                          _backupOn ? Colors.green : cs.onSurfaceVariant)),
                  const SizedBox(height: 6),
                  Text(_backupText(),
                      style: const TextStyle(fontSize: 13)),
                  // 本轮实时进度（0.6.2）：引擎逐张回写，2s 轮询可见
                  if (_progressTotal > 0) ...[
                    const SizedBox(height: 4),
                    Text('备份中 $_progressDone/$_progressTotal 项…',
                        style: TextStyle(fontSize: 12, color: cs.primary)),
                  ] else ...[
                    // 最近一轮/排队状态（0.7.3）：排队等充电/Wi-Fi、失败原因
                    // 不再是黑盒。排队提示优先（它说明"为什么没在跑"）
                    Builder(builder: (_) {
                      final v = queueView(_workerState) ??
                          roundView(_lastRound);
                      if (v == null) return const SizedBox.shrink();
                      return Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Text(v.text,
                              style: TextStyle(
                                  fontSize: 12,
                                  color: v.warn
                                      ? Colors.orange
                                      : cs.onSurfaceVariant)));
                    }),
                  ],
                  // 失败提示（0.6.2）：此前失败只能进账本页才知道
                  if (_failed > 0) ...[
                    const SizedBox(height: 4),
                    InkWell(
                        onTap: () => Navigator.push(
                            context,
                            MaterialPageRoute(
                                builder: (_) => const BackupDetailsPage())),
                        child: Text(
                            '⚠️ 有 $_failed 项备份失败，点此查看详情',
                            style: TextStyle(fontSize: 12, color: cs.error))),
                  ],
                  // 空间预警（0.6.0）：待备份量超过服务端剩余空间时提前提示
                  if (_pendingBytes > 0 && _diskFree > 0 && _pendingBytes > _diskFree) ...[
                    const SizedBox(height: 6),
                    Text(
                        '⚠️ 待备份 ${humanSize(_pendingBytes)}，'
                        '服务端仅剩 ${humanSize(_diskFree)}，请先清理空间',
                        style: TextStyle(
                            fontSize: 12, color: cs.error)),
                  ],
                  const SizedBox(height: 10),
                  Row(children: [
                    FilledButton.tonalIcon(
                        onPressed: _backupOn
                            ? () {
                                BackupBridge.runOnce();
                                ScaffoldMessenger.of(context).showSnackBar(
                                    const SnackBar(
                                        content: Text('已触发一轮备份'),
                                        duration: Duration(seconds: 2)));
                              }
                            : null,
                        icon: const Icon(Icons.play_arrow, size: 18),
                        label: const Text('立即备份')),
                    const SizedBox(width: 8),
                    TextButton(
                        onPressed: () => Navigator.push(
                            context,
                            MaterialPageRoute(
                                builder: (_) =>
                                    const BackupSettingsPage())),
                        child: const Text('备份设置')),
                  ]),
                ])));
      case 'transfers':
        return _card(
            child: InkWell(
                onTap: widget.onGoTransfers,
                borderRadius: BorderRadius.circular(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _cardTitle('传输',
                          icon: Icons.swap_vert_circle_outlined,
                          trailing: Text('全部 ›',
                              style:
                                  TextStyle(fontSize: 12, color: cs.onSurfaceVariant))),
                      const SizedBox(height: 6),
                      Text(
                          active > 0
                              ? '$active 个任务进行中 · ${_speedText(speed)}'
                              : tasks.isEmpty
                                  ? '暂无传输任务'
                                  : '队列空闲 · 共 ${tasks.length} 个任务记录',
                          style: const TextStyle(fontSize: 13)),
                      if (recent.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        for (final t in recent)
                          Padding(
                              padding: const EdgeInsets.symmetric(vertical: 2),
                              child: Row(children: [
                                Icon(
                                    t.kind == TransferKind.upload
                                        ? Icons.upload_outlined
                                        : Icons.download_outlined,
                                    size: 15,
                                    color: cs.onSurfaceVariant),
                                const SizedBox(width: 6),
                                Expanded(
                                    child: Text(t.name,
                                        overflow: TextOverflow.ellipsis,
                                        style: const TextStyle(fontSize: 12))),
                              ])),
                      ],
                    ])));
      case 'storage':
        return _card(
            child: InkWell(
                onTap: () => Navigator.push(context,
                    MaterialPageRoute(builder: (_) => const StorageDetailPage())),
                borderRadius: BorderRadius.circular(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                  _cardTitle('服务端存储',
                      icon: Icons.dns_outlined,
                      trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                        Text('详情 ›',
                            style: TextStyle(
                                fontSize: 12, color: cs.onSurfaceVariant)),
                      ])),
                  const SizedBox(height: 10),
                  LinearProgressIndicator(
                      value: _diskTotal > 0 && _diskUsed <= _diskTotal
                          ? _diskUsed / _diskTotal
                          : null,
                      minHeight: 6,
                      borderRadius: BorderRadius.circular(3)),
                  const SizedBox(height: 6),
                  Text(
                      _diskTotal > 0
                          ? '已用 ${humanSize(_diskUsed)} / ${humanSize(_diskTotal)}'
                              '${_shareSize.isEmpty ? "" : " · 文件库 $_shareSize"}'
                              '（点按看构成）'
                          : '磁盘信息加载中…',
                      style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                ])));
      case 'quick':
        return _card(
            child: Row(mainAxisAlignment: MainAxisAlignment.spaceAround,
                children: [
                  _quick(Icons.photo_outlined, '照片', widget.onGoPhotos),
                  _quick(Icons.folder_outlined, '文件', widget.onGoFiles),
                  _quick(Icons.edit_note_outlined, '笔记', () {
                    Navigator.push(context,
                        MaterialPageRoute(builder: (_) => const NotesPage()));
                  }),
                  _quick(Icons.delete_outline, '回收站', () {
                    Navigator.push(context,
                        MaterialPageRoute(builder: (_) => const TrashPage()));
                  }),
                  _quick(Icons.link_outlined, '我的分享', () {
                    Navigator.push(context,
                        MaterialPageRoute(builder: (_) => const SharesPage()));
                  }),
                ]));
      case 'invite':
        // v0.15.2：邀请码注册入口——新成员凭码自助建号，管理端建码
        // 在管理面板 → 邀请码（服务端 /api/invites/redeem 匿名可用）
        return _card(
            child: InkWell(
                onTap: () => Navigator.push(context,
                    MaterialPageRoute(builder: (_) => const InviteRedeemPage())),
                borderRadius: BorderRadius.circular(12),
                child: Row(children: [
                  Icon(Icons.how_to_reg_outlined,
                      size: 22, color: cs.primary),
                  const SizedBox(width: 12),
                  Expanded(
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                        const Text('邀请码注册',
                            style: TextStyle(
                                fontSize: 14, fontWeight: FontWeight.w600)),
                        const SizedBox(height: 2),
                        Text('新成员凭邀请码自助建号，无需管理员代录',
                            style: TextStyle(
                                fontSize: 12, color: cs.onSurfaceVariant)),
                      ])),
                  Text('去注册 ›',
                      style:
                          TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                ])));
    }
    return null;
  }

  String _base = '';

  String _greeting(String username) {
    final h = DateTime.now().hour;
    final hi = h < 6
        ? '夜深了'
        : h < 12
            ? '早上好'
            : h < 14
                ? '中午好'
                : h < 18
                    ? '下午好'
                    : '晚上好';
    return username.isEmpty ? '$hi 👋' : '$hi，$username';
  }

  String _backupText() {
    if (!_backupOn) return '自动备份未开启，去设置页打开后新照片会自动进服务器';
    // 0.7.1 起分照片/视频显示待备数（引擎按账本现状回写，感知排除相册）
    final String p;
    if (_pending < 0) {
      p = '待统计';
    } else if (_pendingPhotos == null) {
      p = '$_pending 项待备'; // 升级前旧引擎没回写过分类型，退回总数
    } else if (_pending == 0) {
      p = '暂无待备份项';
    } else {
      p = '待备 照片 $_pendingPhotos · 视频 $_pendingVideos';
    }
    final last = _lastDoneAt == null
        ? ''
        : ' · 上次 ${_ago(_lastDoneAt!)}完成';
    return p + last;
  }

  /// 动态动作 → 图标（与动态页 _iconFor 同表；首页横排用小号 outline）
  IconData _activityIcon(String action) => switch (action) {
        'upload' || 'upload_instant' => Icons.upload_outlined,
        'mkdir' => Icons.create_new_folder_outlined,
        'share' => Icons.link,
        'share_target' => Icons.alternate_email,
        'share_download' => Icons.download_outlined,
        'invite_redeemed' => Icons.waving_hand_outlined,
        'trash' => Icons.delete_outline,
        'restore' => Icons.restore_from_trash_outlined,
        'move' => Icons.drive_file_move_outlined,
        'rename' => Icons.edit_outlined,
        _ => Icons.circle_outlined,
      };

  String _ago(int atMs) {
    final d = DateTime.now().millisecondsSinceEpoch - atMs;
    if (d < 60_000) return '刚刚';
    if (d < 3_600_000) return '${d ~/ 60_000} 分钟前';
    if (d < 86_400_000) return '${d ~/ 3_600_000} 小时前';
    return '${d ~/ 86_400_000} 天前';
  }

  String _speedText(int bytesPerSec) =>
      bytesPerSec <= 0 ? '几乎静止' : '${humanSize(bytesPerSec)}/s';

  Widget _badge(String text, Color color) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(10)),
      child: Text(text, style: TextStyle(fontSize: 11, color: color)));

  Widget _cardTitle(String title, {IconData? icon, Widget? trailing}) => Row(
      children: [
        if (icon != null) ...[
          Icon(icon, size: 16, color: Theme.of(context).colorScheme.primary),
          const SizedBox(width: 6),
        ],
        Text(title,
            style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700)),
        const Spacer(),
        ?trailing,
      ]);

  // 0.8.4：圆角/描边由 CardTheme 统一（Brand.cardRadius + 低对比描边）
  Widget _card({required Widget child}) => Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(padding: const EdgeInsets.all(14), child: child));

  Widget _quick(IconData icon, String label, VoidCallback onTap) => InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(14),
      child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Container(
                width: 46,
                height: 46,
                decoration: BoxDecoration(
                    color: Theme.of(context)
                        .colorScheme
                        .primary
                        .withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(14)),
                child: Icon(icon,
                    size: 22,
                    color: Theme.of(context).colorScheme.primary)),
            const SizedBox(height: 5),
            Text(label, style: const TextStyle(fontSize: 12)),
          ])));
}


class _CardSpec {
  final String id;
  final Widget widget;
  const _CardSpec(this.id, this.widget);
}

class _RecentUpload {
  final String path;
  final String name;
  final String ext;
  _RecentUpload({required this.path, required this.name, required this.ext});

  static const _videoExts = {
    '.mp4', '.mov', '.m4v', '.3gp', '.webm', '.avi', '.mkv'
  };
  static const _imageExts = {
    '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.heic', '.heif', '.gif'
  };

  bool get isVideo => _videoExts.contains(ext);
  bool get isImage => _imageExts.contains(ext);

  /// 非媒体文件的类型图标（v0.20：最近上传条扩面——文档/压缩包/音频等
  /// 也进横滑条，用类型色块替代缩略图）
  IconData get typeIcon {
    switch (ext) {
      case '.mp3':
      case '.m4a':
      case '.flac':
      case '.wav':
      case '.aac':
      case '.ogg':
      case '.opus':
        return Icons.music_note;
      case '.pdf':
        return Icons.picture_as_pdf;
      case '.xls':
      case '.xlsx':
      case '.csv':
        return Icons.table_chart;
      case '.zip':
      case '.7z':
      case '.rar':
        return Icons.folder_zip;
      case '.md':
      case '.markdown':
      case '.txt':
      case '.html':
      case '.htm':
        return Icons.edit_note;
      case '.apk':
        return Icons.android;
      default:
        return Icons.insert_drive_file;
    }
  }
}


/// 首页欢迎头（v0.21.3 自绘 delegate）：渐变铺满当前实际高度——
/// 收缩/回弹/过滚任何状态都不露底色；问候行随折叠淡出、收起标题淡入。
class _WelcomeHeaderDelegate extends SliverPersistentHeaderDelegate {
  final double topInset;
  final String greeting;
  final Widget avatar;
  final List<Color> gradient;

  _WelcomeHeaderDelegate({
    required this.topInset,
    required this.greeting,
    required this.avatar,
    required this.gradient,
  });

  @override
  double get minExtent => topInset + 48;

  @override
  double get maxExtent => topInset + 78;

  @override
  Widget build(BuildContext context, double shrinkOffset, bool overlapsContent) {
    // t=0 完全展开（问候可见）；t=1 完全收起（标题可见）
    final t = (shrinkOffset / (maxExtent - minExtent)).clamp(0.0, 1.0);
    return Stack(children: [
      // 渐变铺满当前实际高度（overscroll 拉伸时 extent 变大也照样填满）
      Positioned.fill(
          child: DecoratedBox(
              decoration: BoxDecoration(
                  gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: gradient)))),
      // 问候行 + 头像：随折叠淡出（8% 黑罩提对比，与 BrandHeader 同款）
      Positioned(
          left: 0,
          right: 0,
          bottom: 0,
          child: Opacity(
              opacity: 1 - t,
              child: Container(
                  padding: EdgeInsets.fromLTRB(20, 18 + topInset, 20, 16),
                  foregroundDecoration: BoxDecoration(
                      color: Colors.black.withValues(alpha: 0.08 * (1 - t))),
                  child: Row(children: [
                    Expanded(
                        child: Text(greeting,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                                color: Colors.white,
                                fontSize: 20,
                                fontWeight: FontWeight.w700))),
                    avatar,
                  ])))),
      // 收起态标题：随折叠淡入（48dp 工具条内垂直居中）
      Positioned(
          left: 16,
          right: 16,
          top: topInset + 12,
          child: Opacity(
              opacity: t,
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(Brand.cloud, size: 18, color: Colors.white),
                const SizedBox(width: 6),
                const Text('LAN Share',
                    style: TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.w700)),
              ]))),
    ]);
  }

  @override
  bool shouldRebuild(_WelcomeHeaderDelegate oldDelegate) =>
      oldDelegate.greeting != greeting ||
      oldDelegate.topInset != topInset ||
      oldDelegate.avatar != avatar;
}
