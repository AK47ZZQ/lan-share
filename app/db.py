"""元数据库双引擎：SQLite（默认，开发/测试）或 PostgreSQL（DATABASE_URL，生产）。

- SQL 统一用 '?' 占位符书写；PG 适配层在执行前转为 '%s'（SQL 字符串字面量内的
  '?' 不替换——本项目 SQL 无该写法）。SQL 字面量里的 '%'（LIKE 模式等）会
  统一翻倍成 '%%'——psycopg 把裸 '%' 当占位符前缀，不翻倍直接 ProgrammingError。
- 行访问统一按 key（r["col"]）：SQLite 用 Row，PG 用 dict_row。
- 写并发：SQLite 靠 _db_lock 全局串行 + WAL；PG 用 autocommit（多语句原子序列
  已改为引擎中立的补偿写法，见 admin.py invite_redeem）。
- schema：SQLite 走 init_db + _migrate_v3 幂等增量；PG 走 _pg_schema 全量
  CREATE TABLE IF NOT EXISTS（迁移脚本灌数据）。
"""
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
import atexit

from app.config import DB_PATH, SHARE_ROOT

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = DATABASE_URL.startswith("postgres")

_db_local = threading.local()
_txn_depth = threading.local()   # transaction() 嵌套深度（决定由谁 commit）
# 元数据库写锁。**RLock 而非 Lock**（11 号复审批次 2，A1）：本锁在写路径上被
# 广泛嵌套持有（调用方 `with _db_lock:` 临界区里再调用仓储方法/助手函数），
# 不可重入会自死锁——项目已被咬过一次（R1-P0-1：锁内调用 audit() 内部再取
# 本锁）。同一线程重入是无害的（所有 DB 访问都在单线程内完成，无跨线程移交），
# 故改为可重入锁，把"小心别嵌套"的隐式纪律变成结构性安全。
_db_lock = threading.RLock()


# ── PostgreSQL 适配层 ─────────────────────────────────────

def _pg_sql(sql: str) -> str:
    """SQLite 风格 '?' 占位符 → psycopg 的 '%s'，并把字面 '%' 翻倍。

    契约（tests/test_pgsql_adapter.py 钉死）：
    - 字符串字面量内的 '?' **不**替换（单引号开关状态机，'' 转义按 PG 语义处理）；
    - 所有 '%' 一律翻倍——psycopg 的 %-格式化作用于整条 SQL，字面量内也要翻；
    - 调用方**不得**预先转义 '%'（'%%' 会被再翻倍成 '%%%%'）。

    不支持的语法直接**响亮失败**而非静默错改（11 号复审 P1-1）：美元引号
    `$$...$$` 里的 '?' 会被错误替换而不报错。此前该风险只写在注释里，抓不到。
    """
    if "$$" in sql:
        raise ValueError("_pg_sql 不支持美元引号（$$...$$）：请改用参数占位符")
    out = []
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
        if ch == "?" and not in_str:
            out.append("%s")
        elif ch == "%":
            # psycopg 把裸 % 当占位符前缀（only '%s','%b','%t' are allowed），
            # LIKE 模式里的 'private/%' 会让整条 SQL 炸成 ProgrammingError——
            # 字面 % 一律翻倍（R1-P2-10 照片下推 SQL 生产 500 的根因）
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    def __iter__(self):
        return iter(self._cur.fetchall())


class _PgConn:
    """psycopg 连接包装：autocommit（commit/rollback 为兼容 no-op）。"""

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        self._conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(_pg_sql(sql), tuple(params))
        return _PgCursor(cur)

    def executemany(self, sql, seq):
        with self._conn.cursor() as cur:
            cur.executemany(_pg_sql(sql), [tuple(p) for p in seq])

    def commit(self):
        pass  # autocommit

    def rollback(self):
        pass

    @contextmanager
    def transaction(self):
        """真事务块（11 号复审 P2-6），**支持嵌套**。

        本项目默认 autocommit=True，多语句操作靠"补偿写法"（先做、失败再撤），
        中途进程崩溃会留下孤儿数据。关键多语句序列改用本上下文后可获得原子性：
        块内抛异常 = 整体回滚。

        嵌套语义：psycopg 的 `conn.transaction()` 在已处于事务内时会自动改用
        **savepoint**——内层回滚不影响外层。因此 autocommit 的切换**只在最外层**
        做（按连接当前状态判断，不依赖计数器）：内层若再切 autocommit，psycopg
        会抛 "can't change 'autocommit' now"（PG 全量实测踩到）。
        """
        prev = self._conn.autocommit
        if prev:
            self._conn.autocommit = False
        try:
            with self._conn.transaction():
                yield self
        finally:
            if prev:
                self._conn.autocommit = prev

    def executescript(self, script: str):
        for stmt in script.split(";"):
            if stmt.strip():
                self.execute(stmt)

    def close(self):
        self._conn.close()


