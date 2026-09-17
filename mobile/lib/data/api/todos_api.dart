// 效率舱·待办域 API（v1.1 M1）：清单/待办 CRUD + 家庭指派。
// 形状兜底沿用 notes_api 惯例：本层只请求与兜底，解析进展示模型。
import 'package:dio/dio.dart';

import 'api_client.dart';

class TodosApi {
  TodosApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  /// 全量可见待办 + 在用成员简表（assignee_id/done_by → 昵称映射用）。
  /// 返回 (todos 原始 Map 列表, members id→Map(username,nickname))。
  static Future<(List<dynamic>, Map<int, Map<String, String>>)> listAll() async {
    final dio = await _dio();
    final r = await dio.get('/api/todos');
    final todos = (r.data['todos'] as List? ?? const []);
    return (todos, _members(r));
  }

  /// 清单列表（含 open_count）+ 成员简表。
  static Future<(List<dynamic>, Map<int, Map<String, String>>)> lists() async {
    final dio = await _dio();
    final r = await dio.get('/api/todos/lists');
    final lists = (r.data['lists'] as List? ?? const []);
    return (lists, _members(r));
  }

  static Map<int, Map<String, String>> _members(Response r) {
    final out = <int, Map<String, String>>{};
    for (final m in (r.data['members'] as List? ?? const [])) {
      final id = (m as Map)['id'];
      if (id is num) {
        out[id.toInt()] = {
          'username': (m['username'] as String?) ?? '',
          'nickname': (m['nickname'] as String?) ?? '',
        };
      }
    }
    return out;
  }

  static Future<int> createList(String name, String kind) async {
    final dio = await _dio();
    final r = await dio.post('/api/todos/lists', data: {'name': name, 'kind': kind});
    return ((r.data['id'] as num?) ?? 0).toInt();
  }

  static Future<void> renameList(int id, String name) async {
    final dio = await _dio();
    await dio.patch('/api/todos/lists/$id', data: {'name': name});
  }

  static Future<void> deleteList(int id) async {
    final dio = await _dio();
    await dio.delete('/api/todos/lists/$id');
  }

  static Future<int> createTodo(int listId, String title,
      {String notes = '', num? dueAt, int priority = 0, int? assigneeId}) async {
    final dio = await _dio();
    final r = await dio.post('/api/todos', data: {
      'list_id': listId,
      'title': title,
      'notes': notes,
      'due_at': dueAt,
      'priority': priority,
      'assignee_id': assigneeId,
    });
    return ((r.data['id'] as num?) ?? 0).toInt();
  }

  /// [dueAt] 传 null 且 [clearDue] 为 true 才清空截止日（区分"未传"）。
  static Future<void> patchTodo(int id,
      {String? title,
      String? notes,
      num? dueAt,
      bool clearDue = false,
      int? priority,
      int? assigneeId,
      String? status}) async {
    final dio = await _dio();
    final data = <String, dynamic>{
      'title': ?title,
      'notes': ?notes,
      if (clearDue) 'due_at': null,
      'due_at': ?dueAt,
      'priority': ?priority,
      'assignee_id': ?assigneeId,
      'status': ?status,
    };
    await dio.patch('/api/todos/$id', data: data);
  }

  static Future<void> deleteTodo(int id) async {
    final dio = await _dio();
    await dio.delete('/api/todos/$id');
  }
}
