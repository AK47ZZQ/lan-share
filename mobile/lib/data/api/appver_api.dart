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
}
