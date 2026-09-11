// 站内通知域 API（裸 dio 收编：从 notifications_page 抽出 UI 层手拼请求）：
// 通知列表/单条已读/全部已读/单条删除/接收偏好（0.8.0，9.6-P2-14 增强）。
//
// 归属说明：动态流 /api/activity 与最近文件 /api/recent、/api/search 不在本域
// ——前两者 home_api.dart 已覆盖（HomeApi.activity / HomeApi.recent），搜索
// 与 /api/recent 同源同构（app/api/files.py 相邻端点），一并归 HomeApi。
import 'package:dio/dio.dart';

import 'api_client.dart';

/// 站内通知条目。`read` 由服务端给 bool，客户端翻成 unread（0.8.1 审查修复：
/// 字段是 read，非 read_at）。
class NotificationItem {
  final int id;
  final String type;
  final String title;
  final String body;
  final int createdAt;
  final bool unread;

  const NotificationItem({
    required this.id,
    required this.type,
    required this.title,
    required this.body,
    required this.createdAt,
    required this.unread,
  });

  factory NotificationItem.fromJson(Map<String, dynamic> j) =>
      NotificationItem(
          id: (j['id'] as num?)?.toInt() ?? 0,
          type: j['type'] as String? ?? '',
          title: j['title'] as String? ?? '',
          body: j['body'] as String? ?? '',
          createdAt: (j['created_at'] as num?)?.toInt() ?? 0,
          unread: j['read'] != true);

  /// 原样回填构造已读副本（点按单条已读的乐观更新用）
  NotificationItem asRead() => NotificationItem.fromJson({
        'id': id,
        'type': type,
        'title': title,
        'body': body,
        'created_at': createdAt,
        'read': true,
      });

  /// 通知类型 → 偏好组/过滤 tab：share=分享、admin=广播、其余归系统
  String get group =>
      type == 'share' ? 'share' : (type == 'admin' ? 'admin' : 'system');
}

class NotificationsApi {
  NotificationsApi._();

  static Future<Dio> _dio() => ApiClient.I.dio();

  /// 通知列表 + 未读数（items/unread 同包返回）
  static Future<({List<NotificationItem> items, int unread})> list() async {
    final dio = await _dio();
    final r = await dio.get('/api/notifications');
    final raw = ((r.data['items'] as List?) ?? const []).toList();
    return (
      items: raw
          .map((e) =>
              NotificationItem.fromJson(e as Map<String, dynamic>))
          .toList(),
      unread: (r.data['unread'] as num?)?.toInt() ?? 0,
    );
  }

  /// 单条标记已读（不再"打开即全量已读"误伤）
  static Future<void> markRead(int id) async {
    final dio = await _dio();
    await dio.post('/api/notifications/$id/read');
  }

  /// 全部已读（AppBar 按钮手动触发）
  static Future<void> markAllRead() async {
    final dio = await _dio();
    await dio.post('/api/notifications/read');
  }

  /// 单条删除（不可恢复，服务端直接删）
  static Future<void> delete(int id) async {
    final dio = await _dio();
    await dio.delete('/api/notifications/$id');
  }

  /// 接收偏好（缺失的组视为开启，与 Web 端 `!= false` 口径一致）
  static Future<Map<String, bool>> prefs() async {
    final dio = await _dio();
    final r = await dio.get('/api/notifications/prefs');
    return {
      'share': r.data['share'] != false,
      'admin': r.data['admin'] != false,
      'system': r.data['system'] != false,
    };
  }

  /// 保存单组偏好（key: share|admin|system）
  static Future<void> savePref(String key, bool enabled) async {
    final dio = await _dio();
    await dio.put('/api/notifications/prefs', data: {key: enabled});
  }
}
