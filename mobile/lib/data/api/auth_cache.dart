// 缩略图/预览图的两级缓存：走与主 API 相同的自签信任策略，并附 Bearer 头
// （/thumb/、/preview/ 都是 require_user 端点，APP-DESIGN §1 图片选型）。
import 'dart:async';
import 'dart:io';

import 'package:flutter_cache_manager/flutter_cache_manager.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart' show IOClient;

import '../local/prefs.dart';
import '../local/secure_store.dart';
import 'trust.dart';

/// 简易 FIFO 并发闸：时间线一次重建会同时发起几十张缩略图请求，
/// 不加闸会瞬间打满连接池并放大服务端峰值；8 并发对 LAN 足够跑满带宽。
class _Semaphore {
  final int max;
  int _running = 0;
  final _queue = <void Function()>[];
  _Semaphore(this.max);

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

  void _pump() {
    if (_running < max && _queue.isNotEmpty) {
      _running++;
      _queue.removeAt(0)();
    }
  }
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
    // flutter_cache_manager 对存过 ETag 的图片会带 If-None-Match 走条件下载，
    // 服务端命中即 304——但走下载路径时管理器没有本地文件可复用，304 等于
    // 空手而归（0.4.4 真机"加载完划走就不显示"真凶）。剥掉条件头强制 200
    // 全量；秒开由管理器磁盘缓存负责（5000 对象），不依赖 HTTP 304。
    final h = Map<String, String>.from(headers ?? {})
      ..remove('if-none-match')
      ..remove('If-None-Match');
    var resp = await _semaphore.run(() => _send(url, headers: h));
    // 非 2xx 绝不入缓存（否则 401 错误页会被当图片缓存住）
    if (resp.statusCode == 401) {
      // Token 可能被并发读坏或已轮换：清缓存强读一次再试
      await resp.stream.drain<void>();
      _cachedToken = null;
      resp = await _semaphore.run(() => _send(url, headers: h));
    }
    // 公网弱网加固（v0.10.1）：5xx/429 是瞬时扰动，等 1.5s 重试一次
    // 再判死；304 剥离在上游（不依赖条件请求）——重试不重复扣缓存。
    if (resp.statusCode >= 500 || resp.statusCode == 429) {
      await resp.stream.drain<void>();
      await Future.delayed(const Duration(milliseconds: 1500));
      resp = await _semaphore.run(() => _send(url, headers: h));
    }
    if (resp.statusCode < 200 || resp.statusCode >= 300) {
      await resp.stream.drain<void>();
      throw Exception('image fetch HTTP ${resp.statusCode}');
    }
    return HttpGetResponse(resp);
  }
}

class AuthCacheManager extends CacheManager {
  static final AuthCacheManager _instance = AuthCacheManager._();

  factory AuthCacheManager() => _instance;
  static AuthCacheManager get I => _instance;

  /// 本会话已解析过的 url → 本地文件（进程内记忆）：滚动往返时绕过异步
  /// 缓存查询，直接同步给 File——网格重建零转圈。文件可能被磁盘缓存淘汰，
  /// 命中前先 existsSync 校验（stat 开销可忽略）
  final _resolved = <String, File>{};

  File? peekResolved(String url) {
    final f = _resolved[url];
    if (f == null) return null;
    return f.existsSync() ? f : (_resolved.remove(url), null).$2;
  }

  void rememberResolved(String url, File f) => _resolved[url] = f;

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
