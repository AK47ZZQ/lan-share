"""效率舱 · 番茄钟（M2 服务端切片，v1.2）。

设计依据 `docs/效率舱-最终计划-20260917.md` §3.3/§3.5：

- **服务端权威时间戳 + 三态结算**：正常结束 `focus`（App 上报 ended_at）；
  中途放弃 `abandoned`（已发生的专注时长仍计入统计，不惩罚）；App 被杀/
  断网未回传由 sweep（每小时）与各写路径前置惰性结算为 `auto_closed`
  （按计划时长收尾）。running 行只增不减、状态机单向。
- **同用户单 running**（R2-06）：开新会话自动把残留 running 按 `abandoned`
  收束到当前时刻（已发生的时长计入）。
- **按日归属**（R2-05）：会话按 started_at 归属本地某日，跨零点不拆分；
  "今天"判断走可注入 `_today()`（backup.py 先例：直呼 time.strftime 会在
  午夜两侧错位）。
- **个人数据**：会话仅本人可读（admin 也不看，与 personal 清单同口径）；
  guest 整体不可用（效率页对访客隐藏）。
- **break_min 本期只落库不消费**：专注-休息循环的休息段是 App 侧计时，
  服务端结算只认 focus_min——向前兼容留列，避免后续加列迁移。
- **不审计**：高频小记录（一次专注一条），审计流无排查价值且会挤占
  AUDIT_KEEP 窗口；异常可观测性由 loop_health 承担。
"""
import datetime
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security import require_user
from app.services import cleanup                     # 清理循环调度（底部注册 sweep）
from app.api.todos import _visible_todo_or_404
from app.store import pomodoros as pomodoros_store

router = APIRouter()

_MAX_FOCUS_MIN = 180
_MAX_BREAK_MIN = 60
_MAX_DAYS = 30
_RUNNING = "running"


class StartBody(BaseModel):
    todo_id: int | None = None
    focus_min: int = Field(default=25, ge=1, le=_MAX_FOCUS_MIN)
    break_min: int = Field(default=5, ge=0, le=_MAX_BREAK_MIN)


class FinishBody(BaseModel):
    # Literal 进 OpenAPI 契约（二轮审查 P2-1）：客户端可自描述合法值
    status: Literal["focus", "abandoned"]
    ended_at: float | None = None   # 客户端上报的权威结束时刻；缺省服务端 now


def _today() -> str:
    """可注入"今天"出口（§3.5；backup.py 先例）。"""
    return time.strftime("%Y-%m-%d")


def _require_member(user) -> None:
    if user["role"] == "guest":
        raise HTTPException(status_code=403, detail="访客不支持番茄钟")


def _own_session_or_404(sid: int, user):
    row = pomodoros_store.get(sid)
    if not row or row["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="会话不存在")
    return row


def _minutes_of(row) -> int:
    """一条会话计入统计的专注分钟：显式收束按实际时长，惰性结算按计划时长。"""
    if row["status"] == "auto_closed" or row["ended_at"] is None:
        return int(row["focus_min"])
    return max(0, int((row["ended_at"] - row["started_at"]) / 60))


@router.post("/api/pomodoro/start")
def start_pomodoro(body: StartBody, user=Depends(require_user)):
    _require_member(user)
    now = time.time()
    # R2-06：残留 running（含超时未回传的）先收束——超时的按计划时长算
    # （用户没说放弃），未超时的按 abandoned 到当前时刻（不惩罚也不多记）
    pomodoros_store.auto_close_stale(now)
    pomodoros_store.finish_running_for_user(user["user_id"], "abandoned", now)
    if body.todo_id is not None:
        # 二轮审查 P1-1：归属收口——personal 待办仅本人、family 对 member 开放
        # （404 不泄露存在）；此前只查存在性，可越权把他人 personal 待办挂进
        # 自己的会话（存在性 oracle + 关联泄露）
        _visible_todo_or_404(body.todo_id, user)
    sid = pomodoros_store.create(user["user_id"], body.todo_id, now,
                                 body.focus_min, body.break_min)
    return {"ok": True, "id": sid, "started_at": now,
            "focus_min": body.focus_min, "break_min": body.break_min}


@router.post("/api/pomodoro/{sid}/finish")
def finish_pomodoro(sid: int, body: FinishBody, user=Depends(require_user)):
    _require_member(user)
    row = _own_session_or_404(sid, user)
    if row["status"] != _RUNNING:
        raise HTTPException(status_code=409, detail="会话已结束")
    ended_at = body.ended_at if body.ended_at is not None else time.time()
    if ended_at < row["started_at"]:
        raise HTTPException(status_code=400, detail="ended_at 早于开始时刻")
    if not pomodoros_store.finish(sid, body.status, ended_at):
        raise HTTPException(status_code=409, detail="会话已结束")
    return {"ok": True, "status": body.status, "minutes": _minutes_of(
        {"status": body.status, "ended_at": ended_at,
         "started_at": row["started_at"], "focus_min": row["focus_min"]})}


@router.get("/api/pomodoro/running")
def running_pomodoro(user=Depends(require_user)):
    _require_member(user)
    pomodoros_store.auto_close_stale(time.time())
    rows = pomodoros_store.running_for_user(user["user_id"])
    r = rows[0] if rows else None
    return {"running": dict(r) if r else None}


@router.get("/api/pomodoro/summary")
def pomodoro_summary(days: int = 7, user=Depends(require_user)):
    """近 N 天（含今天）逐日专注统计。SQL 只做区间过滤，聚合在 Python 侧
    （§3.5 双引擎同构）；今日 = 本地日期，跨零点不拆分（R2-05）。"""
    _require_member(user)
    if not 1 <= days <= _MAX_DAYS:
        raise HTTPException(status_code=400, detail=f"days 需为 1-{_MAX_DAYS}")
    now = time.time()
    pomodoros_store.auto_close_stale(now)
    today_midnight = datetime.datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    since = today_midnight - (days - 1) * 86400
    buckets: dict[str, dict[str, int]] = {
        _today(): {"count": 0, "minutes": 0}}
    for row in pomodoros_store.list_since(user["user_id"], since):
        # 只计已收束会话：running 尚未"挣到"，按计划时长计入会虚增统计
        # （二轮审查后自测发现；running 行由 auto_close/finish 后续进入统计）
        if row["status"] == _RUNNING:
            continue
        day = datetime.datetime.fromtimestamp(row["started_at"]).strftime("%Y-%m-%d")
        b = buckets.setdefault(day, {"count": 0, "minutes": 0})
        b["count"] += 1
        b["minutes"] += _minutes_of(row)
    today = buckets[_today()]
    return {"today": today, "days": [
        {"date": d, "count": buckets[d]["count"], "minutes": buckets[d]["minutes"]}
        for d in sorted(buckets)]}


def sweep_pomodoro_settle() -> None:
    """惰性结算兜底（每小时一轮，CLEANUP_INTERVAL）：被杀/断网 App 的
    running 会话最晚在计划时长结束后一个周期内被 auto_closed。"""
    pomodoros_store.auto_close_stale(time.time())


cleanup.register_sweep("pomodoro_settle", sweep_pomodoro_settle)
