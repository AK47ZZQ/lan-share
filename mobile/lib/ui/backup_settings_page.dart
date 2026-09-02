// 自动备份设置（FR-2 / FR-9）：开关、约束、统计、账本入口。
// 开启流程：申请媒体权限（+通知）→ 落配置 → 挂 WorkManager 周期任务 → 立即跑一轮。
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../backup/backup_bridge.dart';
import '../backup/backup_config.dart';
import '../backup/backup_ledger.dart';
import '../backup/media_source.dart';
import '../state/session.dart';
import '../transfer/transfer_task.dart' show humanSize;

class BackupSettingsPage extends ConsumerStatefulWidget {
  const BackupSettingsPage({super.key});

  @override
  ConsumerState<BackupSettingsPage> createState() => _BackupSettingsPageState();
}

class _BackupSettingsPageState extends ConsumerState<BackupSettingsPage> {
  BackupConfig? _cfg;
  LedgerStats? _stats;
  Timer? _poll;
  bool _busy = false; // 立即备份防连点（引擎侧互斥兜底，这里管 UI 观感）

  @override
  void initState() {
    super.initState();
    _reload();
    // 备份在后台 isolate 跑，账本轮询即可看到进度（2s 足够，页面级）
    _poll = Timer.periodic(const Duration(seconds: 2), (_) => _loadStats());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _reload() async {
    final cfg = await BackupConfig.load();
    if (!mounted) return;
    setState(() => _cfg = cfg);
    await _loadStats();
  }

  Future<void> _loadStats() async {
    final s = await BackupLedger.I.stats();
    if (mounted) setState(() => _stats = s);
  }

  Future<void> _toggle(bool on) async {
    var cfg = _cfg!;
    if (on) {
      final granted = await BackupBridge.requestPermissions(
          includeVideos: cfg.includeVideos);
      if (!granted) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('需要"照片和视频"权限才能自动备份')));
        }
        return; // 保持关闭
      }
    }
    cfg = cfg.copyWith(enabled: on);
    await cfg.save();
    setState(() => _cfg = cfg);
    await BackupBridge.applySchedule(cfg);
    if (on) await BackupBridge.runOnce();
  }

  Future<void> _set(BackupConfig cfg) async {
    if (cfg.includeVideos != _cfg!.includeVideos && cfg.enabled) {
      // 视频开关变化要重新申请（READ_MEDIA_VIDEO 是独立权限项）
      final granted = await BackupBridge.requestPermissions(
          includeVideos: cfg.includeVideos);
      if (!granted) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('未授予视频权限，本次不包含视频')));
        }
        cfg = cfg.copyWith(includeVideos: false);
      }
    }
    await cfg.save();
    setState(() => _cfg = cfg);
    await BackupBridge.applySchedule(cfg);
    if (cfg.enabled) await BackupBridge.runOnce();
  }

  @override
  Widget build(BuildContext context) {
    final cfg = _cfg;
    final stats = _stats;
    final username = ref.watch(sessionProvider).username ?? '';
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('自动备份')),
      body: cfg == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(padding: const EdgeInsets.only(bottom: 24), children: [
              SwitchListTile(
                secondary: Icon(cfg.enabled
                    ? Icons.backup
                    : Icons.backup_outlined),
                title: const Text('自动备份手机相册'),
                subtitle: const Text('连 Wi-Fi 后自动把新照片存进网盘'),
                value: cfg.enabled,
                onChanged: (v) => _toggle(v),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Card(
                  elevation: 0,
                  color: cs.surfaceContainerHighest,
                  child: Padding(
                    padding: const EdgeInsets.all(14),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                      Text('已备份 ${stats?.done ?? 0} 张'
                          '（${humanSize(stats?.doneBytes ?? 0)}）'
                          ' · 待备份 ${stats?.pending ?? 0} 张'
                          ' · 失败 ${stats?.error ?? 0} 张',
                          style: const TextStyle(fontWeight: FontWeight.bold)),
                      const SizedBox(height: 4),
                      Text('备份位置：照片/手机相册/$username/',
                          style: const TextStyle(fontSize: 12)),
                      if (stats?.lastUploadedAt != null) ...[
                        const SizedBox(height: 2),
                        Text(
                            '最近备份：${DateTime.fromMillisecondsSinceEpoch(
                                stats!.lastUploadedAt! * 1000)
                                .toString().split('.').first}',
                            style: const TextStyle(fontSize: 12)),
                      ],
                    ]),
                  ),
                ),
              ),
              const Divider(height: 24),
              SwitchListTile(
                  title: const Text('仅 Wi-Fi'),
                  subtitle: const Text('流量下不备份（推荐）'),
                  value: cfg.wifiOnly,
                  onChanged: cfg.enabled
                      ? (v) => _set(cfg.copyWith(wifiOnly: v))
                      : null),
              SwitchListTile(
                  title: const Text('仅充电时'),
                  value: cfg.chargingOnly,
                  onChanged: cfg.enabled
                      ? (v) => _set(cfg.copyWith(chargingOnly: v))
                      : null),
              SwitchListTile(
                  title: const Text('包含视频'),
                  subtitle: const Text('默认关闭：视频大、耗流量'),
                  value: cfg.includeVideos,
                  onChanged: (v) => _set(cfg.copyWith(includeVideos: v))),
              ListTile(
                  leading: const Icon(Icons.photo_library_outlined),
                  title: const Text('排除相册'),
                  subtitle: Text(cfg.excludedBuckets.isEmpty
                      ? '全部相册都备份'
                      : '已排除 ${cfg.excludedBuckets.length} 个'
                          '：${cfg.excludedBuckets.take(3).join("、")}'
                          '${cfg.excludedBuckets.length > 3 ? "…" : ""}'),
                  onTap: () async {
                    await Navigator.push(context, MaterialPageRoute(
                        builder: (_) => BackupExclusionsPage(cfg: cfg)));
                    await _reload();
                  }),
              SwitchListTile(
                  title: const Text('备份完成提醒'),
                  value: cfg.notifyDone,
                  onChanged: (v) => _set(cfg.copyWith(notifyDone: v))),
              SwitchListTile(
                  title: const Text('公网地址也备份'),
                  subtitle: const Text('默认关闭：走公网流量，注意资费'),
                  value: cfg.allowPublicServer,
                  onChanged: (v) => _set(cfg.copyWith(allowPublicServer: v))),
              const Divider(height: 24),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Row(children: [
                  Expanded(
                    child: OutlinedButton.icon(
                        onPressed: cfg.enabled && !_busy
                            ? () async {
                                setState(() => _busy = true);
                                try {
                                  final messenger = ScaffoldMessenger.of(context);
                                  await BackupBridge.runOnce();
                                  messenger.showSnackBar(const SnackBar(
                                      content: Text('已开始备份（见通知栏进度）')));
                                } finally {
                                  if (mounted) setState(() => _busy = false);
                                }
                              }
                            : null,
                        icon: _busy
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.sync),
                        label: const Text('立即备份')),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: FilledButton.tonalIcon(
                        onPressed: () => Navigator.push(context,
                            MaterialPageRoute(
                                builder: (_) => const BackupLedgerPage())),
                        icon: const Icon(Icons.receipt_long),
                        label: const Text('备份账本')),
                  ),
                ]),
              ),
              const Padding(
                padding: EdgeInsets.all(16),
                child: Text(
                    '· 单向备份：相册里删除的照片不会从网盘删除\n'
                    '· 已备份过的照片自动跳过（换手机重装后重新登录也只传新照片）\n'
                    '· 系统可能在省电时推迟备份，属正常行为',
                    style: TextStyle(fontSize: 12, color: Colors.grey)),
              ),
            ]),
    );
  }
}

