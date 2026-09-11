// 管理域 API（T7 裸 dio 收编：admin_page 全部 12 处直发收口至此，
// UI 分层守卫要求 lib/ui 不得直连 dio）。契约与 Web AdminView 同款：
//   GET    /api/admin/users                        成员列表
//   PATCH  /api/admin/users/{id}                   角色/配额/启停
//   POST   /api/admin/users/{id}/username          改用户名
//   POST   /api/admin/users/{id}/password          重置密码
//   POST   /api/admin/users/{id}/webdav-password   设/清 WebDAV 密码
//   POST   /api/admin/users/{id}/totp-disable      解除两步验证
//   DELETE /api/admin/users/{id}                   删成员
//   GET    /api/admin/invites                      邀请码列表
//   POST   /api/admin/invites                      建邀请码（72 小时有效）
//   DELETE /api/admin/invites/{code}               删邀请记录
//   GET    /api/admin/grants                       授权列表
//   POST   /api/admin/grants                       建/改授权（upsert）
//   DELETE /api/admin/grants/{id}                  撤销授权
//   GET    /api/public-url                         公网入口读取
//   POST   /api/admin/public-url                   公网入口保存
//   GET    /api/admin/webhook                      推送模板读取
//   POST   /api/admin/webhook                      推送模板保存
//   POST   /api/admin/webhook/test                 发测试消息
//   POST   /api/admin/notify                       群发站内通知
//   GET    /api/files                              共享一级目录（建授权选单）
// 删除类操作必须用 DELETE：0.8.0 曾误用 POST 全部 405、删除按钮失效
// （0.8.1 审查修复），收编时随契约一并固化。
// 响应模型 AdminUser/AdminGrant/AdminInvite 一并落在这里——原先定义在
// 页面文件里，契约只解析一处，UI 不再手写 JSON 取值。
import 'package:dio/dio.dart';

import 'api_client.dart';

/// 管理面板成员条目（quota_bytes 为 null=不限额）
class AdminUser {
  final int id;
  final String username;
  final String role;
  final bool isActive;
  final int? quotaBytes;
  final String quotaHuman;
  final String createdAt;
  final bool totpEnabled;
  final bool webdavSet;
  AdminUser.fromJson(Map<String, dynamic> j)
      : id = (j['id'] as num?)?.toInt() ?? 0,
        username = j['username'] as String? ?? '',
        role = j['role'] as String? ?? 'member',
        isActive = j['is_active'] as bool? ?? true,
        quotaBytes = (j['quota_bytes'] as num?)?.toInt(),
        quotaHuman = j['quota_human'] as String? ?? '',
        createdAt = j['created_at'] as String? ?? '',
        totpEnabled = j['totp_enabled'] as bool? ?? false,
        webdavSet = j['webdav_set'] as bool? ?? false;
}

/// 授权条目（expires_at 为秒级 Unix 时间戳，null=永久）
class AdminGrant {
  final int id;
  final int userId;
  final String path;
  final bool canWrite;
  final int? expiresAt;
  AdminGrant.fromJson(Map<String, dynamic> j)
      : id = (j['id'] as num?)?.toInt() ?? 0,
        userId = (j['user_id'] as num?)?.toInt() ?? 0,
        path = j['path'] as String? ?? '',
        canWrite = j['can_write'] as bool? ?? false,
        expiresAt = (j['expires_at'] as num?)?.toInt();
}

/// 邀请码条目
class AdminInvite {
  final String code;
  final String role;
  final bool expired;
  final bool used;
  AdminInvite.fromJson(Map<String, dynamic> j)
      : code = j['code'] as String? ?? '',
        role = j['role'] as String? ?? '',
        expired = j['expired'] as bool? ?? false,
        used = j['used'] as bool? ?? false;
}

/// 公网入口配置（空串=未配置）
class PublicUrlInfo {
  final String base;
  final String alt;
  const PublicUrlInfo({required this.base, required this.alt});
  factory PublicUrlInfo.fromJson(Map<String, dynamic> j) => PublicUrlInfo(
      base: j['public_base_url'] as String? ?? '',
      alt: j['public_base_url_alt'] as String? ?? '');
}

/// 群发通知结果：sent=站内送达人数，webhook=站外是否同步发送
class NotifyResult {
  final int sent;
  final bool webhook;
  const NotifyResult({required this.sent, required this.webhook});
  factory NotifyResult.fromJson(Map<String, dynamic> j) => NotifyResult(
      sent: (j['sent'] as num?)?.toInt() ?? 0,
      webhook: j['webhook'] as bool? ?? false);
}

