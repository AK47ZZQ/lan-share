// 效率舱·番茄钟（v1.2 M2 App 端切片）：开始专注 / 计时 / 完成·放弃 / 今日统计。
// 服务端权威时间戳 + 三态结算（§3.3）：本页只管本地倒计时与触发收束，
// App 被杀/断网的会话由服务端 sweep 惰性结算，重进本页即自动恢复/展示。
import 'dart:async';

import 'package:flutter/material.dart';

import '../data/api/errors.dart';
import '../data/api/pomodoros_api.dart';
import '../data/local/notify.dart';
import 'theme/design_tokens.dart';
import 'widgets/kit.dart';

class PomodoroPage extends StatefulWidget {
  const PomodoroPage({super.key, this.todoId, this.todoTitle});

  /// 从待办发起专注时携带（服务端记账到该待办）。
  final int? todoId;
  final String? todoTitle;

  @override
  State<PomodoroPage> createState() => _PomodoroPageState();
}

class _PomodoroPageState extends State<PomodoroPage> {
  PomodoroSession? _running;
  PomodoroDayStat _today = PomodoroDayStat(date: '', count: 0, minutes: 0);
  int _focusMin = 25;
  bool _loading = true;
  bool _busy = false;
  bool _settling = false;   // 二轮审查 P2-5：到期自动结算与手点完成的防重入闸
  int? _breakRemaining;     // 休息倒计时（秒）；null=不在休息。本地计时，不落服务端
  Timer? _ticker;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  Future<void> _reload() async {
    setState(() => _loading = true);
    try {
      final run = await PomodorosApi.running();
      final (today, _) = await PomodorosApi.summary();
      if (!mounted) return;
      setState(() {
        // 对抗审查 P1-1：服务端出现新 running 会话时，本地休息段立即作废
        // （两者互斥）；正常休息期 run 为 null，休息倒计时不受影响
        if (run != null) _breakRemaining = null;
        _running = run;
        _today = today;
        _loading = false;
      });
      _syncTicker();
      if (run != null) {
        // 恢复的会话重新挂常驻通知
        TransferNotifier.I.focusOngoing(
            title: widget.todoTitle ?? '专注', remainingSec: _remainingSec(run));
      }
      // 恢复的会话若已在页外到期：到点即按正常结束收束（本地推算）
      if (run != null && _remainingSec(run) <= 0) {
        await _finish('focus',
            endedAt: run.startedAt + run.focusMin * 60, refresh: false);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showToast('加载失败：${errDetail(e)}');
    }
  }

  /// 倒计时只在 running 且未到期时走表；到期即自动正常结束。
  void _syncTicker() {
    _ticker?.cancel();
    // 二轮审查 P1：休息段（_running 已清、_breakRemaining 在计）也必须有
    // 活着的计时器——否则 _reload 后休息倒计时冻结
    if (_running == null && _breakRemaining == null) return;
    _ticker = Timer.periodic(const Duration(seconds: 1), (t) {
      if (!mounted) return t.cancel();
      if (_breakRemaining != null) {
        // 休息段（本地计时）：到点轻提示，跳过/到点均回空闲态
        if (_breakRemaining! <= 1) {
          t.cancel();
          if (mounted) {
            setState(() => _breakRemaining = null);
            showToast('休息结束，开始下一段吧');
          }
          return;
        }
        if (mounted) setState(() => _breakRemaining = _breakRemaining! - 1);
        return;
      }
      if (_remainingSec(_running!) <= 0) {
        t.cancel();
        _finish('focus', endedAt: _running!.startedAt + _running!.focusMin * 60);
      } else {
        if (_remainingSec(_running!) % 30 == 0) {
          // 常驻通知 30s 一更（App 存活期间；杀 App 由服务端惰性结算兜底）
          TransferNotifier.I.focusOngoing(
              title: widget.todoTitle ?? '专注',
              remainingSec: _remainingSec(_running!));
        }
        setState(() {});   // 纯驱动文本刷新；轻量，一分钟 60 次
      }
    });
  }

  int _remainingSec(PomodoroSession s) {
    final end = s.startedAt + s.focusMin * 60;
    return end - DateTime.now().millisecondsSinceEpoch / 1000 > 0
        ? (end - DateTime.now().millisecondsSinceEpoch / 1000).ceil()
        : 0;
  }

  Future<void> _start() async {
    setState(() => _busy = true);
    try {
      final s = await PomodorosApi.start(
          todoId: widget.todoId, focusMin: _focusMin);
      if (!mounted) return;
      setState(() => _running = s);
      _syncTicker();
      TransferNotifier.I.focusOngoing(
          title: widget.todoTitle ?? '专注', remainingSec: _remainingSec(s!));
    } catch (e) {
      if (mounted) showToast('开始失败：${errDetail(e)}');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _finish(String status, {double? endedAt, bool refresh = true}) async {
    final s = _running;
    if (s == null || _settling) return;   // 已在结算中：吞掉并发触发（含 ticker）
    _settling = true;
    setState(() => _busy = true);
    try {
      await PomodorosApi.finish(s.id, status, endedAt: endedAt);
      if (!mounted) return;
      showToast(status == 'focus'
          ? '专注完成 🎉 已计入今日统计'
          : '已放弃，已专注时长仍计入统计');
    } catch (e) {
      if (mounted) showToast('结算失败：${errDetail(e)}');
    } finally {
      _ticker?.cancel();
      if (status == 'focus') {
        // M2 常驻通知收尾：撤下 ongoing，按需补完成通知；休息段本地计时
        unawaited(TransferNotifier.I.focusFinished(title: _breakTitle(s)));
        if (s.breakMin > 0 && mounted) {
          setState(() => _breakRemaining = s.breakMin * 60);
          _syncTicker();
        }
      } else {
        unawaited(TransferNotifier.I.focusDismissed());
      }
      _settling = false;
      if (mounted) {
        setState(() {
          _running = null;
          _busy = false;
        });
      }
      // P3-8：恢复链路（_reload → 到期自动结算）的调用方传 refresh=false
      // 免嵌套双请求；其余路径结算后刷新统计
      if (refresh) await _reload();
    }
  }

  String _breakTitle(PomodoroSession? s) =>
      widget.todoTitle ?? (s?.todoId != null ? '待办专注' : '专注');

  String _fmt(int totalSec) {
    final m = totalSec ~/ 60;
    final s = totalSec % 60;
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final remaining = _running == null ? 0 : _remainingSec(_running!);
    return Scaffold(
      appBar: AppBar(title: const Text('专注')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(Gap.lg),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (widget.todoTitle != null) ...[
                      Text(widget.todoTitle!,
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: Gap.sm),
                    ],
                    Text(
                      _breakRemaining != null
                          ? '休息 ${_fmt(_breakRemaining!)}'
                          : (_running == null ? '开始专注' : _fmt(remaining)),
                      textAlign: TextAlign.center,
                      style: Theme.of(context)
                          .textTheme
                          .displayLarge
                          ?.copyWith(color: cs.primary),
                    ),
                    const SizedBox(height: Gap.lg),
                    if (_running == null) ...[
                      if (_breakRemaining != null) ...[
                        TextButton.icon(
                          onPressed: () =>
                              setState(() => _breakRemaining = null),
                          icon: const Icon(Icons.skip_next),
                          label: const Text('跳过休息'),
                        ),
                      ] else ...[
                        Wrap(
                          alignment: WrapAlignment.center,
                          spacing: Gap.xs,
                          children: [for (final m in const [15, 25, 45])
                            ChoiceChip(
                              label: Text('$m 分钟'),
                              selected: _focusMin == m,
                              onSelected: (_) =>
                                  setState(() => _focusMin = m),
                            ),
                          ],
                        ),
                        const SizedBox(height: Gap.md),
                        FilledButton.icon(
                          onPressed: _busy ? null : _start,
                          icon: const Icon(Icons.play_arrow),
                          label: const Text('开始'),
                        ),
                      ],
                    ] else ...[
                      const SizedBox(height: Gap.xl),
                      FilledButton.icon(
                        onPressed:
                            _busy ? null : () => _finish('focus'),
                        icon: const Icon(Icons.check),
                        label: const Text('完成'),
                      ),
                      const SizedBox(height: Gap.xs),
                      TextButton.icon(
                        onPressed:
                            _busy ? null : () => _finish('abandoned'),
                        icon: const Icon(Icons.stop),
                        label: const Text('放弃（已专注仍计入）'),
                      ),
                    ],
                    const SizedBox(height: Gap.xxl),
                    Card(
                      shape: const RoundedRectangleBorder(
                          borderRadius: Radii.bCard),
                      child: Padding(
                        padding: const EdgeInsets.all(Gap.md),
                        child: Column(children: [
                          Text('今日专注',
                              style: Theme.of(context).textTheme.labelLarge),
                          const SizedBox(height: Gap.xxs),
                          Text(
                            '${_today.count} 个 · ${_today.minutes} 分钟',
                            style: Theme.of(context).textTheme.headlineSmall,
                          ),
                        ]),
                      ),
                    ),
                  ],
                ),
              ),
            ),
    );
  }
}
