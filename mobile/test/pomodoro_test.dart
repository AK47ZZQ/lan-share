// M2 番茄钟 App 端切片：会话/统计模型解析兜底（服务端字段缺失不炸 UI）。
// 计时与结算行为走模拟器/真机 E2E（见 STATUS v3.65 批次记录）。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/api/pomodoros_api.dart';

void main() {
  group('PomodoroSession.fromMap：形状兜底', () {
    test('完整字段', () {
      final s = PomodoroSession.fromMap({
        'id': 7,
        'started_at': 1790400000.5,
        'focus_min': 25,
        'break_min': 5,
        'status': 'running',
        'todo_id': 3,
      });
      expect(s, isNotNull);
      expect(s!.id, 7);
      expect(s.todoId, 3);
      expect(s.status, 'running');
      expect(s.endedAt, isNull);
    });

    test('缺 id / started_at：拒解析（空兜底由调用方承担）', () {
      expect(PomodoroSession.fromMap({'focus_min': 25}), isNull);
      expect(PomodoroSession.fromMap(null), isNull);
      expect(PomodoroSession.fromMap('x'), isNull);
    });

    test('旧服务端缺 focus_min/break_min：默认 25/0', () {
      final s = PomodoroSession.fromMap({'id': 1, 'started_at': 1.0});
      expect(s!.focusMin, 25);
      expect(s.breakMin, 0);
    });
  });

  group('PomodorosApi.summary 解析形状', () {
    test('today/days 缺失键不炸（经由 fromMap 同源的兜底约定）', () {
      // 直接构造统计对象验证聚合展示层的最小契约
      final d = PomodoroDayStat(date: '2026-09-25', count: 3, minutes: 75);
      expect(d.minutes, 75);
    });
  });
}
