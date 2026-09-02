// 0.7.3 备份可观测性纯逻辑单测（backup_round.dart）：
// 引擎轮次 JSON / Worker 队列 JSON → 用户可读文案。真机"点了没反应"
// 的排障全靠这些行，标签映射错了比不显示更糟。
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/backup/backup_round.dart';

String _round(String status, {String? reason, int uploaded = 0, int failed = 0,
    int ts = 1_000}) {
  return jsonEncode({
    'status': status,
    'reason': reason,
    'uploaded': uploaded,
    'failed': failed,
    'pending': 0,
    'trigger': 'manual',
    'ts': ts,
  });
}

void main() {
  group('roundView（引擎最近一轮）', () {
    test('空/坏 JSON 返回 null（UI 静默跳过）', () {
      expect(roundView(null), isNull);
      expect(roundView(''), isNull);
      expect(roundView('not-json{'), isNull);
      expect(roundView(jsonEncode([1, 2])), isNull);
    });

    test('done：有成果/零成果/含失败', () {
      expect(roundView(_round('done', uploaded: 12))!.text,
          contains('已备份 12 张'));
      expect(roundView(_round('done', uploaded: 12, failed: 3))!.text,
          contains('3 张失败'));
      final zero = roundView(_round('done'))!;
      expect(zero.text, contains('没有需要备份的内容'));
      expect(zero.warn, isFalse);
    });

    test('paused：等待 Wi-Fi / 等待充电标记 warn', () {
      final wifi = roundView(_round('paused', reason: 'wifi_lost'))!;
      expect(wifi.text, contains('等待 Wi-Fi'));
      expect(wifi.warn, isTrue);
      expect(roundView(_round('paused', reason: 'unplugged'))!.text,
          contains('等待充电'));
    });

    test('skip/trust/error/cooldown 文案与 warn 标记', () {
      expect(roundView(_round('skip', reason: 'busy'))!.text,
          contains('上一轮仍在进行'));
      expect(roundView(_round('skip', reason: 'disabled'))!.warn, isFalse);
      expect(roundView(_round('trust_required'))!.text, contains('证书'));
      expect(roundView(_round('trust_required'))!.warn, isTrue);
      final err = roundView(_round('error', reason: 'HTTP 401'))!;
      expect(err.text, contains('HTTP 401'));
      expect(err.warn, isTrue);
      expect(roundView(_round('cooldown'))!.text, contains('冷却'));
    });

    test('时间前缀：相对时间拼接', () {
      final now = 10 * 3_600_000;
      final v = roundView(_round('done', uploaded: 1, ts: now - 5 * 60_000),
          nowMs: now)!;
      expect(v.text, startsWith('5 分钟前 · '));
    });
  });

  group('queueView（Worker 队列）', () {
    test('queued_wait 列出等待的充电/Wi-Fi', () {
      final v = queueView(jsonEncode({
        'status': 'queued_wait',
        'reason': 'charging,wifi',
        'ts': 1,
      }))!;
      expect(v.text, contains('充电'));
      expect(v.text, contains('Wi-Fi'));
      expect(v.warn, isTrue);
    });

    test('charging 单独等待', () {
      expect(
          queueView(jsonEncode({
            'status': 'queued_wait',
            'reason': 'charging',
            'ts': 1,
          }))!.text,
          contains('等待充电'));
    });

    test('busy 有提示；queued/running/坏数据不显示', () {
      expect(queueView(jsonEncode({'status': 'busy', 'ts': 1})), isNotNull);
      expect(queueView(jsonEncode({'status': 'queued', 'ts': 1})), isNull);
      expect(queueView(jsonEncode({'status': 'running', 'ts': 1})), isNull);
      expect(queueView(null), isNull);
      expect(queueView('{{bad'), isNull);
    });
  });

  group('workerView（诊断卡完整状态）', () {
    test('running 显示触发来源', () {
      final v = workerView(jsonEncode({
        'status': 'running',
        'reason': 'manual',
        'ts': 1,
      }))!;
      expect(v.text, contains('手动立即备份'));
    });

    test('skip_disabled / skip_idle / done', () {
      expect(workerView(jsonEncode({'status': 'skip_disabled', 'ts': 1}))!.warn,
          isTrue);
      expect(workerView(jsonEncode({'status': 'skip_idle', 'ts': 1}))!.text,
          contains('没有待备份内容'));
      expect(workerView(jsonEncode({'status': 'done', 'ts': 1}))!.text,
          contains('完成'));
    });

    test('queued_wait 委托 queueView 文案', () {
      expect(
          workerView(jsonEncode({
            'status': 'queued_wait',
            'reason': 'charging',
            'ts': 1,
          }))!.text,
          contains('等待充电'));
    });
  });
}
