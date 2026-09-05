// 局域网/公网自动切换（v0.15）：家庭网盘的"在家直连、出门走隧道"自动化。
//
// 策略（防抖动优先）：
//   · 当前公网 → 每轮探测已知局域网地址，通则切回（LAN 永远优先，一次
//     成功即切，因为直连更快更省流量）；
//   · 当前局域网 → 探测当前地址，**连续 2 次失败**才切公网（单次抖动
//     不切换）；
//   · 任意切换后 60s 冷却，冷却期内只探测不切换；
//   · 信任零弹框：后台无 BuildContext。候选地址只有在「TLS 指纹与已信任
//     指纹同源」或「探测直通（已信任/明文 http）」时才允许切换，陌生
//     指纹一律放弃，等用户手动选一次完成 TOFU。
//
// 公网入口来源：探测成功时从 /api/app/entries 拿到并落 prefs 缓存——
// 局域网挂掉时服务端问不到，只能靠这份缓存兜底。缓存时顺带把公网端点
// 的证书指纹预信任入库（指纹来自已确认的本系统服务器），首次切公网
// TLS 握手直通，不再有两拍延迟/静默失败。
// 切换与传输：切换前全量暂停在途任务（分片边界优雅停），切回局域网只
// 恢复这批自动暂停的——原"有活动传输就不切"在自动备份持续入队时构成
// 死锁（pending 也算 active），是用户实测"切公网失败"的根因（v0.15.2）。
// 触发时机：20s 周期 + 启动 3s 首探 + 回前台 1s 即探 + LAN 首败 5s 快速
// 复核；探测超时 2s、候选并行探测。
// 生命周期：HomePage（IndexedStack 常驻）initState start、dispose stop；
// 退后台不停表但 _tick 里按 lifecycleState 跳过。
import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/api_client.dart';
import '../data/api/trust.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart' show TransferStatus;
import '../data/entries_service.dart';
import '../data/local/prefs.dart';
import '../data/net.dart';

class AutoSwitch {
  AutoSwitch._();
  static final AutoSwitch I = AutoSwitch._();

  static const _kPubCache = 'autoswitch_public_addrs';
  static const _kFpCache = 'autoswitch_cert_fp';
  static const _kAutoPaused = 'autoswitch_autopaused';
  static const _probeInterval = Duration(seconds: 20);
  static const _cooldown = Duration(seconds: 60);

  Timer? _timer;
  Timer? _fastRetry; // LAN 首次失败后的 5s 快速复核（v0.15.2）
  AppLifecycleListener? _lifecycle; // 回前台即探（v0.15.2）
  int _gen = 0; // start/stop 竞态代数：stop 后完成的 start 不得建表
  bool _probing = false;
  int _lanFails = 0; // 当前 LAN 连续失败计数（2 次才切公网）
  Set<String> _autoPausedIds = {}; // 切换时自动暂停的任务（切回 LAN 只恢复这批）
  DateTime _lastSwitchAt = DateTime.fromMillisecondsSinceEpoch(0);
  DateTime _manualStickyUntil = DateTime.fromMillisecondsSinceEpoch(0);
  static bool _fallbackUsed = false; // 登录页公网兜底换上的地址：下一次 noteManualPick 跳过

  /// 最近一次自动切换描述（UI 徽标/调试用）；空=本次会话尚未切过
  final ValueNotifier<String> lastEvent = ValueNotifier('');

  bool get _enabled {
    // 同步读不到 prefs：enabled 与否在 start/ tick 内异步判，这里不阻塞
    return _enabledCache;
  }

  bool _enabledCache = true;

