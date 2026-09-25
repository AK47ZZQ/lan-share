// 效率舱待办离线缓存（M2 最终计划 §3.7，App v1.1.12）：sqflite 快照 +
// 断网勾选补传队列。策略：
//   · 在线拉取成功 → 全量落快照（失败可丢，不影响在线功能）；
//   · 断网 → 读快照只读展示，勾选进补传队列（每待办只留最新意图，
//     最后写入胜），联网后由页面加载前回放（§3.7"最后写入胜"）；
//   · R3-05：本地表带 schema_version（sqflite version），结构不匹配 →
//     onUpgrade 清空重建（缓存可丢、不迁移）。
// DB 行为（建表/回放/断网）由模拟器 E2E 覆盖（svc wifi/data 开关）；
// 本模块不做单测拉真 SQLite（同 backup_ledger 的取舍）。
import 'package:sqflite/sqflite.dart';

class TodoCache {
  TodoCache._();
  static Database? _db;
  static const int _kVersion = 2;

  static Future<Database> _open() async =>
      _db ??= await openDatabase('todo_cache.db',
          version: _kVersion,
          onCreate: (db, v) => _create(db),
          onUpgrade: (db, oldV, newV) async {
            await db.execute('DROP TABLE IF EXISTS todos');
            await db.execute('DROP TABLE IF EXISTS pending_ops');
            await _create(db);
          });

  static Future<void> _create(Database db) async {
    await db.execute('CREATE TABLE todos('
        'id INTEGER PRIMARY KEY, list_id INTEGER NOT NULL, title TEXT NOT NULL,'
        'notes TEXT, due_at REAL, priority INTEGER, assignee_id INTEGER,'
        'status TEXT NOT NULL, done_at REAL, done_by INTEGER, list_kind TEXT,'
        'list_name TEXT, list_owner_id INTEGER, saved_at REAL NOT NULL)');
    await db.execute('CREATE TABLE pending_ops('
        'todo_id INTEGER PRIMARY KEY, status TEXT NOT NULL, ts REAL NOT NULL)');
  }

  /// 在线拉取成功后落快照（全量替换）。
  static Future<void> saveSnapshot(List<dynamic> todos) async {
    try {
      final db = await _open();
      final now = DateTime.now().millisecondsSinceEpoch / 1000;
      final batch = db.batch();
      batch.delete('todos');
      for (final raw in todos) {
        final m = raw as Map;
        final id = (m['id'] as num?)?.toInt();
        if (id == null) continue;
        batch.insert('todos', {
          'id': id,
          'list_id': ((m['list_id'] as num?) ?? 0).toInt(),
          'title': (m['title'] as String?) ?? '',
          'notes': (m['notes'] as String?) ?? '',
          'due_at': (m['due_at'] as num?)?.toDouble(),
          'priority': ((m['priority'] as num?) ?? 0).toInt(),
          'assignee_id': (m['assignee_id'] as num?)?.toInt(),
          'status': (m['status'] as String?) ?? 'todo',
          'done_at': (m['done_at'] as num?)?.toDouble(),
          'done_by': (m['done_by'] as num?)?.toInt(),
          'list_kind': (m['list_kind'] as String?) ?? 'personal',
          'list_name': (m['list_name'] as String?) ?? '',
          'list_owner_id': (m['list_owner_id'] as num?)?.toInt(),
          'saved_at': now,
        });
      }
      await batch.commit(noResult: true);
    } catch (_) {
      // 缓存失败不影响在线功能
    }
  }

  /// 断网兜底：读快照（形状与 TodosApi.listAll 的 todos 列表一致）。
  static Future<List<dynamic>> loadSnapshot() async {
    try {
      final db = await _open();
      final rows = await db.query('todos', orderBy: 'id DESC');
      return rows;
    } catch (_) {
      return const [];
    }
  }

  /// 断网勾选入队：每待办只留最新意图（最后写入胜，R3-05 同源口径）。
  static Future<void> queueToggle(int todoId, String status) async {
    try {
      final db = await _open();
      await db.insert(
          'pending_ops',
          {
            'todo_id': todoId,
            'status': status,
            'ts': DateTime.now().millisecondsSinceEpoch / 1000
          },
          conflictAlgorithm: ConflictAlgorithm.replace);
    } catch (_) {}
  }

  /// 联网补传：按 ts 升序逐条回放，成功出队；一条失败（仍断网）即停。
  /// 页面在每次 _load 前调用——回放完再拉取，端上自然收敛到服务端真值。
  static Future<void> replay(
      Future<void> Function(int id, String status) patch) async {
    try {
      final db = await _open();
      final ops = await db.query('pending_ops', orderBy: 'ts ASC');
      for (final op in ops) {
        try {
          await patch((op['todo_id'] as num).toInt(), op['status'] as String);
          await db.delete('pending_ops',
              where: 'todo_id=?', whereArgs: [op['todo_id']]);
        } catch (_) {
          return;   // 仍断网：剩余留下轮
        }
      }
    } catch (_) {}
  }
}
