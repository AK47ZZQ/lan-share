// 缩略图/预览图的两级缓存：走与主 API 相同的自签信任策略，并附 Bearer 头
// （/thumb/、/preview/ 都是 require_user 端点，APP-DESIGN §1 图片选型）。
import 'dart:async';
import 'dart:io';

import 'package:flutter_cache_manager/flutter_cache_manager.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart' show IOClient;

import '../local/prefs.dart';
import '../local/secure_store.dart';
import '../net.dart';
import 'trust.dart';

/// 简易 FIFO 并发闸：时间线一次重建会同时发起几十张缩略图请求，
/// 不加闸会瞬间打满连接池并放大服务端峰值。并发数按入口动态调整——
/// 局域网 8 路足够跑满带宽；公网 10Mbps 下 8 路反而互相拖累拥塞控制，
/// 3 路即可串满（2026-09-03 弱网加固）。
class _Semaphore {
  int max;
  int _running = 0;
  final _queue = <void Function()>[];
  _Semaphore(this.max);

  void setMax(int m) {
    max = m;
  }

  Future<T> run<T>(Future<T> Function() body) {
    final c = Completer<T>();
    _queue.add(() async {
      try {
        c.complete(await body());
      } catch (e, st) {
        c.completeError(e, st);
      } finally {
        _running--;
        _pump();
      }
    });
    _pump();
    return c.future;
  }

  /// 占坑/释放分离（R2-P2-2）：正文传输阶段也要占坑——此前 run 只覆盖
  /// "发请求+等响应头"，几十路并发流照样抢带宽，公网限流形同虚设
  Future<void> acquire() {
    final c = Completer<void>();
    _queue.add(() async { c.complete(); });
    _pump();
    return c.future;
  }

  void release() {
    _running--;
    _pump();
  }

  void _pump() {
    if (_running < max && _queue.isNotEmpty) {
      _running++;
      _queue.removeAt(0)();
    }
  }
}

/// 图片拉取失败的类型化异常：statusCode 可空（超时/断网等非 HTTP 失败）。
/// 负缓存（AuthImage）据它区分"服务端稳定出不了图"（404/重试后仍 5xx，
/// 可负缓存）与"瞬时故障"（401/429/网络异常，不记——否则 token 过期
/// 一瞬间会把整屏缩略图固化成 10 分钟坏图）。
class ImageFetchException implements Exception {
  final int? statusCode;
  ImageFetchException(this.statusCode);
  @override
  String toString() => statusCode == null
      ? 'image fetch failed'
      : 'image fetch HTTP $statusCode';
}

class AuthFileService extends FileService {
  final http.Client _client = IOClient(TrustStore.createHttpClient());
  final _semaphore = _Semaphore(8);

  // Token 进程内缓存：快速滑动时几十个格子并发读 flutter_secure_storage
  // 不可靠（可能拿到空值 → 请求 401 → 全部坏图）。401 时清缓存重试一次，
  // 登出/换号后自动自愈。
  String? _cachedToken;
  Future<String?> _getToken() async =>
      _cachedToken ??= await SecureStore.token();

  Future<http.StreamedResponse> _send(String url,
      {Map<String, String>? headers}) async {
    await TrustStore.I.warmup();
    final req = http.Request('GET', Uri.parse(url));
    final token = await _getToken();
    req.headers['authorization'] = 'Bearer ${token ?? ''}';
    if (headers != null) req.headers.addAll(headers);
    // 公网弱网加固（v0.10.1）：整体 45s 上限——蜂窝下卡死的连接
    // 会抛 TimeoutException 而非无限转圈；下轮网格重建自动再试
    return _client.send(req).timeout(const Duration(seconds: 45));
  }

  @override
  Future<FileServiceResponse> get(String url,
      {Map<String, String>? headers}) async {
    // 公网弱网加固（2026-09-03）：并发数按入口动态调整
    // （局域网 8 路 / 公网 3 路，见 _Semaphore 注释）
    _semaphore.setMax(isPublicAddress(await Prefs.lastAddress()) ? 3 : 8);
    // flutter_cache_manager 对存过 ETag 的图片会带 If-None-Match 走条件下载，
    // 服务端命中即 304——但走下载路径时管理器没有本地文件可复用，304 等于
    // 空手而归（0.4.4 真机"加载完划走就不显示"真凶）。剥掉条件头强制 200
    // 全量；秒开由管理器磁盘缓存负责（5000 对象），不依赖 HTTP 304。
    final h = Map<String, String>.from(headers ?? {})
      ..remove('if-none-match')
      ..remove('If-None-Match');

    // 占坑持续到响应体读完（R2-P2-2）：acquire 后拿到头即包成 Scoped 响应，
    // body 流 done/取消/出错时才释放并发闸
    Future<HttpGetResponse> send() async {
      await _semaphore.acquire();
      http.StreamedResponse r;
      try {
        r = await _send(url, headers: h);
      } catch (_) {
        _semaphore.release();
        rethrow;
      }
      return _ScopedGetResponse(r, _semaphore.release);
    }

    var resp = await send();
    // 非 2xx 绝不入缓存（否则 401 错误页会被当图片缓存住）
    if (resp.statusCode == 401) {
      // Token 可能被并发读坏或已轮换：清缓存强读一次再试
      await resp.content.drain<void>();
      _cachedToken = null;
      resp = await send();
    }
    // 公网弱网加固（v0.10.1）：5xx/429 是瞬时扰动，等 1.5s 重试一次
    // 再判死；304 剥离在上游（不依赖条件请求）——重试不重复扣缓存。
    if (resp.statusCode >= 500 || resp.statusCode == 429) {
      await resp.content.drain<void>();
      await Future.delayed(const Duration(milliseconds: 1500));
      resp = await send();
    }
    if (resp.statusCode < 200 || resp.statusCode >= 300) {
      await resp.content.drain<void>();
      throw ImageFetchException(resp.statusCode);
    }
    return resp;
  }
}

