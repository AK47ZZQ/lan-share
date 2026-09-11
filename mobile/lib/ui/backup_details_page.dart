// 备份详情页（v0.17 由"备份账本"重构）：顶部实时进度卡（本轮 X/Y 进度条 +
// 正在上传的文件；空闲时显示最近一轮状态），下方为账本明细（筛选 + 列表）。
// v1.0（2026-09-11）从 backup_settings_page.dart 拆出——此前单文件 1523 行
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../backup/backup_keys.dart';
import '../backup/backup_bridge.dart';
import '../backup/backup_ledger.dart';
import '../backup/backup_round.dart';
import '../transfer/transfer_task.dart' show humanSize;
import 'theme/design_tokens.dart';

/// 备份详情页（v0.17 由"备份账本"重构）：顶部实时进度卡（本轮 X/Y 进度条
/// + 正在上传的文件；空闲时显示最近一轮状态），下方为账本明细（筛选 + 列表）。
class BackupDetailsPage extends StatefulWidget {
  const BackupDetailsPage({super.key});

  @override
  State<BackupDetailsPage> createState() => _BackupDetailsPageState();
}

class _BackupDetailsPageState extends State<BackupDetailsPage> {
  String _filter = 'all';
  String _kind = 'all'; // 类型筛选（0.7.1：照片/视频分开看）
  List<LedgerRow> _rows = [];
  bool _loading = true;
  bool _retrying = false;
  String? _error;
  Timer? _poll;
  int _progDone = 0;
  int _progTotal = 0;
  String _progCurrent = '';
  String? _lastRound;
  int? _doneCount;
  int? _doneBytes;
  List<Map<String, Object>> _missing = const []; // 服务端已删相册（9.6-P1-10）

  @override
  void initState() {
    super.initState();
    _load();
    // 备份在后台 isolate 跑：2s 轮询进度卡与列表（与设置页同节奏）
    _poll = Timer.periodic(const Duration(seconds: 2), (_) => _loadProgress());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _loadProgress() async {
    try {
      final sp = await SharedPreferences.getInstance();
      if (!mounted) return;
      setState(() {
        _progDone = sp.getInt(BackupKeys.progressDone) ?? 0;
        _progTotal = sp.getInt(BackupKeys.progressTotal) ?? 0;
        _progCurrent = sp.getString(BackupKeys.progressCurrent) ?? '';
        _lastRound = sp.getString(BackupKeys.lastRound);
      });
    } catch (_) {}
  }

  Future<void> _load() async {
    try {
      final rows = await BackupLedger.I.recent(
          status: _filter == 'all' ? null : _filter,
          kind: _kind == 'all' ? null : _kind);
      LedgerStats? st;
      List<Map<String, Object>> missing = const [];
      try {
        st = await BackupLedger.I.stats();
        missing = await BackupLedger.I.serverMissingBuckets();
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _rows = rows;
        _loading = false;
        _error = null;
        _missing = missing;
        _doneCount = st == null ? 0 : st.donePhotos + st.doneVideos;
        _doneBytes = st?.doneBytes ?? 0;
      });
    } catch (e) {
      // 后台引擎正拿着同一个库跑备份时偶发锁冲突；不能让异常把页面
      // 永远卡在转圈（0.7.1 修的"备份账本打不开"）——给出错误和重试
      if (!mounted) return;
      final s = e.toString();
      setState(() {
        _error = s.length > 160 ? s.substring(0, 160) : s;
        _loading = false;
      });
    }
  }

  /// "重新备份"：missing 行回 pending（sha 秒传，只重建服务端目录引用）
  Future<void> _reBackup(Map<String, Object> m) async {
    await BackupLedger.I.reBackupBucket(m['kind'] as String, m['bucket'] as String);
    _load();
  }

