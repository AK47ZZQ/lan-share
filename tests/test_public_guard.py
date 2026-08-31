"""公网入口防护（2026-08-31 加固）：

FRP 隧道使公网流量 client_ip 恒为 127.0.0.1，只能靠 Host 区分入口。
- 防爆破按入口分账：公网侧连续失败只锁公网侧，LAN 家人登录不受波及（DoS 隔离）
- healthz 不对公网暴露（磁盘水位信息泄露）
"""

from conftest import ADMIN_PASS, ADMIN_USER
import server

PUBLIC_HOST = "frp-test.example.com:35391"   # 测试中的"公网"入口
LAN_HOST = "lan.local:8765"                  # 测试中的"局域网"入口


def _sub(client):
    """独立子客户端：成功登录的会话 cookie 不能落进 session 级共享的 client
    （否则污染后续测试的"匿名"断言——conftest 仅在进程启动时清一次罐）。"""
    return client.__class__(server.app, base_url="https://testserver")


def _set_public(auth, url: str):
    assert auth.post("/api/admin/public-url", json={"url": url}).status_code == 200


def _login(client, host: str, password: str, username: str = ADMIN_USER):
    return client.post("/api/login", json={"username": username, "password": password},
                       headers={"Host": host})


def test_healthz_hidden_on_public(client, auth):
    _set_public(auth, f"http://{PUBLIC_HOST}")
    # 公网入口：404（磁盘水位不外泄）
    r = client.get("/healthz", headers={"Host": PUBLIC_HOST})
    assert r.status_code == 404
    # 局域网入口：正常探活
    r = client.get("/healthz", headers={"Host": LAN_HOST})
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_public_lockout_does_not_affect_lan(client, auth):
    _set_public(auth, f"http://{PUBLIC_HOST}")
    # 公网入口连续失败 5 次（LOGIN_MAX_FAILS）→ 公网侧被锁
    for _ in range(5):
        r = _login(client, PUBLIC_HOST, "wrong-pass")
        assert r.status_code == 401
    r = _login(client, PUBLIC_HOST, ADMIN_PASS)   # 公网侧：对的密码也进不来（已锁）
    assert r.status_code == 429
    # 局域网入口：同一账号正常登录，不受公网攻击波及（DoS 隔离核心断言）
    r = _sub(client).post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS},
                          headers={"Host": LAN_HOST})
    assert r.status_code == 200 and r.json()["ok"] is True
    # LAN 登录成功不解除公网侧锁定（攻击者继续被关在公网门外）
    r = _login(client, PUBLIC_HOST, ADMIN_PASS)
    assert r.status_code == 429


def test_lan_lockout_isolated_to_lan(client, auth):
    _set_public(auth, f"http://{PUBLIC_HOST}")
    for _ in range(5):
        _login(client, LAN_HOST, "wrong-pass")
    assert _login(client, LAN_HOST, ADMIN_PASS).status_code == 429     # LAN 锁 LAN
    # 公网不受 LAN 失败影响（成功登录走子客户端，不污染共享罐）
    r = _sub(client).post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS},
                          headers={"Host": PUBLIC_HOST})
    assert r.status_code == 200


def test_unset_public_url_keeps_single_bucket(client, auth):
    # 未配置公网地址 → is_public_entry 恒 False，所有请求同一入口（向后兼容）
    _set_public(auth, "")
    for _ in range(5):
        _login(client, PUBLIC_HOST, "wrong-pass")
    assert _login(client, PUBLIC_HOST, ADMIN_PASS).status_code == 429
    assert _login(client, LAN_HOST, ADMIN_PASS).status_code == 429   # 同一账本
