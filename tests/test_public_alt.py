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


def test_stale_alt_cleared_on_save(client, auth):
    """历史残留的 alt 值在下次保存主地址时被清掉。"""
    _set_public(auth, f"http://{PRIMARY_HOST}", f"http://{STALE_ALT_HOST}")
    _set_public(auth, f"http://{PRIMARY_HOST}")
    got = auth.get("/api/admin/public-url").json()
    assert "public_base_url_alt" not in got


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


def test_no_alt_in_app_module():
    """防止有人把备用入口加回来却漏掉安全语义。"""
    import app.api.appver as appver

    src = open(appver.__file__, encoding="utf-8").read()
    assert '"public_alt"' not in src.split("def app_entries")[-1]
