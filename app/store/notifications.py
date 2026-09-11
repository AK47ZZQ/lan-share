"""notifications / admin_broadcasts / announcements 三表的**唯一主人**（B1 存储层拆分）。

## 为什么

此前三张表的 SQL 全部内联在 `app/api/notify.py`（路由层直接拼 SQL）与
`app/services/cleanup.py`：表结构知识散在两处，"加一列/换索引"要人肉找全调用点。
沿用 `app/store/users.py` 确立的表主人模式（11 号复审批次 2，A1）收拢。

## 边界

- 本模块是三张表**唯一**的 SQL 出口：业务模块只调用语义方法，不认识列名。
- schema 仍归 `app/store/schema.py`（DDL），加一列 = 改 schema.py + 本模块。
- 读方法返回数据库原始行（调用方按 r["col"] 取值），迁移行为等价。
- 写方法自持 `_db_lock` + commit；处在 `db.transaction()` 内时推迟给最外层收束
  （与 users.py::_write 同一规则）。

## 命名约定

`get_*` 单行；`list_*` 集合；`mark_*`/`set_*` 更新；`create/delete/insert` 增删。
"""
import time

from app import db as _dbmod
from app.db import _db, _db_lock

# ─── notifications：读 ────────────────────────────────────


def count_unread(uid: int) -> int:
    return _db().execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND read_at IS NULL",
        (uid,)).fetchone()["c"]