  /// 进入登录态后调用（幂等）；登出调 stop()
  Future<void> start() async {
    final gen = ++_gen;
    final sp = await SharedPreferences.getInstance();
    _enabledCache = sp.getBool('autoswitch_enabled') ?? true;
    if (!_enabledCache || gen != _gen) {
      return; // await 期间发生过 stop（秒锁/秒退）：不再建表
    }
    _timer ??= Timer.periodic(_probeInterval, (_) => _tick());
    // 红队 P2-2：切公网后锁屏/杀进程，任务 userPaused 已落盘而快照只在
    // 内存会丢——快照持久化，start（含解锁重进）时恢复
    final saved = sp.getStringList(_kAutoPaused);
    _autoPausedIds = saved?.toSet() ?? {};
    _lifecycle ??= AppLifecycleListener(onResume: () {
      // 回前台即探（v0.15.2）：出门/回家后打开 App 不再干等下个 20s 周期。
      // 缓 1s：让路给首帧业务请求，也规避指纹弹窗把生命周期抖成 resumed。
      final gen = _gen;
      Future.delayed(const Duration(seconds: 1), () {
        if (gen == _gen) _tick();
      });
    });
    // 启动后 3s 先探一轮（刚进 App 业务请求正忙，稍让路）
    Timer(const Duration(seconds: 3), () {
      if (gen == _gen) _tick();
    });
  }

  void stop() {
    _gen++;
    _timer?.cancel();
    _timer = null;
    _fastRetry?.cancel();
    _fastRetry = null;
    _lifecycle?.dispose();
    _lifecycle = null;
    _lanFails = 0;
    _autoPausedIds = {};
  }

  /// 登录页公网兜底（第二轮 review P2）：未登录时本服务不跑，"出门冷启动
  /// App"场景由这里补——局域网地址连不上时用缓存的公网候选做一轮探测，
  /// 返回第一个确认是本系统且已可用的地址（静默信任门槛与运行期一致：
  /// 只信已入库的同源指纹，绝不静默 TOFU 陌生证书）。无缓存/全不可达返回 null。
  static Future<String?> cachedPublicFallback({String? exclude}) async {
    final sp = await SharedPreferences.getInstance();
    final pubs = sp.getStringList(_kPubCache) ?? const [];
    for (final raw in pubs) {
      final a = normUrl(raw);
      // https-only（红队 P1-2）：http 候选的自报指纹可被同网段 MITM
      // 伪造成已信任值，兜底会把登录凭据发过去
      if (a.isEmpty || !a.startsWith('https://') || a == exclude) continue;
      final entries = await I._probeAddress(a, trustIfKnown: true);
      if (entries == null) continue;
      // 红队 P1-2：登录页兜底会把用户输入改写成候选地址——必须确认候选
      // 与本机信任的证书同源，防跨部署污染把新账号的凭据发给旧服务器
      if (!TrustStore.I.knowsFingerprint(entries.certSha256) &&
          normFp(entries.certSha256) != await _sentinelFp()) {
        continue;
      }
      _fallbackUsed = true;
      return a;
    }
    return null;
  }

  static String normFp(String fp) => fp.replaceAll(':', '').toUpperCase();

  /// 地址归一：去首尾空白与尾部斜杠。此前两处内联正则写成 r'/+\$'——
  /// \$ 是字面美元符不是结尾锚点，归一从未生效过（v0.15.2 修复）；
  /// 带斜杠的候选会让 '$url/api/...' 直拼成 //api/... 恒 404，成为死项。
  static String normUrl(String raw) =>
      raw.trim().replaceAll(RegExp(r'/+$'), '');

  static Future<String> _sentinelFp() async {
    final sp = await SharedPreferences.getInstance();
    return normFp(sp.getString(_kFpCache) ?? '');
  }

  /// @visibleForTesting：单测探针（与 _sentinelFp 同实现）
  static Future<String> sentinelFpForTest() => _sentinelFp();

  /// 登出（换账号）时调用：停表并清上一会话的切换痕迹。应用锁落锁会
  /// unmount HomePage 触发 stop()，但那不算换会话——痕迹要留到解锁。
  Future<void> reset() async {
    stop();
    lastEvent.value = '';
    _autoPausedIds = {};
    // 红队 P1-2：换账号/换部署时公网候选缓存必须清空——否则新账号的
    // 局域网掉线时会被切到上一台服务器的公网入口（Token 发给错误服务端）
    try {
      final sp = await SharedPreferences.getInstance();
      await sp.remove(_kPubCache);
      await sp.remove(_kFpCache);
      await sp.remove(_kAutoPaused);
    } catch (_) {}
  }

