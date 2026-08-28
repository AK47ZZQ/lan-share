"""SQLite 元数据库：每线程独立连接 + 全局写锁。"""
import sqlite3
import threading

from app.config import DB_PATH, SHARE_ROOT

_db_local = threading.local()
_db_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    """每线程独立连接（WAL 支持多读并发）；写操作仍由 _db_lock 全局串行，避免共享
    连接的跨线程游标交错问题。"""
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        _db_local.conn = conn
    return conn


def init_db():
    SHARE_ROOT.mkdir(parents=True, exist_ok=True)
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


