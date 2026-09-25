"""pomodoro_sessions 表的**唯一主人**（M2 番茄钟，效率舱最终计划 §3.1/§3.3）。

## 边界

- 本模块是该表唯一 SQL 出口：业务模块只调用语义方法，不认识列名。
- 三态结算语义（focus/abandoned/auto_closed）、同用户单 running、按日归属
  是业务规则，留在 API 层；本模块只管行的存取。
- 时间戳一律 UTC 存（最终计划 §3.5）；SQL 只做 started_at 区间过滤，
  聚合在 Python 侧（避免 SQLite/PG 日期函数分叉）。
- 写方法自持 `_db_lock` + commit；处在 `db.transaction()` 内时推迟收束
  （shares.py / todos.py 同款纪律）。
"""
from app import db as _dbmod
from app.db import _db, _db_lock

# ─── 读 ───────────────────────────────────────────────────


def get(sid: int):
    return _db().execute("SELECT * FROM pomodoro_sessions WHERE id=?", (sid,)).fetchone()


def running_for_user(user_id: int):
    return _db().execute(
        "SELECT * FROM pomodoro_sessions WHERE user_id=? AND status='running'",
        (user_id,)).fetchall()


def list_since(user_id: int, since: float) -> list:
    """统计出口：只做 started_at 区间过滤（§3.5：聚合在 Python 侧）。"""
    return _db().execute(
        "SELECT * FROM pomodoro_sessions WHERE user_id=? AND started_at>=? "
        "ORDER BY started_at DESC LIMIT 500", (user_id, since)).fetchall()


# ─── 写 ───────────────────────────────────────────────────


def create(user_id: int, todo_id: int | None, started_at: float,
           focus_min: int, break_min: int) -> int:
    """开一个 running 会话；唯一性（同用户单 running）由调用方先结算保证。"""
    cur = _db().execute(
        "INSERT INTO pomodoro_sessions(user_id, todo_id, started_at, "
        "focus_min, break_min, status) VALUES(?,?,?,?,?,'running') RETURNING id",
        (user_id, todo_id, started_at, focus_min, break_min))
    row = cur.fetchone()
    sid = int(row["id"])
    if not _dbmod.in_transaction():
        _db().commit()
    return sid


def finish(sid: int, status: str, ended_at: float) -> int:
    """显式收束一个 running 会话（focus/abandoned）；只对 running 行生效。"""
    return _write("UPDATE pomodoro_sessions SET status=?, ended_at=? "
                  "WHERE id=? AND status='running'", (status, ended_at, sid))


def finish_running_for_user(user_id: int, status: str, ended_at: float) -> int:
    """R2-06：开新会话前把该用户残留 running 一并收束（rowcount 便于审计）。"""
    return _write("UPDATE pomodoro_sessions SET status=?, ended_at=? "
                  "WHERE user_id=? AND status='running'", (status, ended_at, user_id))


def auto_close_stale(now: float) -> list[int]:
    """惰性结算（sweep 兜底 + 各写路径前置）：started_at + 计划时长 < now 的
    running 按计划时长收尾并标 auto_closed。RETURNING 保证双引擎都被真执行
    （store_readers_smoke 纪律），返回被结算的 id 列表供 loop_health 观测。"""
    with _db_lock:
        cur = _db().execute(
            "UPDATE pomodoro_sessions SET status='auto_closed', "
            "ended_at = started_at + focus_min * 60.0 "
            "WHERE status='running' AND started_at + focus_min * 60.0 < ? "
            "RETURNING id", (now,))
        rows = cur.fetchall()
        if not _dbmod.in_transaction():
            _db().commit()
        return [int(r["id"]) for r in rows]


def _write(sql: str, params: tuple) -> int:
    with _db_lock:
        cur = _db().execute(sql, params)
        n = cur.rowcount
        if not _dbmod.in_transaction():
            _db().commit()
        return n
