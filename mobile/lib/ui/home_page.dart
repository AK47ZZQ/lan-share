// 首页综合面板（0.4.2）：App 打开不再直落照片墙，先给一块"家里的盘还好吗"
// 的概览——备份状态、传输动态、服务端存储、最近上传 + 快捷入口。
// 只读概览加少量动作（立即备份/跳转）；细节操作仍归各职能页。
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../backup/backup_bridge.dart';
import '../backup/backup_round.dart';
import '../data/activity.dart';
import '../data/api/api_client.dart';
import '../data/auto_switch.dart';
import '../data/local/prefs.dart';
import '../data/net.dart';
import '../state/session.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'activity_page.dart';
import 'backup_settings_page.dart';
import 'invite_redeem_page.dart';
import 'notifications_page.dart';
import 'recent_page.dart';
import 'storage_detail_page.dart';
import 'trash_page.dart';
import 'widgets/auth_image.dart';
import 'widgets/brand.dart';

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
    // 本地状态（备份开关/待备数/传输动态）轻，2s 一刷保证跨页回来也是新的；
    // 磁盘要服务端 rglob 统计，30s 一刷 + 下拉手动刷
    _tick = Timer.periodic(const Duration(seconds: 2), (_) => _loadLocal());
    // ⚠️ 必须持有并在 dispose 取消：匿名 Timer 会登出后继续空转（泄漏）
    _diskTick = Timer.periodic(const Duration(seconds: 30), (_) {
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

  /// 最近上传横滑条：全库最近修改的媒体文件（跨上传入口，30s 一刷）
  Future<void> _loadRecent() async {
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/recent', queryParameters: {'limit': 100});
      const mediaExts = {
        '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.heic', '.heif',
        '.mp4', '.mov', '.m4v', '.3gp', '.webm', '.avi', '.mkv',
      };
      final items = (r.data as List)
          .whereType<Map>()
          .map((e) => _RecentUpload(
              path: (e['path'] as String?) ?? '',
              name: (e['name'] as String?) ?? '',
              ext: ((e['ext'] as String?) ?? '').toLowerCase()))
          .where((e) => mediaExts.contains(e.ext))
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
    final username = ref.watch(sessionProvider).username ?? '';
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

    return RefreshIndicator(
        onRefresh: _refreshAll,
        child: ListView(padding: EdgeInsets.zero, children: [
          // 0.8.4 品牌头：渐变底 + 云朵 + 问候。
          // 0.8.12 起铺满全宽（去 ListView 侧边距）并延伸进状态栏
          // （topInset=状态栏高度，内容从状态栏下方开始，不重叠）
          BrandHeader(
            topInset: MediaQuery.of(context).padding.top,
            child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const BrandCloud(size: 44),
                  const SizedBox(height: 12),
                  Text(_greeting(username),
                      style: const TextStyle(
                          color: Colors.white,
                          fontSize: 19,
                          fontWeight: FontWeight.w700)),
                  const SizedBox(height: 2),
                  ValueListenableBuilder<String>(
                      valueListenable: AutoSwitch.I.lastEvent,
                      builder: (_, ev, _) {
                        final auto = ev.isNotEmpty ? ' · 自动切换已生效' : '';
                        return Text(
                            '${_serverPublic == null ? "" : "${_serverPublic!}入口 · "}家庭网盘概览 · 下拉刷新$auto',
                            style: TextStyle(
                                color: Colors.white.withValues(alpha: 0.92),
                                fontSize: 12));
                      }),
                ]),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
            child: Column(children: [
          // 最近上传横滑条（0.6.0）：跨入口的媒体文件按修改时间倒排
          if (_recent != null && _recent!.isNotEmpty)
            _card(
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
                                                child: AuthImage(
                                                    url: '$_base/thumb/'
                                                        '${it.path.split("/").map(Uri.encodeComponent).join("/")}'))),
                                        if (it.isVideo)
                                          const Positioned(
                                              right: 4,
                                              bottom: 4,
                                              child: Icon(Icons.play_circle_fill,
                                                  size: 18,
                                                  color: Colors.white70)),
                                      ]))),
                          ])),
                ])),
          // 动态（0.6.3 由"家里动态"更名）：家庭可见的上传/分享/新成员事件流
          if (_activity != null && _activity!.isNotEmpty)
            _card(
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
                ])),
          _card(
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
                                  builder: (_) => const BackupLedgerPage())),
                          child: Text(
                              '⚠️ 有 $_failed 项备份失败，点此查看账本',
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
                  ])),
          _card(
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
                      ]))),
          _card(
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
                  ]))),
          _card(
              child: Row(mainAxisAlignment: MainAxisAlignment.spaceAround,
                  children: [
                    _quick(Icons.photo_outlined, '照片', widget.onGoPhotos),
                    _quick(Icons.folder_outlined, '文件', widget.onGoFiles),
                    _quick(Icons.delete_outline, '回收站', () {
                      Navigator.push(context,
                          MaterialPageRoute(builder: (_) => const TrashPage()));
                    }),
                    _quick(Icons.link_outlined, '我的分享', widget.onGoMe),
                  ])),
          // v0.15.2：邀请码注册入口——新成员凭码自助建号，管理端建码
          // 在管理面板 → 邀请码（服务端 /api/invites/redeem 匿名可用）
          _card(
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
                  ]))),
            ])),
        ]));
  }

  String _base = '';


  /// 当前入口标注：null=尚未取到地址（副标题不显示入口字样）
  String? get _serverPublic {
    if (_base.isEmpty) return null;
    return isPublicAddress(_base) ? '公网' : '局域网';
  }
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


class _RecentUpload {
  final String path;
  final String name;
  final String ext;
  _RecentUpload({required this.path, required this.name, required this.ext});

  bool get isVideo => const {
        '.mp4', '.mov', '.m4v', '.3gp', '.webm', '.avi', '.mkv'
      }.contains(ext);
}
