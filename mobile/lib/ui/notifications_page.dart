// 通知中心（0.8.0 对齐 Web NotificationsBell；9.6-P2-14 通知管理增强）：
// · 分类过滤：全部/分享/广播/系统（按 type 分组）
// · 单条操作：点按=标记已读（不再"打开即全量已读"误伤），滑动=删除
// · 通知偏好：按类型组（分享/广播/系统）开关站内接收，AppBar 齿轮进入
// · 全部已读收进 AppBar 按钮手动触发
import 'package:flutter/material.dart';

import '../data/api/errors.dart';
import '../data/api/notifications_api.dart';
import 'theme/design_tokens.dart';

class NotificationsPage extends StatefulWidget {
  const NotificationsPage({super.key});
  @override
  State<NotificationsPage> createState() => _NotificationsPageState();
}

class _NotificationsPageState extends State<NotificationsPage> {
  List<NotificationItem>? _items;
  int _unread = 0;
  String? _error;
  String _tab = 'all'; // all | share | admin | system
  Map<String, bool> _prefs = {};

  @override
  void initState() {
    super.initState();
    _load();
    _loadPrefs();
  }

  Future<void> _loadPrefs() async {
    try {
      final prefs = await NotificationsApi.prefs();
      if (!mounted) return;
      setState(() => _prefs = prefs);
    } catch (_) {/* 偏好加载失败不打扰列表 */}
  }

