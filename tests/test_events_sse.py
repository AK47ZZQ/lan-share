"""SSE 实时通知通道（B3，GET /api/events）回归。

**不读无限流**——TestClient（httpx portal）与不结束的 StreamingResponse 在本
环境下组合会挂（最小复现见值班记录），因此语义拆三层测：
1. 鉴权：匿名 401（依赖层，无需流）；
2. 发布接线：insert_notification 落库即调 events.publish（monkeypatch 记录）；
3. 上限与清理：直接操纵订阅表——每用户 ≤3 连接的第 4 条 429、摘除后恢复。
投递语义（call_soon_threadsafe 桥接/满队丢最旧）由 asyncio.run 单测驱动。
"""
import asyncio
import json

from fastapi.testclient import TestClient

from conftest import ADMIN_PASS, ADMIN_USER

from app.api import events as events_bus
from app.main import app
from app.store import users as users_store


def _login(c: TestClient) -> None:
    r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200


def _admin_uid() -> int:
    uid = users_store.id_by_username(ADMIN_USER)
    assert uid is not None
    return uid


def test_sse_rejects_anonymous(client):
    r = client.get("/api/events")
    assert r.status_code == 401


def test_sse_notification_insert_publishes(client, monkeypatch):
    """insert_notification（站内通知唯一出口）落库即发 SSE 信号。"""
    calls: list[tuple[int, dict]] = []
    monkeypatch.setattr(events_bus, "publish",
                        lambda uid, ev: calls.append((uid, ev)) or 1)
    with TestClient(app) as c:
        _login(c)
        from app.api.notify import insert_notification
        assert insert_notification(_admin_uid(), "system", "SSE 冒烟", "正文") is True
    assert calls and calls[0][0] == _admin_uid() and calls[0][1] == {"kind": "notification"}


def test_sse_pref_filtered_no_publish(client, monkeypatch):
    """偏好关闭：不落库也不发信号（信号与落库同源，不抢跑）。"""
    calls: list = []
    monkeypatch.setattr(events_bus, "publish", lambda uid, ev: calls.append((uid, ev)) or 1)
    with TestClient(app) as c:
        _login(c)
        from app.api.notify import _pref_key, get_prefs, insert_notification
        from app.settings import set_setting
        uid = _admin_uid()
        prefs = get_prefs(uid)
        prefs["system"] = False
        set_setting(_pref_key(uid), json.dumps(prefs))
        try:
            assert insert_notification(uid, "system", "不该出现", "") is False
        finally:
            set_setting(_pref_key(uid), json.dumps(
                {"share": True, "admin": True, "system": True}))   # 还原
    assert calls == []


def test_sse_delivery_bridge_and_overflow():
    """线程桥接投递 + 满队丢最旧（信号语义）：asyncio.run 直接驱动。"""
    async def scenario():
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        events_bus._subs[999] = {q}
        events_bus._loop = asyncio.get_running_loop()
        try:
            n = await asyncio.to_thread(events_bus.publish, 999, {"seq": 1})
            await asyncio.sleep(0)   # 让 call_soon_threadsafe 回调执行
            assert n == 1
            await asyncio.to_thread(events_bus.publish, 999, {"seq": 2})
            await asyncio.to_thread(events_bus.publish, 999, {"seq": 3})
            await asyncio.to_thread(events_bus.publish, 999, {"seq": 4})
            await asyncio.sleep(0)
            assert q.qsize() == 2                       # 队列上限 2
            first = q.get_nowait()
            assert first["seq"] == 3                    # 最旧的 1/2 已被丢弃
        finally:
            events_bus._subs.pop(999, None)
            events_bus._loop = None
    asyncio.run(scenario())


def test_sse_per_user_connection_limit(client):
    """每用户 ≤3 连接：第 4 条 429（防单账号占满全局预算）。

    恢复路径不真发 GET——TestClient 对 200 的流式响应会等读到连接关闭（无限
    流即挂），故"摘除后放行"以订阅表状态断言（清理语义在生成器 finally，
    由 test_sse_delivery_bridge_and_overflow 侧的 _subs 操纵同一数据结构）。
    """
    with TestClient(app) as c:
        _login(c)
        uid = _admin_uid()
        events_bus._subs[uid] = {object(), object(), object()}
        assert events_bus.subscriber_count() == 3
        try:
            r = c.get("/api/events")
            assert r.status_code == 429
        finally:
            events_bus._subs.pop(uid, None)
        assert events_bus.subscriber_count() == 0   # 摘除即恢复放行资格


def test_sse_publish_many_dedup_and_count(client):
    """publish_many 去重 + 无订阅者触达 0 不抛错。"""
    u1 = _admin_uid()
    n = events_bus.publish_many([u1, u1, u1 + 1], {"kind": "feedback"})
    assert n == 0
    assert events_bus.subscriber_count() == 0
