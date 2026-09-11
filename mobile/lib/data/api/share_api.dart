// 分享域 HTTP 契约（B8 裸 dio 收编：自 share/share_service.dart 抽出）。
// 薄封装只管"怎么发请求"；ShareItem 模型/链接拼装/创建失败文案仍在
// ShareService——本模块返回原始 Response，调用方按需取字段。
import 'package:dio/dio.dart';

import 'api_client.dart';

class ShareApi {
  ShareApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  /// 我的分享列表（返回 {token: {...}} 原始 Map）。
  static Future<Response> list() async {
    final dio = await _dio();
    return dio.get('/api/shares');
  }

  /// 创建分享（Form 契约）：password 空=不加密，target 空=非定向。
  static Future<Response> create({
    required String path,
    required int expiresHours,
    String? password,
    int? maxDownloads,
    String? target,
  }) async {
    final dio = await _dio();
    return dio.post('/api/share', data: FormData.fromMap({
      'path': path,
      'expires_hours': expiresHours,
      if (password != null && password.isNotEmpty) 'password': password,
      'max_downloads': ?maxDownloads,
      if (target != null && target.isNotEmpty) 'target': target,
    }));
  }

  static Future<void> revoke(String token) async {
    final dio = await _dio();
    await dio.delete('/api/shares/$token');
  }

  /// 编辑分享（0.6.0）：传 null 的字段不动。
  static Future<void> edit(String token,
      {int? expiresHours, int? maxDownloads}) async {
    final dio = await _dio();
    await dio.patch('/api/shares/$token', data: {
      'expires_hours': ?expiresHours,
      'max_downloads': ?maxDownloads,
    });
  }

  /// 在用成员名单（定向分享收件人候选，GET /api/members）。
  static Future<Response> members() async {
    final dio = await _dio();
    return dio.get('/api/members');
  }
}
