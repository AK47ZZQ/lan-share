// 备份账本（FR-2 去重 + APP-DESIGN §4 状态机）：单表 SQLite。
// 身份 = MediaStore 媒体库 ID（跨重启稳定）；(id, mtime, size) 变了视为文件被
// 编辑过 → 重新备份。状态机：pending → uploading → done | error；
// error 且 attempts≤5 下轮自动重试，attempts>5 = 暂停（账本页"重试失败项"
// 手动复活），否则暂停态会被 reconcile 反复重入，attempts 永远压不住。
// 单向备份语义：相册里删掉的照片账本原样保留（网盘不随相册删除）。
import 'package:sqflite/sqflite.dart';

class LedgerRow {
  final String mediaId; // "img_123" / "vid_456"
  final String kind; // image | video
  final String name;
  final int size;
  final int mtime; // MediaStore DATE_MODIFIED（秒）
  final String bucket; // 相册名（0.7.0：落位按相册分目录，变更触发重备）
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
    this.bucket = '',
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
        'bucket': bucket,
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
        bucket: m['bucket'] as String? ?? '',
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
  int doneBytes = 0; // 已备份内容的逻辑字节和（0.6.0 统计聚合展示）
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
        version: 2,
        onCreate: _create,
        onUpgrade: (db, oldV, newV) async {
      // v1→v2：加 bucket 列（0.7.0 按相册分目录）。存量行 bucket=''
      // 与扫描值必不相等 → 对账视为变更全部重备一次；sha256 已在账本，
      // init 秒传零流量，只在新落位建引用。
      if (oldV < 2) {
        await db.execute(
            "ALTER TABLE ledger ADD COLUMN bucket TEXT NOT NULL DEFAULT ''");
      }
    });
  }

  /// 后台 isolate 引擎跑完一轮后释放句柄（isolate 随引擎销毁，这里是兜底）
  Future<void> close() async {
    final db = _db;
    _db = null;
    await db?.close();
  }

  Future<void> _create(Database db, int v) async {
    await db.execute('''
      CREATE TABLE ledger(
        media_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime INTEGER NOT NULL,
        bucket TEXT NOT NULL DEFAULT '',
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
    // 两段查询控内存：done 行只取对账所需 4 列（大头），全行仅装未完成积压
    // （正常是小集合），10 万张相册不至于全量 12 列进 Dart 堆
    for (final r in await db.query('ledger',
        columns: ['media_id', 'mtime', 'size', 'sha256', 'bucket'],
        where: 'status = ?',
        whereArgs: ['done'])) {
      known[r['media_id'] as String] = LedgerRow(
          mediaId: r['media_id'] as String,
          kind: '',
          name: '',
          size: (r['size'] as num).toInt(),
          mtime: (r['mtime'] as num).toInt(),
          bucket: r['bucket'] as String? ?? '',
          status: 'done',
          sha256: r['sha256'] as String?);
    }
    for (final r in await db
        .query('ledger', where: 'status != ?', whereArgs: ['done'])) {
      final row = LedgerRow.fromMap(r);
      known[row.mediaId] = row;
    }
    final pending = <LedgerRow>[];
    final batch = db.batch();
    for (final s in scanned) {
      final row = known[s.mediaId];
      if (row == null) {
        // replace：与主 isolate 的手动重试/统计写并发时主键冲突不该炸整轮
        batch.insert('ledger', s.toMap(),
            conflictAlgorithm: ConflictAlgorithm.replace);
        pending.add(s);
      } else if (row.mtime != s.mtime ||
          row.size != s.size ||
          row.bucket != s.bucket) {
        // 文件被编辑（mtime/size）或被移动到别的相册（bucket，0.7.0 起
        // 落位跟随相册）→ 重置后重传；服务端旧副本保留（单向备份语义），
        // sha256 沿用 → 秒传免流量，只在目标目录建引用
        final reset = LedgerRow(
            mediaId: s.mediaId,
            kind: s.kind,
            name: s.name,
            size: s.size,
            mtime: s.mtime,
            bucket: s.bucket,
            sha256: row.sha256);
        batch.update('ledger', reset.toMap(),
            where: 'media_id = ?', whereArgs: [s.mediaId]);
        pending.add(reset);
      } else if (row.status != 'done' && row.attempts <= 5) {
        pending.add(row); // pending/uploading/error 且未暂停 → 自动重试
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

  /// 失败：累加 attempts（4xx 判死 / IO 异常 / 5xx 退避耗尽都算一次失败）。
  /// attempts 超 5 → error 暂停（reconcile 不再自动纳入，等手动重试），
  /// 否则留 error 下轮自动重试。message 源自服务端 detail/本地异常，落库前
  /// 清控制符防折行伪装（UI 纯文本渲染，无注入面）
  Future<void> fail(String id, String message) async {
    final db = await open();
    final rows = await db.query('ledger',
        columns: ['attempts'], where: 'media_id = ?', whereArgs: [id]);
    final attempts = ((rows.first['attempts'] as num?)?.toInt() ?? 0) + 1;
    final msg = message.replaceAll(RegExp(r'[\x00-\x1f\x7f]'), ' ');
    await db.update(
        'ledger',
        {
          'attempts': attempts,
          'status': 'error',
          'last_error': attempts > 5 ? '$msg（连续失败多次，已暂停）' : msg,
        },
        where: 'media_id = ?',
        whereArgs: [id]);
  }

  /// 手动重试失败项：清 attempts 回 pending
  Future<void> retryFailed() async {
    final db = await open();
    await db.update('ledger', {'status': 'pending', 'attempts': 0},
        where: 'status = ?', whereArgs: ['error']);
  }

  /// 换账号登录时清空账本："已备份"语义按账号隔离（media_id 是设备本地
  /// MediaStore id，跨账号无意义）。服务端已传文件不删，重对账后靠 sha256
  /// 秒传免网络重传，只花一次本地哈希。
  Future<void> clearAll() async {
    final db = await open();
    await db.delete('ledger');
  }

  Future<LedgerStats> stats() async {
    final db = await open();
    final r = <String, int>{};
    int? last;
    int doneBytes = 0;
    for (final row in await db.rawQuery(
        'SELECT status, COUNT(*) AS c, SUM(size) AS bytes, '
        'MAX(uploaded_at) AS last '
        'FROM ledger GROUP BY status')) {
      r[row['status'] as String] = (row['c'] as num).toInt();
      final l = (row['last'] as num?)?.toInt();
      if (l != null && (last == null || l > last)) last = l;
      if (row['status'] == 'done') {
        doneBytes = (row['bytes'] as num?)?.toInt() ?? 0;
      }
    }
    return LedgerStats()
      ..done = r['done'] ?? 0
      ..pending = (r['pending'] ?? 0) + (r['uploading'] ?? 0)
      ..error = r['error'] ?? 0
      ..doneBytes = doneBytes
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
