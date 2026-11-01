import 'dart:convert';
// 版本/更新域 HTTP 契约（B8 裸 dio 收编：自 data/update_service.dart 抽出）。
// 契约见 app/api/appver.py；管理员把新 APK + version.json 放进共享空间即发版。
import 'dart:io';

import 'package:dio/dio.dart';

import 'api_client.dart';

class AppverApi {
  AppverApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  /// 服务端最新版本清单（version.json 内容）。
  static Future<Response> latest() async {
    final dio = await _dio();
    return dio.get('/api/app/latest');
  }

  /// 更新历史（versions.json 的 items 全量）。
  static Future<Response> history() async {
    final dio = await _dio();
    return dio.get('/api/app/history');
  }

  /// 下载 APK 到沙盒保存路径（大文件流式落盘）。
  static Future<void> download(String savePath, {void Function(int, int)? onProgress}) async {
    final dio = await _dio();
    await dio.download('/api/app/download', savePath,
        onReceiveProgress: onProgress);
  }

  /// 下载到任意 File（transfers 通道复用）。
  static Future<File> downloadToFile(String savePath,
      {void Function(int, int)? onProgress}) async {
    await download(savePath, onProgress: onProgress);
    return File(savePath);
  }

  /// 拉仓库里的 CHANGELOG.md（更新历史的数据源）。
  ///
  /// ## 为什么不用 Release 列表
  ///
  /// 原来历史是从 GitHub Releases 拉 body。但 Release 是**会被清理**的
  /// —— 仓库只保留最新一版之后，历史就只剩一两条了，而用户想看的是
  /// 「这个 App 一路是怎么变过来的」。
  ///
  /// CHANGELOG.md 在 git 历史里，删不掉、不会丢，也自带完整时间线。
  ///
  /// 走 `contents` API 而不是 `raw.githubusercontent.com`：后者在国内
  /// 经常直接 000，前者和 Release API 是同一个域名，可达性一致。
  ///
  /// 失败返回 null（调用方回落 Release 列表）。
  static Future<String?> githubChangelog() async {
    final dio = Dio(BaseOptions(
      connectTimeout: const Duration(seconds: 6),
      receiveTimeout: const Duration(seconds: 10),
      headers: const {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'LANShare-Android',
      },
    ));
    try {
      final r = await dio.get(
        'https://api.github.com/repos/AK47ZZQ/lan-share/contents/CHANGELOG.md',
      );
      final data = r.data;
      if (data is! Map) return null;
      final b64 = '${data['content'] ?? ''}'.replaceAll(RegExp(r'\s'), '');
      if (b64.isEmpty) return null;
      final bytes = base64Decode(b64);
      return utf8.decode(bytes, allowMalformed: true);
    } catch (_) {
      return null;
    }
  }

  /// 拉 GitHub 上所有 `app-` 前缀的 Release（新到旧），更新历史用。
  ///
  /// 和服务端 `/api/app/history` 相比，这条路**不依赖用户那台服务端的版本**——
  /// 别人跑的是旧版服务端时，历史照样是新的。
  ///
  /// 失败返回 null（调用方回落到服务端）。
  static Future<List<Map<String, dynamic>>?> githubAppReleases() async {
    final dio = Dio(BaseOptions(
      connectTimeout: const Duration(seconds: 6),
      receiveTimeout: const Duration(seconds: 8),
      headers: const {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'LANShare-Android',
      },
    ));
    try {
      final r = await dio.get(
        'https://api.github.com/repos/AK47ZZQ/lan-share/releases',
        queryParameters: {'per_page': 30},
      );
      final list = r.data;
      if (list is! List) return null;
      final out = <Map<String, dynamic>>[];
      for (final item in list) {
        if (item is Map && '${item['tag_name'] ?? ''}'.startsWith('app-')) {
          out.add(Map<String, dynamic>.from(item));
        }
      }
      return out;
    } catch (_) {
      return null;
    }
  }

  /// GitHub 上最新一个 **App** Release（主更新源）。
  ///
  /// ## 为什么不能再用 /releases/latest（2026-11-01 修）
  ///
  /// 这个仓库同时发两种 Release：`server-v3.76`（安装包）和
  /// `app-v1.1.18`（APK）。GitHub 的 `/releases/latest` 只返回**发布时间
  /// 最晚的那一个**，跟标签前缀无关 —— 服务端发得晚，它就返回服务端那个，
  /// 里面根本没有 APK，解析直接失败，用户点「检查更新」永远是「已是最新」。
  ///
  /// 改成拉列表自己筛 `app-` 前缀。`per_page=30` 够覆盖几十个版本，
  /// 而且这个响应会走 HTTP 缓存，实际开销比单条查询大不了多少。
  ///
  /// ## 为什么用 api.github.com 而不是 raw.githubusercontent.com
  ///
  /// 后者在国内常被污染，前者虽然慢但可达；且只有 API 才给 digest（sha256）
  /// 字段，而客户端是「sha256 缺失即拒装」的 fail-closed 策略。
  ///
  /// 独立 Dio：不共享 ApiClient 的 baseUrl 与自签证书配置，避免把 GitHub
  /// 的证书策略和自家服务器的混淆。超时压得很短——这是可选的第二源，
  /// 卡住不如直接放弃。
  static Future<Response?> githubLatest() async {
    final dio = Dio(BaseOptions(
      connectTimeout: const Duration(seconds: 6),
      receiveTimeout: const Duration(seconds: 8),
      headers: const {
        'Accept': 'application/vnd.github+json',
        // GitHub API 强制要求 UA，缺了返回 403
        'User-Agent': 'LANShare-Android',
      },
    ));
    try {
      final r = await dio.get(
        'https://api.github.com/repos/AK47ZZQ/lan-share/releases',
        queryParameters: {'per_page': 30},
      );
      final list = r.data;
      if (list is! List) return null;
      for (final item in list) {
        if (item is Map && '${item['tag_name'] ?? ''}'.startsWith('app-')) {
          // 返回单个 Release 对象的形状，调用方不用改解析
          return Response<dynamic>(
            requestOptions: r.requestOptions,
            data: item,
            statusCode: r.statusCode,
          );
        }
      }
      return null; // 列表里没有 app- 前缀的 Release
    } catch (_) {
      return null; // 网络失败/超时/限流一律当作「这个源没有」
    }
  }
}
