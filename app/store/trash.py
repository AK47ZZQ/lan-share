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


def take(trash_id: int):
    """**原子认领**一条回收站记录：同一 id 并发下只有一个调用者能拿到行。

    单语句 `DELETE ... RETURNING`（PG 与 SQLite≥3.35 均语句级原子），与
    `ttl_state.take` / `shares.consume_download` 同一套已验证原语。拿不到行
    就是别人已经取走 ⇒ 调用方返回 404，不得再碰那个文件。
    修复的缺陷（D5）：此前"先 SELECT 判存在、后 DELETE"是 check-then-act，
    两路并发恢复同一 id 会双双成功，同一份内容落出多份副本
    （实测 10 轮里 2 轮出现 3 个赢家）。
    """
    with _db_lock:
        cur = _db().execute("DELETE FROM trash WHERE id=? RETURNING *", (trash_id,))
        row = cur.fetchone()
        if not _dbmod.in_transaction():
            _db().commit()
        return row


def put_back(row) -> None:
    """把刚认领的行**原样退回**（恢复搬移失败时的补偿写回）。

    为什么必须有：认领是"先删行再搬文件"，若不补偿，磁盘故障/权限问题会让
    文件还躺在 .trash 里而索引已消失——用户再也点不到它。
    **保留原 id 与 deleted_at**：id 不变 ⇒ 前端列表状态不跳、调用方可按同一 id
    断言补偿成功；deleted_at 不变 ⇒ 30 天倒计时不重置（否则失败重试等于续期）。
    重用旧 id 不会撞号：`id` 是只前进的序列（PG BIGSERIAL / SQLite AUTOINCREMENT），
    该行已被本事务前的 DELETE 释放，后续插入取到的是更大的值。
    """
    _write("INSERT INTO trash(id, orig_path, trash_path, name, size, deleted_at) "
           "VALUES(?,?,?,?,?,?)",
           (row["id"], row["orig_path"], row["trash_path"], row["name"],
            row["size"], row["deleted_at"]))


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
