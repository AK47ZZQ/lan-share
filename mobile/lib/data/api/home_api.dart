// 首页总览域 API（T7 裸 dio 收编第三批：从 home_page 抽出 UI 层手拼请求）：
// 磁盘水位 / 最近上传 / 家庭动态 / 通知未读数 / 文件名搜索。
// 搜索与最近上传同源同构（app/api/files.py 相邻端点，同一 file_dict 形态），
// /api/search 收编时并入本模块（原 recent_page 裸 dio）。
import 'package:dio/dio.dart';

import '../activity.dart';
import 'notifications_api.dart';
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

  /// 站内通知未读数（铃铛徽标用，0.8.0；B8 收敛为 notifications 单出口委托）
  static Future<int> notifUnread() => NotificationsApi.unreadCount();

  /// 存储详情（storage_detail_page，v0.15）：磁盘总况 + admin 分目录构成 /
  /// 成员私人空间与配额。字段随角色变化较多，返回原始 Map 由页面按需取
  /// （非 Map 响应照旧抛类型错，页面显示加载失败）。
  static Future<Map<String, dynamic>> diskDetail() async {
    final dio = await _dio();
    final r = await dio.get('/api/disk/detail');
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 公网入口（管理端配置的主/备隧道；手机上传二维码展示用）。
  /// 值可能为空串=未配置；trim 与原 UI 一致。
  static Future<({String main, String alt})> publicUrls() async {
    final dio = await _dio();
    final r = await dio.get('/api/public-url');
    return (
      main: (r.data['public_base_url'] as String?)?.trim() ?? '',
      alt: (r.data['public_base_url_alt'] as String?)?.trim() ?? '',
    );
  }

  /// 文件名搜索（服务端 rglob 子串匹配，上限 50 条；recent_page 搜索框用）。
  /// 返回原始 Map 列表，条目形态与 [recent] 相同。
  static Future<List<dynamic>> search(String q) async {
    final dio = await _dio();
    final r = await dio.get('/api/search', queryParameters: {'q': q});
    return (r.data as List).whereType<Map>().toList();
  }
}
