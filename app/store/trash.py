"""trash 表的**唯一主人**（B1 存储层拆分）。

## 边界

- 本模块是该表**唯一**的 SQL 出口。物理文件的删除/恢复（shutil）与逐条可见性
  判定是业务语义，留在 API 层；本模块只管回收站索引行。
- schema 归 `app/store/schema.py`（DDL）。
- 写方法自持 `_db_lock` + commit；处在 `db.transaction()` 内时推迟收束。
"""
from app import db as _dbmod
from app.db import _db, _db_lock

# ─── 读 ───────────────────────────────────────────────────


def list_all() -> list:
    return _db().execute("SELECT * FROM trash ORDER BY deleted_at DESC").fetchall()


def get(trash_id: int):
    return _db().execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()


def overview_stats() -> tuple[int, int]:
    """(条数, 总字节)（管理端概览统计）。"""
    total = _db().execute("SELECT COUNT(*) AS v FROM trash").fetchone()["v"]
    size = _db().execute("SELECT COALESCE(SUM(size), 0) AS v FROM trash").fetchone()["v"]
    return total, size


# ─── 写 ───────────────────────────────────────────────────


def drop(trash_id: int) -> int:
    """单条物理删除后摘行（行随文件走，先删文件再摘行的顺序由调用方保证）。"""
    return _write("DELETE FROM trash WHERE id=?", (trash_id,))


def clear() -> int:
    """清空回收站索引（物理 rmtree 由调用方先做）。"""
    return _write("DELETE FROM trash", ())


def delete_before(cutoff: float) -> int:
    """清理循环：超期条目摘行（物理文件由调用方 rmtree）。"""
    return _write("DELETE FROM trash WHERE deleted_at < ?", (cutoff,))


def _write(sql: str, params: tuple) -> int:
    with _db_lock:
        cur = _db().execute(sql, params)
        n = cur.rowcount
        if not _dbmod.in_transaction():
            _db().commit()
        return n
