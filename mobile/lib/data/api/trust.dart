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
  Future<void>? _warmupFuture; // 并发 warmup 复用同一次 IO

  static String fingerprint(X509Certificate cert) =>
      sha256.convert(cert.der).toString();

  /// 64 位 hex → 冒号分隔的指纹格式。注意 Dart 的 replaceAll 不解释 $1
  /// 分组引用，必须用 replaceAllMapped（曾经渲染出 "$1:$1:..."）。
  static String pretty(String hexFp) {
    final up = hexFp.toUpperCase();
    return up
        .replaceAllMapped(RegExp(r'.{2}'), (m) => '${m[0]}:')
        .replaceFirst(RegExp(r':$'), '');
  }

  Future<void> warmup() {
    if (_warmed) return Future.value();
    return _warmupFuture ??= _doWarmup();
  }

  Future<void> _doWarmup() async {
    try {
      final raw = await SecureStore.string('trust_hosts');
      for (final key in raw?.split('\n') ?? const []) {
        if (key.isEmpty) continue;
        final fp = await SecureStore.string('trust_$key');
        if (fp != null && fp.isNotEmpty) _trusted[key] = fp;
      }
    } finally {
      // SecureStore.string 已不抛错，这里是兜底：失败的最坏后果 =
      // 已信任服务器需重新确认一次，而不是所有连接永久失败。
      _warmed = true;
    }
  }

  /// badCertificateCallback 用（同步）：已信任且指纹匹配 → 放行；
  /// 否则记 pending 供 UI 确认，并拒绝。known 存在但不匹配（服务器换
  /// 证书）也要记 pending——不记的话 probe/确认框都拿不到指纹，且
  /// revoke 无 UI 入口，该端点从此无自愈路径（v0.15.2 红队 P1）。
  bool allow(X509Certificate cert, String host, int port) {
    final key = '$host:$port';
    final fp = fingerprint(cert);
    final known = _trusted[key];
    if (known != null) {
      if (known == fp) return true;
      _pending[key] = fp; // 换证书：留待重新确认（登录页/账号安全可裁决）
      return false;
    }
    _pending[key] = fp;
    return false;
  }

  /// 连接失败后 UI 读取待确认指纹（无则 null）
  String? pendingFor(String host, int port) => _pending['$host:$port'];

  /// 该端点当前已信任的指纹（无则 null）。自动切换预信任用它做"同值
  /// 跳过"，避免每 20s 重复写 SecureStore。
  String? trustedFor(String host, int port) => _trusted['$host:$port'];

  /// 指纹是否与本机任一已信任地址的指纹同源。主/备隧道指向同一服务器 →
  /// 同一副证书密钥 → 指纹必然一致；新入口出示已知指纹可安全免确认信任，
  /// 出示未知指纹（节点端 TLS 终结/中间人/换了服务器）仍须走显式确认。
  bool knowsFingerprint(String fp) {
    final norm = _normFp(fp);
    if (norm.isEmpty) return false;
    return _trusted.values.any((v) => _normFp(v) == norm);
  }

  /// 归一化：去冒号、统一大写（服务端 /api/app/entries 下发冒号大写格式，
  /// 本地存的是无冒号小写 hex——比对前必须归一）
  static String _normFp(String fp) => fp.replaceAll(':', '').toUpperCase();

  /// 入库格式归一：去冒号、统一小写 hex——与 fingerprint(X509) 产出对齐。
  /// 服务端 /api/app/entries 下发的是冒号大写格式；调用方若原样入库，
  /// allow() 的严格相等永远失败，且该端点因 known!=null 不再记 pending，
  /// 信任记录从此既不能自动静默信任也无法走确认框，无自愈路径
  /// （v0.15.2 预信任踩坑，收口到此让所有调用方免疫）。
  static String normHex(String fp) => fp.replaceAll(':', '').toLowerCase();

  Future<void> trust(String host, int port, String fpRaw) async {
    final key = '$host:$port';
    final fp = normHex(fpRaw);
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

  /// 创建带信任策略的 HttpClient（Dio 适配器/缩略图/媒体代理共用）。
  /// 显式超时是公网弱网加固（v0.10.1）：Dart 默认 connectionTimeout 很长、
  /// keep-alive idle 15s——蜂窝下容易卡死/误断。活动传输不受 idleTimeout
  /// 影响（它只作用于空闲复用连接），这里放宽是安全兜底。
  static HttpClient createHttpClient() {
    final client = HttpClient();
    client.connectionTimeout = const Duration(seconds: 12);
    client.idleTimeout = const Duration(minutes: 5);
    client.badCertificateCallback = (cert, host, port) =>
        I.allow(cert, host, port);
    return client;
  }

  static String jsonOf(Object? o) => jsonEncode(o);
}
