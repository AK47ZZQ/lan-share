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
  int donePhotos = 0;
  int doneVideos = 0;
  int pendingPhotos = 0; // pending/uploading
  int pendingVideos = 0;
  int errorPhotos = 0;
  int errorVideos = 0;
  int excludedPending = 0; // 已排除相册中的未完成项（不参与备份，单列免得数字对不上）
  int doneBytes = 0; // 已备份内容的逻辑字节和（0.6.0 统计聚合展示）
  int? lastUploadedAt; // 秒

  int get done => donePhotos + doneVideos;
  int get pending => pendingPhotos + pendingVideos;
  int get error => errorPhotos + errorVideos;
}

/// 纯聚合：GROUP BY status,kind 的行 + 排除相册的未完成行 → 统计。
/// 抽出来是为了单测不用拉真 SQLite（sqflite 单测要 ffi 依赖）。
LedgerStats statsFromGroups(
    List<Map<Object?, Object?>> groups, List<Map<Object?, Object?>> excluded) {
  final s = LedgerStats();
  int? last;
  void add(int kindIsVideo, String status, int c,
      {int? bytes, int? uploadedAt}) {
    if (uploadedAt != null) {
      final l = last;
      if (l == null || uploadedAt > l) last = uploadedAt;
    }
    if (status == 'done') {
      kindIsVideo == 1 ? s.doneVideos += c : s.donePhotos += c;
      if (bytes != null) s.doneBytes += bytes;
    } else if (status == 'error') {
      kindIsVideo == 1 ? s.errorVideos += c : s.errorPhotos += c;
    } else {
      // pending/uploading 都算待备
      kindIsVideo == 1 ? s.pendingVideos += c : s.pendingPhotos += c;
    }
  }

  for (final r in groups) {
    add((r['kind'] as String?) == 'video' ? 1 : 0, r['status'] as String,
        (r['c'] as num).toInt(),
        bytes: (r['bytes'] as num?)?.toInt(),
        uploadedAt: (r['last'] as num?)?.toInt());
  }
  for (final r in excluded) {
    final c = (r['c'] as num).toInt();
    s.excludedPending += c;
    // 被排除相册的未完成项从待备/失败里扣掉：它们永远不会被备份，
    // 留在数字里用户永远等不到（失败红字也永远消不掉）
    add((r['kind'] as String?) == 'video' ? 1 : 0, r['status'] as String, -c);
  }
  s.lastUploadedAt = last;
  return s;
}

class BackupLedger {
  BackupLedger._();
  static final BackupLedger I = BackupLedger._();

  Database? _db;

  Future<Database> open() async {
    var db = _db;
    // 后台引擎 isolate 与主 isolate 共享 sqflite 的进程级连接；引擎侧一旦
    // 关闭（历史版本的 finally close()/引擎销毁牵连），主 isolate 缓存的
    // 句柄就变成死句柄，之后所有查询都抛 database_closed——统计卡冻结、
    // 账本页永远转圈（0.7.1 实测复现）。发现死句柄立即弃掉重开。
    if (db != null && !db.isOpen) {
      _db = null;
      db = null;
    }
    if (db != null) return db;
    return _db = await openDatabase('backup_ledger.db',
        version: 2,
        onCreate: _create,
        onUpgrade: (db, oldV, newV) async {
      // v1→v2：加 bucket 列（0.7.0 按相册分目录）。存量行 bucket=''
      // 与扫描值必不相等 → 对账视为变更全部重备一次；sha256 已在账本，
      // init 秒传零流量，只在新落位建引用。
      if (oldV < 2) {
        // 主 isolate（设置页 2s 统计轮询）与后台引擎 isolate 会并发打开
        // 同一个库，两边都看到 v1 时后到的 ALTER 撞 duplicate column，
        // 异常把 open() 打挂 → 统计卡假死/账本页永远转圈。幂等化：
        // 先查列再补，真撞上了也只吞 duplicate column 这一种错。
        final cols = await db.rawQuery('PRAGMA table_info(ledger)');
        if (cols.any((c) => c['name'] == 'bucket')) return;
        try {
          await db.execute(
              "ALTER TABLE ledger ADD COLUMN bucket TEXT NOT NULL DEFAULT ''");
        } on Exception catch (e) {
          if (!e.toString().contains('duplicate column')) rethrow;
        }
      }
    });
  }

  /// 注意：不要在后台 isolate 引擎结束时调用 close()——sqflite 的连接是
  /// 进程级共享的，关掉会把主 isolate（统计卡/账本页）一起打死；引擎侧
  /// 资源回收交给 BackupDartRunner 的 engine.destroy()。

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

  /// 统计（0.7.1 起分照片/视频，且感知排除相册）：排除只影响扫描阶段、
  /// 账本行不动，所以被排除相册的 pending/error 行会永远留在库里——
  /// 不从统计里扣掉，"待备份/失败"就永远等不到归零，状态卡看起来像坏了。
  Future<LedgerStats> stats({List<String> excluded = const []}) async {
    final db = await open();
    final groups = await db.rawQuery(
        'SELECT status, kind, COUNT(*) AS c, SUM(size) AS bytes, '
        'MAX(uploaded_at) AS last '
        'FROM ledger GROUP BY status, kind');
    List<Map<Object?, Object?>> excludedGroups = const [];
    final names = excluded.where((e) => e.isNotEmpty).take(500).toList();
    if (names.isNotEmpty) {
      final marks = List.filled(names.length, '?').join(',');
      excludedGroups = await db.rawQuery(
          'SELECT status, kind, COUNT(*) AS c FROM ledger '
          "WHERE status != 'done' AND bucket IN ($marks) GROUP BY status, kind",
          names);
    }
    return statsFromGroups(groups, excludedGroups);
  }

  Future<List<LedgerRow>> recent(
      {String? status, String? kind, int limit = 300}) async {
    final db = await open();
    final conds = <String>[];
    final args = <Object?>[];
    if (status != null) {
      conds.add('status = ?');
      args.add(status);
    }
    if (kind != null) {
      conds.add('kind = ?');
      args.add(kind);
    }
    final rows = await db.query('ledger',
        where: conds.isEmpty ? null : conds.join(' AND '),
        whereArgs: args.isEmpty ? null : args,
        orderBy: 'rowid DESC',
        limit: limit);
    return rows.map(LedgerRow.fromMap).toList();
  }
}
