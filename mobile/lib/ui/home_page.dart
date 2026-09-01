// 首页综合面板（0.4.2）：App 打开不再直落照片墙，先给一块"家里的盘还好吗"
// 的概览——备份状态、传输动态、服务端存储、最近上传 + 快捷入口。
// 只读概览加少量动作（立即备份/跳转）；细节操作仍归各职能页。
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../backup/backup_bridge.dart';
import '../data/api/api_client.dart';
import '../state/session.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'backup_settings_page.dart';
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
  int? _lastDoneAt;
  int _diskUsed = 0, _diskTotal = 0;
  String _shareSize = '';
  Timer? _tick, _diskTick;

  @override
  void initState() {
    super.initState();
    _loadLocal();
    _loadDisk();
    // 本地状态（备份开关/待备数/传输动态）轻，2s 一刷保证跨页回来也是新的；
    // 磁盘要服务端 rglob 统计，30s 一刷 + 下拉手动刷
    _tick = Timer.periodic(const Duration(seconds: 2), (_) => _loadLocal());
    // ⚠️ 必须持有并在 dispose 取消：匿名 Timer 会登出后继续空转（泄漏）
    _diskTick = Timer.periodic(const Duration(seconds: 30), (_) => _loadDisk());
  }

  @override
  void dispose() {
    _tick?.cancel();
    _diskTick?.cancel();
    super.dispose();
  }

  Future<void> _loadLocal() async {
    final sp = await SharedPreferences.getInstance();
    // 引擎在独立 isolate 写 prefs，不 reload 就永远是首帧缓存（备份卡假死）
    await sp.reload();
    if (!mounted) return;
    setState(() {
      _backupOn = sp.getBool('backup_enabled') ?? false;
      _pending = sp.getInt('backup_pending_count') ?? -1;
      _lastDoneAt = sp.getInt('backup_last_done_at');
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
        _shareSize = r.data['share_size'] as String? ?? '';
      });
    } catch (_) {}
  }

  Future<void> _refreshAll() async {
    await _loadDisk();
    await _loadLocal();
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
        child: ListView(padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
            children: [
          Padding(
              padding: const EdgeInsets.fromLTRB(4, 8, 4, 12),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(_greeting(username),
                        style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: 2),
                    Text('家庭网盘概览 · 下拉刷新',
                        style: TextStyle(
                            fontSize: 12, color: cs.outline)),
                  ])),
          _card(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _cardTitle('自动备份',
                        icon: Icons.backup_outlined,
                        trailing: _badge(_backupOn ? '已开启' : '已关闭',
                            _backupOn ? Colors.green : cs.outline)),
                    const SizedBox(height: 6),
                    Text(_backupText(),
                        style: const TextStyle(fontSize: 13)),
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
                                    TextStyle(fontSize: 12, color: cs.outline))),
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
                                      color: cs.outline),
                                  const SizedBox(width: 6),
                                  Expanded(
                                      child: Text(t.name,
                                          overflow: TextOverflow.ellipsis,
                                          style: const TextStyle(fontSize: 12))),
                                ])),
                        ],
                      ]))),
          _card(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _cardTitle('服务端存储',
                        icon: Icons.dns_outlined,
                        trailing: _shareSize.isEmpty
                            ? null
                            : Text('文件库 $_shareSize',
                                style: TextStyle(
                                    fontSize: 12, color: cs.outline))),
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
                            : '磁盘信息加载中…',
                        style: TextStyle(fontSize: 12, color: cs.outline)),
                  ])),
          _card(
              child: Row(mainAxisAlignment: MainAxisAlignment.spaceAround,
                  children: [
                    _quick(Icons.photo_outlined, '照片', widget.onGoPhotos),
                    _quick(Icons.folder_outlined, '文件', widget.onGoFiles),
                    _quick(Icons.link_outlined, '我的分享', widget.onGoMe),
                  ])),
        ]));
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
    final p = _pending < 0 ? '待统计' : '$_pending 张待备';
    final last = _lastDoneAt == null
        ? ''
        : ' · 上次 ${_ago(_lastDoneAt!)}完成';
    return p + last;
  }

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
          Icon(icon, size: 16),
          const SizedBox(width: 6),
        ],
        Text(title,
            style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
        const Spacer(),
        ?trailing,
      ]);

  Widget _card({required Widget child}) => Card(
      elevation: 0,
      margin: const EdgeInsets.only(bottom: 10),
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: Theme.of(context).dividerColor, width: 0.5)),
      child: Padding(padding: const EdgeInsets.all(14), child: child));

  Widget _quick(IconData icon, String label, VoidCallback onTap) => InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(10),
      child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 26),
            const SizedBox(height: 4),
            Text(label, style: const TextStyle(fontSize: 12)),
          ])));
}
