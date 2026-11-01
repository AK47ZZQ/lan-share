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

    // build 号三级兜底（2026-11-01 修）：
    //
    //   1. asset 文件名里的 `+N` 或 `_N`（lanshare-v1.1.16+123.apk / v1.1.15_122）
    //   2. tag 里的 `+N`
    //   3. 都没有 → 用 semver 推算 major*10000+minor*100+patch
    //
    // 第 3 条是必需的：早期 Release 的资产叫 `LANShare-App-1.1.18.apk`，
    // 没有 `+N`，原来直接 `return null` 把这个源整个丢掉了 —— 这是
    // 「检查更新没反应」的第二个原因。推算值只用于排序/降级比较，
    // 真正判断新旧优先比 version 字符串（见 isNewer）。
    final name = '${apk['name']}';
    var build = 0;
    final plusInName = RegExp(r'[+_](\d+)').firstMatch(name);
    final plusInTag = RegExp(r'[+_](\d+)').firstMatch(tag);
    if (plusInName != null) {
      build = int.tryParse(plusInName.group(1)!) ?? 0;
    } else if (plusInTag != null) {
      build = int.tryParse(plusInTag.group(1)!) ?? 0;
    }
    if (build <= 0) {
      final parts = version.split('.').map(int.tryParse).toList();
      if (parts.length == 3 && parts.every((x) => x != null)) {
        build = parts[0]! * 10000 + parts[1]! * 100 + parts[2]!;
      }
    }
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

  /// 从 GitHub Release 构造历史条目（2026-11-01，更新历史改从 GitHub 拉）。
  ///
  /// 字段映射：tag_name → version，published_at → date（只取日期部分），
  /// body → notes。build 用和检查更新同一套三级兜底（文件名 +N → tag +N →
  /// 版本号推算），没有就返回 null 让调用方跳过这条。
  static UpdateHistoryItem? fromGitHub(Map<String, dynamic> j) {
    final tag = (j['tag_name'] as String? ?? '').trim();
    final m = RegExp(r'(\d+\.\d+\.\d+)').firstMatch(tag);
    if (m == null) return null;
    final version = m.group(1)!;

    var build = 0;
    final assets = (j['assets'] as List?) ?? const [];
    for (final a in assets) {
      if (a is Map && '${a['name']}'.toLowerCase().endsWith('.apk')) {
        final bm = RegExp(r'[+_](\d+)').firstMatch('${a['name']}');
        if (bm != null) build = int.tryParse(bm.group(1)!) ?? 0;
        break;
      }
    }
    if (build <= 0) {
      final parts = version.split('.').map(int.tryParse).toList();
      if (parts.length == 3 && parts.every((x) => x != null)) {
        build = parts[0]! * 10000 + parts[1]! * 100 + parts[2]!;
      }
    }

    final rawDate = '${j['published_at'] ?? ''}';
    return UpdateHistoryItem(
        version: version,
        build: build,
        date: rawDate.length >= 10 ? rawDate.substring(0, 10) : rawDate,
        notes: '${j['body'] ?? ''}');
  }
}

/// 检查更新的结果 —— **三态**，不是「有/没有」两态。
///
/// ## 为什么要三态（2026-11-01 真机反馈）
///
/// 原来两个源都失败时返回 null，UI 一律显示「已是最新」。用户开着 VPN
/// （局域网地址被路由劫持 → 服务端连不上；GitHub 节点也不通）点检查更新，
/// 看到的是「已是最新」，还以为是自己记错了版本。
///
/// 「真的没有新版」和「压根没查成」必须分开：前者该安静，后者该告诉用户
/// 换个网络再试。静默失败是最糟糕的失败方式 —— 用户不知道该重试。
class UpdateCheckResult {
  /// 候选新版本；null 表示「所有可达的源都答复：没有比本地更新的」。
  final UpdateInfo? info;

  /// 是否至少有一个源成功应答。false 时 [info] 无意义。
  final bool reachable;

  /// 各自通没通。分开展示才能给出正确的 VPN 建议 ——
  /// 这两个源的网络诉求是**相反**的：
  ///   · 服务端（局域网 IP）：开 VPN 会被路由劫持，要关
  ///   · GitHub：国内直连经常失败，要开
  /// 混在一起说「连不上更新源」等于让用户猜。
  final bool serverReachable;
  final bool githubReachable;

