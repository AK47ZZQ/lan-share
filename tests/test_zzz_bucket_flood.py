"""v3.67.2 dav/media 限速桶钉（移植自分叉批 P1）：这两个前缀原落 asgi
"未列举即不限速"分支——/dav 的 scrypt Basic 校验可被匿名洪泛占满线程池，
批量冷缩略图可占满渲染。桶在认证前计频，429 先于 KDF/渲染发生。
zzz 前缀排到最后跑：限速桶是进程级共享状态，洪泛会污染后续用例。
"""
LAN_HOST = "lan.local:8765"


def test_dav_kdf_budget_429(client, monkeypatch):
    """KDF 前每 IP 校验预算：5 次合法格式 Basic 后必须 429（一次 scrypt 都
    不再多跑）。放在桶洪泛用例之前——它只耗 dav 桶 6/300，不干扰后面的
    打满实验。消融锚：删掉 webdav 里的 davtry 预算 → 本测试红。"""
    import base64
    import app.api.webdav as webdav
    import app.security as _sec
    monkeypatch.setattr(webdav, "DAV_VERIFY_PER_MIN", 5)
    # 调高账号锁定阈值：否则 5 次失败先触发 login_locked 401，抢在 davtry 之前
    monkeypatch.setattr(_sec, "LOGIN_MAX_FAILS", 1000)
    hdr = {"Host": LAN_HOST,
           "Authorization": "Basic " + base64.b64encode(b"nouser:badpw").decode()}
    seen = None
    for i in range(10):
        # PROPFIND 是 DAV 洪泛的真实载体（GET 对集合 400 早在认证前返回）
        r = client.request("PROPFIND", "/dav/", headers=hdr,
                           content='<?xml version="1.0"?><D:propfind xmlns:D="DAV:"/>')
        if r.status_code == 429:
            seen = i
            break
    assert seen is not None, "davtry 预算应在 5 次后 429"
    r = client.get("/api/auth/me", headers={"Host": LAN_HOST})
    assert r.status_code in (200, 401), "davtry 不得误伤 api 桶"


def test_media_bucket_flood_429_and_api_bucket_unaffected(client):
    from app.config import MEDIA_RATE_LIMIT
    seen = None
    # 阈值随 config 走（2026-10-24 由 240 上调 600，反馈 2305 误伤实证），
    # 多打 20 次确保触发——钉的是"桶存在且在阈值处 429"，不是具体数字
    for i in range(MEDIA_RATE_LIMIT + 20):
        r = client.get("/thumb/nonexist.jpg", headers={"Host": LAN_HOST})
        if r.status_code == 429:
            seen = i
            break
    assert seen is not None, f"media 桶应在 {MEDIA_RATE_LIMIT}/min 处 429"
    r = client.get("/api/auth/me", headers={"Host": LAN_HOST})
    assert r.status_code in (200, 401), "media 桶洪泛不得误伤 api 桶"


def test_dav_bucket_flood_429(client):
    seen = None
    for i in range(320):          # DAV 300/min
        r = client.get("/dav/", headers={"Host": LAN_HOST})
        if r.status_code == 429:
            seen = i
            break
    assert seen is not None, "dav 桶应在 300/min 处 429"


def test_rate_rejects_counter_durable(client, monkeypatch):
    """观测收口钉（v3.68 不自信点①）：桶 429 必须进 metrics.rate_rejects
    （进程启动累计，不随 1h 窗口滑动）——否则阈值误伤与否事后无从查。
    消融锚：摘掉 asgi 的 record_rate_reject 调用 → delta 为 0 → 红。"""
    import app.asgi as asgi_mod
    from app.services import metrics as _m

    before = _m.snapshot()["rate_rejects"].get("media:lan", 0)
    monkeypatch.setattr(asgi_mod, "MEDIA_RATE_LIMIT", 2)
    got429 = False
    for _ in range(5):
        r = client.get("/thumb/nonexist.jpg", headers={"Host": "lan.local:8765"})
        if r.status_code == 429:
            got429 = True
            break
    assert got429, "应触发 429"
    snap = _m.snapshot()
    assert snap["rate_rejects"].get("media:lan", 0) > before, "429 未计入 rate_rejects"
    assert isinstance(snap["uptime_seconds"], (int, float))
