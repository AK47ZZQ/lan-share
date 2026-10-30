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

  /// GitHub 公开仓库的最新 Release（第二更新源）。
  ///
  /// 为什么要有这条：服务端 /api/app/latest 读的是管理员手动维护的
  /// `App安装包/version.json`，发版时忘了同步就永远推不出新版（真发生过）。
  /// GitHub Release 是发布动作本身，不存在「忘了同步」。
  ///
  /// 为什么用 api.github.com 而不是 raw.githubusercontent.com：后者在国内
  /// 常被污染，前者虽然慢但可达；且只有 API 才给 digest（sha256）字段，
  /// 而客户端是「sha256 缺失即拒装」的 fail-closed 策略。
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
      return await dio.get(
          'https://api.github.com/repos/AK47ZZQ/lan-share/releases/latest');
    } catch (_) {
      return null;   // 网络失败/超时/限流一律当作「这个源没有」
    }
  }
}
