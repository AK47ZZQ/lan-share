// 账号与设备域 API（T7 裸 dio 收编第二批：从 me_page 抽出 UI 层手拼请求）。
// WebDAV 独立密码/设备管理/改密/改用户名/配对码；两步验证恢复码/口令复核
// /邀请码兑换（本批收编 me_page/backup_settings_page/invite_redeem_page）。
import 'package:dio/dio.dart';
import 'package:dio/io.dart';

import 'api_client.dart';
import 'trust.dart';

class AccountApi {
  AccountApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  /// WebDAV 独立密码是否已设置（只暴露布尔，绝不回传哈希）
  static Future<bool> webdavPwdStatus() async {
    final dio = await _dio();
    final r = await dio.get('/api/auth/webdav-password');
    return r.data['set'] as bool? ?? false;
  }

  /// 设置/轮换/清除 WebDAV 独立密码；返回服务端标记的最终 set 状态
  static Future<bool> setWebdavPwd(String current, String next) async {
    final dio = await _dio();
    final r = await dio.post('/api/auth/webdav-password',
        data: {'current_password': current, 'webdav_password': next});
    return r.data['set'] as bool? ?? false;
  }

  /// 本账号设备列表（items 原始 Map 列表，解析留给展示模型）
  static Future<List<dynamic>> listDevices() async {
    final dio = await _dio();
    final r = await dio.get('/api/auth/devices');
    return (r.data['devices'] as List?) ?? const [];
  }

  /// 吊销设备（吊销本机后调用方需自行清 Token 回登录页）
  static Future<void> revokeDevice(int id) async {
    final dio = await _dio();
    await dio.delete('/api/auth/devices/$id');
  }

  /// 上传/替换头像（服务端 PIL 中心裁方 512px JPEG）
  static Future<void> uploadAvatar(String filePath) async {
    final dio = await _dio();
    await dio.post('/api/auth/avatar',
        data: FormData.fromMap({
          'file': await MultipartFile.fromFile(filePath, filename: 'avatar.jpg'),
        }));
  }

  static Future<void> changePassword(String oldPwd, String newPwd) async {
    final dio = await _dio();
    await dio.post('/api/auth/password',
        data: {'old_password': oldPwd, 'new_password': newPwd});
  }

  /// 改用户名：私人目录/incoming 同步改名；设备 Token 保持有效
  static Future<void> changeUsername(String newName, String password) async {
    final dio = await _dio();
    await dio.post('/api/auth/username',
        data: {'new_username': newName, 'password': password});
  }

  /// 一次性配对码（3 分钟有效）；返回 (code, expires 秒)
  static Future<({String? code, int expires})> pairCode() async {
    final dio = await _dio();
    final r = await dio.post('/api/pair/code');
    return (
      code: r.data['code'] as String?,
      expires: (r.data['expires_in'] as num?)?.toInt() ?? 180,
    );
  }

  /// 重新生成两步验证恢复码（旧码全部作废，新码仅本次明文展示）
  static Future<List<String>> regenRecoveryCodes(String oldPassword) async {
    final dio = await _dio();
    final r = await dio
        .post('/api/auth/totp/rc', data: {'old_password': oldPassword});
    return ((r.data['recovery_codes'] as List?) ?? const [])
        .whereType<String>()
        .toList();
  }

  /// 危险操作前的本人口令复核（/api/auth/verify-password）。
  /// x-login-flow：密码错误回 401 是本框的业务语义，不带头会被拦截器当
  /// 「Token 失效」清凭据全局登出（P0-1）。不抛 Dio 异常而是返回结构化
  /// 结果（status/detail 原样透出），错误文案由 UI 拼装保持不变。
  static Future<({bool ok, int? status, String? detail})> verifyPassword(
      String password) async {
    final dio = await _dio();
    try {
      await dio.post('/api/auth/verify-password',
          options: Options(headers: {'x-login-flow': '1'}),
          data: {'password': password});
      return (ok: true, status: null, detail: null);
    } on DioException catch (e) {
      final d = e.response?.data;
      return (
        ok: false,
        status: e.response?.statusCode,
        detail:
            d is Map && d['detail'] != null ? d['detail'].toString() : null,
      );
    }
  }

  /// 邀请码兑换（v0.15.2 匿名入口；登录态调用也无妨，Bearer 会被忽略）。
  /// [baseUrl] 传入时建一次性 Dio 用该地址提交——登录页入口无会话且可能
  /// 刚改过地址，ApiClient.dio() 懒建缓存可能把 code+password 打到默认/
  /// 旧地址（审查 P1）；主页（已登录）入口传 null 走正常底座。
  static Future<({String username, String role})> redeemInvite(
      String code, String username, String password,
      {String? baseUrl}) async {
    Dio? own;
    if (baseUrl != null) {
      final d = Dio(BaseOptions(
          baseUrl: baseUrl,
          connectTimeout: const Duration(seconds: 8),
          receiveTimeout: const Duration(seconds: 30)));
      d.httpClientAdapter =
          IOHttpClientAdapter(createHttpClient: TrustStore.createHttpClient);
      own = d;
    }
    try {
      await TrustStore.I.warmup();
      // x-login-flow：匿名端点不吃 401 踢登逻辑，带上防万一
      final r = await (own ?? await _dio()).post('/api/invites/redeem',
          data: {
            'code': code,
            'username': username,
            'password': password,
          },
          options: Options(headers: {'x-login-flow': '1'}));
      return (
        username: r.data is Map ? '${r.data['username'] ?? ''}' : '',
        role: r.data is Map ? '${r.data['role'] ?? 'member'}' : 'member',
      );
    } finally {
      own?.close(force: true);
    }
  }
}