def list_for_user(uid: int, limit: int = 50) -> list:
    return _db().execute(
        "SELECT id, type, title, body, link, created_at, read_at FROM notifications "
        "WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()


def stats_by_broadcast(bid: str, target_ids: list[int]) -> dict:
    """一条广播在其目标人群内的未读/已读人数（管理端发送记录列表用）。"""
    marks = ",".join("?" * len(target_ids))
    row = _db().execute(
        f"SELECT SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS unread, "
        f"SUM(CASE WHEN read_at IS NOT NULL THEN 1 ELSE 0 END) AS read "
        f"FROM notifications WHERE broadcast_id=? AND user_id IN ({marks})",
        [bid, *target_ids]).fetchone()
    return {"unread": row["unread"] or 0, "read": row["read"] or 0}


def list_unread_users(bid: str, target_ids: list[int]) -> list:
    """广播目标人群中仍未读的 user_id 行（站外提醒用）。"""
    marks = ",".join("?" * len(target_ids))
    return _db().execute(
        f"SELECT user_id FROM notifications WHERE broadcast_id=? AND read_at IS NULL "
        f"AND user_id IN ({marks})", [bid, *target_ids]).fetchall()


def list_for_admin(uid: int | None, limit: int) -> list:
    """全站通知近况（新→旧）；uid 非 None 时按成员筛选。"""
    if uid is not None:
        return _db().execute(
            "SELECT id, user_id, type, title, body, link, created_at, read_at, "
            "broadcast_id FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (uid, limit)).fetchall()
    return _db().execute(
        "SELECT id, user_id, type, title, body, link, created_at, read_at, "
        "broadcast_id FROM notifications ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()


# ─── notifications：写 ────────────────────────────────────


def insert(user_id: int, type: str, title: str, body: str = "",
           link: str | None = None, broadcast_id: str | None = None) -> None:
    """落一条站内通知。偏好过滤（发不发）是调用方的语义，本方法只管写。"""
    _write("INSERT INTO notifications(user_id, type, title, body, link, "
           "created_at, broadcast_id) VALUES(?,?,?,?,?,?,?)",
           (user_id, type, title, body, link, time.time(), broadcast_id))


def mark_all_read(uid: int) -> int:
    return _write("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",
                  (time.time(), uid))


def mark_one_read(nid: int, uid: int) -> int:
    return _write("UPDATE notifications SET read_at=? WHERE id=? AND user_id=? "
                  "AND read_at IS NULL", (time.time(), nid, uid))


def delete_for_user(nid: int, uid: int) -> int:
    return _write("DELETE FROM notifications WHERE id=? AND user_id=?", (nid, uid))


def delete_unread_by_broadcast(bid: str) -> int:
    """撤回语义：只删该广播全部未读站内行（已读的保留为历史）。"""
    return _write("DELETE FROM notifications WHERE broadcast_id=? AND read_at IS NULL", (bid,))


def delete_by_broadcast(bid: str) -> int:
    """彻底清除语义：该广播已送达的站内通知一并删除（广播 purge 用）。"""
    return _write("DELETE FROM notifications WHERE broadcast_id = ?", (bid,))


def delete(nid: int) -> int:
    """admin 全站视角单条删除。"""
    return _write("DELETE FROM notifications WHERE id = ?", (nid,))


def delete_read_before(cutoff: float) -> int:
    """清理循环：已读超期的通知行。"""
    return _write("DELETE FROM notifications WHERE read_at IS NOT NULL AND read_at < ?",
                  (cutoff,))


# ─── admin_broadcasts：读 ─────────────────────────────────


def get_broadcast(bid: str):
    return _db().execute("SELECT * FROM admin_broadcasts WHERE id=?", (bid,)).fetchone()


def get_live_broadcast(bid: str):
    """未撤回的广播行（草稿发送用——撤回过的不能再发）。"""
    return _db().execute(
        "SELECT * FROM admin_broadcasts WHERE id=? AND withdrawn_at IS NULL",
        (bid,)).fetchone()


def list_broadcasts(limit: int = 100) -> list:
    """发送记录 + 草稿/定时箱（管理端列表，新→旧）。"""
    return _db().execute(
        "SELECT * FROM admin_broadcasts ORDER BY created_at DESC LIMIT ?",
        (limit,)).fetchall()


def list_due_broadcasts(now: float) -> list:
    """到期待发送的定时广播（启动后台扫描用）。"""
    return _db().execute(
        "SELECT id, title, body, targets FROM admin_broadcasts "
        "WHERE status='scheduled' AND scheduled_at IS NOT NULL AND scheduled_at<=?",
        (now,)).fetchall()


# ─── admin_broadcasts：写 ─────────────────────────────────


def create_broadcast(bid: str, title: str, body: str, targets_json: str,
                     created_by: str, status: str, now: float,
                     sent_at: float | None = None,
                     scheduled_at: float | None = None) -> None:
    """建一条广播记录（status: sent/draft/scheduled，按调用方语义给参）。"""
    _write("INSERT INTO admin_broadcasts(id, title, body, targets, created_by, "
           "status, scheduled_at, sent_at, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
           (bid, title, body, targets_json, created_by, status,
            scheduled_at, sent_at, now))


def mark_broadcast_withdrawn(bid: str, now: float) -> int:
    return _write("UPDATE admin_broadcasts SET withdrawn_at=? WHERE id=? "
                  "AND withdrawn_at IS NULL", (now, bid))


def mark_broadcast_sent(bid: str, now: float, targets_json: str | None = None) -> int:
    """标记已发送；targets_json 非 None 时一并改写目标集（草稿发送时按实际解析重写）。"""
    if targets_json is not None:
        return _write("UPDATE admin_broadcasts SET status='sent', sent_at=?, targets=? "
                      "WHERE id=?", (now, targets_json, bid))
    return _write("UPDATE admin_broadcasts SET status='sent', sent_at=? WHERE id=?",
                  (now, bid))


def delete_draft(bid: str) -> int:
    """只允许删草稿/定时箱里的记录（已发送的走 purge）。"""
    return _write("DELETE FROM admin_broadcasts WHERE id=? AND status IN ('draft','scheduled')",
                  (bid,))


def delete_broadcast(bid: str) -> int:
    """彻底删除广播记录（purge；站内行由调用方配 delete_by_broadcast 清）。"""
    return _write("DELETE FROM admin_broadcasts WHERE id = ?", (bid,))


# ─── announcements：读 ────────────────────────────────────


def list_recent_announcements(limit: int = 10) -> list:
    return _db().execute(
        "SELECT id, title, body, created_by, created_at FROM announcements "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ─── announcements：写 ────────────────────────────────────


def create_announcement(title: str, body: str, created_by: str) -> None:
    _write("INSERT INTO announcements(title, body, created_by, created_at) "
           "VALUES(?,?,?,?)", (title, body, created_by, time.time()))


def delete_announcement(aid: int) -> int:
    return _write("DELETE FROM announcements WHERE id = ?", (aid,))


# ─── 写出口 ───────────────────────────────────────────────


def _write(sql: str, params: tuple) -> int:
    """单语句写的统一出口（持锁 + commit + 返回 rowcount），与项目写约定一致。

    处在 `db.transaction()` 块内时**不自行 commit**——由最外层收束。
    """
    with _db_lock:
        cur = _db().execute(sql, params)
        n = cur.rowcount
        if not _dbmod.in_transaction():
            _db().commit()
        return n
