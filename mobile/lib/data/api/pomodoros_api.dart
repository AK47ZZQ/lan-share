// 效率舱·番茄钟域 API（v1.2 M2）：会话开/收/恢复/统计。
// 形状兜底沿用 todos_api 惯例：本层只请求与兜底，解析进展示模型。
import 'package:dio/dio.dart';

import 'api_client.dart';

/// 一条番茄会话的展示投影（服务端权威时间戳，UTC 秒）。
class PomodoroSession {
  final int id;
  final int? todoId;
  final double startedAt;
  final double? endedAt;
  final int focusMin;
  final int breakMin;
  final String status; // running|focus|break|abandoned|auto_closed
  PomodoroSession({
    required this.id,
    required this.startedAt,
    required this.focusMin,
    required this.breakMin,
    required this.status,
    this.todoId,
    this.endedAt,
  });

  static PomodoroSession? fromMap(Object? m) {
    if (m is! Map) return null;
    final id = (m['id'] as num?)?.toInt();
    final started = (m['started_at'] as num?)?.toDouble();
    if (id == null || started == null) return null;
    return PomodoroSession(
      id: id,
      startedAt: started,
      focusMin: ((m['focus_min'] as num?) ?? 25).toInt(),
      breakMin: ((m['break_min'] as num?) ?? 0).toInt(),
      status: (m['status'] as String?) ?? 'running',
      todoId: (m['todo_id'] as num?)?.toInt(),
      endedAt: (m['ended_at'] as num?)?.toDouble(),
    );
  }
}

/// 逐日统计（服务端 Python 侧聚合，按本地日归属）。
class PomodoroDayStat {
  final String date;
  final int count;
  final int minutes;
  PomodoroDayStat({required this.date, required this.count, required this.minutes});
}

class PomodorosApi {
  PomodorosApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  /// 开一个专注会话（同用户单 running：服务端自动收束残留会话）。
  static Future<PomodoroSession?> start(
      {int? todoId, int focusMin = 25, int breakMin = 5}) async {
    final dio = await _dio();
    final r = await dio.post('/api/pomodoro/start',
        data: {'todo_id': todoId, 'focus_min': focusMin, 'break_min': breakMin});
    final started = (r.data['started_at'] as num?)?.toDouble();
    if (started == null) return null;
    return PomodoroSession(
      id: ((r.data['id'] as num?) ?? 0).toInt(),
      startedAt: started,
      focusMin: ((r.data['focus_min'] as num?) ?? focusMin).toInt(),
      breakMin: ((r.data['break_min'] as num?) ?? breakMin).toInt(),
      status: 'running',
      todoId: todoId,
    );
  }

  /// 显式收束：status = focus（正常结束）| abandoned（放弃，已专注时长仍计入）。
  static Future<int> finish(int id, String status, {double? endedAt}) async {
    final dio = await _dio();
    final r = await dio.post('/api/pomodoro/$id/finish',
        data: {'status': status, 'ended_at': endedAt});
    return ((r.data['minutes'] as num?) ?? 0).toInt();
  }

  /// 当前 running 会话（App 重启/切公网后恢复用；超时的已被服务端结算）。
  static Future<PomodoroSession?> running() async {
    final dio = await _dio();
    final r = await dio.get('/api/pomodoro/running');
    return PomodoroSession.fromMap(r.data['running']);
  }

  /// 近 [days] 天逐日统计（含今天）→ (今日统计, 逐日列表)。
  static Future<(PomodoroDayStat, List<PomodoroDayStat>)> summary(
      {int days = 7}) async {
    final dio = await _dio();
    final r =
        await dio.get('/api/pomodoro/summary', queryParameters: {'days': days});
    final today = r.data['today'] as Map? ?? const {};
    final todayStat = PomodoroDayStat(
      date: (today['date'] as String?) ?? '',
      count: ((today['count'] as num?) ?? 0).toInt(),
      minutes: ((today['minutes'] as num?) ?? 0).toInt(),
    );
    final list = (r.data['days'] as List? ?? const [])
        .map((m) => PomodoroDayStat(
              date: (m['date'] as String?) ?? '',
              count: ((m['count'] as num?) ?? 0).toInt(),
              minutes: ((m['minutes'] as num?) ?? 0).toInt(),
            ))
        .toList();
    return (todayStat, list);
  }
}
