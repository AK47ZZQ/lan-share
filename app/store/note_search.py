"""note_search 表（笔记正文小写镜像全文索引）的**唯一主人**（B4 全文搜索）。

## 为什么是普通表 + LIKE 而非 FTS 虚拟表

SQLite FTS5 与 PG 全文检索的语法/分词完全不同构，双引擎要维护两套 DDL 与两套
查询；本服务是家庭规模（笔记 ≤2000 篇、正文读头 8KB），LIKE 全表扫足够快。
镜像存**小写副本**，查询参数同样小写——两引擎的 LIKE 大小写差异即被归一。

## 边界

- 本模块是该表唯一 SQL 出口；schema 在 app/store/schema.py。
- LIKE 通配符（%/_/\\）由本模块统一转义（ESCAPE '\\'，双引擎同支持），
  调用方传**原文**即可。
- 写方法自持 `_db_lock` + commit；处在 `db.transaction()` 内时推迟收束。
"""
from app import db as _dbmod
from app.db import _db, _db_lock

# ─── 读 ───────────────────────────────────────────────────


def load_index() -> list:
    """(path, mtime_ns, size) 全量轻量索引（增量比对的基线）。"""
    return _db().execute(
        "SELECT path, mtime_ns, size FROM note_search").fetchall()


def search_snippets(needle: str, limit: int, *, username: str = "",
                    is_admin: bool = False) -> list:
    """正文命中行：(path, content_lower, 位置)。needle 由调用方做好小写；
    LIKE 通配符在此统一转义。"""
    like = "%" + _escape_like(needle.lower()) + "%"
    _ = is_admin

    # 可见性条件下推（2026-11-04 审查）。
    #
    # 原来这里只 `LIMIT n`，可见性由调用方在 Python 里逐行过滤 —— 而过滤
    # 发生在**截断之后**。索引表是全库的（含各人 private/ 下的笔记），
    # 只要命中行里多数属于他人私有笔记，本人写的笔记就永远进不了那 n 行：
    # 表现是「我明明写过这个词，却搜不到」。
    #
    # 现在把「不属于我的 private 一律排除」放进 SQL，截断前就收敛集合。
    # incoming 的 admin 例外留在 Python 里判（不常见，且 admin 本来就少）。
    sql = ("SELECT path, content_lower FROM note_search "
           "WHERE content_lower LIKE ? ESCAPE '\\'"
           # tmp 是运维作业目录，任何角色都不可见（与 spaces.visible 同口）
           " AND LOWER(path) NOT LIKE 'tmp/%'")
    args: list = [like]
    if username:
        # private 只给本人 —— 注意 visible() 里对 private 不看 role，
        # admin 也看不到别人的私人笔记，这里保持一致。
        sql += " AND (LOWER(path) NOT LIKE 'private/%' OR LOWER(path) LIKE ?)"
        args.append(f"private/{_escape_like(username.lower())}/%")
    return _db().execute(sql + " LIMIT ?", (*args, limit)).fetchall()


# ─── 写 ───────────────────────────────────────────────────


def apply_scan(upserts: list[tuple], gone: list[str]) -> None:
    """增量落库（一次锁+commit）：upserts=(path, content_lower, mtime_ns, size)；
    gone=消失路径。与 photos.apply_scan 同款提交粒度。"""
    if not upserts and not gone:
        return
    with _db_lock:
        if upserts:
            _db().executemany(
                "INSERT INTO note_search(path, content_lower, mtime_ns, size) VALUES(?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET content_lower=excluded.content_lower, "
                "mtime_ns=excluded.mtime_ns, size=excluded.size", upserts)
        if gone:
            _db().executemany("DELETE FROM note_search WHERE path=?", [(g,) for g in gone])
        if not _dbmod.in_transaction():
            _db().commit()


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _write(sql: str, params: tuple) -> int:
    with _db_lock:
        cur = _db().execute(sql, params)
        n = cur.rowcount
        if not _dbmod.in_transaction():
            _db().commit()
        return n
