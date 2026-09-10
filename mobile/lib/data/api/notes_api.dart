// 笔记域 API（T7 裸 dio 收编第一批）：聚合列表端点单实现。
import 'package:dio/dio.dart';

import 'api_client.dart';

class NotesApi {
  NotesApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  /// 聚合列表（v0.21）：一次拿到全部笔记（含子分类与前 200 字摘要）。
  /// 返回 (items 原始 Map 列表, categories path→count)；字段解析留在
  /// 调用方的展示模型（_Note）里——本层只负责请求与形状兜底。
  static Future<(List<dynamic>, Map<String, int>)> list(
      {required String dir, required String sort}) async {
    final dio = await _dio();
    final r = await dio.get('/api/notes/list',
        queryParameters: {'path': dir, 'sort': sort});
    final items = (r.data['items'] as List? ?? const []);
    final cats = <String, int>{};
    for (final c in (r.data['categories'] as List? ?? const [])) {
      final m = c as Map;
      final path = (m['path'] as String?) ?? '';
      if (path.isNotEmpty) cats[path] = (m['count'] as num?)?.toInt() ?? 0;
    }
    return (items, cats);
  }
}
