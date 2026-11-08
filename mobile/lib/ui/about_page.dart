// 关于页（App v0.6.0）：版本信息 + 检查更新（下载自家 APK 并拉起安装器）+
// 开源许可。发版约定：管理员把新 APK 与 version.json 放共享空间「App安装包/」。
import 'package:flutter/material.dart';
import 'package:open_filex/open_filex.dart' show ResultType;
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../data/local/prefs.dart';
import '../data/net.dart';
import '../data/update_service.dart';
import 'update_source_page.dart';
import 'update_history_page.dart';
import '../transfer/transfer_task.dart' show humanSize;
import 'theme/design_tokens.dart';
import 'widgets/brand.dart';

class AboutPage extends StatefulWidget {
  const AboutPage({super.key});

  @override
  State<AboutPage> createState() => _AboutPageState();
}

class _AboutPageState extends State<AboutPage> {
  PackageInfo? _info;
  UpdateInfo? _remote;
  /// 是否已经查过一次。区分「没查过」和「查了但没新版」——
  /// 两者的 _remote 都是 null，只靠它分会话内点完检查更新又退回按钮。
  bool _checked = false;
  String? _checkError;
  bool _checking = false;
  double? _progress; // null=未在下载
  List<UpdateHistoryItem> _history = [];
  bool _historyLoaded = false;

  // 这次结果来自哪一路（3.96 自定义更新源）。回落时必须显示 ——
  // 自定义源连不上却静默回落，用户会以为「我的源里没有新版」。
  String _source = '';
  String _sourceUrl = '';
  String _sourceError = '';

  @override
  void initState() {
    super.initState();
    // 历史安装包清运（v0.16.0）：上一轮装完/放弃的 lanshare-*.apk 静默回收
    // ignore: discarded_futures
    UpdateService.purgeInstallers();
    () async {
      final info = await PackageInfo.fromPlatform();
      if (!mounted) return;
      setState(() => _info = info);
    }();
    // 0.9.0 更新历史：拉取失败静默隐藏栏目（不影响检查更新主流程）
    () async {
      try {
        final items = await UpdateService.history();
        if (!mounted) return;
        setState(() {
          _history = items;
          _historyLoaded = true;
        });
      } catch (_) {
        if (!mounted) return;
        setState(() => _historyLoaded = true);
      }
    }();
  }

