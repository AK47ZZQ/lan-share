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
  Future<Dio>? _building; // 并发首调复用同一次构建（R1-P3），落选实例的连接池滞留到 GC
  int _gen = 0; // switchServer 代际：在途 _build 完成时地址可能已再次切换（红队 P2）
  int _buildSeq = 0; // 构建序号（R2-P3-5）：旧代 build 完成时不得清掉新代的 _building

  Future<Dio> dio() async {
    final d = _dio;
    if (d != null) return d;
    return _building ??= _build(++_buildSeq);
  }

  Future<Dio> _build(int seq) async {
    final gen = _gen;
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
        // GET 幂等自动重试（改进建议 9.6-P2-4）：备份窗口（pg_dump+文件
        // 复制）内 DB/IO 短暂压力会偶发超时/5xx；对幂等 GET 静默重试一次，
        // 把"加载失败"降成"慢一点"。只救无响应、5xx 或 429，4xx/证书不重试。
        //
        // 429 也救（bug·882a）：限速是**瞬时**状态——备份洪峰用尽配额时
        // 服务端回 429+Retry-After:30，此前被归入「4xx 不重试」直接抛给 UI，
        // 用户看到的就是"照片墙/文件偶发加载失败"。按 Retry-After（上限夹到
        // 3s，避免 UI 长时间空转）退避一次即可恢复。服务端侧已改为上传独立桶
        // （不挤占交互桶），此处是第二道兜底：真被限也自动恢复而非报错。
        final ro = e.requestOptions;
        final noResp = e.response == null;
        final status = e.response?.statusCode ?? 0;
        final rateLimited = status == 429;
        if (ro.method == 'GET' &&
            ro.extra['__retried'] != true &&
            e.error is! HandshakeException &&
            (noResp || status >= 500 || rateLimited)) {
          ro.extra['__retried'] = true;
          var wait = const Duration(milliseconds: 800);
          if (rateLimited) {
            final ra = int.tryParse(
                e.response?.headers.value('retry-after') ?? '');
            if (ra != null && ra > 0) {
              wait = Duration(
                  milliseconds: (ra * 1000).clamp(500, 3000));
            }
          }
          await Future.delayed(wait);
          try {
            final res = await fresh.fetch<dynamic>(ro);
            return handler.resolve(res);
          } on DioException catch (re) {
            return handler.next(re);
          }
        }
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
          // 收紧为 sentTok == cur（R2-P2-3）：无凭据请求（sentTok==null）的 401
          // 可能是登出瞬间在途请求——误清刚重新登录保存的新 Token，形成
          // "登录→401→再踢"循环
          if (!inLogin && cur != null && sentTok != null && sentTok == cur) {
            await SecureStore.clearToken();
            onUnauthorized?.call();
          }
        }
        handler.next(e);
      },
    ));
    // 构建期间发生过切服：旧地址 Dio 不能写回 _dio（否则切服后请求带着
    // Bearer 打向刚离开的服务器），关闭并按新地址重建
    if (gen != _gen) {
      fresh.close(force: true);
      // 只清自己那一代的构建条目（R2-P3-5）：旧代完成时无条件置空可能
      // 清掉新一代正在进行的构建，再起一路重复构建
      if (seq == _buildSeq) _building = null;
      return dio();
    }
    _dio = fresh;
    return fresh;
  }



  Future<void> switchServer(String url) async {
    await Prefs.rememberAddress(url);
    _dio = null; // 下次 dio() 按新地址重建
    _building = null;
    _gen++;
  }
}
