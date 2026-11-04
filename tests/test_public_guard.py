"""公网入口防护（2026-08-31 加固）：

FRP 隧道使公网流量 client_ip 恒为 127.0.0.1，只能靠 Host 区分入口。
- 防爆破按入口分账：公网侧连续失败只锁公网侧，LAN 家人登录不受波及（DoS 隔离）
- healthz 不对公网暴露（磁盘水位信息泄露）
"""

from conftest import ADMIN_PASS, ADMIN_USER
from app.main import app

PUBLIC_HOST = "frp-test.example.com:35391"   # 测试中的"公网"入口
LAN_HOST = "lan.local:8765"                  # 测试中的"局域网"入口


def _sub(client):
    """独立子客户端：成功登录的会话 cookie 不能落进 session 级共享的 client
    （否则污染后续测试的"匿名"断言——conftest 仅在进程启动时清一次罐）。"""
    return client.__class__(app, base_url="https://testserver")


def _set_public(auth, url: str):
    assert auth.post("/api/admin/public-url", json={"url": url}).status_code == 200


def _login(client, host: str, password: str, username: str = ADMIN_USER):
    return client.post("/api/login", json={"username": username, "password": password},
                       headers={"Host": host})


def test_unknown_host_via_tunnel_is_treated_as_public(auth):
    """P0 回归（2026-11-04 对抗性审查）：隧道把公网流量送进主入口端口时，
    Host 是域名但 netloc 对不上 —— 绝不能因此判成 LAN。

    攻击路径（审查员实测复现）：frpc 本地端口填 8765（文档推荐值）→
    公网请求经隧道进 :8765 站点块 → Host 是任意域名、来源是 127.0.0.1 →
    旧逻辑只看 Host 是否在 public_netlocs 里，不在就返回 False（LAN）→
    免登录的 /local/* 整组对公网开放：能读备份目录绝对路径、
    能 POST /local/public-url 把公网地址改成攻击者的。

    判据是「域名 Host + 回环来源」—— 那是隧道的指纹，不是家人的手机。
    """
    from fastapi.testclient import TestClient
    from app.main import app

    _set_public(auth, f"http://{PUBLIC_HOST}")

    # 模拟 frpc 转发：来源是回环，Host 是个我们没登记过的域名
    with TestClient(app, client=("127.0.0.1", 12345)) as c:
        r = c.get("/local/backup", headers={"Host": "attacker-or-any.example.com:80"})
        assert r.status_code == 403, "未知域名 + 回环来源必须按公网处理，否则 /local/* 全开"

        r = c.get("/healthz", headers={"Host": "attacker-or-any.example.com:80"})
        assert r.status_code == 404, "healthz 不能对未知公网 Host 泄露磁盘水位"


def test_lan_device_using_internal_dns_name_is_still_lan(auth):
    """反过来：局域网设备用内网 DNS 名字访问，来源是真实内网 IP → 仍按 LAN。

    没有这一条，改完 P0 会把「家里用 nas.home 访问」的家人一起判成公网。
    """
    from fastapi.testclient import TestClient
    from app.main import app

    _set_public(auth, f"http://{PUBLIC_HOST}")
    with TestClient(app, client=("192.168.1.100", 12345)) as c:
        r = c.get("/healthz", headers={"Host": "nas.home:8765"})
        assert r.status_code == 200


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
