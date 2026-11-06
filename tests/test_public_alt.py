"""公网入口判定 + 备用入口已删除的回归（2026-11-04）。

历史：2026-09-03 加过「主/备双隧道」，public_base_url_alt 存第二条隧道的地址，
is_public_entry 主备任一命中，/api/app/entries 一并下发给 App 选。

2026-11-04 按用户要求删除备用入口 —— 实际用过一次就发现它是坑：那条 alt 被填成了
另一条隧道（HermesAgent）的端口，App 的「备用入口」点进去连的是别的服务。
现在只保留主入口，但**判定语义不能退化**：
healthz 对任何公网 Host 都必须收敛，防爆破必须按公网分账。
"""

import re

from app.main import app

PRIMARY_HOST = "frp-main.example.com:35391"
STALE_ALT_HOST = "frp-alt.example.com:35390"

_FP_RE = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")


def _set_public(auth, url: str, url_alt: str = ""):
    return auth.post("/api/admin/public-url", json={"url": url, "url_alt": url_alt})


def test_primary_host_is_public_entry(client, auth):
    r = _set_public(auth, f"http://{PRIMARY_HOST}")
    assert r.status_code == 200 and r.json()["public_base_url"].endswith("35391")
    # 公网入口：healthz 收敛（磁盘水位不外泄 + 防爆破分账）
    assert client.get("/healthz", headers={"Host": PRIMARY_HOST}).status_code == 404


def test_old_client_url_alt_is_ignored(client, auth):
    """旧版托盘仍会 POST url_alt —— 必须安静忽略，且不落库。

    落库的危害是实打实的：这条值会被 /api/app/entries 下发给 App，
    而它可能指向完全另一条隧道（历史上就发生过）。
    """
    r = _set_public(auth, f"http://{PRIMARY_HOST}", f"http://{STALE_ALT_HOST}")
    assert r.status_code == 200
    assert "public_base_url_alt" not in r.json()

    got = auth.get("/api/admin/public-url").json()
    assert "public_base_url_alt" not in got

    # T4（2026-11-05）：响应体没提 ≠ 库里没有。「不落库」必须读库证明 ——
    # 否则把实现改成「照单全收写进库」这两条测试照样全绿（对抗审查实测）。
    from app.settings import PUBLIC_BASE_URL_ALT, get_setting

    assert get_setting(PUBLIC_BASE_URL_ALT) == ""


def test_stale_alt_cleared_on_save(client, auth):
    """历史残留的 alt 值在下次保存主地址时被清掉。"""
    _set_public(auth, f"http://{PRIMARY_HOST}", f"http://{STALE_ALT_HOST}")
    _set_public(auth, f"http://{PRIMARY_HOST}")
    got = auth.get("/api/admin/public-url").json()
    assert "public_base_url_alt" not in got

    # T4：同上，「会被清掉」读库证明。
    from app.settings import PUBLIC_BASE_URL_ALT, get_setting

    assert get_setting(PUBLIC_BASE_URL_ALT) == ""


def test_entries_do_not_advertise_alt(client, auth):
    """App 入口清单不再下发 public_alt，只给主入口 + 局域网地址。"""
    _set_public(auth, f"http://{PRIMARY_HOST}")
    body = client.get("/api/app/entries").json()
    assert "public_alt" not in body
    assert body.get("public", "").endswith("35391")


def test_entries_are_login_free_and_carry_cert_fingerprint(client, auth):
    """/api/app/entries 免登录：App 在认证之前就要拿到入口列表。"""
    _set_public(auth, f"http://{PRIMARY_HOST}")
    r = client.get("/api/app/entries")   # 不带 cookie
    assert r.status_code == 200
    fp = r.json().get("cert_sha256") or ""
    assert fp == "" or _FP_RE.match(fp), f"证书指纹格式不对: {fp!r}"


def test_lan_addresses_put_real_nic_first(monkeypatch):
    """LAN 候选必须把「真的连着外面」的那张网卡排第一。

    2026-11-06 实机：下发的是 192.168.56.1（VirtualBox）/ 192.168.192.1
    （Hyper-V）/ 172.31.240.1（WSL），真正能连的 192.168.1.100 反而在后面。
    手机照着列表挨个试，前三个必然超时 —— 用户体感是「局域网连不上」。
    """
    import socket as _s
    from app.api import appver

    fake = [
        (2, 1, 6, "", ("192.168.56.1", 0)),
        (2, 1, 6, "", ("192.168.1.100", 0)),
        (2, 1, 6, "", ("192.168.192.1", 0)),
    ]
    monkeypatch.setattr(appver.socket, "getaddrinfo", lambda *a, **k: fake)
    monkeypatch.setattr(appver, "_egress_ip", lambda: "192.168.1.100")

    out = appver._lan_addresses()
    assert out[0] == "https://192.168.1.100:8765", "出口网卡必须排第一"
    # 已知虚拟网卡垫底
    assert out.index("https://192.168.56.1:8765") > 0
    assert out.index("https://192.168.192.1:8765") > 0


def test_egress_ip_is_safe_when_no_route(monkeypatch):
    """没有默认路由（断网/纯内网）时不能抛，优雅返回空串。"""
    from app.api import appver

    class _BadSocket:
        def __init__(self, *a, **k): pass
        def connect(self, addr): raise OSError("no route")
        def getsockname(self): return ("", 0)
        def close(self): pass

    monkeypatch.setattr(appver.socket, "socket", _BadSocket)
    assert appver._egress_ip() == ""


def test_no_alt_in_app_module():
    """防止有人把备用入口加回来却漏掉安全语义。"""
    import app.api.appver as appver

    src = open(appver.__file__, encoding="utf-8").read()
    assert '"public_alt"' not in src.split("def app_entries")[-1]


def test_admin_public_url_rejects_non_string_url(client, auth):
    """T8-③：{url:123} 必须回 422（请求体格式错），不能 500。

    旧实现 body: dict 直接 .get("url")，非字符串在后续 strip() 上炸成 500。
    """
    r = auth.post("/api/admin/public-url", json={"url": 123})
    assert r.status_code == 422, f"期望 422，实际 {r.status_code}"