  const UpdateCheckResult({
    this.info,
    required this.reachable,
    this.serverReachable = false,
    this.githubReachable = false,
  });

  bool get hasUpdate => info != null;

  /// 给用户看的失败原因 + 建议。只有检查失败（reachable=false）时才有意义。
  ///
  /// 这里分三种情况，因为「怎么办」完全不同：
  /// · 只有服务端挂 → 大概率开了 VPN 或换了网络，不影响检查，不用管
  /// · GitHub 挂而服务端通 → 能查到版本，只是下载要走服务端，也能用
  /// · 两个全挂 → 才是真没辙，此时才建议开 VPN（并对局域网场景说明取舍）
  String get failureHint {
    if (reachable) return '';
    if (!serverReachable && !githubReachable) {
      return '两个更新源都连不上。GitHub 在国内经常直连失败，建议开 VPN 后重试；'
          '但开 VPN 会劫持局域网地址，那时就连不上家里的服务器了 —— '
          '更新完记得关掉，或者改用公网入口。';
    }
    if (githubReachable) {
      return '连不上家里的服务端（换了网络，或者开着 VPN 导致局域网地址被劫持）。'
          'GitHub 是通的，检查更新不受影响。';
    }
    return '连不上 GitHub（国内直连经常失败），服务端也没有应答。'
        '建议开 VPN 后点「重试」。';
  }
}

/// 单个源的应答结果（内部用）：拿到什么 + 这个源到底通没通。
class _SourceResult {
  final UpdateInfo? info;
  final bool reachable;
  const _SourceResult(this.info, this.reachable);
}

class UpdateService {
  UpdateService._();

  /// 查最新版本：服务端和 GitHub 两路并发，取**版本号最高**的那个。
  ///
  /// 为什么要双源：服务端读 GitHub Release（GitHub 不可达时回退本地
  /// version.json），GitHub 直连也走一条 —— 两条路互为兜底，任一可用就
  /// 不会让「检查更新」整体失效。
  ///
  /// **排序必须按 version 字符串，不能按 build 数字**：两个源的编号体系
  /// 可能不一致（真实 versionCode 126 vs 没有 build 号时的推算值 10119），
  /// 按数字排会把 1.1.18 排在 1.1.19 前面。
  static Future<UpdateCheckResult> check() async {
    final results = await Future.wait<_SourceResult>([
      _checkServer(),
      _checkGitHub(),
    ]);
    final candidates = results
        .where((r) => r.info != null)
        .map((r) => r.info!)
        .toList()
      ..sort((a, b) => _compareVersion(b.version, a.version));

    final anyReachable = results.any((r) => r.reachable);
    // 顺序和 Future.wait 一致：0 = 服务端，1 = GitHub。
    // 两个都要带上，UI 靠它们给出针对性的 VPN 建议。
    final serverOk = results[0].reachable;
    final githubOk = results[1].reachable;
    if (candidates.isNotEmpty) {
      return UpdateCheckResult(
        info: candidates.first,
        reachable: true,
        serverReachable: serverOk,
        githubReachable: githubOk,
      );
    }
    return UpdateCheckResult(
      info: null,
      reachable: anyReachable,
      serverReachable: serverOk,
      githubReachable: githubOk,
    );
  }

  /// 服务端源。404（服务端没配版本信息）算「应答了但没有」，网络异常算不可达。
  static Future<_SourceResult> _checkServer() async {
    try {
      final r = await AppverApi.latest();
      return _SourceResult(
          UpdateInfo.fromJson(Map<String, dynamic>.from(r.data as Map)), true);
    } catch (_) {
      return const _SourceResult(null, false);
    }
  }

  /// GitHub 源：任何异常都当不可达（静默降级到服务端）。
  static Future<_SourceResult> _checkGitHub() async {
    try {
      final r = await AppverApi.githubLatest();
      if (r == null || r.data is! Map) {
        return const _SourceResult(null, false);
      }
      return _SourceResult(
          UpdateInfo.fromGitHub(Map<String, dynamic>.from(r.data as Map)), true);
    } catch (_) {
      return const _SourceResult(null, false);
    }
  }


