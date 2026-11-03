// 自定义更新源（App 端，协议与服务端 3.96 一致）。
// 契约见仓库 `docs/更新源协议.md` —— 两边必须同时改。
//
// 默认更新源是「自家服务端 /api/app/latest」+「GitHub」双路并发。
// 配了自定义地址之后：先按顺序试这些地址（局域网写第一条、樱花隧道第二条），
// 第一个通的就用；全都不通才回落到默认双源 —— 回落时界面必须明写来源，
// 否则用户会以为「我的源里没有新版」，实际是源挂了。

import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api/trust.dart';

/// 地址校验 + 本地存取。
///
/// 存在 SharedPreferences（纯文本、非敏感）：丢了的后果只是「更新源配置
/// 没了，回到默认 GitHub」，不涉及凭据，没必要上 SecureStore。
class UpdateSourceStore {
  UpdateSourceStore._();

  static const _key = 'update_source_urls';

  static Future<List<String>> load() async {
    try {
      final sp = await SharedPreferences.getInstance();
      return (sp.getStringList(_key) ?? const <String>[])
          .map((e) => e.trim())
          .where((e) => e.isNotEmpty)
          .toList();
    } catch (_) {
      // 读不出来当「没配」—— 至少还能从默认源更新，不会卡死
      return const <String>[];
    }
  }

  static Future<void> save(List<String> urls) async {
    final clean = <String>[];
    for (final u in urls) {
      final s = u.trim();
      if (s.isEmpty) continue;
      final err = validate(s);
      if (err != null) {
        // 抛而不是静默丢弃：保存时就应该拦住，别等用户回家发现更新不了
        throw FormatException('$s：$err');
      }
      clean.add(s.replaceAll(RegExp(r'/+$'), ''));
    }
    final sp = await SharedPreferences.getInstance();
    if (clean.isEmpty) {
      await sp.remove(_key);
    } else {
      await sp.setStringList(_key, clean);
    }
  }

  /// 校验一个地址。返回 null = 合法，否则是给人看的错误说明。
  ///
  /// 只接受 https（回环除外）。理由和服务端一样：自定义源意味着**按这个
  /// 地址下载 APK 然后让用户安装**，明文 http 下中间人换一个包就是远程
  /// 代码执行，而界面照样显示「有新版本」。
  static String? validate(String raw) {
    final s = raw.trim();
    if (s.isEmpty) return '地址不能为空';

    final uri = Uri.tryParse(s);
    if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
      return '地址要以 https:// 开头（例如 https://192.168.1.100:8765/updates）';
    }

    final scheme = uri.scheme.toLowerCase();
    final host = uri.host.toLowerCase();

    if (scheme == 'https') return null;
    if (scheme == 'http') {
      if (host == '127.0.0.1' || host == 'localhost') return null;
      return '只支持 https://（http 是明文传输，安装包可能被中间人替换）';
    }
    return '不支持的协议 ${uri.scheme}://（只支持 https）';
  }
}

/// 拉一个自定义源的清单（`<base>/index.json`）。
///
/// 只管取和基本形状校验；字段语义交给 [UpdateService] 解析 —— 那边已经有
/// 一套 build 号三级兜底逻辑，没必要为了「统一」再造一份。
class UpdateSourceClient {
  UpdateSourceClient._();

  /// 用户可能填目录、带尾斜杠、或者直接填 index.json，三种都认 ——
  /// 让人去记「到底该不该带文件名」是无谓的负担。
  static String manifestUrl(String base) {
    final b = base.trim().replaceAll(RegExp(r'/+$'), '');
    if (b.toLowerCase().endsWith('.json')) return b;
    return '$b/index.json';
  }

  /// 返回清单 Map；任何失败都抛异常（调用方决定是「这个源挂了」还是「整体挂了」）。
  ///
  /// 证书：复用 [TrustStore.createHttpClient]，也就是 App 连自家服务器那套
  /// 自签信任（按 host:port 固定 SPKI 指纹）。不另开一条「无条件接受任何
  /// 证书」的通道 —— 更新链路上放宽 TLS 等于把「谁都能推包给你」变成默认。
  static Future<Map<String, dynamic>> fetch(String base) async {
    final dio = Dio(BaseOptions(
      connectTimeout: const Duration(seconds: 5),
      receiveTimeout: const Duration(seconds: 8),
      headers: const {
        'Accept': 'application/json',
        'User-Agent': 'LANShare-Android',
      },
    ));
    dio.httpClientAdapter =
        IOHttpClientAdapter(createHttpClient: TrustStore.createHttpClient);

    await TrustStore.I.warmup();
    final r = await dio.get(manifestUrl(base));
    final data = r.data;
    if (data is! Map) {
      throw const FormatException('清单根节点不是 JSON 对象');
    }
    final out = Map<String, dynamic>.from(data);

    // schema 只认 1。看到不认识的版本要明确失败，而不是硬解析出一堆 null
    // 然后告诉用户「没有新版」。
    final schema = out['schema'];
    if (schema != null) {
      final s = schema is num ? schema.toInt() : int.tryParse('$schema');
      if (s == null) {
        throw FormatException('schema 字段不合法：$schema');
      }
      if (s != 1) {
        throw FormatException('清单 schema=$s，这个 App 只认 1，请先升级');
      }
    }
    return out;
  }
}
