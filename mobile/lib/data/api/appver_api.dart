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

  /// 拉仓库里的更新历史（`CHANGELOG.md` + 历史分片）。
  ///
  /// ## 为什么要补拉分片
  ///
  /// 2026-11-19 起 `CHANGELOG.md` 滚动归档：主文件只留最近 50 条（约 150KB），
  /// 更早的按季度切到 `docs/changelog/CHANGELOG-archive-YYYYQN.md`。
  /// 不补拉的话，这条兜底路径的历史会从 187 条缩到 50 条。
  ///
  /// 主文件**顶部**有一行机器可读索引：
  ///
  ///     <!-- ARCHIVES: docs/changelog/CHANGELOG-archive-2026Q4.md ... -->
  ///
  /// 必须在顶部：markdown 没有元数据概念，写在末尾的注释会被 `^## ` 切块
  /// 逻辑算进最后一个条目的正文（实测让末条多出 100 字并显示到更新说明里）。
  ///
  /// 分片拉不到就**只返回主文件** —— 历史短一截好过整页空着，调用方不该感知。
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
      final main = await _fetchRepoText(dio, 'CHANGELOG.md');
      if (main == null || main.isEmpty) return null;

      final shards = archiveShardsOf(main);
      if (shards.isEmpty) return main; // 还没归档过：行为和以前完全一致

      // 并发拉，单个失败返回 null 直接略过（不因为一个分片拖垮整页历史）
      final parts = await Future.wait(shards
          .take(_maxArchiveShards)
          .map((p) => _fetchRepoText(dio, p)));

      final buf = StringBuffer(main);
      for (final part in parts) {
        if (part == null) continue;
        final body = afterFirstH2(part);
        if (body.isEmpty) continue;
        buf
          ..write('\n\n')
          ..write(body);
      }
      return buf.toString();
    } catch (_) {
      return null;
    }
  }

  /// 最多补拉几个历史分片。
  ///
  /// 一个季度约 250KB，全拉一遍会到 MB 级，而这条路本来就只是兜底
  /// （主源是 R2 清单里的 history 数组，19KB）。覆盖最近一年够了。
  static const _maxArchiveShards = 4;

  /// 拉仓库里任意文件的文本（contents API + base64）。
  ///
  /// 文件超过 1MB 时 GitHub 不返回 `content`，这里会拿到空串并返回 null，
  /// 调用方按「这个文件没有」处理即可。
  static Future<String?> _fetchRepoText(Dio dio, String path) async {
    try {
      final r = await dio.get(
        'https://api.github.com/repos/AK47ZZQ/lan-share/contents/$path',
      );
      final data = r.data;
      if (data is! Map) return null;
      final b64 = '${data['content'] ?? ''}'.replaceAll(RegExp(r'\s'), '');
      if (b64.isEmpty) return null;
      return utf8.decode(base64Decode(b64), allowMalformed: true);
    } catch (_) {
      return null;
    }
  }

  /// 主文件顶部的 `<!-- ARCHIVES: a b c -->` → 分片路径（新到旧，脚本已排好）。
  ///
  /// 公开是为了让 `test/update_github_test.dart` 能直接测 —— 这段解析一旦
  /// 出错，App 会静默丢掉全部早期历史，属于「坏了没人发现」的典型。
  static List<String> archiveShardsOf(String md) {
    final m = RegExp(r'<!--\s*ARCHIVES:(.*?)-->').firstMatch(md);
    if (m == null) return const [];
    return m
        .group(1)!
        .split(RegExp(r'\s+'))
        .where((s) => s.isNotEmpty)
        .toList(growable: false);
  }

  /// 分片开头是 `# CHANGELOG 归档 · 2026Q3` + 说明段落，都不是条目。
  /// 从第一个 `## ` 截起，否则这几行会被算进上一条的正文。
  static String afterFirstH2(String md) {
    final m = RegExp(r'^## ', multiLine: true).firstMatch(md);
    return m == null ? '' : md.substring(m.start);
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
