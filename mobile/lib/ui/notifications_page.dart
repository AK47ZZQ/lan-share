// 通知中心（0.8.0 对齐 Web NotificationsBell 的"通知"Tab）：
// /api/notifications 列表 + 打开即全部已读。定向分享被下载/新成员加入等
// 站内事件在此呈现；动态流（活动）仍是另一页。
import 'package:flutter/material.dart';

import '../data/api/api_client.dart';

class _Notif {
  final int id;
  final String type;
  final String title;
  final String body;
  final int createdAt;
  final bool unread;
  _Notif.fromJson(Map<String, dynamic> j)
      : id = (j['id'] as num).toInt(),
        type = j['type'] as String? ?? '',
        title = j['title'] as String? ?? '',
        body = j['body'] as String? ?? '',
        createdAt = (j['created_at'] as num?)?.toInt() ?? 0,
        unread = j['read_at'] == null;
}

class NotificationsPage extends StatefulWidget {
  const NotificationsPage({super.key});
  @override
  State<NotificationsPage> createState() => _NotificationsPageState();
}

class _NotificationsPageState extends State<NotificationsPage> {
  List<_Notif>? _items;
  int _unread = 0;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/notifications');
      final raw = ((r.data['items'] as List?) ?? const []).toList();
      final items = raw
          .map((e) => _Notif.fromJson(e as Map<String, dynamic>))
          .toList();
      if (!mounted) return;
      setState(() {
        _items = items;
        _unread = (r.data['unread'] as num?)?.toInt() ?? 0;
        _error = null;
      });
      // 打开页面即全部已读（与 Web 一致），徽标随之归零
      if (_unread > 0) {
        await dio.post('/api/notifications/read');
      }
    } catch (e) {
      if (!mounted) return;
      final s = e.toString();
      setState(() => _error = s.length > 160 ? s.substring(0, 160) : s);
    }
  }

  IconData _icon(String type) => switch (type) {
        'share_download' || 'target_download' => Icons.download_outlined,
        'member_joined' || 'invite_redeemed' => Icons.waving_hand_outlined,
        'share' => Icons.link,
        _ => Icons.notifications_outlined,
      };

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final items = _items;
    return Scaffold(
        appBar: AppBar(title: const Text('通知')),
        body: items == null
            ? _error != null
                ? ListView(children: [
                    const SizedBox(height: 100),
                    Center(child: Text(_error!,
                        textAlign: TextAlign.center,
                        style:
                            const TextStyle(fontSize: 12, color: Colors.grey))),
                    Center(
                        child: TextButton(
                            onPressed: _load, child: const Text('重试'))),
                  ])
                : const Center(child: CircularProgressIndicator())
            : RefreshIndicator(
                onRefresh: _load,
                child: items.isEmpty
                    ? ListView(children: const [
                        SizedBox(height: 160),
                        Center(
                            child: Text('暂无通知',
                                style: TextStyle(color: Colors.grey))),
                      ])
                    : ListView(children: [
                        if (_unread > 0)
                          Padding(
                              padding:
                                  const EdgeInsets.fromLTRB(16, 10, 16, 0),
                              child: Text('$_unread 条未读 · 打开已全部标记已读',
                                  style: TextStyle(
                                      fontSize: 11, color: cs.outline))),
                        for (final n in items)
                          ListTile(
                              leading: Badge(
                                  isLabelVisible: n.unread,
                                  smallSize: 8,
                                  child: CircleAvatar(
                                      radius: 16,
                                      backgroundColor: cs.surfaceContainerHighest,
                                      child: Icon(_icon(n.type), size: 17))),
                              title: Text(n.title,
                                  style: TextStyle(
                                      fontSize: 14,
                                      fontWeight: n.unread
                                          ? FontWeight.w600
                                          : FontWeight.normal)),
                              subtitle: n.body.isEmpty
                                  ? null
                                  : Text(n.body,
                                      maxLines: 2,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(fontSize: 12)),
                              trailing: Text(_rel(n.createdAt),
                                  style: TextStyle(
                                      fontSize: 11, color: cs.outline))),
                      ])));
  }

  String _rel(int sec) {
    final d = DateTime.now().millisecondsSinceEpoch ~/ 1000 - sec;
    if (d < 60) return '刚刚';
    if (d < 3600) return '${d ~/ 60} 分钟前';
    if (d < 86400) return '${d ~/ 3600} 小时前';
    if (d < 86400 * 30) return '${d ~/ 86400} 天前';
    return DateTime.fromMillisecondsSinceEpoch(sec * 1000)
        .toString()
        .split(' ')
        .first;
  }
}
