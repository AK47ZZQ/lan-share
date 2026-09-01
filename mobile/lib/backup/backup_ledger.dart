// 备份账本（FR-2 去重 + APP-DESIGN §4 状态机）：单表 SQLite。
// 身份 = MediaStore 媒体库 ID（跨重启稳定）；(id, mtime, size) 变了视为文件被
// 编辑过 → 重新备份。状态机：pending → uploading → done | error(attempts≤5)。
// 单向备份语义：相册里删掉的照片账本原样保留（网盘不随相册删除）。
import 'package:sqflite/sqflite.dart';

class LedgerRow {
  final String mediaId; // "img_123" / "vid_456"
  final String kind; // image | video
  final String name;
  final int size;
  final int mtime; // MediaStore DATE_MODIFIED（秒）
  String status; // pending/uploading/done/error
  int attempts;
  String? lastError;
  String? sha256;
  String? remotePath;
  String? sessionId; // 服务端断点会话（跨进程续传）
  int received; // 已确认分片数（展示用，真值在服务端 state）
  int? uploadedAt; // 完成时间（秒）

  LedgerRow({
    required this.mediaId,
    required this.kind,
    required this.name,
    required this.size,
    required this.mtime,
    this.status = 'pending',
    this.attempts = 0,
    this.lastError,
    this.sha256,
    this.remotePath,
    this.sessionId,
    this.received = 0,
    this.uploadedAt,
  });

  bool get finished => status == 'done';

  Map<String, Object?> toMap() => {
        'media_id': mediaId,
        'kind': kind,
        'name': name,
        'size': size,
        'mtime': mtime,
        'status': status,
        'attempts': attempts,
        'last_error': lastError,
        'sha256': sha256,
        'remote_path': remotePath,
        'session_id': sessionId,
        'received': received,
        'uploaded_at': uploadedAt,
      };

  static LedgerRow fromMap(Map<String, Object?> m) => LedgerRow(
        mediaId: m['media_id'] as String,
        kind: m['kind'] as String,
        name: m['name'] as String,
        size: (m['size'] as num).toInt(),
        mtime: (m['mtime'] as num).toInt(),
        status: m['status'] as String? ?? 'pending',
        attempts: (m['attempts'] as num?)?.toInt() ?? 0,
        lastError: m['last_error'] as String?,
        sha256: m['sha256'] as String?,
        remotePath: m['remote_path'] as String?,
        sessionId: m['session_id'] as String?,
        received: (m['received'] as num?)?.toInt() ?? 0,
        uploadedAt: (m['uploaded_at'] as num?)?.toInt(),
      );
}

class LedgerStats {
  int done = 0;
  int pending = 0; // pending/uploading
  int error = 0;
  int? lastUploadedAt; // 秒
}

class BackupLedger {
  BackupLedger._();
  static final BackupLedger I = BackupLedger._();

  Database? _db;

  Future<Database> open() async {
    final db = _db;
    if (db != null) return db;
    return _db = await openDatabase('backup_ledger.db',
        version: 1, onCreate: _create);
  }

  Future<void> _create(Database db, int v) async {
    await db.execute('''
      CREATE TABLE ledger(
        media_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        sha256 TEXT,
        remote_path TEXT,
        session_id TEXT,
        received INTEGER NOT NULL DEFAULT 0,
        uploaded_at INTEGER
      )''');
    await db.execute('CREATE INDEX idx_ledger_status ON ledger(status)');
  }

  /// 媒体库扫描结果与账本对账：新文件/变更文件/未完成项 → 待备份清单
  /// （按拍摄时间升序，网盘里相册顺序自然）。纯 done 且未变更的跳过。
  Future<List<LedgerRow>> reconcile(List<LedgerRow> scanned) async {
    final db = await open();
    final known = <String, LedgerRow>{};
    for (final r in await db.query('ledger')) {
      final row = LedgerRow.fromMap(r);
      known[row.mediaId] = row;
    }
    final pending = <LedgerRow>[];
    final batch = db.batch();
    for (final s in scanned) {
      final row = known[s.mediaId];
      if (row == null) {
        batch.insert('ledger', s.toMap());
        pending.add(s);
      } else if (row.mtime != s.mtime || row.size != s.size) {
        // 文件被编辑：重置后重传（同目录同名覆盖，服务端旧版本进回收站）
        final reset = LedgerRow(
            mediaId: s.mediaId,
            kind: s.kind,
            name: s.name,
            size: s.size,
            mtime: s.mtime,
            sha256: row.sha256);
        batch.update('ledger', reset.toMap(),
            where: 'media_id = ?', whereArgs: [s.mediaId]);
        pending.add(reset);
      } else if (row.status != 'done') {
        pending.add(row); // pending/uploading/error 都纳入本轮（error 自动重试）
      }
      known.remove(s.mediaId);
    }
    await batch.commit(noResult: true);
    pending.sort((a, b) => a.mtime.compareTo(b.mtime));
    return pending;
  }

