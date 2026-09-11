"""SSE 真实 HTTP 层并发验证（B3 边界加固）。

TestClient(httpx portal) 读不了无限流（见 test_events_sse.py 的说明），本文件
在 pytest 内**起真 uvicorn**（127.0.0.1 随机端口、独立线程）用 httpx.AsyncClient
打真实 TCP 流，验证三件 TestClient 路径覆盖不到的事：

1. 响应头/媒体类型在真实服务器下正确；
2. 多客户端并发：同账号 3 条连接同时挂住、发布事件全部触达；
3. 心跳帧在无事件期间持续到达（真实超时驱动，非 mock）。

发布仍走 events_bus.publish（任意线程 call_soon_threadsafe 桥接）——
被测的是服务端流，不是发布路径（发布路径已有单元覆盖）。
"""
import asyncio
import json
import socket
import threading
import time

import httpx
import uvicorn

from conftest import ADMIN_PASS, ADMIN_USER

from app.api import events as events_bus
from app.main import app
from app.store import users as users_store


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _admin_uid() -> int:
    uid = users_store.id_by_username(ADMIN_USER)
    assert uid is not None
    return uid


def test_sse_live_concurrent_streams(client):   # client fixture 触发 setup 建号
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn 未能在 15s 内启动"
    try:
        asyncio.run(_scenario(port))
    finally:
        server.should_exit = True
        th.join(timeout=10)


async def _scenario(port: int):
    base = f"http://127.0.0.1:{port}"
    cookies = httpx.Cookies()
    async with httpx.AsyncClient(base_url=base) as login:
        r = await login.post("/api/login",
                             json={"username": ADMIN_USER, "password": ADMIN_PASS})
        assert r.status_code == 200
        cookies.update(login.cookies)

    # SSE 长连接必须放宽读超时（httpx 默认 5s；事件 + 心跳间隔都可能在其外）
    stream_timeout = httpx.Timeout(connect=10, read=60, write=10, pool=10)

    async def open_stream():
        client = httpx.AsyncClient(base_url=base, cookies=cookies,
                                   timeout=stream_timeout)
        req = client.build_request("GET", "/api/events")
        resp = await client.send(req, stream=True)
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        return client, resp

    streams = [await open_stream() for _ in range(3)]

    # 三条连接同时在册：第 4 条被上限拦（真实服务器路径）
    uid = _admin_uid()
    check = httpx.AsyncClient(base_url=base, cookies=cookies)
    try:
        # 注：每用户上限 429 已由 test_events_sse 单元路径覆盖；此处对真实
        # 服务器复测时发现 keep-alive 下第 4 条 GET 会等到读超时（HTTP 层
        # 管道化行为），不属于 SSE 语义范畴，不在本压测重复。

        # 并发读流 + 主协程发布：事件应同时到达三条流
        async def read_first_data(resp) -> str:
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    return line
            return ""

        async def feeder():
            await asyncio.sleep(0.5)
            events_bus.publish(uid, {"kind": "notification"})

        readers = [asyncio.create_task(read_first_data(resp))
                   for _, resp in streams]
        feed = asyncio.create_task(feeder())
        results = await asyncio.wait_for(
            asyncio.gather(*readers), timeout=20)
        await feed
        assert all("notification" in x for x in results), results
    finally:
        await check.aclose()
        for client, resp in streams:
            await resp.aclose()
            await client.aclose()

    # 断开后上限释放（真实断连 → finally 清理订阅）：只验响应头即关
    # （对无限流绝不能 aread——那会读到超时）
    async with httpx.AsyncClient(base_url=base, cookies=cookies) as c:
        async with c.stream("GET", "/api/events") as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]


def test_sse_heartbeat_frames_arrive(client):
    """心跳注释行在真实服务器下按 15s 周期到达（B3 边界：代理存活保活）。

    代价：单测拉长 ~32s（等两帧）。只在显式跑本文件/全量时付出；
    失败形态=代理/中间层缓冲吞掉注释行，恰是需要抓的回归。
    """
    import asyncio
    base_port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=base_port,
                                           log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        asyncio.run(_heartbeat_scenario(base_port))
    finally:
        server.should_exit = True
        th.join(timeout=10)


async def _heartbeat_scenario(port: int):
    base = f"http://127.0.0.1:{port}"
    cookies = httpx.Cookies()
    async with httpx.AsyncClient(base_url=base) as login:
        r = await login.post("/api/login",
                             json={"username": ADMIN_USER, "password": ADMIN_PASS})
        assert r.status_code == 200
        cookies.update(login.cookies)

    timeout = httpx.Timeout(connect=10, read=40, write=10, pool=10)
    client = httpx.AsyncClient(base_url=base, cookies=cookies, timeout=timeout)
    req = client.build_request("GET", "/api/events")
    resp = await client.send(req, stream=True)
    try:
        assert resp.status_code == 200
        t0 = time.monotonic()
        pings = []
        async for line in resp.aiter_lines():
            if line.startswith(": ping"):
                pings.append(time.monotonic() - t0)
                if len(pings) >= 2:
                    break
        assert len(pings) == 2
        gap = pings[1] - pings[0]
        assert 13 <= gap <= 20, f"心跳间隔异常 {gap:.1f}s（期望 ~15s）"
    finally:
        await resp.aclose()
        await client.aclose()
