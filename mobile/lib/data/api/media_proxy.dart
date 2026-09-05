// 本地媒体代理（App v0.6.0）：video_player 在 Android 走 ExoPlayer 自有网络栈，
// 插件不暴露证书回调——自签 HTTPS 的 TOFU 信任对它无效。此代理在 127.0.0.1 起
// 一个仅本机可达的 HTTP 服务：播放器请求它，它用 TrustStore 信任的 HttpClient
// 带 Bearer 回源拉流，Range 原样转发（进度条拖动依赖 206）。
// 明文 HTTP 只绑 loopback，且 Android 侧 networkSecurityConfig 仅对 127.0.0.1
// 放行明文——不对公网明文开任何口子。
import 'dart:io';
import 'dart:math';

import '../local/prefs.dart';
import '../local/secure_store.dart';
import 'trust.dart';

class MediaProxy {
  MediaProxy._();
  static final MediaProxy I = MediaProxy._();

  HttpServer? _server;
  String _secret = '';
  HttpClient? _client;
  Future<void>? _ensuring; // 并发 urlFor 复用同一次启动（R1-P3：双 bind 会
                           // 泄漏先建的监听 socket，且其端口恒 403）

  /// 远端文件 → 本地代理 URL（幂等：服务随用随起）
  Future<String> urlFor(String remotePath) async {
    await _ensure();
    final enc = remotePath.split('/').map(Uri.encodeComponent).join('/');
    return 'http://127.0.0.1:${_server!.port}/m/$_secret/$enc';
  }

  Future<void> _ensure() async {
    if (_server != null) return;
    return _ensuring ??= _doEnsure();
  }

  Future<void> _doEnsure() async {
    try {
      // 路径秘钥：同机其它 App 理论上可访问 loopback，随机秘钥防枚举拉流。
      // 只在为空时生成（R2-P2-4）：reset() 重建代理时重新生成会作废所有
      // 已发 URL（含正在播放的视频），且是全局杀伤
      if (_secret.isEmpty) _secret = _randomToken();
      _client = TrustStore.createHttpClient()..autoUncompress = false;
      _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      _server!.listen(_handle, onError: (Object _) {});
    } catch (_) {
      _ensuring = null; // 失败可重试
      rethrow;
    }
  }

  /// 重试前重置回源 HttpClient 与本地服务：蜂窝下回源 socket 可能已僵死，
  /// 不重置则"重试"永远复用同一个坏连接（2026-09-03 弱网加固）。
  Future<void> reset() async {
    final s = _server;
    _server = null;
    _ensuring = null;
    final c = _client;
    _client = null;
    c?.close(force: true);
    if (s != null) {
      try {
        await s.close(force: true);
      } catch (_) {}
    }
  }

  static String _randomToken() {
    final rnd = Random.secure();
    return List.generate(24, (_) => 'abcdefghjkmnpqrstuvwxyz23456789'[rnd.nextInt(31)]).join();
  }

  Future<void> _handle(HttpRequest req) async {
    try {
      final segs = req.uri.pathSegments; // ['m', secret, ...path]
      if (segs.length < 3 || segs[0] != 'm' || segs[1] != _secret) {
        req.response.statusCode = HttpStatus.forbidden;
        await req.response.close();
        return;
      }
      final token = await SecureStore.token();
      if (token == null || token.isEmpty) {
        req.response.statusCode = HttpStatus.unauthorized;
        await req.response.close();
        return;
      }
      final base = await Prefs.lastAddress();
      final b = base.endsWith('/') ? base.substring(0, base.length - 1) : base;
      final encPath = segs.sublist(2).join('/');
      final origin = Uri.parse('$b/preview/$encPath');
      final creq = await _client!.openUrl('GET', origin);
      creq.headers.set('Authorization', 'Bearer $token');
      // 明确 identity：内容长度/Range 语义按原始字节算，杜绝编码层错位
      creq.headers.set('Accept-Encoding', 'identity');
      final range = req.headers.value(HttpHeaders.rangeHeader);
      if (range != null) creq.headers.set('Range', range);
      final cres = await creq.close();

      req.response.statusCode = cres.statusCode;
      for (final h in const [
        HttpHeaders.contentTypeHeader,
        HttpHeaders.contentLengthHeader,
        HttpHeaders.contentRangeHeader,
        HttpHeaders.acceptRangesHeader,
      ]) {
        final v = cres.headers.value(h);
        if (v != null) req.response.headers.set(h, v);
      }
      // 播放器 seek 会中途断开：写失败按客户端断开处理，不算错误
      await cres.pipe(req.response).catchError((Object _) {});
    } catch (_) {
      try {
        req.response.statusCode = HttpStatus.badGateway;
        await req.response.close();
      } catch (_) {}
    }
  }
}