@contextmanager
def transaction():
    """多语句原子块（引擎中立）。

    - PG：委托 `_PgConn.transaction()`，真回滚；
    - SQLite：调用方须已持 `_db_lock`（项目既有写约定），此处只负责收束
      commit / rollback。

    用法：`with transaction(): ...` —— 与 `with _db_lock:` 配合（嵌套安全，
    `_db_lock` 已是 RLock）。

    嵌套感知：事务内的写入方（含 store 仓储的 `_write`）应查 `in_transaction()`
    决定是否自行 commit——**只有最外层收束**，否则内层 commit 会把事务切断，
    原子性丢失。见 `app/store/users.py::_write`。
    """
    conn = _db()
    _txn_depth.depth = getattr(_txn_depth, "depth", 0) + 1
    try:
        if USE_PG:
            with conn.transaction():
                yield conn
            return
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        _txn_depth.depth -= 1


def in_transaction() -> bool:
    """当前线程是否正处在 `transaction()` 块内（决定写入方是否自行 commit）。"""
    return getattr(_txn_depth, "depth", 0) > 0


_PG_SCHEMA = """
    CREATE TABLE IF NOT EXISTS users(
        id BIGSERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        is_active INTEGER NOT NULL DEFAULT 1,
        quota_bytes BIGINT,
        totp_secret TEXT,
        totp_enabled INTEGER NOT NULL DEFAULT 0,
        webdav_password_hash TEXT,
        totp_rc TEXT,
        totp_last_step BIGINT,
        nickname TEXT NOT NULL DEFAULT '',
        avatar_path TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS sessions(
        token_hash TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL,
        ip TEXT, ua TEXT
    );
    CREATE TABLE IF NOT EXISTS shares(
        token TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        name TEXT NOT NULL,
        size BIGINT,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL,
        password_hash TEXT,
        max_downloads INTEGER,
        downloads INTEGER NOT NULL DEFAULT 0,
        owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
        password_plain TEXT,
        target_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS audit_log(
        id BIGSERIAL PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        ip TEXT,
        action TEXT NOT NULL,
        target TEXT,
        detail TEXT,
        username TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS blobs(
        sha256 TEXT PRIMARY KEY,
        size BIGINT NOT NULL,
        sample_path TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL
    );
    CREATE TABLE IF NOT EXISTS trash(
        id BIGSERIAL PRIMARY KEY,
        orig_path TEXT NOT NULL,
        trash_path TEXT NOT NULL,
        name TEXT NOT NULL,
        size BIGINT,
        deleted_at DOUBLE PRECISION NOT NULL
    );
    CREATE TABLE IF NOT EXISTS upload_sessions(
        id TEXT PRIMARY KEY,
        dest_dir TEXT NOT NULL,
        filename TEXT NOT NULL,
        total_size BIGINT NOT NULL,
        sha256 TEXT,
        created_at DOUBLE PRECISION NOT NULL,
        user_id BIGINT
    );
    CREATE TABLE IF NOT EXISTS photos(
        path TEXT PRIMARY KEY,
        taken_at TEXT NOT NULL,
        mtime_ns BIGINT NOT NULL,
        size BIGINT NOT NULL,
        duration DOUBLE PRECISION,
        codec TEXT,
        width INTEGER,
        height INTEGER,
        added_at DOUBLE PRECISION
    );
    CREATE TABLE IF NOT EXISTS grants(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        can_read INTEGER NOT NULL DEFAULT 1,
        can_write INTEGER NOT NULL DEFAULT 0,
        created_by BIGINT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION,
        UNIQUE(user_id, path)
    );
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS invites(
        code TEXT PRIMARY KEY,
        role TEXT NOT NULL DEFAULT 'member',
        created_by BIGINT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL,
        used_by BIGINT,
        used_at DOUBLE PRECISION
    );
    CREATE TABLE IF NOT EXISTS notifications(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT,
        link TEXT,
        created_at DOUBLE PRECISION NOT NULL,
        read_at DOUBLE PRECISION,
        broadcast_id TEXT
    );
    CREATE TABLE IF NOT EXISTS admin_broadcasts(
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        body TEXT,
        targets TEXT NOT NULL DEFAULT '[]',
        created_by TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'sent',
        scheduled_at DOUBLE PRECISION,
        sent_at DOUBLE PRECISION,
        withdrawn_at DOUBLE PRECISION,
        created_at DOUBLE PRECISION NOT NULL
    );
    CREATE TABLE IF NOT EXISTS device_tokens(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at DOUBLE PRECISION NOT NULL,
        last_seen DOUBLE PRECISION,
        expires_at DOUBLE PRECISION NOT NULL
    );
    CREATE TABLE IF NOT EXISTS announcements(
        id BIGSERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        body TEXT,
        created_by TEXT NOT NULL DEFAULT '',
        created_at DOUBLE PRECISION NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, id);
    CREATE INDEX IF NOT EXISTS idx_device_tokens_user ON device_tokens(user_id, id);
"""