class BackupLedgerPage extends StatefulWidget {
  const BackupLedgerPage({super.key});

  @override
  State<BackupLedgerPage> createState() => _BackupLedgerPageState();
}

class _BackupLedgerPageState extends State<BackupLedgerPage> {
  String _filter = 'all';
  List<LedgerRow> _rows = [];
  bool _loading = true;
  bool _retrying = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final rows = await BackupLedger.I.recent(
        status: _filter == 'all' ? null : _filter);
    if (!mounted) return;
    setState(() {
      _rows = rows;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('备份账本'),
        actions: [
          TextButton.icon(
              onPressed: _retrying
                  ? null
                  : () async {
                      setState(() => _retrying = true);
                      try {
                        final messenger = ScaffoldMessenger.of(context);
                        await BackupLedger.I.retryFailed();
                        await BackupBridge.runOnce();
                        messenger.showSnackBar(
                            const SnackBar(content: Text('已重新排队失败项')));
                        if (!mounted) return;
                        _filter = 'all';
                        _loading = true;
                        _load();
                      } finally {
                        if (mounted) setState(() => _retrying = false);
                      }
                    },
              icon: const Icon(Icons.refresh, size: 18),
              label: const Text('重试失败项')),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                child: Wrap(spacing: 8, children: [
                  for (final e in {
                    'all': '全部',
                    'uploading': '备份中',
                    'error': '失败',
                    'done': '已完成',
                  }.entries)
                    ChoiceChip(
                        label: Text(e.value),
                        selected: _filter == e.key,
                        onSelected: (_) {
                          _filter = e.key;
                          _loading = true;
                          _load();
                        }),
                ]),
              ),
              Expanded(
                child: _rows.isEmpty
                    ? const Center(child: Text('还没有备份记录'))
                    : ListView.builder(
                        itemCount: _rows.length,
                        itemBuilder: (_, i) {
                          final r = _rows[i];
                          final (icon, color) = switch (r.status) {
                            'done' => (Icons.check_circle, Colors.green),
                            'error' => (Icons.error, Colors.red),
                            'uploading' => (Icons.sync, Colors.blue),
                            _ => (Icons.schedule, Colors.orange),
                          };
                          final time = r.uploadedAt != null
                              ? DateTime.fromMillisecondsSinceEpoch(
                                  r.uploadedAt! * 1000)
                                  .toString().split('.').first
                              : null;
                          return ListTile(
                            leading: Icon(icon, color: color),
                            title: Text(r.name,
                                maxLines: 1, overflow: TextOverflow.ellipsis),
                            subtitle: Text(time ?? r.lastError ?? r.status,
                                maxLines: 1, overflow: TextOverflow.ellipsis),
                            trailing: Text(humanSize(r.size),
                                style: const TextStyle(fontSize: 12)),
                          );
                        },
                      ),
              ),
            ]),
    );
  }
}

