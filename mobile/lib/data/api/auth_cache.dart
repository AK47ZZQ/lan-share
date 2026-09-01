// 缩略图/预览图的两级缓存：走与主 API 相同的自签信任策略，并附 Bearer 头
// （/thumb/、/preview/ 都是 require_user 端点，APP-DESIGN §1 图片选型）。
import 'package:flutter_cache_manager/flutter_cache_manager.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart' show IOClient;

import '../local/prefs.dart';
import '../local/secure_store.dart';
import 'trust.dart';

class AuthFileService extends FileService {
  final http.Client _client = IOClient(TrustStore.createHttpClient());

  @override
  Future<FileServiceResponse> get(String url, {Map<String, String>? headers}) async {
    await TrustStore.I.warmup();
    final token = await SecureStore.token();
    final req = http.Request('GET', Uri.parse(url));
    req.headers['authorization'] = 'Bearer ${token ?? ''}';
    if (headers != null) req.headers.addAll(headers);
    final resp = await _client.send(req);
    // 非 2xx 绝不入缓存（否则 401 错误页会被当图片缓存住）
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
