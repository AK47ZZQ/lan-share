// 分享服务（M-A4 / APP-REQUIREMENTS FR-7）：与网页版分享管理对齐
// （创建/撤销/复制链接），端点全部复用现有服务端契约，无后端改动。
import 'package:dio/dio.dart';

import '../data/api/api_client.dart';
import '../data/local/prefs.dart';

class ShareItem {
  final String token;
  final String name;
  final String path;
  final String owner;
  final String? target; // 定向分享收件成员
  final String sizeHuman;
  final String expiresAt;
  final String expiresIn;
  final bool hasPassword;
  final String? password; // 服务端回明文（创建者可见）
  final int downloads;
  final int? maxDownloads;
  final String createdAt;
  ShareItem({
    required this.token,
    required this.name,
    required this.path,
    required this.owner,
    required this.target,
    required this.sizeHuman,
    required this.expiresAt,
    required this.expiresIn,
    required this.hasPassword,
    required this.password,
    required this.downloads,
    required this.maxDownloads,
    required this.createdAt,
  });

  factory ShareItem.fromJson(String token, Map<String, dynamic> j) => ShareItem(
      token: token,
      name: j['name'] as String? ?? '',
      path: j['path'] as String? ?? '',
      owner: j['owner'] as String? ?? '',
      target: j['target'] as String?,
      sizeHuman: j['size_human'] as String? ?? '-',
      expiresAt: j['expires_at'] as String? ?? '',
      expiresIn: j['expires_in'] as String? ?? '',
      hasPassword: j['has_password'] as bool? ?? false,
      password: j['password'] as String?,
      downloads: (j['downloads'] as num?)?.toInt() ?? 0,
      maxDownloads: (j['max_downloads'] as num?)?.toInt(),
      createdAt: j['created_at'] as String? ?? '');
}

class ShareService {
  /// 我的分享列表（GET /api/shares 返回 {token: {...}}）
  static Future<List<ShareItem>> list() async {
    final dio = await ApiClient.I.dio();
    final r = await dio.get('/api/shares');
    final m = r.data as Map;
    return m.entries
        .map((e) => ShareItem.fromJson(
            e.key as String, Map<String, dynamic>.from(e.value as Map)))
        .toList()
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
  }

  /// 创建分享 → token。参数与 POST /api/share（Form）契约一致。
  static Future<String> create({
    required String path,
    required int expiresHours,
    String? password,
    int? maxDownloads,
    String? target,
  }) async {
    final dio = await ApiClient.I.dio();
    final r = await dio.post('/api/share', data: FormData.fromMap({
      'path': path,
      'expires_hours': expiresHours,
      if (password != null && password.isNotEmpty) 'password': password,
      'max_downloads': ?maxDownloads,
      if (target != null && target.isNotEmpty) 'target': target,
    }));
    return r.data['token'] as String;
  }

  static Future<void> revoke(String token) async {
    final dio = await ApiClient.I.dio();
    await dio.delete('/api/shares/$token');
  }

  /// 编辑分享（0.6.0 / PATCH /api/shares/{token}）：有效期从现在重计，
  /// 下载上限可改。传 null 的字段不动。
  static Future<void> edit(String token,
      {int? expiresHours, int? maxDownloads}) async {
    final dio = await ApiClient.I.dio();
    await dio.patch('/api/shares/$token', data: {
      'expires_hours': ?expiresHours,
      'max_downloads': ?maxDownloads,
    });
  }

  /// 在用成员名单（定向分享收件人候选，GET /api/members）
  static Future<List<String>> members() async {
    final dio = await ApiClient.I.dio();
    final r = await dio.get('/api/members');
    return (r.data as List)
        .map((e) => (e as Map)['username'] as String)
        .toList();
  }

  /// 取件链接：指定服务器地址 + /s/{token}；当前地址版见 linkOnCurrent。
  static Future<String> linkOn(String base, String token) async {
    final b = base.endsWith('/') ? base.substring(0, base.length - 1) : base;
    return '$b/s/$token';
  }

  static Future<String> linkOnCurrent(String token) async =>
      linkOn(await Prefs.lastAddress(), token);

  /// 创建分享失败的用户可读原因（UI 不必 import dio 解 response.detail）：
  /// 服务端中文 detail 优先，其次 HTTP 状态码兜底；非 dio 异常原样串出。
  /// 文案与 share_create_sheet 原地解析逐字一致。
  static String createErrorText(Object e) {
    if (e is DioException) {
      final d = e.response?.data;
      return d is Map && d['detail'] != null
          ? d['detail'].toString()
          : '创建失败（HTTP ${e.response?.statusCode ?? '?'}）';
    }
    return '创建失败：$e';
  }
}
