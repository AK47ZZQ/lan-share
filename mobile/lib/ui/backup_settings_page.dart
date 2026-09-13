// 自动备份设置（FR-2 / FR-9）：开关、约束、统计、账本入口。
// 开启流程：申请媒体权限（+通知）→ 落配置 → 挂 WorkManager 周期任务 → 立即跑一轮。
// v1.0：详情 / 相册选择 / 服务端相册三个页面已各自独立成文件，本文件只留设置主页
// （原先 1523 行装 4 个页面类，改一处要满文件翻），并用下方 export 门面保证
// 既有 import 方（home_page / me_page）零改动。
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../backup/backup_keys.dart';
import '../backup/backup_bridge.dart';
import '../backup/backup_config.dart';
import '../backup/backup_ledger.dart';
import '../backup/backup_round.dart';
import '../data/api/account_api.dart'; // verify-password 口令复核（x-login-flow 特化收编 data 层）
import '../data/api/purge.dart' show purgeServerFiles;
import '../state/session.dart';
import '../transfer/transfer_task.dart' show humanSize;
import 'backup_buckets_page.dart';
import 'backup_details_page.dart';
import 'server_albums_page.dart';
import 'theme/design_tokens.dart';
import 'widgets/kit.dart';

// ── 门面导出：调用方仍可从本文件引入这些页面（零改动）─────────
// 注意 export 只对外转发名字、不把名字引入本库作用域，所以本文件要
// push 这些页面时仍需上面各自的 import。
export 'backup_details_page.dart';
export 'backup_buckets_page.dart';
export 'server_albums_page.dart';

class BackupSettingsPage extends ConsumerStatefulWidget {
  const BackupSettingsPage({super.key});

  @override
  ConsumerState<BackupSettingsPage> createState() => _BackupSettingsPageState();
}

class _BackupSettingsPageState extends ConsumerState<BackupSettingsPage> {
  BackupConfig? _cfg;
  LedgerStats? _stats;
  Timer? _poll;
  bool _busy = false; // 立即备份防连点（引擎侧互斥兜底，这里管 UI 观感）
  bool _batteryIgnoring = true; // 电池优化白名单；取不到按已豁免，别空报警
  Map<String, Object?>? _diag; // 备份诊断快照（充电/网络/通知，0.7.3）
  String? _lastRound;   // 引擎最近一轮 JSON
  String? _workerState; // Worker/队列状态 JSON
  int _progDone = 0;    // 本轮进度（引擎逐张落盘，详情页/设置页共用）
  int _progTotal = 0;
  String _progCurrent = '';

