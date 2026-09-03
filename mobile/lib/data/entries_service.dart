// 公网入口清单（/api/app/entries，免登录）：主/备隧道地址 + 服务器证书指纹。
// 登录页服务器选择器据此展示「主隧道/备用隧道」快捷项；指纹供跨入口信任
// 预校验（同一服务器的入口共用同一副证书密钥 → 指纹一致）。
// 必须用证书放行的独立 Dio：清单要在新地址被信任**之前**拿到，
// 而拿到的指纹恰恰用于接下来的信任决策——所以这里只回答"服务器在不在线、
// 它自报什么入口"，信不信由 TrustStore 的指纹比对裁决。
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:dio/io.dart';

class AppEntries {
  final String? pub; // 主公网入口（如 https://your-frp-domain.com:xxxxx），未配置为 null
  final String? pubAlt; // 备用公网入口
  final String certSha256; // 冒号格式指纹；无证书/解析失败为空串
  AppEntries({required this.pub, required this.pubAlt, required this.certSha256});

  /// 主/备中非空的地址（顺序：主在前）
  List<String> get publicAddresses => [pub, pubAlt].whereType<String>().toList();

  factory AppEntries.fromJson(Map<String, dynamic> j) => AppEntries(
      pub: j['public'] as String?,
      pubAlt: j['public_alt'] as String?,
      certSha256: j['cert_sha256'] as String? ?? '');

  /// 指纹是否与给定指纹同源（忽略大小写与冒号分隔差异）
  bool fingerprintMatches(String fp) {
    final norm = fp.replaceAll(':', '').toUpperCase();
    if (norm.isEmpty) return false;
    return certSha256.replaceAll(':', '').toUpperCase() == norm;
  }
}

class EntriesService {
  EntriesService._();

  /// 拉取入口清单；服务器不可达/非本系统服务端返回 null（调用方静默降级）
  static Future<AppEntries?> fetch(String base) async {
    final dio = Dio(BaseOptions(
        connectTimeout: const Duration(milliseconds: 4000),
        receiveTimeout: const Duration(milliseconds: 4000),
        validateStatus: (_) => true))
      ..httpClientAdapter = IOHttpClientAdapter(createHttpClient: () {
        final hc = HttpClient()
          ..connectionTimeout = const Duration(milliseconds: 4000)
          ..badCertificateCallback = ((cert, host, port) => true);
        return hc;
      });
    try {
      final r = await dio.get('$base/api/app/entries');
      if (r.statusCode != 200 || r.data is! Map) return null;
      return AppEntries.fromJson(Map<String, dynamic>.from(r.data as Map));
    } catch (_) {
      return null;
    } finally {
      dio.close(force: true);
    }
  }
}