  /// "从备份移除"：missing 行删除（用户确认服务端已不要这批照片）
  Future<void> _dropBucket(Map<String, Object> m) async {
    await BackupLedger.I.dropBucket(m['kind'] as String, m['bucket'] as String);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final running = _progTotal > 0;
    return Scaffold(
      appBar: AppBar(
        title: const Text('备份详情'),
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
          : _error != null
              ? ListView(children: [
                  const SizedBox(height: 100),
                  const Center(child: Text('账本暂时打不开')),
                  const SizedBox(height: Gap.xs),
                  Padding(
                      padding: const EdgeInsets.symmetric(horizontal: Gap.xl),
                      child: Center(
                          child: Text(_error!,
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                  fontSize: 12, color: Colors.grey)))),
                  const SizedBox(height: Gap.xs),
                  Center(
                      child: TextButton(
                          onPressed: () {
                            setState(() => _loading = true);
                            _load();
                          },
                          child: const Text('重试'))),
                ])
              : Column(children: [
              // ── 实时进度/最近一轮卡 ──────────────────────────
              Padding(
                padding: const EdgeInsets.fromLTRB(Gap.sm, 10, Gap.sm, 0),
                child: Card(
                  elevation: 0,
                  color: cs.surfaceContainerHighest,
                  child: Padding(
                    padding: const EdgeInsets.all(14),
                    child: running
                        ? Column(crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                            Row(children: [
                              const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(
                                      strokeWidth: 2)),
                              const SizedBox(width: 10),
                              Expanded(
                                  child: Text('备份中 $_progDone/$_progTotal 项'
                                      '（${(_progDone * 100 ~/ _progTotal)}%）',
                                  style: const TextStyle(
                                      fontWeight: FontWeight.bold))),
                            ]),
                            const SizedBox(height: 10),
                            ClipRRect(
                              borderRadius: BorderRadius.circular(6),
                              child: TweenAnimationBuilder<double>(
                                tween: Tween(begin: 0,
                                    end: _progDone / _progTotal),
                                duration: const Duration(milliseconds: 400),
                                builder: (_, v, _) => LinearProgressIndicator(
                                    value: v.clamp(0.0, 1.0), minHeight: 8),
                              ),
                            ),
                            if (_progCurrent.isNotEmpty) ...[
                              const SizedBox(height: Gap.xs),
                              Text('正在上传：$_progCurrent',
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(
                                      fontSize: 12, color: Colors.grey)),
                            ],
                          ])
                        : Row(children: [
                            if (roundView(_lastRound) case final r?) ...[
                              Icon(r.warn
                                      ? Icons.info_outline
                                      : Icons.check_circle_outline,
                                  size: 18,
                                  color: r.warn
                                      ? Colors.orange
                                      : Colors.green),
                              const SizedBox(width: Gap.xs),
                              Expanded(child: Text(r.text,
                                  style: const TextStyle(fontSize: 13))),
                            ] else
                              const Expanded(
                                  child: Text('暂无备份记录',
                                      style: TextStyle(fontSize: 13))),
                            if (_doneCount != null && _doneCount! > 0) ...[
                              Text(
                                  '累计 $_doneCount 项 · ${humanSize(_doneBytes ?? 0)}',
                                  style: const TextStyle(
                                      fontSize: 12, color: Colors.grey)),
                            ],
                          ]),
                  ),
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: Gap.sm, vertical: Gap.xs),
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
                          setState(() => _filter = e.key);
                          _load();
                        }),
                ]),
              ),
              // 服务端已删相册提示（9.6-P1-10 场景1）：备份对账发现目录
              // 在服务端被手动删除——用户选择"重新备份"或"从备份移除"
              if (_missing.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(Gap.sm, 0, Gap.sm, Gap.xs),
                  child: Card(
                    elevation: 0,
                    color: cs.tertiaryContainer,
                    child: Padding(
                      padding: const EdgeInsets.all(Gap.sm),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(children: [
                              Icon(Icons.folder_delete_outlined,
                                  size: 18, color: cs.onTertiaryContainer),
                              const SizedBox(width: Gap.xs),
                              Expanded(
                                  child: Text(
                                      '发现 ${_missing.length} 个相册已在服务端被删除',
                                      style: const TextStyle(
                                          fontSize: 13,
                                          fontWeight: FontWeight.bold))),
                            ]),
                            const SizedBox(height: Gap.xxs),
                            Text('这些相册的照片本机仍在，本轮已暂停重传。可重新备份（重建目录）或从备份记录移除。',
                                style: TextStyle(
                                    fontSize: 11.5,
                                    color: cs.onTertiaryContainer)),
                            for (final m in _missing)
                              ListTile(
                                  dense: true,
                                  contentPadding: EdgeInsets.zero,
                                  title: Text(
                                      '${m['bucket']}（${m['kind'] == 'video' ? '视频' : '照片'} ${m['count']} 项）',
                                      style: const TextStyle(fontSize: 13)),
                                  subtitle: const Text('服务端目录不存在',
                                      style:
                                          TextStyle(fontSize: 11)),
                                  trailing: Wrap(spacing: 4, children: [
                                    TextButton(
                                        onPressed: () => _reBackup(m),
                                        child: const Text('重新备份',
                                            style: TextStyle(fontSize: 12))),
                                    TextButton(
                                        onPressed: () => _dropBucket(m),
                                        child: Text('移除',
                                            style: TextStyle(
                                                fontSize: 12,
                                                color: cs.error)),
                                  )])),
                          ]),
                    ),
                  ),
                ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: Gap.sm),
                child: Wrap(spacing: 8, children: [
                  for (final e in {
                    'all': '全部类型',
                    'image': '照片',
                    'video': '视频',
                  }.entries)
                    ChoiceChip(
                        label: Text(e.value),
                        selected: _kind == e.key,
                        onSelected: (_) {
                          setState(() => _kind = e.key);
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
                          final isVideo = r.kind == 'video';
                          final time = r.uploadedAt != null
                              ? DateTime.fromMillisecondsSinceEpoch(
                                  r.uploadedAt! * 1000)
                                  .toString().split('.').first
                              : null;
                          // 0.7.1：类型图标 + 相册名入列，账本一眼分清照片/视频
                          final subtitle = [
                            if (r.bucket.isNotEmpty) r.bucket,
                            time ?? r.lastError ?? r.status,
                          ].join(' · ');
                          return ListTile(
                            leading: Icon(icon, color: color),
                            title: Row(children: [
                              Icon(
                                  isVideo
                                      ? Icons.movie_outlined
                                      : Icons.photo_outlined,
                                  size: 15,
                                  color:
                                      Theme.of(context).colorScheme.outline),
                              const SizedBox(width: Gap.xxs),
                              Expanded(
                                  child: Text(r.name,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis)),
                            ]),
                            subtitle: Text(subtitle,
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
