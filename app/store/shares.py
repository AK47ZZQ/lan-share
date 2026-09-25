"""shares 表的**唯一主人**（B1 存储层拆分）。

## 边界

- 本模块是该表**唯一**的 SQL 出口：业务模块只调用语义方法，不认识列名。
- schema 归 `app/store/schema.py`（DDL）。
- 访问控制（密码校验/定向判定/过期判定）是业务语义，留在 API 层；本模块只管
  行的存取与计数。
- 写方法自持 `_db_lock` + commit；处在 `db.transaction()` 内时推迟收束。
"""
import time

from app import db as _dbmod
from app.db import _db, _db_lock

# ─── 读 ───────────────────────────────────────────────────


def get_by_token(token: str):
    return _db().execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()


def exists(token: str) -> bool:
    return _db().execute("SELECT 1 FROM shares WHERE token=?",
                         (token,)).fetchone() is not None


def list_all() -> list:
    """全量分享列表（管理端全见/成员按 owner 过滤在 API 层做）。"""
    return _db().execute("SELECT * FROM shares ORDER BY created_at DESC").fetchall()


def list_brief_by_owner(owner_id: int) -> list:
    """成员数据导出用轻量投影（不含口令材料/计数内部态）。"""
    return _db().execute(
        "SELECT token, path, name, created_at, expires_at FROM shares "
        "WHERE owner_id=?", (owner_id,)).fetchall()


def count() -> int:
    return _db().execute("SELECT COUNT(*) AS c FROM shares").fetchone()["c"]


def overview_stats(now: float) -> dict:
    """管理端概览统计：总数/未过期数/累计下载次数。"""
    total = _db().execute("SELECT COUNT(*) AS v FROM shares").fetchone()["v"]
    active = _db().execute("SELECT COUNT(*) AS v FROM shares WHERE expires_at > ?",
                           (now,)).fetchone()["v"]
    downloads = _db().execute(
        "SELECT COALESCE(SUM(downloads), 0) AS v FROM shares").fetchone()["v"]
    return {"total": total, "active": active, "downloads": downloads}


# ─── 写 ───────────────────────────────────────────────────


def create(token: str, rel: str, name: str, size: int | None, created_at: float,
           expires_at: float, password_hash: str | None,
           max_downloads: int | None, owner_id: int | None,
           target_user_id: int | None) -> None:
    """落一条分享。提取码明文永不落库（R1-P2-5）——只存哈希，列 password_plain
    保持 NULL（列本体为兼容旧迁移路径保留）。"""
    _write("INSERT INTO shares(token, path, name, size, created_at, expires_at, "
           "password_hash, max_downloads, owner_id, password_plain, target_user_id) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
           (token, rel, name, size, created_at, expires_at,
            password_hash, max_downloads, owner_id, None, target_user_id))


def consume_download(token: str) -> bool:
    """发票时原子递增下载计数并把关次数上限（R1-P2-4）。命中返回 True；
    返回 False = 已达上限（调用方给 410）。"""
    with _db_lock:
        cur = _db().execute(
            "UPDATE shares SET downloads=downloads+1 WHERE token=? "
            "AND (max_downloads IS NULL OR downloads < max_downloads)", (token,))
        # commit 必须在 raise 之前：UPDATE 命中 0 行也已开启事务，锁内 raise
        # 会把连接留在持有 RESERVED 锁的未决事务里，锁死后续所有写操作
        if not _dbmod.in_transaction():
            _db().commit()
        return bool(cur.rowcount)


def purge_expired(now: float | None = None) -> int:
    """清过期分享行（列表进入时惰性清 + 清理循环定期清，双保险）。"""
    return _write("DELETE FROM shares WHERE expires_at < ?", (now or time.time(),))


def delete_by_token(token: str) -> int:
    """撤销分享（创建者或 admin）。"""
    return _write("DELETE FROM shares WHERE token=?", (token,))


def renew(token: str, expires_at: float) -> None:
    """编辑：有效期从现在起重新计时。"""
    _write("UPDATE shares SET expires_at=? WHERE token=?", (expires_at, token))


def set_password_hash(token: str, password_hash: str) -> None:
    """提取码哈希惰性升级（api/share.py 校验成功时把旧 sha256 行改写为 scrypt）。"""
    _write("UPDATE shares SET password_hash=? WHERE token=?", (password_hash, token))


def set_max_downloads(token: str, max_downloads: int) -> None:
    """编辑：改/设下载上限。"""
    _write("UPDATE shares SET max_downloads=? WHERE token=?", (max_downloads, token))


def _write(sql: str, params: tuple) -> int:
    with _db_lock:
        cur = _db().execute(sql, params)
        n = cur.rowcount
        if not _dbmod.in_transaction():
            _db().commit()
        return n
