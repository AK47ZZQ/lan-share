"""一次性迁移：SQLite .meta.db → PostgreSQL（v3.5 PG 切换）。

用法（项目根目录）：
    set DATABASE_URL=postgresql://lanshare:<密码>@127.0.0.1:5432/lanshare
    python scripts/migrate_sqlite_to_pg.py [--sqlite path/to/.meta.db]

行为：建表（幂等）→ 每表清空后全量重灌（保留显式 id）→ 序列拨号。
文件数据（data/ 目录）不经数据库，无需迁移。

## 列清单为什么不再手写（v3.57）

旧版给每张表抄了一份列清单，与 `store/schema.py` 形成第三轨漂移，实测后果：
`users` 少 `nickname/avatar_path/totp_rc/totp_last_step/webdav_password_hash`
（迁完昵称/头像/两步验证恢复码全丢）、`photos` 少
`duration/codec/width/height/added_at`、`notifications` 少 `broadcast_id`，
而 `todo_lists/todos/device_tokens/note_search/admin_broadcasts/announcements`
**整张表根本不在清单里**＝迁移静默丢表。这与本仓反复记录的"单测绿也可能
生产 500"是同一类根因：**手工同步的清单一定会漏。**

现在列集合在运行时向两侧库反射求交集（按源库列序），只有**表顺序**仍需手写
（外键灌入依赖它）。漏表由 `tests/test_migration_coverage.py` 兜住：源库里
出现清单外的表 → 本脚本直接 FATAL 退出，不再静默跳过。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import DB_PATH
from app.db import USE_PG, pg_setval_sequences

# 灌入顺序满足外键依赖（users 最先，引用它的随后）。**只列表名**，列在运行时反射。
TABLE_ORDER = [
    "users", "sessions", "device_tokens", "grants", "invites", "shares",
    "audit_log", "blobs", "trash", "upload_sessions", "photos", "note_search",
    "settings", "notifications", "admin_broadcasts", "announcements",
    "todo_lists", "todos", "ttl_state", "pomodoro_sessions",
]

# 有意不迁移的表：已下线功能的残留（schema 里也不再建表，存量库可能还有）
EXCLUDE = {"photo_comments", "photo_reactions", "sqlite_sequence"}


def _common_columns(src: sqlite3.Connection, pg, table: str) -> list[str]:
    """源库真实列 ∩ 目标库真实列（按源库顺序）。

    交集而非源库全集：老 SQLite 库可能有 PG 侧已下线的列，硬灌会 ProgrammingError；
    目标库多出的列留 NULL/默认值，由 `init_db` 的增量 ALTER 负责补齐。
    """
    src_cols = [r["name"] for r in src.execute(f"PRAGMA table_info({table})")]
    pg_cols = {r["column_name"] for r in pg.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ?",
        (table,)).fetchall()}
    return [c for c in src_cols if c in pg_cols]


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
    src_tables = {r["name"] for r in
                  src.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    unmigrated = sorted(src_tables - set(TABLE_ORDER) - EXCLUDE
                        - {t for t in src_tables if t.startswith("sqlite_")})
    if unmigrated:
        print(f"FATAL: 源库这些表不在迁移清单，会被静默丢弃: {unmigrated}\n"
              f"       → 加进 TABLE_ORDER（有外键依赖时注意位置）或 EXCLUDE（写明理由）")
        sys.exit(1)

    pg = _db()
    total_rows = 0
    for table in TABLE_ORDER:
        if table not in src_tables:
            print(f"  skip {table}: 源库无此表（老库未升到该版本）")
            continue
        cols = _common_columns(src, pg, table)
        if not cols:
            print(f"FATAL: {table} 两侧列集合无交集（schema 漂移？）")
            sys.exit(1)
        rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        pg.execute(f"DELETE FROM {table}")
        if rows:
            placeholders = ", ".join(["?"] * len(cols))
            pg.executemany(
                f"INSERT INTO {table}({', '.join(cols)}) VALUES({placeholders})",
                [tuple(r[c] for c in cols) for r in rows])
        total_rows += len(rows)
        print(f"  {table}: {len(rows)} 行 · {len(cols)} 列")
    pg_setval_sequences(pg)
    print(f"DONE: 共 {total_rows} 行已迁入 PostgreSQL")


if __name__ == "__main__":
    main()