# 照片评论/表情回应（photo_comments / photo_reactions）已按改进建议
# 9.6-P2-13 下线：不再建表（存量部署中的旧表与数据保留不动，只是没有
# 代码再读写）。若从 SQLite 迁移到 PG，这两张表也不会再出现在目标库。
# 注意：不再使用，勿把两表加回 _PG_SEQUENCES。

_PG_SEQUENCES = ("users", "audit_log", "trash", "grants", "notifications", "device_tokens",
                 "announcements")


def pg_setval_sequences(conn: _PgConn):
    """数据迁移灌入显式 id 后，把 serial 序列拨到 MAX(id)。"""
    for table in _PG_SEQUENCES:
        conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))")


# ── 连接与初始化 ──────────────────────────────────────────

def is_unique_violation(e: Exception) -> bool:
    """引擎中立地识别唯一约束冲突（sqlite3.IntegrityError / psycopg UniqueViolation）。
    并发竞态兜底场景只允许吞这一种异常，其它 DB 故障必须上抛。"""
    if isinstance(e, sqlite3.IntegrityError):
        return True
    return type(e).__name__ == "UniqueViolation"


# ── PG 连接登记与有界化（11 号复审批次 3，P2-1）──────────────
# 原 `_db()` 每线程一条连接、**从不主动关闭**：线程池上限 40 时最坏常驻 40 条
# PG 后端连接（每条在 PG 侧占内存），且 PG 默认 max_connections=100，再叠加
# pg_dump / 迁移脚本就可能撞顶。这里做**有界化**（不改任何调用方）：
#   · 全局登记活跃连接，超过上限时回收最久未用的空闲连接；
#   · 惰性清扫：顺带关掉"线程已死但连接还挂着"的孤儿连接；
#   · 进程退出时统一关闭（atexit）。
# 连接的生命周期语义不变（仍是每线程一条、断线自愈）。
_PG_MAX_CONN = int(os.environ.get("SHARE_PG_MAX_CONN", "16"))
_PG_IDLE_TTL = 300.0          # 秒：超过则视为空闲可回收
_pg_registry: list = []       # [(conn, thread_id, last_used_monotonic)]
_pg_registry_mu = threading.Lock()


def _reap_pg_conns(force_close_oldest: bool = False) -> None:
    """关闭空闲超时 / 线程已死的 PG 连接；force 时再关一条最久未用的。"""
    now = time.monotonic()
    with _pg_registry_mu:
        keep = []
        for conn, tid, used in _pg_registry:
            dead_thread = not _thread_alive(tid)
            if (now - used > _PG_IDLE_TTL) or dead_thread:
                try:
                    conn.close()
                except Exception:
                    pass
                if getattr(_db_local, "conn", None) is conn:
                    _db_local.conn = None
                continue
            keep.append((conn, tid, used))
        if force_close_oldest and len(keep) >= _PG_MAX_CONN:
            keep.sort(key=lambda x: x[2])
            conn, _, _ = keep.pop(0)
            try:
                conn.close()
            except Exception:
                pass
            if getattr(_db_local, "conn", None) is conn:
                _db_local.conn = None
        _pg_registry[:] = keep


