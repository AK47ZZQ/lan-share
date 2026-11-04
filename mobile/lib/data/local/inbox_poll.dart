// 站内通知前台实时通道（B3）：SSE /api/events 优先——通知/广播到达即信号，
// 立即拉一次 /api/notifications（未读数比上次多 → 取最新一条发系统通知，一次
// 事件一条不连环轰炸）。SSE 不可用（旧服务端 404/被限流 429/网络断）自动回退
// 60s 前台轮询（v0.11 起的原路径），轮询常开兜底、SSE 就绪只是"更快一步"。
// 边界：仅前台（后台推送属厂商通道阶段 2）；登录失败/网络差静默吞掉；
// SSE 解析零新依赖（dio ResponseType.stream 手工分帧，镜像 424 拉不了新包）；
// "已读"仍以通知中心页面打开为准，这里只做"告知"。
import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';

import '../api/api_client.dart';
import '../api/notifications_api.dart';
import 'notify.dart';

class InboxPoll {
  InboxPoll._();
  static final InboxPoll I = InboxPoll._();

  Timer? _timer;
  CancelToken? _sseCancel;
  bool _busy = false;
  bool _sseTried = false; // 每个前台周期只试一次 SSE，失败留给下轮
  int _lastUnread = -1; // -1 = 首次对齐基线不提醒（避免冷启动炸历史通知）

  /// 登出/换账号时必须调到，否则新账号的首轮未读数会和**上一个账号**的
  /// 基线比较：偏小则新账号的第一条通知永远不弹（2026-11-04 审查 P3）。

  void start() {
    _timer ??= Timer.periodic(const Duration(seconds: 60), (_) => _tick());
    _tick();
    _sseTried = false;
    _openSse(); // 前台回来先试实时通道
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
    _sseCancel?.cancel();
    _sseCancel = null;
    // 清掉未读基线（2026-11-04 审查 P3）。
    //
    // _lastUnread 是进程级字段，登出路径会走到这里。不清的话，换账号后
    // 首轮 _tick() 拿新账号的未读数去和**旧账号**的基线比：只要偏小，
    // 新账号的第一条通知就永远不弹。换账号是真实场景（家里多个成员共用
    // 一台手机、或帮家人登录），不是理论问题。
    _lastUnread = -1;
  }

  /// 建立 SSE 长连接：事件帧到达即拉通知。任何异常都静默转轮询兜底。
  Future<void> _openSse() async {
    if (_sseTried) return;
    _sseTried = true;
    // 开新流前先断旧流（2026-09-13 生产日志 4 连 429）：快速前后台切换时
    // 旧连接要等服务端下一个心跳（≤15s）才被回收，期间累积可撞每用户
    // 3 连接甚至全局 64 上限——挤占所有人的实时通道。cancel 对已完成的
    // 流是无害 no-op。
    _sseCancel?.cancel();
    final cancel = CancelToken();
    _sseCancel = cancel;
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/events',
          options: Options(
            responseType: ResponseType.stream,
            headers: {'Accept': 'text/event-stream'},
            // 长连接不走全局 receiveTimeout 之外的特判：心跳 15s < 30s 超时，安全
          ),
          cancelToken: cancel);
      final stream = (r.data as ResponseBody).stream;
      final buf = StringBuffer();
      await for (final chunk in stream) {
        // 已知限制：allowMalformed 对跨 chunk 的 UTF-8 多字节字符会产生替换符
        // ——当前事件仅 ASCII 信号帧无碍；若未来载荷含中文需改增量解码器
        buf.write(utf8.decode(chunk, allowMalformed: true));
        while (true) {
          final idx = buf.toString().indexOf('\n');
          if (idx < 0) break;
          final line = buf.toString().substring(0, idx);
          final rest = buf.toString().substring(idx + 1);
          buf
            ..clear()
            ..write(rest);
          if (line.startsWith('data:')) {
            _tick(); // 信号语义：收到即拉全量（畸形帧也无害）
          }
        }
      }
      // 服务端正常关流（重启/超限）：本轮不再试，等下个前台周期
    } catch (_) {
      // 旧服务端 404 / 429 限流 / 网络差：轮询兜底，静默
    } finally {
      if (identical(_sseCancel, cancel)) _sseCancel = null;
    }
  }

  Future<void> _tick() async {
    if (_busy) return;
    _busy = true;
    try {
      final box = await NotificationsApi.list();
      if (_lastUnread >= 0 && box.unread > _lastUnread && box.items.isNotEmpty) {
        final n = box.items.first;
        await TransferNotifier.I.inbox(title: n.title, body: n.body);
      }
      _lastUnread = box.unread;
    } catch (_) {
      // 未登录/网络差：保持上次基线，下轮再试
    } finally {
      _busy = false;
    }
  }
}
