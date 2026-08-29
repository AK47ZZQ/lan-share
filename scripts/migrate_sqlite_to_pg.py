"""一次性迁移：SQLite .meta.db → PostgreSQL（v3.5 PG 切换）。

用法（项目根目录）：
    set DATABASE_URL=postgresql://lanshare:<密码>@127.0.0.1:5432/lanshare
    python scripts/migrate_sqlite_to_pg.py [--sqlite path/to/.meta.db]

行为：建表（幂等）→ 每表 TRUNCATE 后全量重灌（保留显式 id）→ 序列拨号。
文件数据（data/ 目录）不经数据库，无需迁移。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import DB_PATH  # noqa: E402
from app.db import DATABASE_URL, USE_PG, pg_setval_sequences  # noqa: E402

# 灌入顺序满足外键依赖（users 最先，引用它的随后）
TABLES = [
    ("users", ["id", "username", "password_hash", "created_at", "role", "is_active",
               "quota_bytes", "totp_secret", "totp_enabled"]),
    ("sessions", ["token_hash", "user_id", "created_at", "expires_at", "ip", "ua"]),
    ("shares", ["token", "path", "name", "size", "created_at", "expires_at",
                "password_hash", "max_downloads", "downloads", "owner_id",
                "password_plain", "target_user_id"]),
    ("audit_log", ["id", "ts", "ip", "action", "target", "detail", "username"]),
    ("blobs", ["sha256", "size", "sample_path", "created_at"]),
    ("trash", ["id", "orig_path", "trash_path", "name", "size", "deleted_at"]),
    ("upload_sessions", ["id", "dest_dir", "filename", "total_size", "sha256",
                         "created_at", "user_id"]),
    ("photos", ["path", "taken_at", "mtime_ns", "size"]),
    ("grants", ["id", "user_id", "path", "can_read", "can_write", "created_by",
                "created_at", "expires_at"]),
    ("settings", ["key", "value"]),
    ("invites", ["code", "role", "created_by", "created_at", "expires_at",
                 "used_by", "used_at"]),
    ("notifications", ["id", "user_id", "type", "title", "body", "link",
                       "created_at", "read_at"]),
]


def main():
    sqlite_path = DB_PATH
    if "--sqlite" in sys.argv:
        sqlite_path = sys.argv[sys.argv.index("--sqlite") + 1]
    if not USE_PG:
        print("FATAL: DATABASE_URL 未设置或不是 postgres:// 前缀")
        sys.exit(1)

    from app.db import init_db
    init_db()  # PG 建表（幂等）
    from app.db import _db  # init_db 之后取连接（同线程）

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    total_rows = 0
    for table, cols in TABLES:
        try:
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        except sqlite3.OperationalError as e:
            print(f"SKIP {table}: {e}（源库缺表/缺列，视为 0 行）")
            continue
        _db().execute(f"DELETE FROM {table}")
        if rows:
            placeholders = ", ".join(["?"] * len(cols))
            _db().executemany(
                f"INSERT INTO {table}({', '.join(cols)}) VALUES({placeholders})",
                [tuple(r[c] for c in cols) for r in rows])
        total_rows += len(rows)
        print(f"  {table}: {len(rows)} 行")
    pg_setval_sequences(_db())
    print(f"DONE: 共 {total_rows} 行已迁入 PostgreSQL")


if __name__ == "__main__":
    main()
