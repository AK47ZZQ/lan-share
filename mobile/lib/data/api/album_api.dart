// 精选专辑 API（v1.0 · 2026-09-11）
//
// 收编 photos_page / album_detail_page 专辑侧全部裸 dio 直发。契约与 Web 端同款：
//   GET    /api/albums              专辑列表（count/cover 概要）
//   POST   /api/albums              新建（响应带生成的 id）
//   GET    /api/albums/{id}         详情（title/created_by/month/items）
//   PUT    /api/albums/{id}/items   编辑条目（add/remove 路径表，服务端单次上限 500）
//   DELETE /api/albums/{id}         删除（admin 或创建者，服务端兜底 403）
//
// 列表/详情返回原始 Map：列表条目解析（_Album）留在 photos_page（仅该页
// 使用），详情字段判读（旧月份书签型专辑退化、title/created_by 回填兜底）
// 是展示层决策，原样留在详情页——分层收编只搬请求，不改行为。
import 'package:dio/dio.dart';

import 'api_client.dart';

class AlbumApi {
  AlbumApi._();

  static Future<Dio> _dio() => ApiClient.I.dio();

  static String _enc(String id) => Uri.encodeComponent(id);

  /// 专辑列表（照片页封面迷你卡行）：count/cover 概要，原始 Map 列表
  static Future<List<dynamic>> list() async {
    final dio = await _dio();
    final r = await dio.get('/api/albums');
    return r.data as List;
  }

  /// 新建精选专辑：返回服务端生成的 id（null/空=创建失败，调用方提示）
  static Future<String?> create(String title) async {
    final dio = await _dio();
    final r = await dio.post('/api/albums', data: {'title': title});
    return r.data['id'] as String?;
  }

  /// 专辑详情：title/created_by/month + 旧月份书签型专辑的 items 路径表
  static Future<Map<String, dynamic>> detail(String id) async {
    final dio = await _dio();
    return (await dio.get('/api/albums/${_enc(id)}')).data
        as Map<String, dynamic>;
  }

  /// 加入条目（选照片页按批提交，每批 ≤200 由调用方编排，逐批幂等去重）
  static Future<void> addItems(String id, List<String> paths) async {
    final dio = await _dio();
    await dio.put('/api/albums/${_enc(id)}/items', data: {'add': paths});
  }

  /// 移出条目（只移出专辑，不删除照片本身）
  static Future<void> removeItems(String id, List<String> paths) async {
    final dio = await _dio();
    await dio.put('/api/albums/${_enc(id)}/items', data: {'remove': paths});
  }

  /// 删除专辑：admin 或创建者可删（403 由服务端兜底）
  static Future<void> delete(String id) async {
    final dio = await _dio();
    await dio.delete('/api/albums/${_enc(id)}');
  }
}
