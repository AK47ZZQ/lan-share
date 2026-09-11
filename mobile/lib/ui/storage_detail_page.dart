// 存储详情（v0.15）：/api/disk/detail。
// 磁盘总况全员可见；admin 追加分目录构成（照片/视频/文件/备份…占比），
// 成员返回私人空间用量与配额。服务端 rglob 扫描要几秒：页面自转圈 +
// 下拉刷新，不阻塞。
import 'package:flutter/material.dart';

import '../data/api/errors.dart';
import '../data/api/home_api.dart';
import '../transfer/transfer_task.dart' show humanSize;
import 'theme/design_tokens.dart';

class StorageDetailPage extends StatefulWidget {
  const StorageDetailPage({super.key});

  @override
  State<StorageDetailPage> createState() => _StorageDetailPageState();
}

class _StorageDetailPageState extends State<StorageDetailPage> {
  Map<String, dynamic>? _data;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final d = await HomeApi.diskDetail();
      if (!mounted) return;
      setState(() {
        _data = d;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = errDetail(e);
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final d = _data;
    return Scaffold(
        appBar: AppBar(title: const Text('存储详情')),
        body: _loading && d == null
            ? const Center(child: CircularProgressIndicator())
            : _error != null && d == null
                ? ListView(children: [
                    const SizedBox(height: 100),
                    Center(child: Text(_error!)),
                    const SizedBox(height: Gap.xs),
                    Center(
                        child: TextButton(
                            onPressed: _load, child: const Text('重试'))),
                  ])
                : RefreshIndicator(
                    onRefresh: _load,
                    child: ListView(padding: const EdgeInsets.all(Gap.sm),
                        children: [
                      if (d?['warning'] == true)
                        Padding(
                            padding: const EdgeInsets.only(bottom: 10),
                            child: Row(children: [
                              Icon(Icons.warning_amber_rounded,
                                  size: 18, color: cs.error),
                              const SizedBox(width: 6),
                              Text('服务端剩余空间不足 15 GB',
                                  style:
                                      TextStyle(fontSize: 13, color: cs.error)),
                            ])),
                      _diskCard(d!, cs),
                      const SizedBox(height: 10),
                      // admin：分目录构成；成员：私人空间+配额
                      if (d['categories'] is Map)
                        _categoriesCard(
                            Map<String, dynamic>.from(
                                d['categories'] as Map),
                            cs)
                      else if (d['private_used'] != null || d['quota_bytes'] != null)
                        _privateCard(d, cs),
                    ])));
  }

  Widget _diskCard(Map<String, dynamic> d, ColorScheme cs) => _frame(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('磁盘总况',
            style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700)),
        const SizedBox(height: Gap.sm),
        LinearProgressIndicator(
            value: ((d['total'] as num?) ?? 0) > 0
                ? (((d['used'] as num?) ?? 0) / ((d['total'] as num?) ?? 1))
                    .clamp(0.0, 1.0)
                : null,
            minHeight: 8,
            borderRadius: BorderRadius.circular(4)),
        const SizedBox(height: Gap.sm),
        Row(children: [
          _stat('已用', d['used_human'] as String? ?? '-', cs),
          _stat('剩余', d['free_human'] as String? ?? '-', cs),
          _stat('总容量', d['total_human'] as String? ?? '-', cs),
        ]),
        const SizedBox(height: Gap.xxs),
        Text('文件库实际占用 ${d['share_size_human'] ?? '-'}（去重物理字节）',
            style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
      ]));

  /// 分目录构成：按字节倒序，条形占比（相对最大项）。
  /// 红队 P2-5：条目值逐个校验（服务端老版本/代理可能返回扁平值，
  /// build 期 NoSuchMethodError = 管理员一进页面即崩）
  Widget _categoriesCard(Map<String, dynamic> cats, ColorScheme cs) {
    final entries = cats.entries
        .where((e) => e.value is Map)
        .toList()
      ..sort((a, b) => _asInt((b.value as Map)['size'])
          .compareTo(_asInt((a.value as Map)['size'])));
    if (entries.isEmpty) {
      // 空库/全新实例：服务端可能返回 categories:{}，first 会炸 build
      return _frame(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('空间构成（按顶级目录）',
                    style: TextStyle(
                        fontSize: 14, fontWeight: FontWeight.w700)),
                const SizedBox(height: 10),
                Text('还没有文件',
                    style: TextStyle(
                        fontSize: 12.5, color: cs.onSurfaceVariant)),
              ]));
    }
    final max = ((entries.first.value['size'] as num?) ?? 0).toDouble();
    return _frame(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Text('空间构成（按顶级目录）',
          style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700)),
      const SizedBox(height: 10),
      for (final e in entries)
        Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(children: [
                    Expanded(
                        child: Text(e.key,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 13))),
                    const SizedBox(width: Gap.xs),
                    Text((e.value as Map)['size_human'] as String? ?? '-',
                        style: TextStyle(
                            fontSize: 12, color: cs.onSurfaceVariant)),
                  ]),
                  const SizedBox(height: Gap.xxs),
                  ClipRRect(
                      borderRadius: BorderRadius.circular(3),
                      child: LinearProgressIndicator(
                          value: max <= 0
                              ? 0
                              : _asInt((e.value as Map)['size']) / max,
                          minHeight: 5)),
                ])),
    ]));
  }

  /// 成员视角：私人空间用量 + 配额（quota null=不限）
  Widget _privateCard(Map<String, dynamic> d, ColorScheme cs) {
    final used = _asInt(d['private_used']);
    final q = d['quota_bytes'];
    final quota = (q is num)
        ? q.toInt()
        : (q is String ? int.tryParse(q) : null);
    final ratio = (quota == null || quota <= 0)
        ? null
        : (used / quota).clamp(0.0, 1.0);
    return _frame(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Text('我的私人空间',
          style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700)),
      const SizedBox(height: Gap.sm),
      LinearProgressIndicator(
          value: ratio, minHeight: 8, borderRadius: BorderRadius.circular(4)),
      const SizedBox(height: 10),
      Text(
          quota == null || quota <= 0
              ? '已用 ${humanSize(used)} · 未设配额'
              : '已用 ${humanSize(used)} / ${humanSize(quota)}'
                  '${ratio != null && ratio >= 0.9 ? ' · 快满了' : ''}',
          style: TextStyle(
              fontSize: 12.5,
              color: ratio != null && ratio >= 0.9
                  ? cs.error
                  : cs.onSurfaceVariant)),
    ]));
  }

  static int _asInt(Object? v) {
    if (v is num) return v.toInt();
    if (v is String) return int.tryParse(v) ?? 0;
    return 0;
  }

  Widget _stat(String label, String value, ColorScheme cs) => Expanded(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(value,
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
        const SizedBox(height: 2),
        Text(label,
            style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
      ]));

  Widget _frame({required Widget child}) => Card(
      margin: EdgeInsets.zero,
      child: Padding(padding: const EdgeInsets.all(14), child: child));
}
