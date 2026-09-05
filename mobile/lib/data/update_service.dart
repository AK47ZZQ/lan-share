// 检查更新（App v0.6.0）：/api/app/latest 拿服务端版本 → 对比本地 build →
// 下载 /api/app/download 到沙盒 → OpenFilex 拉起系统安装器。
// 契约见 app/api/appver.py；管理员把新 APK + version.json 放进共享空间
// 「App安装包/」即完成发版。
import 'dart:io';

import 'package:crypto/crypto.dart';
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

  /// 更新包落盘目录：Downloads 下的独立 updates 子目录（v0.16.0 二轮
  /// 审查 P2）。共享空间里任何人都可能上传名叫 lanshare-x.y.z.apk 的普通
  /// 文件——更新包与用户下载混放会让 purgeInstallers 把用户特意下载留存
  /// 的安装包静默清掉；隔离子目录后 purge 只扫自家地盘。
  static Future<Directory> _updateDir() async {
    final dl = Directory(
        '${(await getApplicationDocumentsDirectory()).path}/Downloads');
    final dir = Directory('${dl.path}/updates');
    if (!dir.existsSync()) dir.createSync(recursive: true);
    return dir;
  }

  /// 下载到沙盒下载目录的 updates 子目录（覆盖旧更新包），返回落盘路径。
  /// 完整性校验（R1-P1 + 红队 P2）：sha256 与服务端下发值比对，缺失即拒装
  /// （fail closed，服务端 /api/app/latest 自算兜底）——http 入口下链路内
  /// 攻击者可换包，未校验直接交系统安装器等于 RCE 通道。校验失败删包并抛错。
  static Future<String> download(UpdateInfo info,
      {void Function(int received, int total)? onProgress}) async {
    final dir = await _updateDir();
    // 版本号消毒：只留白名单字符，防 path 拼接注入（version 来自服务端 JSON）
    final safeVer = info.version.replaceAll(RegExp(r'[^A-Za-z0-9._-]'), '');
    final path = '${dir.path}/lanshare-$safeVer.apk';
    final dio = await ApiClient.I.dio();
    await dio.download('/api/app/download', path,
        onReceiveProgress: onProgress,
        options: Options(receiveTimeout: const Duration(minutes: 30)));
    final f = File(path);
    // 红队 P2：sha256 与 size 同信道可整段伪造，size 校验只防意外损坏。
    // sha256 缺失即拒装（fail closed）——服务端 /api/app/latest 已改为
    // version.json 缺失时自算兜底
    if (info.sha256 == null || info.sha256!.isEmpty) {
      await f.delete().catchError((_) => f);
      throw StateError('服务端未提供安装包校验值，已拒绝安装');
    }
    // 流式摘要：85MB 级 APK readAsBytes 全量进内存有 OOM 风险（R2-P2）
    final digest = await sha256.bind(f.openRead()).first;
    final expected = info.sha256!.replaceAll(':', '').toLowerCase();
    if (digest.toString() != expected) {
      await f.delete().catchError((_) => f);
      throw StateError('安装包校验失败（sha256 不符），已删除下载文件');
    }
    return path;
  }

  /// 安装包文件名判定（纯函数，单测锚点）：分发约定 lanshare-<版本>.apk
  static bool isInstallerName(String name) {
    final n = name.toLowerCase();
    return n.startsWith('lanshare-') && n.endsWith('.apk');
  }

  /// 清理 updates 子目录里的历史安装包（v0.16.0 用户需求：更新安装完成
  /// 后包已无用，85MB 级会在沙盒里永久堆积）。返回释放的字节数。
  /// 调用时机：登录启动后 / 进入关于页 / 再次下载前。minAge 内的新文件
  /// 跳过——刚下载完正交给系统安装器的包（安装器点"安装"时才流式读源
  /// 文件）不能被同会话内的重进关于页/重登清掉（审查 P1）。
  static Future<int> purgeInstallers(
      {Duration minAge = const Duration(hours: 1)}) async {
    try {
      final dir = await _updateDir();
      final now = DateTime.now();
      var freed = 0;
      await for (final e in dir.list(followLinks: false)) {
        if (e is! File) continue;
        if (!isInstallerName(e.uri.pathSegments.last)) continue;
        try {
          if (now.difference(e.statSync().modified) < minAge) continue;
          freed += e.lengthSync();
          await e.delete();
        } catch (_) {}
      }
      return freed;
    } catch (_) {
      return 0;
    }
  }

  static Future<void> install(String apkPath) async {
    await OpenFilex.open(apkPath, type: 'application/vnd.android.package-archive');
  }
}
