// 回收站 API（v1.0 · 2026-09-11）
//
// 收编 `trash_page` 里 4 处裸 dio 直发（UI 分层守卫基线内）。契约与 Web 端同款：
//   GET    /api/trash                 回收站列表（30 天保留期）
//   POST   /api/trash/{id}/restore    恢复到原位置
//   DELETE /api/trash/{id}            彻底删除单条
//   DELETE /api/trash                 清空（**仅 admin**，含其他成员的项目）
//
// 响应模型 `TrashItem` 一并落在这里——原先定义在页面文件里，换个页面就得
// 重抄一遍解析。分层的意义就是"契约只解析一处"。
import 'package:dio/dio.dart';

import 'api_client.dart';

/// 回收站条目。`days_left` 由服务端按 30 天保留期算好，客户端不重复计算。
class TrashItem {
  final int id;
  final String name;
  final String origPath;
  final String sizeHuman;
  final String deletedAt;
  final int daysLeft;

  const TrashItem({
    required this.id,
    required this.name,
    required this.origPath,
    required this.sizeHuman,
    required this.deletedAt,
    required this.daysLeft,
  });

  factory TrashItem.fromJson(Map<String, dynamic> j) => TrashItem(
      id: (j['id'] as num).toInt(),
      name: j['name'] as String? ?? '',
      origPath: j['orig_path'] as String? ?? '',
      sizeHuman: j['size_human'] as String? ?? '-',
      deletedAt: j['deleted_at'] as String? ?? '',
      daysLeft: (j['days_left'] as num?)?.toInt() ?? 0);
}

class TrashApi {
  TrashApi._();

  static Future<Dio> _dio() => ApiClient.I.dio();

  /// 回收站列表
  static Future<List<TrashItem>> list() async {
    final r = await (await _dio()).get('/api/trash');
    return (r.data as List)
        .map((e) => TrashItem.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// 恢复到原位置（原位置存在同名占位时服务端会拒绝）
  static Future<void> restore(int id) async {
    await (await _dio()).post('/api/trash/$id/restore');
  }

  /// 彻底删除单条（不可恢复）
  static Future<void> deleteForever(int id) async {
    await (await _dio()).delete('/api/trash/$id');
  }

  /// 清空回收站（仅 admin）：会删除**所有成员**的回收站项目，含 private。
  static Future<void> emptyAll() async {
    await (await _dio()).delete('/api/trash');
  }
}