  /// 用户手动选择入口（我的页/登录页切换后调用）：10 分钟内自动切换
  /// 不干预
  void noteManualPick() {
    // 登录兜底自动换上的地址不算用户手动选择（红队 P3-8）：回家后应能
    // 立即自动切回直连，不该被 10 分钟粘性压住
    if (_fallbackUsed) {
      _fallbackUsed = false;
      return;
    }
    _manualStickyUntil = DateTime.now().add(const Duration(minutes: 10));
    _lanFails = 0;
  }

  /// 用户开关切换（我的页）：开 → 立即 start；关 → stop
  Future<void> setEnabled(bool on) async {
    final sp = await SharedPreferences.getInstance();
    await sp.setBool('autoswitch_enabled', on);
    _enabledCache = on;
    if (on) {
      await start();
    } else {
      stop();
    }
  }

  void _tick() {
    if (_probing || !_enabled) return;
    if (SchedulerBinding.instance.lifecycleState != AppLifecycleState.resumed) {
      return; // 退后台：省电省流量（observer 只是额外保险，这里双保险）
    }
    _probing = true;
    final gen = _gen;
    () async {
      try {
        await _probe(gen);
      } catch (_) {
        // 探测自身异常：静默，下个周期再来
      } finally {
        _probing = false;
      }
    }();
  }

  Future<void> _probe(int gen) async {
    // 审查 P2-4：登出/落锁（stop/reset 走 _gen++）后，在途探测不得再
    // 改地址或回写公网缓存——否则 reset 清完缓存又把旧部署候选写回去
    bool stale() => gen != _gen;
    final current = Prefs.addressNotifier.value;
    if (current.isEmpty) return;
    // 手动粘性（审查 P2-6）：用户刚手动选过入口的 10 分钟内不自动干预，
    // 避免"选完立刻被拉回"的打架；域名形态的局域网地址（nas:8765 被判
    // 公网）也因此不会被数字 IP 候选顶掉
    if (DateTime.now().isBefore(_manualStickyUntil)) return;
    final currentPublic = isPublicAddress(current);

    if (currentPublic) {
      // 在公网：探测局域网候选，通则切回（冷却在切换点统一裁决）
      if (DateTime.now().difference(_lastSwitchAt) < _cooldown) return;
      final lan = await _reachable(await _lanCandidates());
      if (lan == null || stale()) return;
      await _doSwitch(lan, '已自动切回局域网（${_hostOf(lan)}）',
          currentAtProbeStart: current);
      return;
    }

    // 在局域网：先探当前地址（探测返回的 entries 顺手落缓存）
    final entries = await _probeAddress(current, trustIfKnown: true);
    if (entries != null) {
      _lanFails = 0;
      _fastRetry?.cancel();
      _fastRetry = null;
      if (stale()) return;
      await _cachePublicEntries(entries, overTls: current.startsWith('https://'));
      // 回家后的恢复兜底：_doSwitch 里没恢复成的（退避重试中）逐轮补
      await _resumeAutoPaused();
      return;
    }
    if (stale()) return;
    _lanFails++;
    if (_lanFails < 2) {
      // 首次失败 5s 后快速复核（v0.15.2）：不必干等下个 20s 周期，
      // 离家后 ~10s 内即可完成"两次确认+切换"
      _fastRetry ??= Timer(const Duration(seconds: 5), () {
        _fastRetry = null;
        _tick();
      });
      return;
    }
    final sp = await SharedPreferences.getInstance();
    // 红队 P1-2：公网候选一律只接受 https——缓存条目源自服务端响应，
    // 当前地址若为 http（明文 LAN）时响应可被同网段 MITM 整体伪造，
    // 自报指纹无法做密码学绑定；https 候选由 TrustStore 握手裁决兜底
    final pubs = (sp.getStringList(_kPubCache) ?? const [])
        .map(normUrl)
        .where((a) => a.startsWith('https://') && a != current)
        .toList();
    if (pubs.isEmpty) {
      // 无缓存多为"装好后没在局域网里开满一个探测周期就出门"：给出可见
      // 诊断而非静默失败（此前用户实测"切公网失败"无从排查）
      lastEvent.value =
          '局域网不可达，但暂无公网入口缓存，无法自动切换 · ${_hhmm(DateTime.now())}';
      return;
    }
    final reachable = await _reachable(pubs);
    if (reachable == null || stale()) return;
    await _doSwitch(reachable, '局域网不可达，已自动切换公网（${_hostOf(reachable)}）',
        currentAtProbeStart: current);
  }

