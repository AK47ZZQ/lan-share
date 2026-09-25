// 效率舱·番茄钟（v1.2 M2 App 端切片）：开始专注 / 计时 / 完成·放弃 / 今日统计。
// 服务端权威时间戳 + 三态结算（§3.3）：本页只管本地倒计时与触发收束，
// App 被杀/断网的会话由服务端 sweep 惰性结算，重进本页即自动恢复/展示。
import 'dart:async';

import 'package:flutter/material.dart';

import '../data/api/errors.dart';
import '../data/api/pomodoros_api.dart';
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
        _running = run;
        _today = today;
        _loading = false;
      });
      _syncTicker();
      // 恢复的会话若已在页外到期：到点即按正常结束收束（本地推算）
      if (run != null && _remainingSec(run) <= 0) {
        await _finish('focus', endedAt: run.startedAt + run.focusMin * 60);
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
    if (_running == null) return;
    _ticker = Timer.periodic(const Duration(seconds: 1), (t) {
      if (!mounted) return t.cancel();
      if (_remainingSec(_running!) <= 0) {
        t.cancel();
        _finish('focus', endedAt: _running!.startedAt + _running!.focusMin * 60);
      } else {
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
    } catch (e) {
      if (mounted) showToast('开始失败：${errDetail(e)}');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _finish(String status, {double? endedAt}) async {
    final s = _running;
    if (s == null) return;
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
      if (mounted) {
        setState(() {
          _running = null;
          _busy = false;
        });
      }
      await _reload();
    }
  }

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
                      _running == null ? '开始专注' : _fmt(remaining),
                      textAlign: TextAlign.center,
                      style: Theme.of(context)
                          .textTheme
                          .displayLarge
                          ?.copyWith(color: cs.primary),
                    ),
                    const SizedBox(height: Gap.lg),
                    if (_running == null) ...[
                      Wrap(
                        alignment: WrapAlignment.center,
                        spacing: Gap.xs,
                        children: [for (final m in const [15, 25, 45])
                          ChoiceChip(
                            label: Text('$m 分钟'),
                            selected: _focusMin == m,
                            onSelected: (_) => setState(() => _focusMin = m),
                          ),
                        ],
                      ),
                      const SizedBox(height: Gap.md),
                      FilledButton.icon(
                        onPressed: _busy ? null : _start,
                        icon: const Icon(Icons.play_arrow),
                        label: const Text('开始'),
                      ),
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
