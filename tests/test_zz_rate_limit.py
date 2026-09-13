"""公网弱网加固批次（2026-09-03）：限速桶按入口分账 + 429 带 Retry-After。

Caddy 反代使内外网 client_ip 都是 127.0.0.1——不改桶分账则全家共享 120/min，
任一设备翻照片墙就会把其他人挤成 429。约定：LAN 桶 120/min、公网桶 240/min。
本文件用 zz_ 前缀排在最后跑：限速桶是进程级共享状态，放前面会污染后续用例。
"""

from conftest import ADMIN_PASS, ADMIN_USER

PUBLIC_HOST = "frp-test.example.com:35391"
LAN_HOST = "lan.local:8765"


def test_retry_after_header_on_429(client):
    """打满 LAN 桶后：429 响应必须带 Retry-After（App/浏览器据此退避）。"""
    # /api/auth/me 未登录也计频（中间件在认证前），匿名直接打
    r429 = None
    for i in range(130):  # LAN 桶 120/min，多打 10 次确保触发
        r = client.get("/api/auth/me", headers={"Host": LAN_HOST})
        if r.status_code == 429:
            r429 = r
            break
    assert r429 is not None, "应该触发 429（130 次 ≥ 120 上限）"
    assert r429.headers.get("retry-after") == "30"


def test_public_bucket_separated_from_lan(client, auth):
    """公网与局域网桶互不挤占：LAN 打满后公网入口仍能正常请求。"""
    _set_public(auth)
    # 1) 打满 LAN 桶
    seen429 = False
    for _ in range(130):
        r = client.get("/api/auth/me", headers={"Host": LAN_HOST})
        if r.status_code == 429:
            seen429 = True
            break
    assert seen429, "LAN 桶应已满"
    # 2) 公网桶独立：此时公网请求仍通过（无 429）
    r = client.get("/api/auth/me", headers={"Host": PUBLIC_HOST})
    assert r.status_code in (200, 401)  # 401=未登录正常，429=被 LAN 桶误伤则失败


def _set_public(auth):
    assert auth.post("/api/admin/public-url",
                     json={"url": f"https://{PUBLIC_HOST}"}).status_code == 200
    # 恢复为空配置不影响后续（测试末位执行）

def test_admin_still_works_after_flood(client, auth):
    """洪水后管理端点仍可用（防误伤回归点）。"""
    r = auth.get("/api/admin/users", headers={"Host": LAN_HOST})
    assert r.status_code == 200


# ─── bug·882a：备份上传不得挤掉交互请求（2026-09-13）────────────────────
# 用户报「备份上传照片时，照片墙和文件也有时会偶发加载失败」。
# 根因：/api/upload/init|state|finish 与直传 multipart 全部计入 api:lan
# 交互桶（120/min），照片备份一批就是几十上百次调用，打满后同 IP 的**所有**
# /api/* 被 429（生产日志实锤 171 次 init 429 + 照片墙/通知/facets 连带 429）。
# 修复：上传控制面独立桶。以下用例钉死"上传洪峰不饿死交互桶"。

from app.security import reset_auth_gates  # noqa: E402
from app.config import API_RATE_LIMIT, UPLOAD_RATE_LIMIT  # noqa: E402


def _stub_upload_calls(auth, n, host=LAN_HOST):
    """打 n 次上传端点（init 是最省的真实上传入口：小 JSON，返回 400/200
    都不影响计频——中间件在路由前计）。返回其中 429 的次数。"""
    got429 = 0
    for _ in range(n):
        r = auth.post("/api/upload/init", headers={"Host": host},
                      json={"path": "照片/手机相册/x.jpg", "size": 1})
        if r.status_code == 429:
            got429 += 1
    return got429


def test_upload_burst_does_not_starve_interactive(client, auth):
    """上传洪峰超过交互桶上限后，交互类端点（/api/photos 等）仍应可用。

    这是 882a 的核心回归：修复前上传与浏览同桶，此处 photos 必被 429。
    """
    reset_auth_gates()
    try:
        # 模拟备份洪峰：超过 API_RATE_LIMIT 次上传调用
        _stub_upload_calls(auth, API_RATE_LIMIT + 20)
        # 交互端点不受影响（独立桶）
        r = auth.get("/api/photos", params={"limit": 1}, headers={"Host": LAN_HOST})
        assert r.status_code != 429, (
            "上传洪峰不应挤占交互桶——882a 回归：photos 被 429 说明桶未隔离")
    finally:
        reset_auth_gates()


def test_upload_bucket_has_own_limit_and_retry_after(client, auth):
    """上传桶是独立桶且有上限：打满后应 429 且带 Retry-After；
    同时交互桶仍独立可用（双重确认隔离方向正确）。"""
    reset_auth_gates()
    try:
        got429 = _stub_upload_calls(auth, UPLOAD_RATE_LIMIT + 20)
        assert got429 > 0, (
            f"上传桶打满 {UPLOAD_RATE_LIMIT} 次后应出现 429（上传桶未被限速=防护缺失）")
        # 交互桶仍未受影响
        r = auth.get("/api/auth/me", headers={"Host": LAN_HOST})
        assert r.status_code != 429, "上传桶打满不应影响交互桶"
    finally:
        reset_auth_gates()


def test_chunk_upload_still_exempt(client, auth):
    """分块上传（chunk）仍按块豁免常规限速——隔离改造不得回退这条。"""
    reset_auth_gates()
    try:
        for _ in range(API_RATE_LIMIT + 30):
            r = auth.post("/api/upload/chunk/deadbeef",
                          headers={"Host": LAN_HOST},
                          content=b"x" * 64)
            if r.status_code == 429:
                raise AssertionError("chunk 分块仍应豁免常规限速（882a 修复不得回退）")
    finally:
        reset_auth_gates()