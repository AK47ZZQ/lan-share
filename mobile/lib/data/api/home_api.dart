// 首页总览域 API（T7 裸 dio 收编第三批：从 home_page 抽出 UI 层手拼请求）：
// 磁盘水位 / 最近上传 / 家庭动态 / 通知未读数。
import 'package:dio/dio.dart';

import '../activity.dart';
import 'api_client.dart';

class HomeApi {
  HomeApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  /// 磁盘水位：返回 (used, total, free 字节, 共享空间人读尺寸)
  static Future<({int used, int total, int free, String shareSize})>
      disk() async {
    final dio = await _dio();
    final r = await dio.get('/api/disk');
    return (
      used: (r.data['used_bytes'] as num?)?.toInt() ?? 0,
      total: (r.data['total_bytes'] as num?)?.toInt() ?? 0,
      free: (r.data['free_bytes'] as num?)?.toInt() ?? 0,
      shareSize: r.data['share_size'] as String? ?? '',
    );
  }

  /// 最近上传（全库最近修改文件，原始 Map 列表；类型色块筛选在 UI）
  static Future<List<dynamic>> recent({int limit = 100}) async {
    final dio = await _dio();
    final r = await dio.get('/api/recent', queryParameters: {'limit': limit});
    return (r.data as List).whereType<Map>().toList();
  }

  /// 家庭动态（0.6.2，与 Web 同源：隐私过滤/聚合在服务端）
  static Future<List<ActivityItem>> activity({int limit = 30}) async {
    final dio = await _dio();
    final r = await dio.get('/api/activity', queryParameters: {'limit': limit});
    return ActivityItem.listFrom(r.data);
  }

  /// 站内通知未读数（铃铛徽标用，0.8.0）
  static Future<int> notifUnread() async {
    final dio = await _dio();
    final r = await dio.get('/api/notifications');
    return (r.data['unread'] as num?)?.toInt() ?? 0;
  }
}