  Future<void> _savePref(String key, bool v) async {
    setState(() => _prefs[key] = v);
    try {
      await NotificationsApi.savePref(key, v);
    } catch (_) {
      if (!mounted) return;
      setState(() => _prefs[key] = !v); // 回滚 UI，服务端为准
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('偏好保存失败')));
    }
  }

  Future<void> _load() async {
    try {
      final res = await NotificationsApi.list();
      if (!mounted) return;
      setState(() {
        _items = res.items;
        _unread = res.unread;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = errDetail(e, max: 160));
    }
  }

  Future<void> _markRead(NotificationItem n) async {
    if (!n.unread) return;
    setState(() {
      _items = _items?.map((x) => x.id == n.id ? x.asRead() : x).toList();
      _unread = (_unread - 1).clamp(0, 1 << 31);
    });
    try {
      await NotificationsApi.markRead(n.id);
    } catch (_) {
      _load(); // 失败以服务端为准回拉
    }
  }

  Future<void> _deleteOne(NotificationItem n) async {
    setState(() {
      _items = _items?.where((x) => x.id != n.id).toList();
      if (n.unread) _unread = (_unread - 1).clamp(0, 1 << 31);
    });
    try {
      await NotificationsApi.delete(n.id);
    } catch (_) {/* 列表已本地移除，失败无碍 */}
  }

  Future<void> _markAllRead() async {
    try {
      await NotificationsApi.markAllRead();
      if (!mounted) return;
      setState(() {
        _unread = 0;
        _items = _items?.map((n) => n.asRead()).toList();
      });
    } catch (_) {}
  }

  Future<void> _showPrefs() async {
    await showModalBottomSheet(
        context: context,
        showDragHandle: true,
        builder: (bctx) => StatefulBuilder(
            builder: (bctx, setS) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  const Padding(
                      padding: EdgeInsets.all(Gap.sm),
                      child: Text('通知偏好',
                          style: TextStyle(fontWeight: FontWeight.bold))),
                  const Padding(
                      padding: EdgeInsets.fromLTRB(Gap.md, 0, Gap.md, Gap.xxs),
                      child: Text('关闭的类型不再出现在站内通知（站外 webhook 不受影响）',
                          style: TextStyle(fontSize: 11.5, color: Colors.grey))),
                  for (final e in const [
                    ('share', '📬 分享', '定向分享、取件提醒'),
                    ('admin', '📣 广播', '管理员手动发送的通知'),
                    ('system', '⚙️ 系统', '新成员加入、下载动态等'),
                  ])
                    SwitchListTile(
                        value: _prefs[e.$1] ?? true,
                        onChanged: (v) {
                          setS(() => _prefs[e.$1] = v);
                          _savePref(e.$1, v);
                        },
                        title: Text(e.$2, style: const TextStyle(fontSize: 14)),
                        subtitle: Text(e.$3,
                            style: const TextStyle(fontSize: 11.5))),
                  const SizedBox(height: Gap.xs),
                ]))));
  }

  List<NotificationItem> get _filtered {
    final all = _items ?? const <NotificationItem>[];
    if (_tab == 'all') return all;
    return all.where((n) => n.group == _tab).toList();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final items = _items;
    return Scaffold(
        appBar: AppBar(title: const Text('通知'), actions: [
          if (_unread > 0)
            TextButton(
                onPressed: _markAllRead, child: const Text('全部已读')),
          IconButton(
              tooltip: '通知偏好',
              icon: const Icon(Icons.tune),
              onPressed: _showPrefs),
        ]),
        body: Column(children: [
          // 分类过滤 tab（9.6-P2-14）
          Padding(
              padding: const EdgeInsets.fromLTRB(Gap.sm, 6, Gap.sm, 2),
              child: Row(children: [
                for (final t in const [
                  ('all', '全部'),
                  ('share', '分享'),
                  ('admin', '广播'),
                  ('system', '系统'),
                ])
                  Padding(
                      padding: const EdgeInsets.only(right: Gap.xs),
                      child: ChoiceChip(
                          label: Text(t.$2, style: const TextStyle(fontSize: 12.5)),
                          selected: _tab == t.$1,
                          visualDensity: VisualDensity.compact,
                          onSelected: (_) => setState(() => _tab = t.$1))),
              ])),
          Expanded(
              child: items == null
                  ? _error != null
                      ? ListView(children: [
                          const SizedBox(height: 100),
                          Center(child: Text(_error!,
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                  fontSize: 12, color: Colors.grey))),
                          Center(
                              child: TextButton(
                                  onPressed: _load,
                                  child: const Text('重试'))),
                        ])
                      : const Center(child: CircularProgressIndicator())
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: _filtered.isEmpty
                          ? ListView(children: const [
                              SizedBox(height: 160),
                              Center(
                                  child: Text('暂无通知',
                                      style: TextStyle(color: Colors.grey))),
                            ])
                          : ListView(padding: const EdgeInsets.only(bottom: Gap.xl), children: [
                              if (_unread > 0)
                                Padding(
                                    padding: const EdgeInsets.fromLTRB(Gap.md, Gap.xs, Gap.md, 0),
                                    child: Text('$_unread 条未读 · 点按单条标记已读',
                                        style: TextStyle(
                                            fontSize: 11,
                                            color: cs.onSurfaceVariant))),
                              // 滑动删除 + 点按已读（9.6-P2-14）
                              for (final n in _filtered)
                                Dismissible(
                                    key: ValueKey('notif-${n.id}'),
                                    direction: DismissDirection.endToStart,
                                    background: Container(
                                        alignment: Alignment.centerRight,
                                        padding: const EdgeInsets.only(right: Gap.lg),
                                        color: cs.errorContainer,
                                        child: Icon(Icons.delete_outline,
                                            color: cs.onErrorContainer)),
                                    onDismissed: (_) => _deleteOne(n),
                                    child: ListTile(
                                        leading: Badge(
                                            isLabelVisible: n.unread,
                                            smallSize: 8,
                                            child: CircleAvatar(
                                                radius: 16,
                                                backgroundColor:
                                                    cs.surfaceContainerHighest,
                                                child:
                                                    Icon(_icon(n.type), size: 17))),
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
                                                style: const TextStyle(
                                                    fontSize: 12)),
                                        trailing: Text(_rel(n.createdAt),
                                            style: TextStyle(
                                                fontSize: 11,
                                                color: cs.onSurfaceVariant)),
                                        onTap: () => _markRead(n))),
                            ]))),
        ]));
  }

  IconData _icon(String type) => switch (type) {
        'share_download' || 'target_download' => Icons.download_outlined,
        'member_joined' || 'invite_redeemed' => Icons.waving_hand_outlined,
        'share' => Icons.link,
        _ => Icons.notifications_outlined,
      };

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