  /// 把 CHANGELOG.md 解析成更新历史。
  ///
  /// 只挑**含 App 版本号**的条目 —— CHANGELOG 里服务端和 App 是混着写的
  /// （标题长这样：`v3.79 + App v1.1.22：修 GUI 检查更新`），而 App 的
  /// 更新历史只该显示 App 自己的版本，把服务端版本混进去会让用户以为
  /// 手机装了 3.79。
  static List<UpdateHistoryItem> parseChangelog(String md) {
    final items = <UpdateHistoryItem>[];
    final seen = <String>{};
    // 按一级标题切块
    final sections = md.split(RegExp(r'^## ', multiLine: true));
    for (final sec in sections.skip(1)) {
      final lines = sec.split('\n');
      if (lines.isEmpty) continue;
      final title = lines.first.trim();

      // 必须带 App 版本号才算 App 的条目
      final m = RegExp(r'App\s+v?(\d+\.\d+\.\d+)').firstMatch(title);
      if (m == null) continue;
      final ver = m.group(1)!;

      // 同一个版本可能被拆成多个小节（比如 1.1.20 分了「（一）」「（二）」），
      // 只留第一条 —— 否则历史里会出现两个一模一样的版本号。
      if (!seen.add(ver)) continue;

      final dm = RegExp(r'(\d{4}-\d{2}-\d{2})').firstMatch(title);
      // build 用和检查更新同一套推算规则，保证「当前」标记能对上
      final parts = ver.split('.').map(int.tryParse).toList();
      final build = (parts.length == 3 && parts.every((x) => x != null))
          ? parts[0]! * 10000 + parts[1]! * 100 + parts[2]!
          : 0;

      items.add(UpdateHistoryItem(
        version: ver,
        build: build,
        date: dm?.group(1) ?? '',
        notes: lines.skip(1).join('\n'),
      ));
    }
    return items;
  }

  /// 更新历史（新版本在前）。
  ///
  /// **GitHub 优先，服务端兜底**（2026-11-01）。原来只读服务端的
  /// `/api/app/history`，而那条路依赖用户那台服务端的版本 —— 别人跑旧版
  /// 服务端时，历史永远停在旧版本，「关于」页看着像死了一样。
  ///
  /// GitHub Release 的正文就是更新日志，直接拉，和 App 检查更新同源。
  static Future<List<UpdateHistoryItem>> history() async {
    // 1. CHANGELOG.md：最完整，且不会被 Release 清理影响
    try {
      final md = await AppverApi.githubChangelog();
      if (md != null && md.isNotEmpty) {
        final items = parseChangelog(md);
        if (items.isNotEmpty) return items;
      }
    } catch (_) {
      // 掉到下一步
    }

    // 2. GitHub Release：CHANGELOG 拉不到时兜底
    try {
      final releases = await AppverApi.githubAppReleases();
      if (releases != null && releases.isNotEmpty) {
        final items = releases
            .map(UpdateHistoryItem.fromGitHub)
            .whereType<UpdateHistoryItem>()
            .toList();
        if (items.isNotEmpty) return items;
      }
    } catch (_) {
      // 掉到服务端兜底
    }

    // 3. 服务端：旧版兼容 / GitHub 不可达
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

  /// 远端是否比本地新。
  ///
  /// **先比 version 字符串，再退回 build 数字。**
  ///
  /// 为什么不能只比 build：仓库里同时存在两种编号体系 —— 真实 versionCode
  /// （125）和「没有 build 号时用 semver 推算」的值（1.1.18 → 10118）。
  /// 只比数字的话，已装 1.1.18 的用户会看到 10118 > 125 而被反复提示更新，
  /// 而且万一哪天回退到旧 Release 也判不出来。version 字符串才是版本先后的
  /// 唯一真相，build 只在版本号相同时用来区分同版本的重新打包。
  static bool isNewer(UpdateInfo remote, int localBuild, {String? localVersion}) {
    final local = (localVersion ?? '').trim();
    if (local.isNotEmpty && remote.version.isNotEmpty) {
      final cmp = _compareVersion(remote.version, local);
      if (cmp != 0) return cmp > 0;
    }
    return remote.build > localBuild;
  }

  /// 比较 `1.1.18` 这类版本串：远大于返回 1，相等 0，小于 -1。
  static int _compareVersion(String a, String b) {
    int at(String s, int i) =>
        i < s.split('.').length ? (int.tryParse(s.split('.')[i]) ?? 0) : 0;
    for (var i = 0; i < 3; i++) {
      final d = at(a, i) - at(b, i);
      if (d != 0) return d > 0 ? 1 : -1;
    }
    return 0;
  }

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
