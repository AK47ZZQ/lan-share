// 回收站（App v0.6.0）：列表 / 恢复 / 彻底删除；30 天保留期提示。
// v1.0：请求全部收编到 data/api/trash_api.dart（页面不再直连 dio），
// 两处手写确认框统一走 widgets/kit.dart 的 showConfirmDialog。
// 清空回收站（0.6.3 新增入口）：DELETE /api/trash 仅 admin——一次清空所有
// 成员的回收站项目，与 Web 端同款语义；按钮只对 admin 显示 + 二次确认。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/trash_api.dart';
import '../state/session.dart';
import 'widgets/brand.dart';
import 'widgets/kit.dart';

class TrashPage extends ConsumerStatefulWidget {
  const TrashPage({super.key});

  @override
  ConsumerState<TrashPage> createState() => _TrashPageState();
}

class _TrashPageState extends ConsumerState<TrashPage> {
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
      final items = await TrashApi.list();
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
      await TrashApi.restore(it.id);
      showToast('已恢复「${it.name}」');
      await _load();
    } catch (_) {
      showToast('恢复失败（原位置可能已存在同名占位）');
    }
  }

  Future<void> _deleteForever(TrashItem it) async {
    // 不可逆操作：确认框走统一件并标 danger（确认按钮用 error 色）
    final ok = await showConfirmDialog(context,
        title: '彻底删除「${it.name}」？',
        message: '此操作不可恢复。',
        confirmText: '彻底删除',
        danger: true);
    if (!ok) return;
    try {
      await TrashApi.deleteForever(it.id);
      showToast('已彻底删除');
      await _load();
    } catch (_) {
      showToast('删除失败');
    }
  }

  /// 清空整个回收站（仅 admin；端点会删掉所有成员的项目，含 private）。
  /// 二次确认写明后果——这与逐项"彻底删除"不是一个量级的不可逆。
  Future<void> _emptyAll() async {
    final ok = await showConfirmDialog(context,
        title: '清空整个回收站？',
        message: '将永久删除回收站中的全部项目（含其他成员放入的），此操作不可恢复。',
        confirmText: '清空',
        danger: true);
    if (!ok) return;
    try {
      await TrashApi.emptyAll();
      showToast('回收站已清空');
      await _load();
    } catch (_) {
      showToast('清空失败（需要管理员身份）');
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    final isAdmin = ref.watch(sessionProvider).role == 'admin';
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
        appBar: AppBar(
            title: const Text('回收站'),
            actions: [
              if (isAdmin && items != null && items.isNotEmpty)
                TextButton.icon(
                    onPressed: _emptyAll,
                    icon: Icon(Icons.delete_sweep_outlined, color: cs.error),
                    label: Text('清空',
                        style: TextStyle(color: cs.error))),
            ]),
        body: items == null
            ? const Center(child: CircularProgressIndicator())
            : items.isEmpty
                ? ListView(children: [
                    EmptyState(
                        title: _error ?? '回收站是空的',
                        hint: '删除的文件在这里保留 30 天',
                        action: TextButton(onPressed: _load, child: const Text('刷新'))),
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
                                // 取排版令牌而非字面量：改层级只动 theme
                                style: Theme.of(context).textTheme.bodySmall),
                            trailing: PopupMenuButton<String>(
                                onSelected: (v) {
                                  if (v == 'restore') _restore(it);
                                  if (v == 'delete') _deleteForever(it);
                                },
                                itemBuilder: (_) => [
                                      const PopupMenuItem(
                                          value: 'restore', child: Text('恢复到原位置')),
                                      // 危险项用主题 error 色——原先硬编码 Colors.red，
                                      // 在深色/AMOLED 下对比不足，且不随色板走
                                      PopupMenuItem(
                                          value: 'delete',
                                          child: Text('彻底删除',
                                              style: TextStyle(color: cs.error))),
                                    ]),
                          );
                        })));
  }
}