def _thread_alive(tid: int) -> bool:
    for t in threading.enumerate():
        if t.ident == tid:
            return True
    return False


def close_all_pg_connections() -> None:
    """进程退出 / 测试收尾时统一关闭全部 PG 连接。"""
    with _pg_registry_mu:
        for conn, _, _ in _pg_registry:
            try:
                conn.close()
            except Exception:
                pass
        _pg_registry.clear()


atexit.register(close_all_pg_connections)


def _db():
    """每线程独立连接。PG 断线自愈：连接池里的缓存连接在 PG 重启/网络闪断后
    会变死连接，超过 30s 未用时先廉价 ping 一次，失败则重建，避免全站持续 500。"""
    conn = getattr(_db_local, "conn", None)
    if conn is not None and USE_PG:
        now = time.monotonic()
        if now - getattr(_db_local, "last_ok", 0.0) > 30:
            try:
                conn.execute("SELECT 1")
                _db_local.last_ok = now
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                _db_local.conn = None
    if conn is None:
        if USE_PG:
            _reap_pg_conns()
            conn = _PgConn(DATABASE_URL)
        else:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
        _db_local.conn = conn
        if USE_PG:
            _db_local.last_ok = time.monotonic()
            with _pg_registry_mu:
                _pg_registry.append((conn, threading.get_ident(), time.monotonic()))
    elif USE_PG:
        now = time.monotonic()
        with _pg_registry_mu:
            for i, (c, tid, _) in enumerate(_pg_registry):
                if c is conn:
                    _pg_registry[i] = (c, tid, now)
                    break
    return conn


def _backfill_audit_usernames():
    """存量审计回填：username 为空且 target 以 incoming/<u>/、private/<u>/ 开头的行，
    从路径反推操作者。幂等（只补空行）；无法反推的保持空串（前端显示"链接访客/
    早期记录"）。"""
    with _db_lock:
        conn = _db()
        rows = conn.execute(
            "SELECT id, target FROM audit_log WHERE username=''").fetchall()
        updates = []
        for r in rows:
            parts = str(r["target"] or "").split("/")
            if len(parts) >= 2 and parts[0] in ("incoming", "private") and parts[1]:
                updates.append((parts[1], r["id"]))
        if updates:
            for u, rid in updates:
                conn.execute("UPDATE audit_log SET username=? WHERE id=? AND username=''", (u, rid))
            if not USE_PG:
                conn.commit()