  /// 并行探测候选（v0.15.2：串行最坏 2s×n 全在超时上），返回按原顺序的
  /// 第一个可用项——保持"主隧道在前"的既有优先级语义。
  Future<String?> _reachable(List<String> candidates) async {
    final results = await Future.wait(candidates.map((raw) async {
      final a = normUrl(raw);
      if (a.isEmpty || a == Prefs.addressNotifier.value) return null;
      return await _probeAddress(a, trustIfKnown: true) != null ? a : null;
    }));
    for (final r in results) {
      if (r != null) return r;
    }
    return null;
  }

  /// 地址探测：GET /api/app/entries 并解析响应体。返回非 null 才算
  /// "确认是本系统"——审查 P1：只看"有 HTTP 响应"会把局域网里同端口的
  /// 路由器管理页/其他设备当服务器，切换后业务全挂、往返横跳。判定对齐
  /// EntriesService.fetch：状态 200 + JSON Map + cert_sha256 键存在。
  /// TLS 握手被拒时查 pending 指纹：与已信任指纹同源 → 静默信任，本轮
  /// 返回 null 下轮直通；陌生指纹 → null（绝不后台弹框）。
  Future<AppEntries?> _probeAddress(String url,
      {required bool trustIfKnown}) async {
    final dio = Dio(BaseOptions(
        connectTimeout: const Duration(milliseconds: 2000),
        receiveTimeout: const Duration(milliseconds: 2000),
        validateStatus: (_) => true))
      ..httpClientAdapter =
          IOHttpClientAdapter(createHttpClient: TrustStore.createHttpClient);
    Uri uri;
    try {
      uri = Uri.parse(url.trim());
    } catch (_) {
      return null;
    }
    try {
      final r = await dio.get('$url/api/app/entries');
      if (r.statusCode != 200 || r.data is! Map) return null;
      final j = Map<String, dynamic>.from(r.data as Map);
      if (!j.containsKey('cert_sha256')) return null; // 非本系统服务端
      final entries = AppEntries.fromJson(j);
      // 红队 R1-P1：cert_sha256 是"自报家门"，指纹门槛必须对所有 scheme 生效
      // （https 由 TrustStore 握手裁决 + 此处同源复核双保险），且空指纹直接
      // 拒绝——哨兵为空时 normFp('') == _sentinelFp() 会让空指纹穿透。
      final fp = normFp(entries.certSha256);
      if (fp.isEmpty) return null;
      final known = TrustStore.I.knowsFingerprint(entries.certSha256) ||
          fp == await _sentinelFp();
      if (!known) return null;
      return entries;
    } on DioException catch (e) {
      if (!trustIfKnown || e.error is! HandshakeException) return null;
      final fp = TrustStore.I.pendingFor(uri.host, uri.port);
      if (fp == null || !TrustStore.I.knowsFingerprint(fp)) return null;
      await TrustStore.I.trust(uri.host, uri.port, fp);
      return null; // 已静默入库，下一轮探测直通
    } catch (_) {
      return null;
    } finally {
      dio.close(force: true);
    }
  }