/// 包住 HttpGetResponse：底层 body 流结束/出错/被取消时释放一次并发闸。
class _ScopedGetResponse extends HttpGetResponse {
  _ScopedGetResponse(super.resp, this._release);

  final void Function() _release;
  bool _released = false;
  Stream<List<int>>? _wrapped;

  void _releaseOnce() {
    if (!_released) {
      _released = true;
      _release();
    }
  }

  @override
  Stream<List<int>> get content {
    // content 可能被读多次的防御：包装只挂一次；显式订阅以便覆盖
    // "管理器中途取消订阅"（图片划出屏幕）的释放路径
    return _wrapped ??= () {
      late StreamController<List<int>> ctrl;
      StreamSubscription<List<int>>? sub;
      ctrl = StreamController<List<int>>(
        onListen: () {
          sub ??= super.content.listen(ctrl.add, onError: ctrl.addError,
              onDone: () {
                _releaseOnce();
                ctrl.close();
              },
              cancelOnError: true);
        },
        onPause: () => sub?.pause(),
        onResume: () => sub?.resume(),
        onCancel: () {
          _releaseOnce();
          return sub?.cancel();
        },
      );
      return ctrl.stream;
    }();
  }
}

class AuthCacheManager extends CacheManager {
  static final AuthCacheManager _instance = AuthCacheManager._();

  factory AuthCacheManager() => _instance;
  static AuthCacheManager get I => _instance;

  /// 本会话已解析过的 url → 本地文件（进程内记忆）：滚动往返时绕过异步
  /// 缓存查询，直接同步给 File——网格重建零转圈。文件可能被磁盘缓存淘汰，
  /// 命中前先 existsSync 校验（stat 开销可忽略）。上限 4000 按 FIFO 淘汰
  /// （R2-P3-6）：长会话刷上千张图后无界增长
  final _resolved = <String, File>{};
  static const _resolvedMax = 4000;

  File? peekResolved(String url) {
    final f = _resolved[url];
    if (f == null) return null;
    return f.existsSync() ? f : (_resolved.remove(url), null).$2;
  }

  void rememberResolved(String url, File f) {
    while (_resolved.length >= _resolvedMax) {
      _resolved.remove(_resolved.keys.first);   // FIFO 淘汰（R2-P3-6）
    }
    _resolved[url] = f;
  }

  // ── 会话级失败负缓存（0.12.0 文件页缩略图扩面后引入）──────────
  // 服务端对损坏媒体/缺解码器（如 HEIC 无 heif 插件）会稳定 404/500，
  // 客户端对 5xx 还有 1.5s 重试——文件页任意目录都出缩略图后，坏格子
  // 在滚动往返中每次重挂载都重复"2 次请求 + 最坏 50s 占用信号量槽"，
  // 会饿死照片墙共用通道。失败 URL 记入，TTL 过期自动放行重试
  // （服务端文件被替换修复后最多 10 分钟自愈）。
  static const _failTtl = Duration(minutes: 10);
  final _failed = <String, DateTime>{};

  bool isFailed(String url) {
    final t = _failed[url];
    if (t == null) return false;
    if (DateTime.now().difference(t) > _failTtl) {
      _failed.remove(url);
      return false;
    }
    return true;
  }

  void markFailed(String url) => _failed[url] = DateTime.now();

  /// 主动放行单个 URL 重试（查看器"重试"按钮用）
  void clearFailed(String url) => _failed.remove(url);

  /// 登出/换号时整体清空：负缓存 URL 不含账号，服务端按可见性返回 404，
  /// 账号 A 无权看（被记 404）的路径换账号 B 后可能可看（review 0.12.0 P2）
  void clearFailureCache() => _failed.clear();

  AuthCacheManager._()
      : super(Config('authCachedImageData', fileService: AuthFileService(),
          // 默认 200 个对象——上千张照片的时间线会把缓存反复挤掉，
          // "每次都转圈"的元凶之一；缩略图单张几十 KB，放宽到 5000/180 天
          maxNrOfCacheObjects: 5000,
          stalePeriod: const Duration(days: 180)));
}

/// 相对路径（如 /thumb/a/b.jpg）→ 服务器绝对 URL
Future<String> absoluteUrl(String dioPath) async {
  final base = await Prefs.lastAddress();
  return '$base$dioPath';
}
