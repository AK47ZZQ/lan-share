// 家里动态页（0.6.2）：/api/activity 全量列表。首页动态卡的"全部 ›"落点。
// 只读流，下拉刷新；点击不跳转（手机端目录跳转语义与 Web 不同，暂不做）。
import 'package:flutter/material.dart';

import '../data/activity.dart';
import '../data/api/api_client.dart';

class ActivityPage extends StatefulWidget {
  const ActivityPage({super.key});

  @override
  State<ActivityPage> createState() => _ActivityPageState();
}

class _ActivityPageState extends State<ActivityPage> {
  List<ActivityItem>? _items;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/activity', queryParameters: {'limit': 100});
      final items = ActivityItem.listFrom(r.data);
      if (!mounted) return;
      setState(() {
        _items = items;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '动态加载失败，下拉重试');
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final items = _items;
    return Scaffold(
        appBar: AppBar(title: const Text('动态')),
        body: RefreshIndicator(
            onRefresh: _load,
            child: items == null && _error == null
                ? const Center(child: CircularProgressIndicator())
                : items != null && items.isEmpty
                    ? ListView(children: const [
                        SizedBox(height: 120),
                        Center(
                            child: Text('还没有动态\n上传、分享、新成员加入都会显示在这里',
                                textAlign: TextAlign.center,
                                style: TextStyle(color: Colors.grey))),
                      ])
                    : _error != null
                        ? ListView(children: [
                            const SizedBox(height: 120),
                            Center(child: Text(_error!)),
                          ])
                        : ListView.separated(
                            padding: const EdgeInsets.only(bottom: 24),
                            itemCount: items!.length,
                            separatorBuilder: (_, _) => Divider(
                                height: 0.5,
                                thickness: 0.5,
                                color: cs.outline.withValues(alpha: 0.2)),
                            itemBuilder: (_, i) =>
                                _tile(context, items[i], cs))));
  }

  Widget _tile(BuildContext context, ActivityItem n, ColorScheme cs) =>
      ListTile(
        leading: CircleAvatar(
            radius: 16,
            backgroundColor: cs.primary.withValues(alpha: 0.12),
            child: Icon(_iconFor(n.action), size: 17, color: cs.primary)),
        title: Text(activityText(n),
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 14)),
        subtitle: Text(activityRelTime(n.ts),
            style: TextStyle(fontSize: 12, color: cs.outline)),
      );
}

/// 动作 → 图标（与 Web ACTION_ICON 语义对齐）
IconData _iconFor(String action) => switch (action) {
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