def init_db():
    SHARE_ROOT.mkdir(parents=True, exist_ok=True)
    if USE_PG:
        _db().executescript(_PG_SCHEMA)
        # CREATE IF NOT EXISTS 不会给存量表补列：PG 生产库逐列显式增量（幂等）
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS webdav_password_hash TEXT")
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_rc TEXT")
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_last_step BIGINT")
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT NOT NULL DEFAULT ''")
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_path TEXT NOT NULL DEFAULT ''")
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS duration DOUBLE PRECISION")
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS codec TEXT")
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS width INTEGER")
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS height INTEGER")
        # v0.20：入库时间（二维码/手机上传按拍摄时间落历史月份，"没更新"
        # 的感知问题）——扫描首次见到该文件时打点，按入库排序用
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS added_at DOUBLE PRECISION")
        # v0.21：通知管理——广播组 id（撤回/发送记录按组操作）
        _db().execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS broadcast_id TEXT")
        # 提取码明文停存（R1-P2-5 + R2-P2）：PG 分支同样清洗存量值（幂等）
        _db().execute("UPDATE shares SET password_plain=NULL WHERE password_plain IS NOT NULL")
        _backfill_audit_usernames()
        return
    conn = _db()
    conn.execute("PRAGMA journal_mode=WAL")
    _db().executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            ip TEXT, ua TEXT
        );
        CREATE TABLE IF NOT EXISTS shares(
            token TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            size INTEGER,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            password_hash TEXT,
            max_downloads INTEGER,
            downloads INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            ip TEXT,
            action TEXT NOT NULL,
            target TEXT,
            detail TEXT
        );
        CREATE TABLE IF NOT EXISTS blobs(
            sha256 TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            sample_path TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trash(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orig_path TEXT NOT NULL,
            trash_path TEXT NOT NULL,
            name TEXT NOT NULL,
            size INTEGER,
            deleted_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS upload_sessions(
            id TEXT PRIMARY KEY,
            dest_dir TEXT NOT NULL,
            filename TEXT NOT NULL,
            total_size INTEGER NOT NULL,
            sha256 TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS photos(
            path TEXT PRIMARY KEY,
            taken_at TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            duration REAL,
            codec TEXT,
            width INTEGER,
            height INTEGER,
            added_at REAL
        );
    """)
    _db().commit()
    _migrate_v3()


def _migrate_v3():
    """v3 多用户 schema 增量迁移（幂等；仅 SQLite——PG 全量建表无此需求）：
    角色/激活/配额 + 授权表 + 邀请表 + 通知表 + 定向分享/两步验证列。
    首个（最小 id）用户自动晋升 admin，兼容 v2 单管理员存量。"""
    with _db_lock:
        conn = _db()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "role" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
        if "is_active" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "quota_bytes" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN quota_bytes INTEGER")
        if "totp_secret" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
        if "totp_enabled" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")
        if "webdav_password_hash" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN webdav_password_hash TEXT")
        if "totp_rc" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_rc TEXT")
        if "totp_last_step" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_last_step INTEGER")
        if "nickname" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
        if "avatar_path" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT NOT NULL DEFAULT ''")
        ucols = {r["name"] for r in conn.execute("PRAGMA table_info(upload_sessions)").fetchall()}
        if "user_id" not in ucols:
            conn.execute("ALTER TABLE upload_sessions ADD COLUMN user_id INTEGER")
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(shares)").fetchall()}
        if "owner_id" not in scols:
            conn.execute("ALTER TABLE shares ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        if "password_plain" not in scols:
            conn.execute("ALTER TABLE shares ADD COLUMN password_plain TEXT")
        # 提取码明文停存（R1-P2-5 + R2-P2）：一次性清洗存量值——列保留是为了
        # 兼容旧 schema 迁移路径，但值不再留存（一次 dump 全泄露的根源）
        conn.execute("UPDATE shares SET password_plain=NULL WHERE password_plain IS NOT NULL")
        if "target_user_id" not in scols:
            conn.execute("ALTER TABLE shares ADD COLUMN target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        acols = {r["name"] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
        if "username" not in acols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN username TEXT NOT NULL DEFAULT ''")
        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
        if "duration" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN duration REAL")
        if "codec" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN codec TEXT")
        if "width" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN width INTEGER")
        if "height" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN height INTEGER")
        if "added_at" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN added_at REAL")
        # v0.21：通知广播组 id（改进建议 9.6-P2-14，撤回/发送记录按组操作）
        ncols = {r["name"] for r in conn.execute("PRAGMA table_info(notifications)").fetchall()}
        if ncols and "broadcast_id" not in ncols:
            conn.execute("ALTER TABLE notifications ADD COLUMN broadcast_id TEXT")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS grants(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                can_read INTEGER NOT NULL DEFAULT 1,
                can_write INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL,
                UNIQUE(user_id, path)
            );
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS invites(
                code TEXT PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'member',
                created_by INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used_by INTEGER,
                used_at REAL
            );
            CREATE TABLE IF NOT EXISTS notifications(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                link TEXT,
                created_at REAL NOT NULL,
                read_at REAL,
                broadcast_id TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_broadcasts(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT,
                targets TEXT NOT NULL DEFAULT '[]',
                created_by TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                scheduled_at REAL,
                sent_at REAL,
                withdrawn_at REAL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS device_tokens(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                last_seen REAL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS announcements(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT,
                created_by TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, id);
            CREATE INDEX IF NOT EXISTS idx_device_tokens_user ON device_tokens(user_id, id);
            -- 照片评论/表情回应两表（photo_comments/photo_reactions）已按
            -- 改进建议 9.6-P2-13 下线，不再建表；存量库中的旧表保留不删
        """)
        # 仅当库内已无 admin 时才晋升（R1-P2-1）：此前每次启动无条件把最小 id
        # 用户提为 admin，管理员被降级/删号后重启会"复活"成 admin
        conn.execute("UPDATE users SET role='admin' WHERE id=(SELECT MIN(id) FROM users) "
                     "AND NOT EXISTS(SELECT 1 FROM users WHERE role='admin')")
        conn.commit()
