// 会话域 HTTP 契约（B8 裸 dio 收编：自 state/session.dart 抽出）。
// 薄封装只管"怎么发请求"；登录状态机/错误文案/Token 生命周期仍在
// SessionController——返回原始 Response，调用方按需取字段。
// x-login-flow 头语义（登录流程专用标记，服务端按它放宽拦截器行为）随调用
// 点逐字保留：bootstrap/登录三入口带，refreshFromMe 不带（review P3）。
import 'package:dio/dio.dart';

import 'api_client.dart';

class SessionApi {
  SessionApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  static const _loginFlow = {'x-login-flow': '1'};

  /// 当前身份（/api/auth/me）。loginFlow=true 用于登录/换 Token 流程内。
  static Future<Response> me({bool loginFlow = false}) async {
    final dio = await _dio();
    return dio.get('/api/auth/me',
        options: Options(headers: loginFlow ? _loginFlow : null));
  }

  /// 密码登录（启用 TOTP 的账号响应 totp_required，由调用方转状态机）。
  static Future<Response> loginWithPassword(
      {required String username, required String password}) async {
    final dio = await _dio();
    return dio.post('/api/login',
        options: Options(headers: _loginFlow),
        data: {'username': username, 'password': password});
  }

  /// TOTP 动态码换会话。
  static Future<Response> loginTotp(
      {required String username, required String password, required String code}) async {
    final dio = await _dio();
    return dio.post('/api/login/totp',
        options: Options(headers: _loginFlow),
        data: {'username': username, 'password': password, 'code': code});
  }

  /// 配对码换长效设备 Token（免账号密码；返回体含 token）。
  static Future<Response> pairClaim({required String code, required String deviceName}) async {
    final dio = await _dio();
    return dio.post('/api/pair/claim',
        options: Options(headers: _loginFlow),
        data: {'code': code.trim(), 'name': deviceName});
  }

  /// 登录会话 Cookie 换长效设备 Token（M-A0：Cookie 只用这一次）。
  static Future<Response> exchangeDeviceToken(
      {required String cookieHeader, required String deviceName}) async {
    final dio = await _dio();
    return dio.post('/api/auth/token',
        options: Options(headers: {'Cookie': cookieHeader, ..._loginFlow}),
        data: {'name': deviceName});
  }

  /// 资料更新（v0.17 昵称）。
  static Future<Response> updateProfile({required String nickname}) async {
    final dio = await _dio();
    return dio.patch('/api/auth/profile', data: {'nickname': nickname});
  }
}
