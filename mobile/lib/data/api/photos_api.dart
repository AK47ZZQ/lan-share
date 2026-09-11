// 照片时间线 API（v1.0 · 2026-09-11）
//
// 收编 photos_page / album_detail_page（含选照片 AlbumPickerPage）照片侧
// 全部裸 dio 直发。契约与 Web 端同款：
//   GET    /api/photos        时间线分页（limit/offset/refresh/media_type/
//                             month/sort/album，缺省即不带该键）
//   GET    /api/photos/facets 月份分布（快滚轴/按月快跳/按月全选共用）
//   DELETE /api/files/{p}     多选删除（进服务端回收站，与 FilesApi.delete
//                             同端点；statusCode==200 才算成功，见下）
//
// 返回值是 items 原始 Map 列表：PhotoItem 模型留在 ui/photo_models.dart
// （photos_page/photo_viewer/recent_page/files_page 多页共用），本模块只搬
// 请求发起，不重复建模——逐条解析口径原样留在调用方。
import 'package:dio/dio.dart';

import 'api_client.dart';

class PhotosApi {
  PhotosApi._();

  static Future<Dio> _dio() => ApiClient.I.dio();

  static String _enc(String p) => p.split('/').map(Uri.encodeComponent).join('/');

  /// 时间线分页：返回 items 原始 Map 列表（PhotoItem.fromJson 在调用方）。
  /// 可选参数与原页内 query 逐项对应：null/false 即不带该键，契约零变更。
  static Future<List<dynamic>> list({
    required int limit,
    int offset = 0,
    bool refresh = false,
    String? mediaType,
    String? month,
    bool byAdded = false,
    String? album,
  }) async {
    final dio = await _dio();
    final r = await dio.get('/api/photos', queryParameters: {
      'limit': limit,
      'offset': offset,
      if (refresh) 'refresh': true,
      'media_type': ?mediaType,
      'month': ?month,
      if (byAdded) 'sort': 'added',
      'album': ?album,
    });
    return r.data['items'] as List;
  }

  /// 服务端脉搏（0.7.2）：limit=1&refresh=1 轻探测，返回 (总数, 首张 path)；
  /// 快照比对与"变了才整页强刷"的节奏留在 UI。
  static Future<({int total, String firstPath})> probe() async {
    final dio = await _dio();
    final r = await dio.get('/api/photos',
        queryParameters: {'limit': 1, 'refresh': true});
    final total = (r.data['total'] as num?)?.toInt() ?? 0;
    final items = (r.data['items'] as List? ?? const []);
    return (
      total: total,
      firstPath:
          items.isEmpty ? '' : ((items.first as Map)['path'] as String? ?? ''),
    );
  }

  /// 月份分布 facets：解析成 {YYYY-MM: 张数}（空月份键剔除；快滚轴/按月
  /// 快跳/按月全选三处共用）；失败由调用方按各自策略兜底。
  static Future<Map<String, int>> monthFacets() async {
    final dio = await _dio();
    final r = await dio.get('/api/photos/facets');
    return {
      for (final m in ((r.data['months'] as List?) ?? const []))
        (m as Map)['month'] as String? ?? '':
            (m['count'] as num?)?.toInt() ?? 0,
    }..removeWhere((k, _) => k.isEmpty);
  }

  /// 多选删除（进服务端回收站，30 天保留期内可恢复）。与 FilesApi.delete
  /// 同端点但不复用：那边是 void 形态，这里保留 photos_page 的逐张计数
  /// 口径——只有 statusCode==200 算成功（2xx 非 200 也计失败），非 2xx
  /// 由 dio 抛出交调用方 catch。
  static Future<bool> deleteToTrash(String path) async {
    final dio = await _dio();
    final r = await dio.delete('/api/files/${_enc(path)}');
    return r.statusCode == 200;
  }
}
