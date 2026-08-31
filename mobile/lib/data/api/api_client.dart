// API 客户端：Dio + Bearer 注入 + 401 统一处理 + 自签证书信任（APP-DESIGN §2/§3/§5）。
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:dio/io.dart';

import '../local/prefs.dart';
import '../local/secure_store.dart';
import 'trust.dart';

/// 401（Token 失效/被吊销）回调：由 UI 层注入（清会话回登录页）
void Function()? onUnauthorized;

/// 证书未被信任的异常：UI 据此弹"信任此服务器"确认框
class TrustRequiredError implements Exception {
  final String host;
  final int port;
  final String fingerprint; // SHA256 hex
  TrustRequiredError(this.host, this.port, this.fingerprint);
  String get pretty => TrustStore.pretty(fingerprint);
}

class ApiClient {
  ApiClient._();
  static final ApiClient I = ApiClient._();

  Dio? _dio;

  Future<Dio> dio() async {
    final d = _dio;
    if (d != null) return d;
    final base = await Prefs.lastAddress();
    final fresh = Dio(BaseOptions(
      baseUrl: base,
      connectTimeout: const Duration(seconds: 8),
      receiveTimeout: const Duration(seconds: 30),
    ));
    fresh.httpClientAdapter =
        IOHttpClientAdapter(createHttpClient: TrustStore.createHttpClient);
    fresh.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) async {
        await TrustStore.I.warmup();
        final token = await SecureStore.token();
        if (token != null && token.isNotEmpty) {
          options.headers['Authorization'] = 'Bearer $token';
        }
        handler.next(options);
      },
      onError: (e, handler) async {
        // 自签证书被拒：把待确认指纹打包成 TrustRequiredError 给 UI
        if (e.error is HandshakeException) {
          final uri = e.requestOptions.uri;
          final fp = TrustStore.I.pendingFor(uri.host, uri.port);
          if (fp != null) {
            return handler.next(DioException(
              requestOptions: e.requestOptions,
              error: TrustRequiredError(uri.host, uri.port, fp),
            ));
          }
        }
        if (e.response?.statusCode == 401) {
          // 只清"当前存储的 token 就是本次失败请求所带的那个"——
          // 防止并发场景下（登录中签发了新 token）误删新凭据
          final sent = e.requestOptions.headers['authorization'] as String?;
          final sentTok =
              sent != null && sent.startsWith('Bearer ') ? sent.substring(7).trim() : null;
          final cur = await SecureStore.token();
          final inLogin = e.requestOptions.headers['x-login-flow'] == '1';
          if (!inLogin && cur != null && (sentTok == null || sentTok == cur)) {
            await SecureStore.clearToken();
            onUnauthorized?.call();
          }
        }
        handler.next(e);
      },
    ));
    _dio = fresh;
    return fresh;
  }

  Future<void> switchServer(String url) async {
    await Prefs.rememberAddress(url);
    _dio = null; // 下次 dio() 按新地址重建
  }
}
