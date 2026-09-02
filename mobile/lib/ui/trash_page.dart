// 回收站（App v0.6.0）：列表 / 恢复 / 彻底删除；30 天保留期提示。
// 服务端契约：GET /api/trash、POST /api/trash/{id}/restore、DELETE /api/trash/{id}
// （清空回收站仅 admin 有端点，App 不给全员入口——误清空间换不回）。
import 'package:flutter/material.dart';

import '../data/api/api_client.dart';

class TrashItem {
  final int id;
  final String name;
  final String origPath;
  final String sizeHuman;
  final String deletedAt;
  final int daysLeft;
  TrashItem({required this.id, required this.name, required this.origPath,
      required this.sizeHuman, required this.deletedAt, required this.daysLeft});

  factory TrashItem.fromJson(Map<String, dynamic> j) => TrashItem(
      id: (j['id'] as num).toInt(),
      name: j['name'] as String? ?? '',
      origPath: j['orig_path'] as String? ?? '',
      sizeHuman: j['size_human'] as String? ?? '-',
      deletedAt: j['deleted_at'] as String? ?? '',
      daysLeft: (j['days_left'] as num?)?.toInt() ?? 0);
}

class TrashPage extends StatefulWidget {
  const TrashPage({super.key});

  @override
  State<TrashPage> createState() => _TrashPageState();
}

class _TrashPageState extends State<TrashPage> {
  List<TrashItem>? _items;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/trash');
      final items = (r.data as List)
          .map((e) => TrashItem.fromJson(Map<String, dynamic>.from(e as Map)))
          .toList();
      if (!mounted) return;
      setState(() => _items = items);
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _items = [];
        _error = '回收站加载失败';
      });
    }
  }

  Future<void> _restore(TrashItem it) async {
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/trash/${it.id}/restore');
      _toast('已恢复「${it.name}」');
      await _load();
    } catch (_) {
      _toast('恢复失败（原位置可能已存在同名占位）');
    }
  }

  Future<void> _deleteForever(TrashItem it) async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: Text('彻底删除「${it.name}」？'),
                content: const Text('此操作不可恢复。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('彻底删除')),
                ]));
    if (ok != true) return;
    try {
      final dio = await ApiClient.I.dio();
      await dio.delete('/api/trash/${it.id}');
      _toast('已彻底删除');
      await _load();
    } catch (_) {
      _toast('删除失败');
    }
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg), duration: const Duration(seconds: 2)));
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    return Scaffold(
        appBar: AppBar(title: const Text('回收站')),
        body: items == null
            ? const Center(child: CircularProgressIndicator())
            : items.isEmpty
                ? ListView(children: [
                    const SizedBox(height: 120),
                    Center(
                        child: Text(_error ?? '回收站是空的',
                            style: const TextStyle(color: Colors.grey))),
                    const SizedBox(height: 8),
                    Center(child: TextButton(
                        onPressed: _load, child: const Text('刷新'))),
                  ])
                : RefreshIndicator(
                    onRefresh: _load,
                    child: ListView.builder(
                        itemCount: items.length,
                        itemBuilder: (context, i) {
                          final it = items[i];
                          return ListTile(
                            leading: const Icon(Icons.delete_outline),
                            title: Text(it.name,
                                maxLines: 1, overflow: TextOverflow.ellipsis),
                            subtitle: Text(
                                '${it.sizeHuman} · 剩 ${it.daysLeft} 天 · 原位置 ${it.origPath}',
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(fontSize: 12)),
                            trailing: PopupMenuButton<String>(
                                onSelected: (v) {
                                  if (v == 'restore') _restore(it);
                                  if (v == 'delete') _deleteForever(it);
                                },
                                itemBuilder: (_) => [
                                      const PopupMenuItem(
                                          value: 'restore', child: Text('恢复到原位置')),
                                      const PopupMenuItem(
                                          value: 'delete',
                                          child: Text('彻底删除',
                                              style:
                                                  TextStyle(color: Colors.red))),
                                    ]),
                          );
                        })));
  }
}