  Future<void> _doSwitch(String url, String event,
      {required String currentAtProbeStart}) async {
    // 审查 P3：探测链可长达数秒，期间用户可能手动切了入口——以切换瞬间
    // 的实际地址为准，不一致就放弃本轮结论
    if (Prefs.addressNotifier.value != currentAtProbeStart) return;
    // 红队 P2-6：冷却统一裁决（此前 LAN→公网路径没有，服务器崩溃循环时
    // 每次 _doSwitch 都置空 _dio，进行中的传输被反复掐死）
    if (DateTime.now().difference(_lastSwitchAt) < _cooldown) return;
    // 红队 P2-7 修订（v0.15.2）：原实现"有活动传输就不切"在 pending 也算
    // active 的语义下会死锁——离家后自动备份持续产生 pending/重试任务，
    // 闸门永远关闭，局域网切公网永不发生（用户实测根因）。改为切换前
    // 全量暂停：cancelToken 在分片边界优雅停、进度留断点；切回局域网时
    // 只恢复这批自动暂停的任务（不碰用户手动暂停的，不违背省流意图）。
    // 审查 P1：快照取 pauseAll 会翻转 userPaused 的全集（!finished &&
    // !userPaused），并集累积——多次切换后旧一批不被覆盖遗弃。
    final autoPauseIds = {
      for (final t in TransferEngine.I.tasks)
        if (!t.finished && !t.userPaused) t.id
    };
    if (autoPauseIds.isNotEmpty) {
      _autoPausedIds.addAll(autoPauseIds);
      // R1-P3：pauseAll 必须在 await 之前——中间隔着 _persistAutoPaused()
      // 的写盘 await 时，窗口内入队的任务会被 pauseAll 翻成 userPaused
      // 却不在快照里，切回 LAN 后永不被自动恢复
      TransferEngine.I.pauseAll();
      await _persistAutoPaused();
    }
    final backToLan = !isPublicAddress(url);
    _lastSwitchAt = DateTime.now();
    _lanFails = 0;
    await ApiClient.I.switchServer(url);
    if (backToLan) await _resumeAutoPaused();
    lastEvent.value = '$event · ${_hhmm(DateTime.now())}';
    debugPrint('[AutoSwitch] $event');
  }

  /// 恢复切换时自动暂停的任务。不一次性清空快照：退避重试中的任务此刻
  /// 仍是 uploading 状态（resumeTask 只接受 paused/error），要等退避
  /// sleep 结束落成 paused 后由切回后的探测轮次补恢复（红队 P2-1）。
  /// 条件只认 userPaused：用户手动恢复过的（userPaused=false）立即出列，
  /// 不会被覆盖。
  Future<void> _resumeAutoPaused() async {
    if (_autoPausedIds.isEmpty) return;
    final engine = TransferEngine.I;
    final consumed = <String>{};
    for (final t in List.of(engine.tasks)) {
      if (!_autoPausedIds.contains(t.id)) continue;
      if (t.finished || !t.userPaused) {
        consumed.add(t.id);
      } else if (t.status == TransferStatus.paused ||
          t.status == TransferStatus.error) {
        engine.resumeTask(t);
        consumed.add(t.id);
      }
      // 其余（退避 sleep 中的 uploading/downloading 等）：本轮不动，下轮补
    }
    // 任务已被用户移除记录：id 出列，不留悬挂
    final present = engine.tasks.map((t) => t.id).toSet();
    _autoPausedIds.removeAll(consumed);
    _autoPausedIds.removeWhere((id) => !present.contains(id));
    if (consumed.isNotEmpty) await _persistAutoPaused();
  }

  Future<void> _persistAutoPaused() async {
    try {
      final sp = await SharedPreferences.getInstance();
      if (_autoPausedIds.isEmpty) {
        await sp.remove(_kAutoPaused);
      } else {
        await sp.setStringList(_kAutoPaused, _autoPausedIds.toList());
      }
    } catch (_) {}
  }

  /// 局域网候选：地址历史里的非公网 https 项（最近使用在前，天然优先级）。
  /// 红队 P1：http 候选的"指纹同源"检查输入是明文自报值 + 公开常量
  /// （cert_sha256 任何 TLS 连接者都能读到），对链路内攻击者不构成密码学
  /// 约束——重放真实指纹即可把 Bearer 导向假服务器。https 候选有 TrustStore
  /// 握手裁决兜底，http 候选只允许用户手动选择（实际部署 LAN 入口即 Caddy
  /// https :8765，直连 8766 仅本机）。
  Future<List<String>> _lanCandidates() async =>
      (await Prefs.addresses())
          .where((a) => !isPublicAddress(a) && a.startsWith('https://'))
          .toList();