/// 排除相册管理页（0.6.0）：勾选不参与自动备份的相册（截图/聊天图等）。
/// 排除只影响扫描阶段，账本不动：取消勾选后同 id 媒体自动重回待备集合。
class BackupExclusionsPage extends StatefulWidget {
  final BackupConfig cfg;
  const BackupExclusionsPage({super.key, required this.cfg});

  @override
  State<BackupExclusionsPage> createState() => _BackupExclusionsPageState();
}

class _BackupExclusionsPageState extends State<BackupExclusionsPage> {
  Map<String, int>? _buckets;
  String? _error;
  late final Set<String> _excluded = Set.of(widget.cfg.excludedBuckets);
  bool _dirty = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final b = await MediaSource.buckets();
      if (!mounted) return;
      setState(() => _buckets = b);
    } catch (_) {
      if (!mounted) return;
      setState(() => _error = '无法读取相册列表（需要媒体权限）');
    }
  }

  Future<void> _save() async {
    final cfg = widget.cfg.copyWith(excludedBuckets: _excluded.toList());
    await cfg.save();
  }

  @override
  Widget build(BuildContext context) {
    final buckets = _buckets;
    final names = (buckets?.keys.toList() ?? const <String>[])..sort();
    return PopScope(
        canPop: !_dirty,
        onPopInvokedWithResult: (didPop, _) async {
          if (didPop || !_dirty) return;
          final nav = Navigator.of(context);
          await _save();
          nav.pop();
        },
        child: Scaffold(
            appBar: AppBar(
                title: const Text('排除相册'),
                actions: [
                  if (_dirty)
                    TextButton(
                        onPressed: () async {
                          final messenger = ScaffoldMessenger.of(context);
                          await _save();
                          if (!mounted) return;
                          setState(() => _dirty = false);
                          messenger.showSnackBar(
                              const SnackBar(
                                  content: Text('已保存，下轮备份生效'),
                                  duration: Duration(seconds: 2)));
                        },
                        child: const Text('保存')),
                ]),
            body: buckets == null
                ? _error != null
                    ? ListView(children: [
                        const SizedBox(height: 100),
                        Center(child: Text(_error!)),
                      ])
                    : const Center(child: CircularProgressIndicator())
                : ListView(children: [
                    Padding(
                        padding: const EdgeInsets.all(16),
                        child: Text(
                            '勾选的相册不参与自动备份（截图、聊天图等建议排除）。'
                            '取消勾选会重新纳入备份。',
                            style: TextStyle(
                                fontSize: 12,
                                color: Theme.of(context).colorScheme.outline))),
                    for (final n in names)
                      CheckboxListTile(
                          value: _excluded.contains(n),
                          title: Text(n.isEmpty ? '（未分类）' : n,
                              maxLines: 1, overflow: TextOverflow.ellipsis),
                          subtitle: Text('${buckets[n]} 项',
                              style: const TextStyle(fontSize: 12)),
                          onChanged: (v) {
                            setState(() {
                              v == true
                                  ? _excluded.add(n)
                                  : _excluded.remove(n);
                              _dirty = true;
                            });
                          }),
                  ])));
  }
}
