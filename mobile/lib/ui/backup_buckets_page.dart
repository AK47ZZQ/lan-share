// 备份相册选择页（0.14 白名单化）：勾选要备份的相册。
// 白名单只影响扫描阶段、账本不动：取消勾选后同 id 媒体自动重回待备集合；
// 旧"排除相册"（0.6.0 黑名单）不再单独编辑——保存本页即清除旧表。
// v1.0（2026-09-11）从 backup_settings_page.dart 拆出——此前单文件 1523 行
import 'dart:async';

import 'package:flutter/material.dart';

import '../backup/backup_bridge.dart';
import '../backup/backup_config.dart';
import '../backup/media_source.dart';

/// 备份相册选择页（0.14 白名单化）：勾选要备份的相册。
/// 白名单只影响扫描阶段，账本不动：取消勾选后同 id 媒体自动重回待备集合。
/// 旧"排除相册"（0.6.0 黑名单）不再单独编辑——保存本页即清除旧表，
/// 语义统一收敛到"勾选要备份的相册，不勾=全部备份"。
class BackupBucketsPage extends StatefulWidget {
  final BackupConfig cfg;
  const BackupBucketsPage({super.key, required this.cfg});

  @override
  State<BackupBucketsPage> createState() => _BackupBucketsPageState();
}

class _BackupBucketsPageState extends State<BackupBucketsPage> {
  Map<String, int>? _buckets;
  String? _error;
  late final Set<String> _selected = Set.of(widget.cfg.selectedBuckets);
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
      setState(() {
        _buckets = b;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      // 带上原生/通道的原始错误：远程排障时这一行能直接分辨
      // 缺权限、通道未注册、MediaStore 异常（0.6.3 排查小米"页面不显示"）
      final s = e.toString();
      setState(() => _error = s.length > 160 ? s.substring(0, 160) : s);
    }
  }

  Future<void> _save() async {
    final cfg = widget.cfg.copyWith(
        selectedBuckets: _selected.toList(), excludedBuckets: const []);
    await cfg.save();
    // 白名单即刻生效：save() 已把 pending 置 -1 强制下轮，这里再拉一轮，
    // 别让用户等最长 15 分钟的周期窗口才看到待备数变化（0.6.2）
    if (cfg.enabled) BackupBridge.runOnce();
  }

  @override
  Widget build(BuildContext context) {
    final buckets = _buckets;
    final names = (buckets?.keys.toList() ?? const <String>[])..sort();
    final legacyExcluded = widget.cfg.excludedBuckets;
    return PopScope(
        canPop: !_dirty,
        onPopInvokedWithResult: (didPop, _) async {
          if (didPop || !_dirty) return;
          final nav = Navigator.of(context);
          try {
            await _save();
          } finally {
            // _save 抛错也必须退出页面，否则用户被卡死（R1-P3）
            nav.pop();
          }
        },
        child: Scaffold(
            appBar: AppBar(
                title: const Text('选择备份相册'),
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
                                  content: Text('已保存，正在应用…'),
                                  duration: Duration(seconds: 2)));
                        },
                        child: const Text('保存')),
                ]),
            body: buckets == null
                ? _error != null
                    ? ListView(children: [
                        const SizedBox(height: 100),
                        const Center(child: Text('无法读取相册列表')),
                        const SizedBox(height: 8),
                        Padding(
                            padding:
                                const EdgeInsets.symmetric(horizontal: 24),
                            child: Center(
                                child: Text(_error!,
                                    textAlign: TextAlign.center,
                                    style: const TextStyle(
                                        fontSize: 12, color: Colors.grey)))),
                        const SizedBox(height: 8),
                        Center(
                            child: TextButton(
                                onPressed: _load,
                                child: const Text('重试'))),
                      ])
                    : const Center(child: CircularProgressIndicator())
                : names.isEmpty
                    ? ListView(children: [
                        const SizedBox(height: 100),
                        const Center(
                            child: Text('没有读到任何相册',
                                style: TextStyle(fontWeight: FontWeight.bold))),
                        const SizedBox(height: 8),
                        const Padding(
                            padding: EdgeInsets.symmetric(horizontal: 24),
                            child: Center(
                                child: Text(
                                    '手机媒体库为空，或系统未授齐"照片和视频"权限',
                                    textAlign: TextAlign.center,
                                    style: TextStyle(
                                        fontSize: 12, color: Colors.grey)))),
                        const SizedBox(height: 8),
                        Center(
                            child: TextButton(
                                onPressed: _load,
                                child: const Text('刷新'))),
                      ])
                    : ListView(children: [
                    Padding(
                        padding: const EdgeInsets.all(16),
                        child: Text(
                            '勾选要自动备份的相册；一个都不勾 = 全部不备份。\n'
                            '取消勾选只是不备份新照片，已备份的不受影响。',
                            style: TextStyle(
                                fontSize: 12,
                                color: Theme.of(context).colorScheme.outline))),
                    if (legacyExcluded.isNotEmpty && _dirty)
                      Padding(
                          padding:
                              const EdgeInsets.fromLTRB(16, 0, 16, 12),
                          child: Text(
                              '保存后将清除旧的"排除相册"设置'
                              '（${legacyExcluded.take(3).join("、")}'
                              '${legacyExcluded.length > 3 ? "…" : ""}），'
                              '以后都用这里的勾选控制。',
                              style: const TextStyle(
                                  fontSize: 12, color: Colors.orange))),
                    for (final n in names)
                      CheckboxListTile(
                          value: _selected.contains(n),
                          title: Text(n.isEmpty ? '（未分类）' : n,
                              maxLines: 1, overflow: TextOverflow.ellipsis),
                          subtitle: Text('${buckets[n]} 项',
                              style: const TextStyle(fontSize: 12)),
                          onChanged: (v) {
                            setState(() {
                              v == true
                                  ? _selected.add(n)
                                  : _selected.remove(n);
                              _dirty = true;
                            });
                          }),
                  ])));
  }
}
