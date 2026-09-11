"""device_tokens 表的**唯一主人**（B1 存储层拆分）。

## 边界

- 本模块是该表**唯一**的 SQL 出口：业务模块只调用语义方法，不认识列名。
  （身份解析 JOIN users 的读走 `users.principal_by_device`——那是 users 表
  主人的既有出口，本模块不重复。）
- schema 归 `app/store/schema.py`（DDL）。
- Token 明文的生成/哈希是调用方语义（secrets/_sha），本模块只见哈希。
- 写方法自持 `_db_lock` + commit；处在 `db.transaction()` 内时推迟收束。
  改密/停用伴随的"会话+设备一起吊销"跨表序列由调用方持锁开事务。
"""

from app import db as _dbmod
from app.db import _db, _db_lock

# ─── 读 ───────────────────────────────────────────────────


def count_for_user(uid: int) -> int:
    return _db().execute("SELECT COUNT(*) AS c FROM device_tokens WHERE user_id=?",
                         (uid,)).fetchone()["c"]


def find_id_by_name(uid: int, name: str):
    """同名设备行 id（轮换判定；不存在返回 None）。"""
    row = _db().execute("SELECT id FROM device_tokens WHERE user_id=? AND name=?",
                        (uid, name)).fetchone()
    return row["id"] if row else None


def list_for_user(uid: int) -> list:
    return _db().execute(
        "SELECT id, name, created_at, last_seen, token_hash FROM device_tokens "
        "WHERE user_id=? ORDER BY id", (uid,)).fetchall()


def get_for_user(device_id: int, uid: int):
    return _db().execute("SELECT * FROM device_tokens WHERE id=? AND user_id=?",
                         (device_id, uid)).fetchone()


def list_active(now: float) -> list:
    """未过期设备（管理端设备面板，新→旧）。"""
    return _db().execute(
        "SELECT id, user_id, name, created_at, last_seen, expires_at FROM device_tokens "
        "WHERE expires_at > ? ORDER BY created_at DESC", (now,)).fetchall()


def count_active(now: float) -> int:
    return _db().execute("SELECT COUNT(*) AS v FROM device_tokens WHERE expires_at > ?",
                         (now,)).fetchone()["v"]


def count() -> int:
    return _db().execute("SELECT COUNT(*) AS c FROM device_tokens").fetchone()["c"]


def list_brief_by_user(uid: int) -> list:
    """成员数据导出用轻量投影（不含哈希）。"""
    return _db().execute(
        "SELECT name, created_at, last_seen FROM device_tokens WHERE user_id=?",
        (uid,)).fetchall()


# ─── 写 ───────────────────────────────────────────────────


def rotate(device_id: int, token_hash: str, now: float, ttl: float) -> None:
    """同名设备轮换：换哈希、重置创建时间与滑动过期（重装/重复登录不产生
    僵尸设备）。调用方须已在 `_db_lock` 内（SELECT-then-write 竞态面）。"""
    _db().execute(
        "UPDATE device_tokens SET token_hash=?, created_at=?, last_seen=?, expires_at=? WHERE id=?",
        (token_hash, now, now, now + ttl, device_id))


def insert(uid: int, name: str, token_hash: str, now: float, ttl: float) -> int:
    """新建设备行，返回 id。token_hash UNIQUE 碰撞由调用方重试
    （换一枚哈希再走 rotate/insert）。调用方须已在 `_db_lock` 内。"""
    cur = _db().execute(
        "INSERT INTO device_tokens(user_id, name, token_hash, created_at, last_seen, expires_at) "
        "VALUES(?,?,?,?,?,?) RETURNING id",
        (uid, name, token_hash, now, now, now + ttl))
    return cur.fetchone()["id"]


def commit_if_standalone() -> None:
    """签发序列（锁内 rotate/insert 组合）的收束点：事务内不自行 commit。"""
    if not _dbmod.in_transaction():
        _db().commit()


def renew(device_id: int, expires_at: float) -> None:
    """滑动续期（Bearer 热路径；距上次续期超阈值的节流判定在调用方）。"""
    _write("UPDATE device_tokens SET expires_at=? WHERE id=?", (expires_at, device_id))


def touch_last_seen(device_id: int, seen_at: float) -> None:
    """活跃打点（1h 节流判定在调用方）。"""
    _write("UPDATE device_tokens SET last_seen=? WHERE id=?", (seen_at, device_id))


def delete(device_id: int) -> int:
    return _write("DELETE FROM device_tokens WHERE id=?", (device_id,))


def delete_for_user(uid: int) -> int:
    """吊销某账号全部设备（改密/停用/重置密码伴随动作，常在事务内）。"""
    return _write("DELETE FROM device_tokens WHERE user_id=?", (uid,))


def delete_expired(now: float) -> int:
    """清理循环：过期设备行。"""
    return _write("DELETE FROM device_tokens WHERE expires_at < ?", (now,))


def _write(sql: str, params: tuple) -> int:
    with _db_lock:
        cur = _db().execute(sql, params)
        n = cur.rowcount
        if not _dbmod.in_transaction():
            _db().commit()
        return n