  @override
  void initState() {
    super.initState();
    _reload();
    // 备份在后台 isolate 跑，账本轮询即可看到进度（2s 足够，页面级）
    _poll = Timer.periodic(const Duration(seconds: 2), (_) => _loadStats());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _reload() async {
    final cfg = await BackupConfig.load();
    final ign = await BackupBridge.batteryIgnoring();
    final diag = await BackupBridge.diag();
    final sp = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _cfg = cfg;
      _batteryIgnoring = ign;
      _diag = diag;
      _lastRound = sp.getString(BackupKeys.lastRound);
      _workerState = sp.getString(BackupKeys.workerState);
    });
    await _loadStats();
  }

  Widget _diagRow(String label, String value, {bool warn = false}) =>
      Padding(
          padding: const EdgeInsets.symmetric(vertical: 2),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(
                width: 72,
                child: Text(label,
                    style: const TextStyle(fontSize: 12, color: Colors.grey))),
            Expanded(
                child: Text(value,
                    style: TextStyle(
                        fontSize: 12,
                        color: warn ? Colors.orange : null))),
          ]));

  Future<void> _loadStats() async {
    try {
      final s = await BackupLedger.I.stats(
          excluded: _cfg?.excludedBuckets ?? const [],
          included: _cfg?.selectedBuckets);
      if (mounted) setState(() => _stats = s);
    } catch (_) {
      // 与后台引擎 isolate 并发开库时可能短暂失败（迁移竞态/库锁）。
      // 必须吞掉：异常飞进 Timer.periodic 会中断本次 setState，统计卡
      // 从此停在旧数字假死（0.7.1 修的"状态卡不更新/无法实时显示"）；
      // 下个 2s tick 自然重试。
    }
    try {
      final sp = await SharedPreferences.getInstance();
      if (mounted) {
        setState(() {
          _progDone = sp.getInt(BackupKeys.progressDone) ?? 0;
          _progTotal = sp.getInt(BackupKeys.progressTotal) ?? 0;
          _progCurrent = sp.getString(BackupKeys.progressCurrent) ?? '';
        });
      }
    } catch (_) {}
  }

  Future<void> _toggle(bool on) async {
    var cfg = _cfg!;
    if (on) {
      final granted = await BackupBridge.requestPermissions(
          includeVideos: cfg.includeVideos);
      if (!granted) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('需要"照片和视频"权限才能自动备份')));
        }
        return; // 保持关闭
      }
    }
    cfg = cfg.copyWith(enabled: on);
    await cfg.save();
    if (!mounted) return; // await 期间页面可能已被卸载（R1-P2）
    setState(() => _cfg = cfg);
    await BackupBridge.applySchedule(cfg);
    if (on) {
      // 白名单空 = 全部不备份（0.14.1 语义）：开了开关但什么都不会传，
      // 当场点名，免得用户以为坏了
      if (cfg.selectedBuckets.isEmpty && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('还没有勾选要备份的相册，暂不会备份；请到「选择备份相册」勾选'),
            duration: Duration(seconds: 4)));
      }
      await BackupBridge.runOnce();
    }
  }

  Future<void> _set(BackupConfig cfg) async {
    if (cfg.includeVideos != _cfg!.includeVideos && cfg.enabled) {
      // 视频开关变化要重新申请（READ_MEDIA_VIDEO 是独立权限项）
      final granted = await BackupBridge.requestPermissions(
          includeVideos: cfg.includeVideos);
      if (!granted) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('未授予视频权限，本次不包含视频')));
        }
        cfg = cfg.copyWith(includeVideos: false);
      }
    }
    await cfg.save();
    if (!mounted) return; // await 期间页面可能已被卸载（R1-P2）
    setState(() => _cfg = cfg);
    await BackupBridge.applySchedule(cfg);
    if (cfg.enabled) await BackupBridge.runOnce();
  }

  // ── 危险区：清空服务端备份（v0.16.1）────────────────────
  // 服务端没有"按用户清空"端点，客户端两刀解决：整目录 DELETE
  // /api/files/照片|视频/<用户名>（服务端 move_to_trash 支持目录，一次
  // 进回收站）→ 按回收站快照差集找到本次条目彻底删除。防呆四层：
  // ① 两步确认（模式/账本选择 → 输入用户名）；② 执行期间临时关停自动
  // 备份（防新调度把刚删的又传回来，完毕恢复原状）；③ 账本默认保留
  // （"释放空间"意图下已备份记录防止重传），勾选才清（"重置备份"意图）；
  // ④ 目录不存在按成功计（视频树常缺）。

  /// 第一步：选模式 + 账本选项。返回 null=取消。
  Future<(bool purge, bool clearLedger)?> _wipeOptions() async {
    var purge = true; // 默认彻底删除：清备份的主诉求就是释放空间
    var clearLedger = false;
    final ok = await showDialog<bool>(
      context: context,
      builder: (dctx) => StatefulBuilder(
        builder: (dctx, setD) {
          final cs = Theme.of(dctx).colorScheme;
          return AlertDialog(
            title: const Text('清空服务端备份'),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    '将删除备份位置里的全部内容：\n'
                    '照片/<我的用户名>/ 与 视频/<我的用户名>/\n\n'
                    '手机里的原照片不受影响。',
                    style: TextStyle(fontSize: 13)),
                  const SizedBox(height: 10),
                  RadioGroup<bool>(
                    groupValue: purge,
                    onChanged: (v) => setD(() => purge = v ?? true),
                    child: Column(children: [
                      RadioListTile<bool>(
                        value: true,
                        title: const Text('彻底删除'),
                        subtitle: const Text(
                            '立即释放服务端空间，不进回收站、无法恢复',
                            style: TextStyle(fontSize: 12)),
                        contentPadding: EdgeInsets.zero,
                        visualDensity: VisualDensity.compact),
                      RadioListTile<bool>(
                        value: false,
                        title: const Text('移入回收站'),
                        subtitle: const Text(
                            '不立即释放空间，30 天内可到网页端恢复',
                            style: TextStyle(fontSize: 12)),
                        contentPadding: EdgeInsets.zero,
                        visualDensity: VisualDensity.compact),
                    ]),
                  ),
                  CheckboxListTile(
                    value: clearLedger,
                    onChanged: (v) => setD(() => clearLedger = v ?? false),
                    title: const Text('同时清空备份账本'),
                    subtitle: const Text(
                        '默认保留（删除后不会自动重传）；勾选后下次备份会把'
                        '手机里的照片全部重新上传',
                        style: TextStyle(fontSize: 12)),
                    contentPadding: EdgeInsets.zero,
                    visualDensity: VisualDensity.compact),
                  Text(
                      purge ? '确定要彻底删除吗？此操作不可恢复。' : '文件将进入服务端回收站。',
                      style: TextStyle(
                          fontSize: 12.5,
                          color: purge ? cs.error : null)),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dctx, false),
                child: const Text('取消')),
              FilledButton(
                style: FilledButton.styleFrom(
                    backgroundColor: purge ? cs.error : null),
                onPressed: () => Navigator.pop(dctx, true),
                child: const Text('下一步')),
            ],
          );
        },
      ),
    );
    if (ok != true) return null;
    return (purge, clearLedger);
  }

  /// 第二步：输入用户名 + 登录密码（v0.17：在 type-to-confirm 之上加本人口令
  /// 复核，服务端 /api/auth/verify-password 校验——捡到解锁手机的人也删不掉）。
  Future<bool> _wipeConfirm(String username) async {
    final userCtrl = TextEditingController();
    final pwCtrl = TextEditingController();
    final userFocus = FocusNode();
    final pwFocus = FocusNode();
    final ok = await showDialog<bool>(
      context: context,
      barrierDismissible: false, // 复核进行中不可误触关闭（review P2-5）
      builder: (dctx) => StatefulBuilder(
        builder: (dctx, setD) {
          var err = '';
          var checking = false;

          Future<void> submit() async {
            if (checking) return;
            if (userCtrl.text.trim() != username) {
              setD(() => err = '用户名不一致');
              return;
            }
            if (pwCtrl.text.isEmpty) {
              setD(() => err = '请输入密码');
              return;
            }
            setD(() { checking = true; err = ''; });
            try {
              final res = await AccountApi.verifyPassword(pwCtrl.text);
              if (res.ok) {
                if (dctx.mounted) Navigator.pop(dctx, true);
                return;
              }
              // busy 期间对话框可被点遮罩关闭：setD 前必须验活（review P2-5）
              if (dctx.mounted) {
                setD(() {
                  checking = false;
                  err = res.status == 429
                      ? (res.detail ?? '尝试过于频繁，请稍后再试')
                      : (res.status == 401
                          ? '密码错误'
                          : '服务器不可达，请稍后重试');
                });
              }
            } catch (_) {
              if (dctx.mounted) {
                setD(() { checking = false; err = '服务器不可达，请稍后重试'; });
              }
            }
          }

          return AlertDialog(
            title: const Text('确认执行'),
            content: SingleChildScrollView(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                TextField(
                  controller: userCtrl,
                  focusNode: userFocus,
                  autocorrect: false,
                  autofillHints: const [AutofillHints.username],
                  textInputAction: TextInputAction.next,
                  onSubmitted: (_) => pwFocus.requestFocus(),
                  decoration: const InputDecoration(
                    labelText: '用户名',
                    hintText: '输入与用户名完全一致')),
                const SizedBox(height: Gap.sm),
                TextField(
                  controller: pwCtrl,
                  focusNode: pwFocus,
                  obscureText: true,
                  autocorrect: false,
                  autofillHints: const [AutofillHints.password],
                  onSubmitted: (_) => submit(),
                  decoration: const InputDecoration(
                    labelText: '登录密码',
                    hintText: '校验本人口令后才会执行')),
                if (err.isNotEmpty) ...[
                  const SizedBox(height: Gap.xs),
                  Text(err,
                      style: TextStyle(
                          color: Theme.of(dctx).colorScheme.error,
                          fontSize: 12.5)),
                ],
              ]),
            ),
            actions: [
              TextButton(
                onPressed: checking ? null : () => Navigator.pop(dctx, false),
                child: const Text('取消')),
              ListenableBuilder(
                listenable: Listenable.merge([userCtrl, pwCtrl]),
                builder: (dctx, _) => FilledButton(
                  style: FilledButton.styleFrom(
                      backgroundColor: Theme.of(dctx).colorScheme.error),
                  onPressed: checking ||
                          userCtrl.text.trim() == username &&
                              pwCtrl.text.isNotEmpty
                      ? submit
                      : null,
                  child: checking
                      ? const SizedBox(
                          width: 16, height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('确认清空'),
                ),
              ),
            ],
          );
        },
      ),
    );
    // 不能在这里 ctrl.dispose()：对话框退场动画期间 TextField 与
    // ListenableBuilder 仍在使用控制器（E2E 红屏复现），交给 GC 即可。
    return ok == true;
  }

  Future<void> _wipeServerBackup(BackupConfig cfg) async {
    final username = ref.read(sessionProvider).username ?? '';
    if (username.isEmpty) {
      showToast('未取到当前用户名，无法执行');
      return;
    }
    final opts = await _wipeOptions();
    if (opts == null || !mounted) return;
    final (purge, clearLedger) = opts;
    if (!await _wipeConfirm(username) || !mounted) return;
    // 身份复核（红队 P2）：确认框打开期间可能顶号/换号，执行身份必须
    // 与确认时一致；用户名是拼服务端路径的原料，字符集与备份引擎同口径
    if ((ref.read(sessionProvider).username ?? '') != username ||
        username.contains('/') ||
        username.contains('\\') ||
        username == '..') {
      showToast('登录身份已变化，请重新进入本页操作');
      return;
    }
    // 在途备份轮次拦截（红队 P2）：已开始的那一个文件拦不住，会横跨整个
    // 清空窗口（finish 先到=随树误删，后到=清空后长出残留）。轮次进行中
    // 先拒了，等本轮结束再来；引擎被杀残留的 total>0 由"重启 App"兜底
    final sp = await SharedPreferences.getInstance();
    final inFlight = (sp.getInt(BackupKeys.progressTotal) ?? 0) -
        (sp.getInt(BackupKeys.progressDone) ?? 0);
    if (inFlight > 0) {
      if (!mounted) return;
      showToast('正在备份中（约 $inFlight 项），请等本轮完成后再清空；'
          '若长时间不结束请重启 App 后重试');
      return;
    }
    if (!mounted) return;
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    // 执行期间关停自动备份（防新调度把刚删的又传回来），完毕恢复。
    // 落 prefs 标记：若恢复前进程被杀，下次登录 _recoverWipePause 会把
    // 备份开关拉回来——否则自动备份会静默停摆（审查 P1）。标记先落盘
    // 再关开关（两步之间被杀时方向才是失败安全的，红队 P3）
    final wasEnabled = cfg.enabled;
    try {
      if (wasEnabled) {
        final off = cfg.copyWith(enabled: false);
        await sp.setString(BackupKeys.wipePaused, '1');
        await off.save();
        await BackupBridge.applySchedule(off);
      }
      final res = await purgeServerFiles(
          ['照片/$username', '视频/$username'],
          trashOnly: !purge,
          absentOk: true);
      var ledgerNote = '';
      if (clearLedger) {
        try {
          await BackupLedger.I.clearAll();
          ledgerNote = '；备份账本已清空（下次备份将全部重传）';
        } catch (_) {
          // 服务端已删完才走到这：不能谎报整体失败，账本可稍后手动处理
          ledgerNote = '；本地备份账本清空失败，可稍后在账本页处理';
        }
      }
      final absentNote =
          res.absent > 0 ? '，${res.absent} 项目标本就不存在' : '';
      final body = purge
          ? (res.failed == 0 && res.staged == 0
              ? '已彻底清空（服务端空间已释放）$absentNote'
              : res.report)
          : (res.failed == 0
              ? '已移入回收站（30 天内可恢复）$absentNote'
              : res.report);
      messenger.showSnackBar(SnackBar(
          content: Text('$body$ledgerNote'),
          duration: const Duration(seconds: 4)));
    } catch (_) {
      messenger.showSnackBar(const SnackBar(
          content: Text('清空失败：服务器不可达或权限不足'),
          duration: Duration(seconds: 3)));
    } finally {
      if (wasEnabled) {
        try {
          await cfg.save();
          await sp.remove(BackupKeys.wipePaused);
          await BackupBridge.applySchedule(cfg);
        } catch (_) {
          // 恢复失败不静默：标记还留着，下次登录 _recoverWipePause 会兜底
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                content: Text('自动备份未能恢复开启，请到本页手动打开开关'),
                duration: Duration(seconds: 4)));
          }
        }
      }
      if (mounted) {
        setState(() => _busy = false);
        await _reload();
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final cfg = _cfg;
    final stats = _stats;
    final username = ref.watch(sessionProvider).username ?? '';
    final cs = Theme.of(context).colorScheme;
    // 清空服务端备份执行中：全部会改配置/触发备份的控件一并锁住，
    // 防中途 _set 抢跑触发重传并被 finally 的旧 cfg 回滚（审查 P2）
    final locked = _busy;
    return Scaffold(
      appBar: AppBar(title: const Text('自动备份')),
      body: cfg == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(padding: const EdgeInsets.only(bottom: Gap.xl), children: [
              SwitchListTile(
                secondary: Icon(cfg.enabled
                    ? Icons.backup
                    : Icons.backup_outlined),
                title: const Text('自动备份手机相册'),
                subtitle: const Text('连 Wi-Fi 后自动把新照片存进网盘'),
                value: cfg.enabled,
                onChanged: locked ? null : (v) => _toggle(v),
              ),
              // 用户暂停（v0.17）：开关照开、调度照在，引擎逐张前让路——
              // 与"关闭备份"的区别：暂停保留全部配置，一键恢复继续跑
              if (cfg.enabled)
                SwitchListTile(
                  secondary: Icon(cfg.paused
                      ? Icons.pause_circle
                      : Icons.pause_circle_outline),
                  title: const Text('暂停备份'),
                  subtitle: Text(cfg.paused
                      ? '已暂停：满足约束后也不会自动备份，关闭此开关恢复'
                      : '临时停一段时间（出差/省流量），配置保持不变'),
                  value: cfg.paused,
                  onChanged: locked
                      ? null
                      : (v) async {
                          final c = cfg.copyWith(paused: v);
                          await c.save();
                          if (!mounted) return;
                          setState(() => _cfg = c);
                          await BackupBridge.applySchedule(c);
                        },
                ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: Gap.md),
                child: Card(
                  elevation: 0,
                  color: cs.surfaceContainerHighest,
                  child: Padding(
                    padding: const EdgeInsets.all(14),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                      // 0.7.1 起照片/视频分开计数；待备/失败不包含已排除相册
                      // 里的项（它们永远不会被备份，单列一行免得数字对不上）
                      Text('已备份 照片 ${stats?.donePhotos ?? 0} · 视频 ${stats?.doneVideos ?? 0}'
                          '（${humanSize(stats?.doneBytes ?? 0)}）',
                          style: const TextStyle(fontWeight: FontWeight.bold)),
                      const SizedBox(height: Gap.xxs),
                      Text('待备份 照片 ${stats?.pendingPhotos ?? 0} · 视频 ${stats?.pendingVideos ?? 0}'
                          ' · 失败 ${stats?.error ?? 0} 项',
                          style: const TextStyle(fontSize: 13)),
                      const SizedBox(height: 2),
                      // 口径说明（20260913215824-61fa）：用户困惑"备份页已备
                      // 份数 ≠ 主页照片墙数"——两者本就不同集合，说清楚
                      Text('已备份是本机备份账本；照片墙统计服务端全部可见照片（含网页上传、家人照片），两边数字本就不同。',
                          style: TextStyle(fontSize: 11.5, color: Colors.grey)),
                      // server_missing 单列（20260913215824-61fa）：服务端相册
                      // 目录被删的项此前落进"待备份"永不消化。注意本页数字
                      // 是"本机备份账本"口径，与照片墙（服务端全部可见媒体）
                      // 本就不同：这里含你在别处删过的、照片墙还统计网页/
                      // 家人上传的，两边不该相等。
                      if ((stats?.missing ?? 0) > 0) ...[
                        const SizedBox(height: 2),
                        Text(
                            '服务端目录已删 ${stats!.missingPhotos} 照片 / ${stats.missingVideos} 视频'
                            '（去备份详情选重新备份或移除）',
                            style: const TextStyle(
                                fontSize: 12, color: Colors.orange)),
                      ],
                      if ((stats?.excludedPending ?? 0) > 0) ...[
                        const SizedBox(height: 2),
                        Text(
                            '不在备份范围内的相册还有 ${stats!.excludedPending} 项不参与备份',
                            style: const TextStyle(
                                fontSize: 12,
                                color: Colors.grey)),
                      ],
                      const SizedBox(height: Gap.xxs),
                      // 0.7.0 起按手机相册分子目录：照片/<u>/<相册>/、视频/<u>/<相册>/
                      Text('备份位置：照片、视频/$username/<手机相册名>/',
                          style: const TextStyle(fontSize: 12)),
                      if (stats?.lastUploadedAt != null) ...[
                        const SizedBox(height: 2),
                        Text(
                            '最近备份：${DateTime.fromMillisecondsSinceEpoch(
                                stats!.lastUploadedAt! * 1000)
                                .toString().split('.').first}',
                            style: const TextStyle(fontSize: 12)),
                      ],
                    ]),
                  ),
                ),
              ),
              // 本轮进度卡（v0.17 备份详情化）：引擎逐张落盘，2s 轮询刷新
              if (_progTotal > 0 && !_busy)
                Padding(
                  padding: const EdgeInsets.fromLTRB(Gap.md, Gap.sm, Gap.md, 0),
                  child: Card(
                    elevation: 0,
                    color: cs.surfaceContainerHighest,
                    child: Padding(
                      padding: const EdgeInsets.all(14),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                        Row(children: [
                          const SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(strokeWidth: 2)),
                          const SizedBox(width: 10),
                          Expanded(
                              child: Text('备份中 $_progDone/$_progTotal 项'
                                  '（${(_progDone * 100 ~/ _progTotal)}%）',
                                  style: const TextStyle(
                                      fontWeight: FontWeight.bold))),
                        ]),
                        const SizedBox(height: 10),
                        ClipRRect(
                          borderRadius: BorderRadius.circular(6),
                          child: TweenAnimationBuilder<double>(
                            tween: Tween(begin: 0,
                                end: _progDone / _progTotal),
                            duration: const Duration(milliseconds: 400),
                            builder: (_, v, _) => LinearProgressIndicator(
                                value: v.clamp(0.0, 1.0), minHeight: 8),
                          ),
                        ),
                        if (_progCurrent.isNotEmpty) ...[
                          const SizedBox(height: Gap.xs),
                          Text('正在上传：$_progCurrent',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                  fontSize: 12, color: Colors.grey)),
                        ],
                      ]),
                    ),
                  ),
                ),
              const Divider(height: 24),
              // 备份诊断卡（0.7.3）：环境快照 + 最近调度/轮次状态。
              // "点了立即备份没反应"不再靠猜——卡在哪一环这里一眼可见
              if (cfg.enabled) ...[
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: Gap.md),
                  child: Card(
                    elevation: 0,
                    color: cs.surfaceContainerHighest,
                    child: Padding(
                      padding: const EdgeInsets.all(Gap.sm),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('备份诊断',
                                style: const TextStyle(
                                    fontWeight: FontWeight.bold)),
                            const SizedBox(height: 6),
                            _diagRow('网络', _diag == null
                                ? '未知'
                                : ((_diag!['metered'] as bool?) == true
                                    ? '流量/计费网络'
                                    : 'Wi-Fi/不计费')),
                            _diagRow('电源', _diag == null
                                ? '未知'
                                : ((_diag!['charging'] as bool?) == true
                                    ? '充电中'
                                    : '未充电')),
                            // 通知被关 = 状态栏什么都看不到（API 33+ 权限独立）
                            InkWell(
                                onTap: ((_diag?['notifications'] as bool?) ==
                                        false)
                                    ? () => BackupBridge
                                        .openNotificationSettings()
                                    : null,
                                child: _diagRow(
                                    '通知',
                                    ((_diag?['notifications'] as bool?) ??
                                            false)
                                        ? '已开启'
                                        : '已关闭，点此去开启')),
                            if (workerView(_workerState) case final w?)
                              _diagRow('最近调度', w.text, warn: w.warn),
                            if (roundView(_lastRound) case final r?)
                              _diagRow('最近一轮', r.text, warn: r.warn),
                          ]),
                    ),
                  ),
                ),
                const SizedBox(height: Gap.xs),
              ],
              // 电池优化白名单引导（0.6.2）：小米/华为等激进省电把 WorkManager
              // 周期任务压到几小时一次甚至不跑，是"备份不自动跑"投诉的常见根因
              if (cfg.enabled && !_batteryIgnoring)
                ListTile(
                    leading: const Icon(Icons.battery_alert,
                        color: Colors.orange),
                    title: const Text('允许后台备份不受省电限制'),
                    subtitle: const Text('部分手机会拦截后台备份，点此申请电池优化豁免',
                        style: TextStyle(fontSize: 12)),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () async {
                      await BackupBridge.batteryRequest();
                      await _reload();
                    }),
              SwitchListTile(
                  title: const Text('仅 Wi-Fi'),
                  subtitle: const Text('流量下不备份（推荐）'),
                  value: cfg.wifiOnly,
                  onChanged: !locked && cfg.enabled
                      ? (v) => _set(cfg.copyWith(wifiOnly: v))
                      : null),
              SwitchListTile(
                  title: const Text('仅充电时'),
                  value: cfg.chargingOnly,
                  onChanged: !locked && cfg.enabled
                      ? (v) => _set(cfg.copyWith(chargingOnly: v))
                      : null),
              SwitchListTile(
                  title: const Text('包含视频'),
                  subtitle: const Text('默认关闭：视频大、耗流量'),
                  value: cfg.includeVideos,
                  onChanged: locked ? null : (v) => _set(cfg.copyWith(includeVideos: v))),
              ListTile(
                  leading: const Icon(Icons.photo_library_outlined),
                  title: const Text('选择备份相册'),
                  subtitle: Text(cfg.selectedBuckets.isEmpty
                      ? (cfg.excludedBuckets.isEmpty
                          ? '尚未勾选任何相册，暂不备份（点此选择）'
                          : '尚未勾选任何相册，暂不备份（旧排除 '
                              '${cfg.excludedBuckets.length} 个将随保存清除）')
                      : '只备份勾选的 ${cfg.selectedBuckets.length} 个'
                          '：${cfg.selectedBuckets.take(3).join("、")}'
                          '${cfg.selectedBuckets.length > 3 ? "…" : ""}'),
                  onTap: locked
                      ? null
                      : () async {
                    await Navigator.push(context, MaterialPageRoute(
                        builder: (_) => BackupBucketsPage(cfg: cfg)));
                    await _reload();
                  }),
              SwitchListTile(
                  title: const Text('备份完成提醒'),
                  value: cfg.notifyDone,
                  onChanged: locked ? null : (v) => _set(cfg.copyWith(notifyDone: v))),
              SwitchListTile(
                  title: const Text('公网地址也备份'),
                  subtitle: const Text('默认关闭：走公网流量，注意资费'),
                  value: cfg.allowPublicServer,
                  onChanged: locked ? null : (v) => _set(cfg.copyWith(allowPublicServer: v))),
              const Divider(height: 24),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: Gap.md),
                child: Row(children: [
                  Expanded(
                    child: OutlinedButton.icon(
                        onPressed: cfg.enabled && !cfg.paused && !_busy
                            ? () async {
                                setState(() => _busy = true);
                                try {
                                  final messenger = ScaffoldMessenger.of(context);
                                  await BackupBridge.runOnce();
                                  messenger.showSnackBar(const SnackBar(
                                      content: Text('已开始备份（见通知栏进度）')));
                                } finally {
                                  if (mounted) setState(() => _busy = false);
                                }
                              }
                            : null,
                        icon: _busy
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.sync),
                        label: const Text('立即备份')),
                  ),
                  const SizedBox(width: Gap.sm),
                  Expanded(
                    child: FilledButton.tonalIcon(
                        onPressed: () => Navigator.push(context,
                            MaterialPageRoute(
                                builder: (_) => const BackupDetailsPage())),
                        icon: const Icon(Icons.fact_check_outlined),
                        label: const Text('备份详情')),
                  ),
                ]),
              ),
              const Divider(height: 24),
              // 服务端相册管理（9.6-P1-10 场景2）：在 App 端取消相册备份后，
              // 已上传副本可在此逐相册删除（进服务端回收站，30 天可恢复）
              if (ref.watch(sessionProvider).role != 'guest')
                Padding(
                padding: const EdgeInsets.fromLTRB(Gap.md, 0, Gap.md, 0),
                child: Card(
                  elevation: 0,
                  margin: EdgeInsets.zero,
                  child: ListTile(
                    leading: const Icon(Icons.folder_special_outlined),
                    title: const Text('管理服务端相册',
                        style: TextStyle(fontWeight: FontWeight.w600)),
                    subtitle: const Text(
                        '查看/删除已备份的照片、视频相册副本，'
                        '删除后同步清理备份记录',
                        style: TextStyle(fontSize: 12)),
                    onTap: () async {
                      await Navigator.push(context,
                          MaterialPageRoute(
                              builder: (_) => const ServerAlbumsPage()));
                      if (mounted) setState(() {}); // 返回后统计卡刷新
                    },
                  ),
                ),
              ),
              const SizedBox(height: Gap.sm),
              // 危险区（v0.16.1）：清空服务端备份（释放空间/重置备份）。
              // guest 只读：me 页宫格挡了本页，这里再挡卡片本身（红队 P2）
              if (ref.watch(sessionProvider).role != 'guest')
                Padding(
                padding: const EdgeInsets.fromLTRB(Gap.md, 0, Gap.md, 0),
                child: Card(
                  elevation: 0,
                  margin: EdgeInsets.zero,
                  shape: RoundedRectangleBorder(
                      borderRadius: Radii.bControl,
                      side: BorderSide(
                          color: cs.error.withValues(alpha: 0.45),
                          width: 1)),
                  child: ListTile(
                    leading: Icon(Icons.delete_forever, color: cs.error),
                    title: Text('清空服务端备份',
                        style: TextStyle(
                            color: cs.error,
                            fontWeight: FontWeight.w600)),
                    subtitle: const Text(
                        '删除 照片、视频/<我的用户名>/ 下的全部内容，'
                        '手机原照片不受影响；需输入用户名和登录密码确认，'
                        '可选是否清空备份账本',
                        style: TextStyle(fontSize: 12)),
                    onTap: _busy ? null : () => _wipeServerBackup(cfg),
                  ),
                ),
              ),
              const Padding(
                padding: EdgeInsets.all(Gap.md),
                child: Text(
                    '· 单向备份：相册里删除的照片不会从网盘删除\n'
                    '· 已备份过的照片自动跳过（换手机重装后重新登录也只传新照片）\n'
                    '· 系统可能在省电时推迟备份，属正常行为',
                    style: TextStyle(fontSize: 12, color: Colors.grey)),
              ),
            ]),
    );
  }
}
