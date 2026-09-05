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
// 局域网挂掉时服务端问不到，只能靠这份缓存兜底。
// 生命周期：HomePage（IndexedStack 常驻）initState start、dispose stop；
// 退后台不停表但 _tick 里按 lifecycleState 跳过探测（后台 Timer 空转开销
// 可忽略，不额外挂 WidgetsBindingObserver）。
import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/api_client.dart';
import '../data/api/trust.dart';
import '../transfer/transfer_engine.dart';
import '../data/entries_service.dart';
import '../data/local/prefs.dart';
import '../data/net.dart';

class AutoSwitch {
  AutoSwitch._();
  static final AutoSwitch I = AutoSwitch._();

  static const _kPubCache = 'autoswitch_public_addrs';
  static const _kFpCache = 'autoswitch_cert_fp';
  static const _probeInterval = Duration(seconds: 20);
  static const _cooldown = Duration(seconds: 60);

  Timer? _timer;
  int _gen = 0; // start/stop 竞态代数：stop 后完成的 start 不得建表
  bool _probing = false;
  int _lanFails = 0; // 当前 LAN 连续失败计数（2 次才切公网）
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
    // 不立即探测：刚进 App 业务请求正忙，等第一个周期
  }

  void stop() {
    _gen++;
    _timer?.cancel();
    _timer = null;
    _lanFails = 0;
  }

  /// 登录页公网兜底（第二轮 review P2）：未登录时本服务不跑，"出门冷启动
  /// App"场景由这里补——局域网地址连不上时用缓存的公网候选做一轮探测，
  /// 返回第一个确认是本系统且已可用的地址（静默信任门槛与运行期一致：
  /// 只信已入库的同源指纹，绝不静默 TOFU 陌生证书）。无缓存/全不可达返回 null。
  static Future<String?> cachedPublicFallback({String? exclude}) async {
    final sp = await SharedPreferences.getInstance();
    final pubs = sp.getStringList(_kPubCache) ?? const [];
    for (final raw in pubs) {
      final a = raw.trim().replaceAll(RegExp(r'/+\$'), '');
      if (a.isEmpty || a == exclude) continue;
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
    // 红队 P1-2：换账号/换部署时公网候选缓存必须清空——否则新账号的
    // 局域网掉线时会被切到上一台服务器的公网入口（Token 发给错误服务端）
    try {
      final sp = await SharedPreferences.getInstance();
      await sp.remove(_kPubCache);
      await sp.remove(_kFpCache);
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
    () async {
      try {
        await _probe();
      } catch (_) {
        // 探测自身异常：静默，下个周期再来
      } finally {
        _probing = false;
      }
    }();
  }

  Future<void> _probe() async {
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
      if (lan != null) {
        await _doSwitch(lan, '已自动切回局域网（${_hostOf(lan)}）',
            currentAtProbeStart: current);
      }
      return;
    }

    // 在局域网：先探当前地址（探测返回的 entries 顺手落缓存）
    final entries = await _probeAddress(current, trustIfKnown: true);
    if (entries != null) {
      _lanFails = 0;
      await _cachePublicEntries(entries);
      return;
    }
    _lanFails++;
    if (_lanFails < 2) return; // 单次抖动不切
    final sp = await SharedPreferences.getInstance();
    final pubs = sp.getStringList(_kPubCache) ?? const [];
    final reachable = await _reachable(pubs.where((a) => a != current).toList());
    if (reachable != null) {
      await _doSwitch(reachable, '局域网不可达，已自动切换公网（${_hostOf(reachable)}）',
          currentAtProbeStart: current);
    }
  }

  /// 依次探测候选，返回第一个确认是本系统的地址
  Future<String?> _reachable(List<String> candidates) async {
    for (final raw in candidates) {
      // 尾斜杠归一（红队 P3-10）：缓存条目原样来自服务端，带斜杠会让
      // '$url/api/...' 直拼成 //api/... 恒 404，候选变成永远选不中的死项
      final a = raw.trim().replaceAll(RegExp(r'/+\$'), '');
      if (a.isEmpty || a == Prefs.addressNotifier.value) continue;
      if (await _probeAddress(a, trustIfKnown: true) != null) return a;
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
        connectTimeout: const Duration(milliseconds: 3500),
        receiveTimeout: const Duration(milliseconds: 3500),
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
      // 红队 P1：cert_sha256 键是"自报家门"，http 候选没有任何密码学校验——
      // 同网段攻击者抢 IP 起假服务即可通过探测，业务流量（含 Bearer）会被
      // 导过去。https 候选的 TLS 握手本身已由 TrustStore 裁决（指纹不在库
      // 必被拒），http 候选则要求证书指纹与已信任库或本地哨兵一致才算本系统。
      if (uri.scheme != 'https') {
        final known = TrustStore.I.knowsFingerprint(entries.certSha256) ||
            normFp(entries.certSha256) == await _sentinelFp();
        if (!known) return null;
      }
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
    // 红队 P2-7：有活动传输时不切——旧 dio 会 close，在途大文件断流；
    // 且 LAN 下确认过的 >100MB 上传会无确认地改走公网。下个周期再试
    if (TransferEngine.I.tasks.any((t) => t.isActive)) return;
    _lastSwitchAt = DateTime.now();
    _lanFails = 0;
    await ApiClient.I.switchServer(url);
    lastEvent.value = '$event · ${_hhmm(DateTime.now())}';
    debugPrint('[AutoSwitch] $event');
  }

  /// 局域网候选：地址历史里的非公网项（最近使用在前，天然优先级）
  Future<List<String>> _lanCandidates() async =>
      (await Prefs.addresses()).where((a) => !isPublicAddress(a)).toList();

  /// 缓存公网入口 + 证书指纹（局域网挂掉时 entries 问不到，切公网只能
  /// 靠这份缓存）。审查 P2：只在值变化时写 prefs（探测每 20s 一次，无条件
  /// 写盘是纯功耗）。指纹兼作失效哨兵：变化说明服务器换证书，候选作废
  /// （清缓存，TrustStore 已信任记录不受影响，用户手动选择时重新 TOFU）。
  Future<void> _cachePublicEntries(AppEntries entries) async {
    try {
      final sp = await SharedPreferences.getInstance();
      final pubs = entries.publicAddresses;
      final fp = entries.certSha256;
      final oldPubs = sp.getStringList(_kPubCache) ?? const [];
      final oldFp = sp.getString(_kFpCache) ?? '';
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
      if (pubs.isNotEmpty && pubs.join('') != oldPubs.join('')) {
        await sp.setStringList(_kPubCache, pubs);
      }
      if (fp.isNotEmpty && fp != oldFp) {
        await sp.setString(_kFpCache, fp);
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
