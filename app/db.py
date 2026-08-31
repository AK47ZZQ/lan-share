"""元数据库双引擎：SQLite（默认，开发/测试）或 PostgreSQL（DATABASE_URL，生产）。

- SQL 统一用 '?' 占位符书写；PG 适配层在执行前转为 '%s'（SQL 字符串字面量内的
  '?' 不替换——本项目 SQL 无该写法）。
- 行访问统一按 key（r["col"]）：SQLite 用 Row，PG 用 dict_row。
- 写并发：SQLite 靠 _db_lock 全局串行 + WAL；PG 用 autocommit（多语句原子序列
  已改为引擎中立的补偿写法，见 admin.py invite_redeem）。
- schema：SQLite 走 init_db + _migrate_v3 幂等增量；PG 走 _pg_schema 全量
  CREATE TABLE IF NOT EXISTS（迁移脚本灌数据）。
"""
import os
import sqlite3
import threading

from app.config import DB_PATH, SHARE_ROOT

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = DATABASE_URL.startswith("postgres")

_db_local = threading.local()
_db_lock = threading.Lock()


# ── PostgreSQL 适配层 ─────────────────────────────────────

def _pg_sql(sql: str) -> str:
    out = []
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
        if ch == "?" and not in_str:
            out.append("%s")
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

    def executescript(self, script: str):
        for stmt in script.split(";"):
            if stmt.strip():
                self.execute(stmt)

    def close(self):
        self._conn.close()


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
        webdav_password_hash TEXT
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
        size BIGINT NOT NULL
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
        read_at DOUBLE PRECISION
    );
    CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, id);
"""

_PG_SEQUENCES = ("users", "audit_log", "trash", "grants", "notifications")


def pg_setval_sequences(conn: _PgConn):
    """数据迁移灌入显式 id 后，把 serial 序列拨到 MAX(id)。"""
    for table in _PG_SEQUENCES:
        conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))")


# ── 连接与初始化 ──────────────────────────────────────────

def _db():
    """每线程独立连接。"""
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        if USE_PG:
            conn = _PgConn(DATABASE_URL)
        else:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
        _db_local.conn = conn
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
            size INTEGER NOT NULL
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
        ucols = {r["name"] for r in conn.execute("PRAGMA table_info(upload_sessions)").fetchall()}
        if "user_id" not in ucols:
            conn.execute("ALTER TABLE upload_sessions ADD COLUMN user_id INTEGER")
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(shares)").fetchall()}
        if "owner_id" not in scols:
            conn.execute("ALTER TABLE shares ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        if "password_plain" not in scols:
            conn.execute("ALTER TABLE shares ADD COLUMN password_plain TEXT")
        if "target_user_id" not in scols:
            conn.execute("ALTER TABLE shares ADD COLUMN target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        acols = {r["name"] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
        if "username" not in acols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN username TEXT NOT NULL DEFAULT ''")
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
                read_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, id);
        """)
        conn.execute("UPDATE users SET role='admin' WHERE id=(SELECT MIN(id) FROM users)")
        conn.commit()
