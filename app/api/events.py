"""SSE 实时通知通道（GET /api/events）。

## 设计

- **双鉴权**：复用 require_user（Cookie 会话与 App Bearer 设备 Token 天然同权）。
- **线程桥接**：业务端点跑在线程池（同步 DB），发布发生在非事件循环线程——
  publish() 经 `loop.call_soon_threadsafe` 把事件投递进各订阅者的 asyncio.Queue，
  不碰事件循环内部状态（与 cleanup/定时广播同款"同步 IO 不进事件循环"纪律）。
- **背压即丢帧**：每订阅者 queue 上限 16，满时丢弃最旧事件——事件只是"有新
  通知"的提醒（客户端收到后拉 /api/notifications 全量），丢帧不丢数据。
- **上限与清理**：每用户 ≤3 连接（Web + App 前台 + 余量），全局 ≤64；
  断连在生成器 finally 中摘除队列；心跳 15s（代理/浏览器超时线之上）。

## 事件形状

`{"kind": "notification"}` 站内通知/广播到达；`{"kind": "feedback"}` 新反馈
（仅 admin）。均为**信号**而非载荷——客户端收到后走既有拉取接口。
"""
import asyncio
import json
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.security import require_user

router = APIRouter()

_PER_USER_LIMIT = 3
_GLOBAL_LIMIT = 64
_QUEUE_MAX = 16
_HEARTBEAT = 15.0

_loop: asyncio.AbstractEventLoop | None = None
_subs: dict[int, set] = {}
_subs_mu = threading.Lock()


@router.on_event("startup")
async def _capture_loop():
    """记录主事件循环（publish 从线程池线程 call_soon_threadsafe 用）。"""
    global _loop
    _loop = asyncio.get_running_loop()


def _deliver(uid: int, q, event: dict) -> None:
    if _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(_put_drop_oldest, q, event)
    else:
        _put_drop_oldest(q, event)   # 测试/未启动：同线程直投


def _put_drop_oldest(q, event: dict) -> None:
    if q.full():
        try:
            q.get_nowait()   # 丢最旧：信号语义，见模块 docstring
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(event)


def publish(user_id: int, event: dict) -> int:
    """向某成员的全部活动连接投递事件（线程安全；返回触达连接数）。"""
    with _subs_mu:
        queues = list(_subs.get(user_id, ()))
    for q in queues:
        _deliver(user_id, q, event)
    return len(queues)


def publish_many(user_ids, event: dict) -> int:
    """群发（新反馈 → 全体在用 admin）。"""
    n = 0
    for uid in set(user_ids):
        n += publish(uid, event)
    return n


def subscriber_count() -> int:
    with _subs_mu:
        return sum(len(v) for v in _subs.values())


@router.get("/api/events")
async def events(request: Request, user=Depends(require_user)):
    uid = user["user_id"]
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    with _subs_mu:
        total = sum(len(v) for v in _subs.values())
        if total >= _GLOBAL_LIMIT or len(_subs.get(uid, ())) >= _PER_USER_LIMIT:
            raise HTTPException(status_code=429, detail="实时连接数已达上限")
        _subs.setdefault(uid, set()).add(q)

    async def gen():
        # 断连不轮询 is_disconnected（在部分 ASGI transport 下 receive 会挂）：
        # 客户端断开时 starlette 直接取消本生成器，finally 摘除队列即完成清理
        try:
            yield "retry: 5000\n\n"   # 断线 5s 重连（叠加客户端退避亦可）
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"   # SSE 注释行心跳
        finally:
            with _subs_mu:
                peers = _subs.get(uid)
                if peers is not None:
                    peers.discard(q)
                    if not peers:
                        _subs.pop(uid, None)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",   # 反代不缓冲（Caddy 默认透传 SSE）
    })