  Future<void> setStatus(String id, String status) async {
    final db = await open();
    await db.update('ledger', {'status': status},
        where: 'media_id = ?', whereArgs: [id]);
  }

  Future<void> setSha(String id, String sha) async {
    final db = await open();
    await db.update('ledger', {'sha256': sha},
        where: 'media_id = ?', whereArgs: [id]);
  }

  Future<void> setSession(String id, String sid, int chunkSize,
      List<int> received) async {
    final db = await open();
    await db.update(
        'ledger',
        {
          'session_id': sid,
          'received': received.length,
          'status': 'uploading',
        },
        where: 'media_id = ?',
        whereArgs: [id]);
  }

  Future<void> setReceived(String id, int count) async {
    final db = await open();
    await db.update('ledger', {'received': count},
        where: 'media_id = ?', whereArgs: [id]);
  }

  Future<void> markDone(String id, String remotePath) async {
    final db = await open();
    await db.update(
        'ledger',
        {
          'status': 'done',
          'remote_path': remotePath,
          'session_id': null,
          'last_error': null,
          'uploaded_at': DateTime.now().millisecondsSinceEpoch ~/ 1000,
        },
        where: 'media_id = ?',
        whereArgs: [id]);
  }

  /// 失败：bump=true 时累加 attempts；attempts 超 5 置 error，否则留在
  /// uploading（下一轮 diff 会再次纳入，等同自动重试）
  Future<bool> bumpAttempt(String id) async {
    final db = await open();
    final rows = await db.query('ledger',
        columns: ['attempts'], where: 'media_id = ?', whereArgs: [id]);
    final attempts = ((rows.first['attempts'] as num?)?.toInt() ?? 0) + 1;
    await db.update(
        'ledger',
        {
          'attempts': attempts,
          'status': attempts > 5 ? 'error' : 'uploading',
          'last_error': attempts > 5 ? '连续失败多次，已暂停' : null,
        },
        where: 'media_id = ?',
        whereArgs: [id]);
    return attempts > 5;
  }

  Future<void> fail(String id, String message) async {
    final db = await open();
    await db.update('ledger', {'status': 'error', 'last_error': message},
        where: 'media_id = ?', whereArgs: [id]);
  }

  /// 手动重试失败项：清 attempts 回 pending
  Future<void> retryFailed() async {
    final db = await open();
    await db.update('ledger', {'status': 'pending', 'attempts': 0},
        where: 'status = ?', whereArgs: ['error']);
  }

  Future<LedgerStats> stats() async {
    final db = await open();
    final r = <String, int>{};
    int? last;
    for (final row in await db.rawQuery(
        'SELECT status, COUNT(*) AS c, MAX(uploaded_at) AS last '
        'FROM ledger GROUP BY status')) {
      r[row['status'] as String] = (row['c'] as num).toInt();
      final l = (row['last'] as num?)?.toInt();
      if (l != null && (last == null || l > last)) last = l;
    }
    return LedgerStats()
      ..done = r['done'] ?? 0
      ..pending = (r['pending'] ?? 0) + (r['uploading'] ?? 0)
      ..error = r['error'] ?? 0
      ..lastUploadedAt = last;
  }

  Future<List<LedgerRow>> recent({String? status, int limit = 300}) async {
    final db = await open();
    final rows = await db.query('ledger',
        where: status != null ? 'status = ?' : null,
        whereArgs: status != null ? [status] : null,
        orderBy: 'rowid DESC',
        limit: limit);
    return rows.map(LedgerRow.fromMap).toList();
  }
}
