// 自签证书信任（APP-DESIGN §5）：不做全局放宽，逐服务器显式信任 + SPKI 指纹固定。
// 流程：首次连接证书错误 → 记下待确认指纹 → UI 弹"信任此服务器"（展示 SHA256）
// → 用户确认后按 host:port 存 SecureStore；此后指纹匹配放行，换证书需重新确认。
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';

import '../local/secure_store.dart';

class TrustStore {
  TrustStore._();
  static final TrustStore I = TrustStore._();

  // host:port → 已信任指纹（启动时从 SecureStore 预热，供同步回调查询）
  final Map<String, String> _trusted = {};
  // host:port → 最近一次被拒证书的指纹（UI 读取后弹确认框）
  final Map<String, String> _pending = {};
  bool _warmed = false;
  Future<void>? _warmupFuture;   // 并发 warmup 复用同一次 IO

  static String fingerprint(X509Certificate cert) =>
      sha256.convert(cert.der).toString();

  /// 64 位 hex → 冒号分隔的指纹格式。注意 Dart 的 replaceAll 不解释 $1
  /// 分组引用，必须用 replaceAllMapped（曾经渲染出 "$1:$1:..."）。
  static String pretty(String hexFp) {
    final up = hexFp.toUpperCase();
    return up.replaceAllMapped(RegExp(r'.{2}'), (m) => '${m[0]}:')
        .replaceFirst(RegExp(r':$'), '');
  }

  Future<void> warmup() {
    if (_warmed) return Future.value();
    return _warmupFuture ??= _doWarmup();
  }

  Future<void> _doWarmup() async {
    final raw = await SecureStore.string('trust_hosts');
    for (final key in raw?.split('\n') ?? const []) {
      if (key.isEmpty) continue;
      final fp = await SecureStore.string('trust_$key');
      if (fp != null && fp.isNotEmpty) _trusted[key] = fp;
    }
    _warmed = true;
  }

  /// badCertificateCallback 用（同步）：已信任且指纹匹配 → 放行；
  /// 否则记 pending 供 UI 确认，并拒绝。
  bool allow(X509Certificate cert, String host, int port) {
    final key = '$host:$port';
    final fp = fingerprint(cert);
    final known = _trusted[key];
    if (known != null) return known == fp; // 换证书 → 不放行，需重新确认
    _pending[key] = fp;
    return false;
  }

  /// 连接失败后 UI 读取待确认指纹（无则 null）
  String? pendingFor(String host, int port) => _pending['$host:$port'];

  Future<void> trust(String host, int port, String fp) async {
    final key = '$host:$port';
    _trusted[key] = fp;
    _pending.remove(key);
    await SecureStore.setString('trust_$key', fp);
    final keys = _trusted.keys.toList();
    await SecureStore.setString('trust_hosts', keys.join('\n'));
  }

  Future<void> revoke(String host, int port) async {
    final key = '$host:$port';
    _trusted.remove(key);
    await SecureStore.setString('trust_hosts', _trusted.keys.join('\n'));
  }

  /// 创建带信任策略的 HttpClient（Dio 适配器与缩略图下载共用）
  static HttpClient createHttpClient() {
    final client = HttpClient();
    client.badCertificateCallback = (cert, host, port) =>
        I.allow(cert, host, port);
    return client;
  }

  static String jsonOf(Object? o) => jsonEncode(o);
}