  /// 缓存公网入口 + 证书指纹（局域网挂掉时 entries 问不到，切公网只能
  /// 靠这份缓存）。审查 P2：只在值变化时写 prefs（探测每 20s 一次，无条件
  /// 写盘是纯功耗）。指纹兼作失效哨兵：变化说明服务器换证书，候选作废
  /// （清缓存，TrustStore 已信任记录不受影响，用户手动选择时重新 TOFU）。
  ///
  /// v0.15.2 预信任：entries 来自"当前已通过 TrustStore/http 指纹双门槛
  /// 确认"的服务器，把它自报的公网端点 + 证书指纹预先入库——首次出门
  /// 切公网时 TLS 握手直通，不再需要"先被拒一轮、下轮静默信任"的两拍
  /// 延迟，也杜绝指纹同源但端口未入库导致的静默切换失败。overTls=false
  /// （当前走 http 明文 LAN）时不预信任：明文信道上的 entries 可被同
  /// 网段 MITM 整体伪造，此时写入的是攻击者选定的 host:port（红队 P1-2）；
  /// v0.16.2 起候选缓存与指纹哨兵同样只在 TLS 信道写入（R1-P1），
  /// 消费端仍保留 https-only + 指纹同源双门槛做纵深防御。
  Future<void> _cachePublicEntries(AppEntries entries,
      {required bool overTls}) async {
    try {
      final sp = await SharedPreferences.getInstance();
      final pubs = entries.publicAddresses;
      final fp = entries.certSha256;
      final oldPubs = sp.getStringList(_kPubCache) ?? const [];
      final oldFp = sp.getString(_kFpCache) ?? '';
      // 红队 R1-P1：http 明文信道上的 entries 可被同网段 MITM 整体伪造——
      // 公网候选缓存与指纹哨兵只在 TLS 信道上写入，否则等于给攻击者播种
      // 假公网入口/假哨兵指纹（哨兵是 http 候选的信任锚，被播种即失守）。
      // 清空/撤销类操作保留：最坏是 DoS（缓存被清），不构成凭据泄露。
      if (!overTls) {
        if (pubs.isEmpty && oldPubs.isNotEmpty) {
          await sp.remove(_kPubCache);
        }
        return;
      }
      // 红队 P1-2：服务端撤销公网配置（public/public_alt 变空）时旧缓存
      // 必须失效，否则会拿 A 服务器的入口当 B 的兜底
      if (pubs.isEmpty && oldPubs.isNotEmpty) {
        await sp.remove(_kPubCache);
        return;
      }
      if (fp.isNotEmpty && oldFp.isNotEmpty && oldFp != fp) {
        // 服务器换证书：旧入口作废；同时写入新指纹让哨兵"清一次后重新
        // 武装"——不写回来的话 oldFp 永远是旧值，每 20s 重复清缓存且
        // 公网兜底再也建不起来（第二轮 review P1）
        await sp.remove(_kPubCache);
        await sp.setString(_kFpCache, fp);
        return;
      }
      if (pubs.isNotEmpty && pubs.join('') != oldPubs.join('')) {
        await sp.setStringList(_kPubCache, pubs);
      }
      if (fp.isNotEmpty && fp != oldFp) {
        await sp.setString(_kFpCache, fp);
      }
      // 预信任公网端点（仅 https + 仅在验证过的 TLS 信道上取得时；
      // TrustStore.trust 内部归一 + 有写盘，warmup 后按归一格式查同值
      // 跳过，避免每 20s 重复写 SecureStore——红队 P3-2）
      if (overTls && fp.isNotEmpty) {
        await TrustStore.I.warmup();
        final fpHex = TrustStore.normHex(fp);
        for (final raw in pubs) {
          final u = Uri.tryParse(normUrl(raw));
          if (u == null || u.scheme != 'https' || u.host.isEmpty) continue;
          final port = u.hasPort ? u.port : 443;
          if (TrustStore.I.trustedFor(u.host, port) == fpHex) continue;
          await TrustStore.I.trust(u.host, port, fp);
        }
      }
    } catch (_) {}
  }

  String _hostOf(String url) {
    try {
      final u = Uri.parse(url);
      return u.port > 0 &&
              !((u.scheme == 'https' && u.port == 443) ||
                  (u.scheme == 'http' && u.port == 80))
          ? '${u.host}:${u.port}'
          : u.host;
    } catch (_) {
      return url;
    }
  }

  String _hhmm(DateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}
