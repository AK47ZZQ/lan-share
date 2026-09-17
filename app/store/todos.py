"""todo_lists / todos 表的**唯一主人**（效率舱 M1，v1.1）。

## 边界

- 本模块是两表**唯一**的 SQL 出口：业务模块只调用语义方法，不认识列名。
- schema 归 `app/store/schema.py`（DDL，双引擎同步）。
- 可见性语义（personal=仅创建者；family=member+admin，guest 不可见）的判定
  在 API 层做，本模块只提供按语义命名的查询出口。
- 写方法自持 `_db_lock` + commit；处在 `db.transaction()` 内时推迟收束
  （与 `store/users.py::create` 同一纪律——悬空 INSERT 会锁死 SQLite）。

## 并发口径（最终计划 v5 §3.7）

- 家庭清单多人同时勾选 = 最后写入胜（本模块不做版本号）。
- 删除走**软删除墓碑**（`deleted_at`），避免"A 删除、B 勾选 → 复活"；
  `purge_deleted_before` 由清理循环兜底物理清除。
- 指派成员停用/删除 → `assignee_id/done_by` 由 schema `ON DELETE SET NULL`
  自然悬空，UI 显示"已停用成员"。
"""
import time

from app import db as _dbmod
from app.db import _db, _db_lock


# ─── 读 ───────────────────────────────────────────────────


def get_list(lid: int):
    return _db().execute("SELECT * FROM todo_lists WHERE id=?", (lid,)).fetchone()


def lists_visible(user_id: int) -> list:
    """某成员可见的清单：自建 personal + 全部 family（guest 过滤在 API 层）。"""
    return _db().execute(
        "SELECT * FROM todo_lists WHERE user_id=? OR kind='family' "
        "ORDER BY created_at", (user_id,)).fetchall()


def count_lists_by_name(user_id: int, name: str) -> int:
    return _db().execute(
        "SELECT COUNT(*) AS c FROM todo_lists WHERE user_id=? AND name=?",
        (user_id, name)).fetchone()["c"]


def get_todo(tid: int):
    """单条待办（带清单归属信息，供 API 层做可见性判定）。"""
    return _db().execute(
        "SELECT t.*, l.kind AS list_kind, l.user_id AS list_owner_id, "
        "l.name AS list_name FROM todos t JOIN todo_lists l ON l.id=t.list_id "
        "WHERE t.id=?", (tid,)).fetchone()


def todos_visible(user_id: int) -> list:
    """某成员可见的全部未删待办（personal=自建清单的 + family 全部）。"""
    return _db().execute(
        "SELECT t.*, l.kind AS list_kind, l.user_id AS list_owner_id, "
        "l.name AS list_name FROM todos t JOIN todo_lists l ON l.id=t.list_id "
        "WHERE t.deleted_at IS NULL AND (l.user_id=? OR l.kind='family') "
        "ORDER BY t.created_at DESC", (user_id,)).fetchall()


def due_unreminded(now: float) -> list:
    """到期未提醒（sweep 用）：status=todo、due_at 已过、reminded_at 为空。"""
    return _db().execute(
        "SELECT t.id, t.title, t.due_at, t.assignee_id, l.user_id AS list_owner_id, "
        "l.kind AS list_kind FROM todos t JOIN todo_lists l ON l.id=t.list_id "
        "WHERE t.deleted_at IS NULL AND t.status='todo' AND t.reminded_at IS NULL "
        "AND t.due_at IS NOT NULL AND t.due_at<=?", (now,)).fetchall()


def deleted_before(cutoff: float) -> list:
    """软删除墓碑超过保留期的 id（物理清除用）。"""
    return [r["id"] for r in _db().execute(
        "SELECT id FROM todos WHERE deleted_at IS NOT NULL AND deleted_at<?",
        (cutoff,)).fetchall()]


# ─── 写 ───────────────────────────────────────────────────


def create_list(user_id: int, name: str, kind: str,
                created_at: float | None = None) -> int:
    cur = _db().execute(
        "INSERT INTO todo_lists(user_id, name, kind, created_at) "
        "VALUES(?,?,?,?) RETURNING id",
        (user_id, name, kind, created_at or time.time()))
    row = cur.fetchone()
    lid = row["id"] if row else _db().execute(
        "SELECT id FROM todo_lists WHERE user_id=? AND name=? ORDER BY id DESC",
        (user_id, name)).fetchone()["id"]
    if not _dbmod.in_transaction():
        _db().commit()
    return lid


def rename_list(lid: int, name: str) -> int:
    return _write("UPDATE todo_lists SET name=? WHERE id=?", (name, lid))


def delete_list(lid: int) -> int:
    """删清单（todos 依 schema CASCADE 物理删除——清单删除是明确意图，
    不走墓碑；墓碑只用于单条待办的多人并发防复活）。"""
    return _write("DELETE FROM todo_lists WHERE id=?", (lid,))


def create_todo(list_id: int, title: str, notes: str, due_at: float | None,
                priority: int, assignee_id: int | None,
                created_at: float | None = None) -> int:
    cur = _db().execute(
        "INSERT INTO todos(list_id, title, notes, due_at, priority, assignee_id, "
        "created_at) VALUES(?,?,?,?,?,?,?) RETURNING id",
        (list_id, title, notes, due_at, priority, assignee_id,
         created_at or time.time()))
    row = cur.fetchone()
    tid = row["id"] if row else _db().execute(
        "SELECT id FROM todos WHERE list_id=? AND title=? ORDER BY id DESC",
        (list_id, title)).fetchone()["id"]
    if not _dbmod.in_transaction():
        _db().commit()
    return tid


def update_todo(tid: int, fields: dict) -> int:
    """白名单字段动态 UPDATE（键由 API 层白名单产生，不收用户输入的列名）。"""
    if not fields:
        return 0
    cols = ", ".join(f"{k}=?" for k in fields)
    return _write(f"UPDATE todos SET {cols} WHERE id=?",
                  (*fields.values(), tid))


def soft_delete_todo(tid: int, now: float | None = None) -> int:
    return _write("UPDATE todos SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                  (now or time.time(), tid))


def mark_reminded(tid: int, now: float | None = None) -> int:
    return _write("UPDATE todos SET reminded_at=? WHERE id=? AND reminded_at IS NULL",
                  (now or time.time(), tid))


def purge_deleted(ids: list[int]) -> int:
    """物理清除墓碑（清理循环用）。"""
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    return _write(f"DELETE FROM todos WHERE id IN ({marks})", tuple(ids))


def _write(sql: str, params: tuple) -> int:
    with _db_lock:
        cur = _db().execute(sql, params)
        n = cur.rowcount
        if not _dbmod.in_transaction():
            _db().commit()
        return n
