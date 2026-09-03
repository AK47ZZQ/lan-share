// 检查更新（App v0.6.0）：/api/app/latest 拿服务端版本 → 对比本地 build →
// 下载 /api/app/download 到沙盒 → OpenFilex 拉起系统安装器。
// 契约见 app/api/appver.py；管理员把新 APK + version.json 放进共享空间
// 「App安装包/」即完成发版。
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';

import 'api/api_client.dart';

class UpdateInfo {
  final String version;
  final int build;
  final String notes;
  final String? sha256;
  final bool available;
  final int? size;
  UpdateInfo({required this.version, required this.build, required this.notes,
      required this.sha256, required this.available, required this.size});

  factory UpdateInfo.fromJson(Map<String, dynamic> j) => UpdateInfo(
      version: j['version'] as String? ?? '',
      build: (j['build'] as num?)?.toInt() ?? 0,
      notes: j['notes'] as String? ?? '',
      sha256: j['sha256'] as String?,
      available: j['available'] as bool? ?? false,
      size: (j['size'] as num?)?.toInt());
}

/// 更新历史条目（/api/app/history，服务端 App安装包/versions.json）
class UpdateHistoryItem {
  final String version;
  final int build;
  final String date;
  final String notes;
  UpdateHistoryItem({required this.version, required this.build,
      required this.date, required this.notes});

  factory UpdateHistoryItem.fromJson(Map<String, dynamic> j) =>
      UpdateHistoryItem(
          version: j['version'] as String? ?? '',
          build: (j['build'] as num?)?.toInt() ?? 0,
          date: j['date'] as String? ?? '',
          notes: j['notes'] as String? ?? '');
}

class UpdateService {
  UpdateService._();

  /// 服务端最新版本；服务端未配置时返回 null
  static Future<UpdateInfo?> check() async {
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/app/latest');
      return UpdateInfo.fromJson(Map<String, dynamic>.from(r.data as Map));
    } on DioException catch (e) {
      if (e.response?.statusCode == 404) return null;
      rethrow;
    }
  }

  /// 更新历史（新版本在前）；未配置/请求失败返回空列表（UI 隐藏栏目）
  static Future<List<UpdateHistoryItem>> history() async {
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/app/history');
      final items = (r.data['items'] as List? ?? const [])
          .map((e) =>
              UpdateHistoryItem.fromJson(Map<String, dynamic>.from(e as Map)))
          .toList();
      return items;
    } catch (_) {
      return [];
    }
  }

  /// 远端 build 比本地新（build 是单调发布号，比 version 字符串可靠）
  static bool isNewer(UpdateInfo remote, int localBuild) =>
      remote.build > localBuild;

  /// 下载到沙盒下载目录（覆盖旧更新包），返回落盘路径
  static Future<String> download(UpdateInfo info,
      {void Function(int received, int total)? onProgress}) async {
    final dir = await getApplicationDocumentsDirectory();
    final dlDir = Directory('${dir.path}/Downloads');
    if (!dlDir.existsSync()) dlDir.createSync(recursive: true);
    final path = '${dlDir.path}/lanshare-${info.version}.apk';
    final dio = await ApiClient.I.dio();
    await dio.download('/api/app/download', path,
        onReceiveProgress: onProgress,
        options: Options(receiveTimeout: const Duration(minutes: 30)));
    return path;
  }

  static Future<void> install(String apkPath) async {
    await OpenFilex.open(apkPath, type: 'application/vnd.android.package-archive');
  }
}