class AdminApi {
  AdminApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  // ── 成员 ─────────────────────────────────────────────────
  /// 成员列表
  static Future<List<AdminUser>> users() async {
    final r = await (await _dio()).get('/api/admin/users');
    return (r.data as List)
        .map((e) => AdminUser.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// 改角色（admin/member/guest）
  static Future<void> setUserRole(int id, String role) async {
    await (await _dio()).patch('/api/admin/users/$id', data: {'role': role});
  }

  /// 私人空间配额（0=清成不限，服务端 0 → NULL）
  static Future<void> setUserQuota(int id, int quotaBytes) async {
    await (await _dio())
        .patch('/api/admin/users/$id', data: {'quota_bytes': quotaBytes});
  }

  /// 停用/启用账号
  static Future<void> setUserActive(int id, bool active) async {
    await (await _dio())
        .patch('/api/admin/users/$id', data: {'is_active': active});
  }

  /// 改用户名（管理员直改，私人目录/incoming 同步改名）
  static Future<void> setUsername(int id, String value) async {
    await (await _dio())
        .post('/api/admin/users/$id/username', data: {'value': value});
  }

  /// 重置密码（服务端同时解除两步验证、吊销全部设备 Token）
  static Future<void> setPassword(int id, String value) async {
    await (await _dio())
        .post('/api/admin/users/$id/password', data: {'value': value});
  }

  /// 设/清 WebDAV 独立密码（空串=清除）
  static Future<void> setWebdavPassword(int id, String value) async {
    await (await _dio())
        .post('/api/admin/users/$id/webdav-password', data: {'value': value});
  }

  /// 解除两步验证（含恢复码，不动密码）
  static Future<void> disableTotp(int id) async {
    await (await _dio()).post('/api/admin/users/$id/totp-disable', data: {});
  }

  /// 删成员（其授权/会话一并清除，私人目录移入 .orphaned/ 保留）
  static Future<void> deleteUser(int id) async {
    await (await _dio()).delete('/api/admin/users/$id');
  }

  // ── 邀请 ─────────────────────────────────────────────────
  /// 邀请码列表
  static Future<List<AdminInvite>> invites() async {
    final r = await (await _dio()).get('/api/admin/invites');
    return (r.data as List)
        .map((e) => AdminInvite.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// 建邀请码（72 小时有效），返回码本身（空串=服务端未回码）
  static Future<String> createInvite(String role) async {
    final r = await (await _dio())
        .post('/api/admin/invites', data: {'role': role, 'hours': 72});
    return r.data['code'] as String? ?? '';
  }

  /// 删邀请记录
  static Future<void> deleteInvite(String code) async {
    await (await _dio())
        .delete('/api/admin/invites/${Uri.encodeComponent(code)}');
  }

  // ── 授权 ─────────────────────────────────────────────────
  /// 授权列表
  static Future<List<AdminGrant>> grants() async {
    final r = await (await _dio()).get('/api/admin/grants');
    return (r.data as List)
        .map((e) => AdminGrant.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// 建/改授权（upsert：同 user+path 再 POST 即改，v0.20）。
  /// [expiresHours] 不传=永久；续期场景由调用方算"原剩余+延长"。
  static Future<void> upsertGrant(
      {required int userId,
      required String path,
      required bool canWrite,
      int? expiresHours}) async {
    await (await _dio()).post('/api/admin/grants', data: {
      'user_id': userId,
      'path': path,
      'can_read': true,
      'can_write': canWrite,
      'expires_hours': ?expiresHours,
    });
  }

  /// 撤销授权
  static Future<void> deleteGrant(int id) async {
    await (await _dio()).delete('/api/admin/grants/$id');
  }

  /// 共享一级目录清单（建授权用，v0.20 重构）：授权只收一级目录，
  /// 子目录随父级授权自动继承（服务端 spaces._grant_hit 前缀匹配语义）。
  /// private/incoming 不参与授权，跳过；无权访问抛错由调用方吞掉。
  static Future<List<String>> sharedTopDirs() async {
    final r = await (await _dio()).get('/api/files');
    final out = <String>[];
    for (final e in (r.data['items'] as List)) {
      final m = e as Map;
      if (m['is_dir'] != true) continue;
      final name = m['name'] as String;
      if (name == 'private' || name == 'incoming') continue;
      out.add(m['path'] as String);
    }
    return out;
  }

  // ── 设置卡 ───────────────────────────────────────────────
  /// 公网入口读取（主/备隧道；保存后分享卡片和登录页一并提供）
  static Future<PublicUrlInfo> publicUrl() async {
    final r = await (await _dio()).get('/api/public-url');
    return PublicUrlInfo.fromJson(Map<String, dynamic>.from(r.data as Map));
  }

  /// 公网入口保存
  static Future<void> savePublicUrl(String url, String urlAlt) async {
    await (await _dio()).post('/api/admin/public-url',
        data: {'url': url, 'url_alt': urlAlt});
  }

  /// 事件推送模板读取（GET 模板 URL，需含 {title}/{body} 占位符）
  static Future<String> webhookUrl() async {
    final r = await (await _dio()).get('/api/admin/webhook');
    return r.data['url'] as String? ?? '';
  }

  /// 事件推送模板保存
  static Future<void> saveWebhook(String url) async {
    await (await _dio()).post('/api/admin/webhook', data: {'url': url});
  }

  /// 发测试消息
  static Future<void> testWebhook() async {
    await (await _dio()).post('/api/admin/webhook/test');
  }

  /// 群发站内通知（站内人手一条 + 已配置 webhook 一起推；userIds 空=全体）
  static Future<NotifyResult> notify(
      {required String title, required String body, List<int>? userIds}) async {
    final r = await (await _dio()).post('/api/admin/notify', data: {
      'title': title,
      'body': body,
      'user_ids': ?userIds,
    });
    return NotifyResult.fromJson(Map<String, dynamic>.from(r.data as Map));
  }
}
