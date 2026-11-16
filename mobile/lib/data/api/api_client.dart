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

/// 取一个服务器地址的 host。解析失败时退回原始字符串 ——
/// 宁可把两个地址判成"不同服务器"（多清一次 token，最多重新登录），
/// 也不能判成"同一台"（把凭据发给别人）。
String hostOfAddress(String url) {
  try {
    final h = Uri.parse(url).host;
    return h.isNotEmpty ? h : url;
  } catch (_) {
    return url;
  }
}

class ApiClient {
  ApiClient._();
  static final ApiClient I = ApiClient._();

  Dio? _dio;
  Future<Dio>? _building; // 并发首调复用同一次构建（R1-P3），落选实例的连接池滞留到 GC
  int _gen = 0; // switchServer 代际：在途 _build 完成时地址可能已再次切换（红队 P2）
  int _buildSeq = 0; // 构建序号（R2-P3-5）：旧代 build 完成时不得清掉新代的 _building
  /// 掉线回调去重闸：一次 401 风暴（照片墙几十个并发请求）只跳一次登录页
  bool _unauthorizedFired = false;

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
          final staleToken = cur != null && sentTok != null && sentTok == cur;
          // 2026-11-16（用户报障「查看存储点进去不加载 / 文件点进去不显示」）：
          // 本地**根本没有** Token 时，此前直接落到 handler.next(e)——不跳登录
          // 页、不提示，页面就那么干着，用户只看到"点了没反应"。而 switchServer
          // 跨 host 切服（局域网 ↔ 公网）恰好会把 Token 清掉，制造这个静默态。
          // 掉线必须让 UI 知道：本来就无效的凭据，跳登录页才是唯一出口。
          final noLocalToken = cur == null || cur.isEmpty;
          if (!inLogin &&
              (staleToken || noLocalToken) &&
              !_unauthorizedFired) {
            _unauthorizedFired = true;
            if (staleToken) await SecureStore.clearToken();
            onUnauthorized?.call();
            // 闸只挡并发 401 风暴；后续真的再次掉线仍要能触发
            Future<void>.delayed(const Duration(seconds: 2), () {
              _unauthorizedFired = false;
            });
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



  /// [preserveToken] 仅 auto_switch 用：它切的是**同一台服务器**的另一个入口
  /// （局域网 ↔ 隧道，候选来自服务端 entries 自报与用户历史地址）。跨 host
  /// 清 Token 的原意是"别把凭据发给第三方服务器"，对同一台服务器就是误杀 ——
  /// 每次进出家门自动切换都掉登录，用户看到的就是"点开什么都不加载"。
  /// 手动添加/扫码接入别人服务器仍走默认（清）。
  Future<void> switchServer(String url, {bool preserveToken = false}) async {
    final prev = await Prefs.lastAddress();
    await Prefs.rememberAddress(url);

    // 换到**不同主机**时必须丢掉旧设备的 token。
    //
    // 登录流程是「先 switchServer(B)，再 POST /api/login」，而请求拦截器
    // 是无条件把 SecureStore 里的 token 塞进 Authorization 的 —— 不清的话
    // A 服务器的设备凭据会跟着密码一起发给 B：换部署、帮家人配、扫码接入
    // 别人的服务器，都会把自己的登录凭据交给第三方，还可能留在对方的
    // 反代访问日志里。
    //
    // 只比 host 不比端口：同一台服务器的局域网地址和隧道映射端口不同，
    // 但那是同一份数据，token 该保留。
    if (!preserveToken && hostOfAddress(prev) != hostOfAddress(url)) {
      await SecureStore.clearToken();
    }

    _dio = null; // 下次 dio() 按新地址重建
    _building = null;
    _gen++;
  }

}
