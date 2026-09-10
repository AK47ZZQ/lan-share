// 账号与设备域 API（T7 裸 dio 收编第二批：从 me_page 抽出 UI 层手拼请求）。
// WebDAV 独立密码/设备管理/改密/改用户名/配对码。
import 'package:dio/dio.dart';

import 'api_client.dart';

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
}