  Future<void> _check() async {
    setState(() {
      _checking = true;
      _checkError = null;
      _remote = null;
    });
    try {
      final result = await UpdateService.check();
      if (!mounted) return;
      setState(() {
        _remote = result.info;
        _checked = true;
        // 一个源都没应答 → 明确告诉用户「没查成」，不要假装「已是最新」。
        // 具体文案交给 UpdateCheckResult.failureHint：它能分清是服务端挂了
        // 还是 GitHub 挂了，因为这两个源的 VPN 诉求是相反的（一个要关一个要开）。
        _checkError = result.reachable ? null : result.failureHint;
        _checking = false;
        _source = result.source;
        _sourceUrl = result.sourceUrl;
        _sourceError = result.sourceError;
        // 自定义源带了历史就用它的（协议里和 latest 在同一个文件），
        // 否则保留原先从 GitHub Releases 拉的
        if (result.history.isNotEmpty) {
          _history = result.history;
          _historyLoaded = true;
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _checkError = '检查失败：服务器不可达或未登录';
        _checked = true;
        _checking = false;
      });
    }
  }

  Future<void> _downloadAndInstall() async {
    final remote = _remote;
    if (remote == null || !remote.available) return;
    final onPublic = isPublicAddress(await Prefs.lastAddress());
    if (!mounted) return;
    // 公网入口下载整包 APK（几十 MB）吃隧道月流量，先确认（2026-09-03）
    if ((remote.size ?? 0) > 30 * 1024 * 1024 && onPublic) {
      final ok = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
              title: const Text('当前为公网入口'),
              content: Text(
                  '更新包约 ${humanSize(remote.size ?? 0)}，将走公网隧道下载'
                  '（限速且消耗月流量）。建议连家里 Wi-Fi 后再更新。'),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(ctx, false),
                    child: const Text('取消')),
                FilledButton(
                    onPressed: () => Navigator.pop(ctx, true),
                    child: const Text('仍要下载')),
              ]));
      if (ok != true || !mounted) return;
    }
    setState(() => _progress = 0);
    try {
      // 下载前先清历史安装包：省出 85MB 级空间，也给本次下载留余量
      await UpdateService.purgeInstallers();
      // f6f2 修复：先查本地缓存——上次下载完在系统安装器里取消、或点安装
      // 后返回，包还在沙盒 updates/ 且 sha256 对得上，直接复用不重下
      // （几十 MB 白流量 + 白等待）。校验不过的残包会被清掉走正常下载。
      var path = await UpdateService.cachedInstaller(remote);
      if (!mounted) return;
      if (path != null) {
        setState(() => _progress = null);
      } else {
        path = await UpdateService.download(remote,
            onProgress: (received, total) {
          if (!mounted || total <= 0) return;
          setState(() => _progress = received / total);
        });
        if (!mounted) return;
        setState(() => _progress = null);
      }
      final res = await UpdateService.install(path);
      if (!mounted) return;
      if (res.type != ResultType.done) {
        final hint = switch (res.type) {
          ResultType.permissionDenied =>
            '没有「安装未知应用」权限。到系统设置 → 应用 → LAN Share → 安装未知应用，允许后重试。',
          ResultType.noAppToOpen => '系统里没有可用的安装程序，无法打开安装包。',
          ResultType.fileNotFound => '安装包已不存在，请重新下载。',
          _ => '无法拉起安装程序：${res.message}',
        };
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(hint)));
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _progress = null);
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('下载失败，请稍后重试')));
    }
  }

  /// 打开项目仓库。
  ///
  /// 打不开时给个提示而不是静默 —— 有些 ROM 没有默认浏览器，
  /// 用户点了没反应会以为是 App 坏了。
  Future<void> _openRepo() async {
    final uri = Uri.parse('https://github.com/AK47ZZQ/lan-share');
    try {
      final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!ok && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('打不开浏览器，地址是 github.com/AK47ZZQ/lan-share')));
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('打不开浏览器，地址是 github.com/AK47ZZQ/lan-share')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final info = _info;
    final localBuild = int.tryParse(info?.buildNumber ?? '') ?? 0;
    final remote = _remote;
    final hasUpdate = remote != null &&
        UpdateService.isNewer(remote, localBuild, localVersion: info?.version);
    final showHistory = _historyLoaded && _history.isNotEmpty;
    // 关于页只放最近 3 条，全量在「查看全部 N 个版本」那一页
    // （服务端线历史已经 85 条，App 线 40 条，全铺在设置页里没法用）
    final recent = _history.take(3).toList();

    // 为什么用 ListView.builder 而不是 ListView(children: [...])：
    // 历史改从 CHANGELOG 拉之后有近 40 个版本。塞进一个 Card 的 Column 里
    // 意味着**一次布局全部 Widget**，手机上直接卡死，表现为「页面滑不动」。
    // builder 只构建可见的那几条，滚动才按需生成。
    //
    // 索引排布：[0] 头部 → [1..n] 历史条目 → [最后] 开源许可
    return Scaffold(
      appBar: AppBar(title: const Text('关于')),
      body: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(Gap.md),
        itemCount: 1 + (showHistory ? recent.length : 0) + 1,
        itemBuilder: (context, i) {
          if (i == 0) {
            return _buildHeader(context, info, localBuild, remote, hasUpdate,
                showHistory);
          }
          if (showHistory && i <= recent.length) {
            return HistoryEntry(
                item: recent[i - 1],
                localBuild: localBuild,
                remoteBuild: remote?.build);
          }
          return _buildFooter(context, info);
        },
      ),
    );
  }

  /// 顶部：图标、版本号、仓库链接、检查更新卡片。
  Widget _buildHeader(BuildContext context, dynamic info, int localBuild,
      UpdateInfo? remote, bool hasUpdate, bool showHistory) {
    return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SizedBox(height: Gap.md),
          const Center(child: BrandCloud(size: 72, onGradient: false)),
          const SizedBox(height: Gap.sm),
          Center(
              child: Text('LAN Share',
                  style: Theme.of(context).textTheme.titleLarge)),
          const SizedBox(height: Gap.xxs),
          Center(
              child: Text('v${info?.version ?? '…'} (${info?.buildNumber ?? "…"})',
                  style: TextStyle(
                      color: Theme.of(context).colorScheme.onSurfaceVariant))),
          const SizedBox(height: Gap.xxs),
          // 这里原来显示当前连接的服务器地址（`家庭自包含网盘 · https://192.168.x.x:8765`）。
          // 那是本地内网 IP —— 关于页是最容易被截图分享的一页，把家里网段
          // 印在上面没有任何用处，只有风险。改成项目仓库，对用户也更实用。
          Center(
              child: InkWell(
            onTap: _openRepo,
            borderRadius: BorderRadius.circular(6),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(Icons.code,
                    size: 14, color: Theme.of(context).colorScheme.primary),
                const SizedBox(width: 5),
                Text('家庭自包含网盘 · github.com/AK47ZZQ/lan-share',
                    style: TextStyle(
                        fontSize: 12,
                        color: Theme.of(context).colorScheme.primary)),
              ]),
            ),
          )),
          const SizedBox(height: Gap.xl),
          Card(
              elevation: 0,
              shape: RoundedRectangleBorder(
                  borderRadius: Radii.bControl,
                  side: BorderSide(
                      color: Theme.of(context).dividerColor, width: 0.5)),
              child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Column(children: [
                    if (!_checking && !_checked && _checkError == null)
                      FilledButton.tonalIcon(
                          onPressed: _check,
                          icon: const Icon(Icons.system_update_outlined),
                          label: const Text('检查更新')),
                    if (_checking) const CircularProgressIndicator(),
                    if (_checkError != null) ...[
                      Text(_checkError!,
                          style: TextStyle(
                              fontSize: 12,
                              color: Theme.of(context).colorScheme.error)),
                      const SizedBox(height: Gap.xs),
                      TextButton(onPressed: _check, child: const Text('重试')),
                    ],
                    // 查过、没有错误、也没有更新 → 才是「已是最新」
                    if (_checked &&
                        !_checking &&
                        !hasUpdate &&
                        _checkError == null &&
                        remote != null) ...[
                      const Icon(Icons.check_circle, color: Colors.green),
                      const SizedBox(height: Gap.xxs),
                      const Text('已是最新版本'),
                      const SizedBox(height: Gap.xxs),
                      Text(
                          '服务端分发版本：v${remote.version} (${remote.build})'
                          '${remote.available ? "" : " · APK 未上传"}',
                          style: const TextStyle(fontSize: 12)),
                      TextButton(onPressed: _check, child: const Text('再查一次')),
                    ],
                    if (hasUpdate && remote != null) ...[
                      Text('发现新版本 v${remote.version} (${remote.build})',
                          style: const TextStyle(fontWeight: FontWeight.bold)),
                      if (remote.notes.isNotEmpty)
                        Padding(
                            padding: const EdgeInsets.only(top: Gap.xxs),
                            child: Text(remote.notes,
                                style: const TextStyle(fontSize: 12))),
                      // 下载走 GitHub CDN。国内直连经常几 KB/s 甚至断流，
                      // 而开 VPN 会快很多 —— 但那个建议只对下载有效，
                      // 对「连家里服务器」是反的，所以单独写在这一块里，
                      // 不跟检查更新的提示混在一起。
                      Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Row(children: [
                          Icon(Icons.info_outline,
                              size: 12,
                              color: Theme.of(context)
                                  .colorScheme
                                  .onSurfaceVariant),
                          const SizedBox(width: 4),
                          Expanded(
                            child: Text(
                                '安装包从 GitHub 下载，国内直连可能很慢，开 VPN 会快很多',
                                style: TextStyle(
                                    fontSize: 11,
                                    color: Theme.of(context)
                                        .colorScheme
                                        .onSurfaceVariant)),
                          ),
                        ]),
                      ),
                      const SizedBox(height: 10),
                      if (_progress != null) ...[
                        LinearProgressIndicator(value: _progress),
                        const SizedBox(height: Gap.xxs),
                        Text(
                            '下载中 ${(_progress! * 100).toStringAsFixed(0)}%',
                            style: const TextStyle(fontSize: 12)),
                      ] else
                        FilledButton.icon(
                            onPressed:
                                remote.available ? _downloadAndInstall : null,
                            icon: const Icon(Icons.download_outlined),
                            label: Text(remote.available
                                ? '下载并安装'
                                : '服务端 APK 未上传')),
                    ],
                    // ── 来源标注 + 更新地址入口（3.96）──
                    const Divider(height: 22),
                    if (_checked && _sourceError.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Row(children: [
                          Icon(Icons.warning_amber_rounded,
                              size: 13,
                              color: Theme.of(context).colorScheme.error),
                          const SizedBox(width: 4),
                          Expanded(
                            child: Text(
                              '自定义更新地址连不上：$_sourceError',
                              style: TextStyle(
                                  fontSize: 11,
                                  color: Theme.of(context).colorScheme.error),
                            ),
                          ),
                        ]),
                      ),
                    if (_checked && _source.isNotEmpty)
                      Align(
                        alignment: Alignment.centerLeft,
                        child: Text(
                          _source == 'custom'
                              ? '来源：自定义地址 $_sourceUrl'
                              : '来源：${_source == 'github' ? 'GitHub' : '自家服务端'}',
                          style: TextStyle(
                              fontSize: 11,
                              color: Theme.of(context).colorScheme.outline),
                        ),
                      ),
                    Align(
                      alignment: Alignment.centerRight,
                      child: TextButton.icon(
                        onPressed: () async {
                          await Navigator.of(context).push(MaterialPageRoute(
                              builder: (_) => const UpdateSourcePage()));
                          // 回来之后不自动重查 —— 用户可能只是进去看一眼，
                          // 主动点「检查更新」更符合预期
                        },
                        icon: const Icon(Icons.tune, size: 16),
                        label: const Text('更新地址'),
                      ),
                    ),
                  ]))),
          if (showHistory) ...[
            const SizedBox(height: Gap.md),
            Row(children: [
              Icon(Icons.history,
                  size: 17, color: Theme.of(context).colorScheme.primary),
              const SizedBox(width: 6),
              Text('更新历史',
                  style: Theme.of(context)
                      .textTheme
                      .titleMedium
                      ?.copyWith(fontSize: 14)),
              const Spacer(),
              // 关于页只展示最近几条，全量点进去看（历史会随发版一直变长）
              TextButton(
                style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    minimumSize: const Size(0, 28),
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap),
                onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => UpdateHistoryPage(
                    items: _history,
                    localBuild: localBuild,
                    remoteBuild: remote?.build,
                  ),
                )),
                child: Text('查看全部 ${_history.length} 个版本 ›',
                    style: TextStyle(
                        fontSize: 11.5,
                        color: Theme.of(context).colorScheme.primary)),
              ),
            ]),
            const SizedBox(height: 2),
          ],
        ]);
  }

  /// 底部：许可与致谢入口。
  ///
  /// 2026-11-07 许可从 AGPL-3.0 改成 PolyForm Noncommercial 1.0.0 ——
  /// 按钮文案也跟着从「开源许可」改成「许可与致谢」：非商业许可严格讲
  /// 不算开源（OSI 要求允许商用），再写「开源许可」是自相矛盾的。
  ///
  /// showLicensePage 列的是 **第三方依赖** 的许可（Flutter 自动收集），
  /// 不包含本项目自己的条款 —— 所以用 applicationLegalese 在页首补一句。
  Widget _buildFooter(BuildContext context, dynamic info) {
    return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SizedBox(height: Gap.md),
          TextButton.icon(
              icon: const Icon(Icons.policy_outlined, size: 18),
              label: const Text('许可与致谢'),
              onPressed: () => showLicensePage(
                  context: context,
                  applicationName: 'LAN Share',
                  applicationVersion: info?.version,
                  applicationLegalese:
                      'PolyForm Noncommercial 1.0.0\n'
                      '个人使用免费 · 商用需授权\n'
                      'polyformproject.org/licenses/noncommercial/1.0.0')),
        ]);
  }
}
