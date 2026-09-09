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