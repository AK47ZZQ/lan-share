// 站内通知前台轮询（v0.11，推送"轮询起步"版）：App 前台期间每 60s 拉一次
// /api/notifications，未读数比上一次多 → 取最新一条发系统通知（一次事件一条，
// 不连环轰炸；点通知打开 App 即是通知中心入口）。
// 边界：仅前台（后台推送属厂商通道阶段 2）；登录失败/网络差静默吞掉；
// "已读"仍以通知中心页面打开为准，这里只做"告知"。
import 'dart:async';

import '../api/api_client.dart';
import 'notify.dart';

class InboxPoll {
  InboxPoll._();
  static final InboxPoll I = InboxPoll._();

  Timer? _timer;
  bool _busy = false;
  int _lastUnread = -1; // -1 = 首次对齐基线不提醒（避免冷启动炸历史通知）

  void start() {
    _timer ??= Timer.periodic(const Duration(seconds: 60), (_) => _tick());
    _tick();
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
  }

  Future<void> _tick() async {
    if (_busy) return;
    _busy = true;
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/notifications');
      final unread = (r.data['unread'] as num?)?.toInt() ?? 0;
      if (_lastUnread >= 0 && unread > _lastUnread) {
        final items = (r.data['items'] as List?) ?? const [];
        if (items.isNotEmpty) {
          final n = items.first as Map<String, dynamic>;
          await TransferNotifier.I.inbox(
              title: (n['title'] as String?) ?? 'LAN Share',
              body: (n['body'] as String?) ?? '');
        }
      }
      _lastUnread = unread;
    } catch (_) {
      // 未登录/网络差：保持上次基线，下轮再试
    } finally {
      _busy = false;
    }
  }
}
