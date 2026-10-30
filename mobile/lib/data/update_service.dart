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
import 'api/appver_api.dart';

class UpdateInfo {
  final String version;
  final int build;
  final String notes;
  final String? sha256;
  final bool available;
  final int? size;

  /// 直链下载地址。null = 走服务端 /api/app/download（自家分发）；
  /// 有值 = 第三方分发（GitHub Release asset 的 browser_download_url）。
  final String? url;

  /// 来源标记：'server' | 'github'。UI 上用来告诉用户这版从哪查到的。
  final String source;

  UpdateInfo({required this.version, required this.build, required this.notes,
      required this.sha256, required this.available, required this.size,
      this.url, this.source = 'server'});

  factory UpdateInfo.fromJson(Map<String, dynamic> j) => UpdateInfo(
      version: j['version'] as String? ?? '',
      build: (j['build'] as num?)?.toInt() ?? 0,
      notes: j['notes'] as String? ?? '',
      sha256: j['sha256'] as String?,
      available: j['available'] as bool? ?? false,
      size: (j['size'] as num?)?.toInt(),
      source: 'server');

  /// 从 GitHub Release 的 JSON 构造。
  ///
  /// 三个字段来源不同，都要解析：
  ///   tag_name              "app-v1.1.16"        → version = 1.1.16
  ///   assets[].name         "lanshare-v1.1.16+123.apk"
  ///                                                → build = 123（tag 里没有 build 号）
  ///   assets[].digest       "sha256:abcd…"       → sha256
  ///   assets[].browser_download_url               → url
  ///
  /// 找不到 apk asset、或 digest 不是 sha256 格式，一律当作「这个源没有新版」
  /// ——客户端是 fail-closed 的，没有校验值就不该提示用户去装。
  static UpdateInfo? fromGitHub(Map<String, dynamic> j) {
    final tag = (j['tag_name'] as String? ?? '').trim();
    if (tag.isEmpty) return null;

    // tag 形如 app-v1.1.16 / v1.1.16 / 1.1.16
    final verMatch = RegExp(r'(\d+\.\d+\.\d+)').firstMatch(tag);
    if (verMatch == null) return null;
    final version = verMatch.group(1)!;

    final assets = (j['assets'] as List?) ?? const [];
    Map<String, dynamic>? apk;
    for (final a in assets) {
      if (a is Map && '${a['name']}'.toLowerCase().endsWith('.apk')) {
        apk = Map<String, dynamic>.from(a);
        break;
      }
    }
    if (apk == null) return null;

    // build 号只能从 asset 文件名里拿：lanshare-v1.1.16+123.apk
    final name = '${apk['name']}';
    final buildMatch = RegExp(r'\+(\d+)').firstMatch(name);
    if (buildMatch == null) return null;
    final build = int.tryParse(buildMatch.group(1)!) ?? 0;
    if (build <= 0) return null;

    final digest = '${apk['digest'] ?? ''}';
    final sha = digest.startsWith('sha256:')
        ? digest.substring('sha256:'.length).trim()
        : '';

    final url = '${apk['browser_download_url'] ?? ''}';
    if (url.isEmpty) return null;

    return UpdateInfo(
      version: version,
      build: build,
      notes: '${j['body'] ?? ''}'.trim(),
      sha256: sha.isEmpty ? null : sha,
      available: true,
      size: (apk['size'] as num?)?.toInt(),
      url: url,
      source: 'github',
    );
  }
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

  /// 查最新版本：服务端和 GitHub 两路并发，取 build 号大的那个。
  ///
  /// 为什么要双源：服务端读 `App安装包/version.json`，靠人工在发版时同步，
  /// 忘了就永远推不出新版（发 1.1.16 时真踩过）。GitHub Release 是发布
  /// 动作本身，不会漏。反过来，GitHub 在部分网络环境下不可达，那就回落到
  /// 服务端——两个源互为兜底，任一可用就不会让「检查更新」整体失效。
  ///
  /// 都不通时返回 null，UI 显示「已是新版」而不是报错——检查更新失败不该
  /// 变成一个红色错误吓唬人。
  static Future<UpdateInfo?> check() async {
    final results = await Future.wait<UpdateInfo?>([
      _checkServer(),
      _checkGitHub(),
    ]);
    final candidates = results.whereType<UpdateInfo>().toList()
      ..sort((a, b) => b.build.compareTo(a.build));
    return candidates.isEmpty ? null : candidates.first;
  }

  /// 服务端源：未配置（404）或网络失败都返回 null
  static Future<UpdateInfo?> _checkServer() async {
    try {
      final r = await AppverApi.latest();
      return UpdateInfo.fromJson(Map<String, dynamic>.from(r.data as Map));
    } catch (_) {
      return null;
    }
  }

  /// GitHub 源：任何异常都返回 null（静默降级到服务端）
  static Future<UpdateInfo?> _checkGitHub() async {
    try {
      final r = await AppverApi.githubLatest();
      if (r == null || r.data is! Map) return null;
      return UpdateInfo.fromGitHub(Map<String, dynamic>.from(r.data as Map));
    } catch (_) {
      return null;
    }
  }

  /// 更新历史（新版本在前）；未配置/请求失败返回空列表（UI 隐藏栏目）
  static Future<List<UpdateHistoryItem>> history() async {
    try {
      final r = await AppverApi.history();
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
    if (info.url != null && info.url!.isNotEmpty) {
      // 第三方源（GitHub Release asset）：直链下载，不带自家会话凭据。
      // 独立 Dio——不要为了图省事复用 ApiClient，那会把 Bearer token 一并
      // 发到 github.com，等于把登录凭据交给第三方。
      final raw = Dio(BaseOptions(
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(minutes: 30),
      ));
      await raw.download(info.url!, path, onReceiveProgress: onProgress);
    } else {
      final dio = await ApiClient.I.dio();
      await dio.download('/api/app/download', path,
          onReceiveProgress: onProgress,
          options: Options(receiveTimeout: const Duration(minutes: 30)));
    }
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

  /// 本版安装包在本地是否已存在且校验通过；有则返回路径，可直接安装。
  ///
  /// 用户反馈 f6f2：下载完点"安装"后在系统安装器里取消，再回来点要重下一遍
  /// ——几十 MB 白流量、白等待，包还占着沙盒。这里做**下载前查缓存**：
  /// 同一 version 的包只要在 updates/ 里且 sha256 与 size 都对得上，就直接
  /// 复用（`purgeInstallers` 的 minAge=1h 保证刚下完的包不会被自己清掉）。
  /// 校验不通过（半截/被换）则删掉返回 null，逼一次干净重下。
  static Future<String?> cachedInstaller(UpdateInfo info) async {
    if (info.sha256 == null || info.sha256!.isEmpty) return null;
    try {
      final dir = await _updateDir();
      final safeVer = info.version.replaceAll(RegExp(r'[^A-Za-z0-9._-]'), '');
      final f = File('${dir.path}/lanshare-$safeVer.apk');
      if (!f.existsSync()) return null;
      if (info.size != null && f.lengthSync() != info.size) {
        await f.delete().catchError((_) => f);
        return null;
      }
      final digest = await sha256.bind(f.openRead()).first;
      final expected = info.sha256!.replaceAll(':', '').toLowerCase();
      if (digest.toString() != expected) {
        await f.delete().catchError((_) => f);
        return null;
      }
      return f.path;
    } catch (_) {
      return null;
    }
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
