"""备用公网入口（2026-09-03 樱花FRP 主/备双隧道）：

- public_base_url_alt 备用地址：is_public_entry 主备任一命中（防爆破分账/healthz
  收敛对备用入口同样生效），主备相同打回 400
- /api/app/entries 免登录下发主/备地址 + 证书指纹（App 登录页快捷选择/预校验用）
"""

import re

from conftest import ADMIN_PASS, ADMIN_USER
from app.main import app

PRIMARY_HOST = "frp-main.example.com:35391"
ALT_HOST = "frp-alt.example.com:35390"
LAN_HOST = "lan.local:8765"

_FP_RE = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")


def _set_public(auth, url: str, url_alt: str = ""):
    return auth.post("/api/admin/public-url", json={"url": url, "url_alt": url_alt})


def test_alt_host_treated_as_public(client, auth):
    r = _set_public(auth, f"http://{PRIMARY_HOST}", f"http://{ALT_HOST}")
    assert r.status_code == 200 and r.json()["public_base_url_alt"].endswith("35390")
    # 备用入口：healthz 同样收敛（否则备用隧道会泄露磁盘水位 + 防爆破不分账）
    assert client.get("/healthz", headers={"Host": ALT_HOST}).status_code == 404
    # 主入口照旧、局域网入口照旧
    assert client.get("/healthz", headers={"Host": PRIMARY_HOST}).status_code == 404
    r = client.get("/healthz", headers={"Host": LAN_HOST})
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_alt_same_netloc_as_primary_rejected(client, auth):
    r = _set_public(auth, f"https://{PRIMARY_HOST}", f"https://{PRIMARY_HOST}")
    assert r.status_code == 400
    # 备用与主不同 scheme 但同 netloc 也算重复
    r = _set_public(auth, f"https://{PRIMARY_HOST}", f"http://{PRIMARY_HOST}")
    assert r.status_code == 400


def test_alt_bad_scheme_rejected(client, auth):
    assert _set_public(auth, f"https://{PRIMARY_HOST}", "ftp://x.example.com").status_code == 400


def test_entries_endpoint_no_auth(client, auth):
    _set_public(auth, f"https://{PRIMARY_HOST}", f"https://{ALT_HOST}")
    r = client.get("/api/app/entries", headers={"Host": LAN_HOST})
    assert r.status_code == 200
    data = r.json()
    assert data["public"] == f"https://{PRIMARY_HOST}"
    assert data["public_alt"] == f"https://{ALT_HOST}"
    # 仓库自带真实证书：指纹是 32 字节 SHA256 的冒号 hex（与 App TOFU 弹框逐字可比）
    assert _FP_RE.match(data["cert_sha256"] or "")


def test_entries_null_when_unset(client, auth):
    _set_public(auth, "", "")
    r = client.get("/api/app/entries", headers={"Host": LAN_HOST})
    assert r.status_code == 200
    data = r.json()
    assert data["public"] is None and data["public_alt"] is None


def test_login_still_works_after_alt_set(client):
    """备用地址配置不干扰正常登录（settings 双键写入的回归保护）。"""
    c = client.__class__(app, base_url="https://testserver")
    r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS},
               headers={"Host": LAN_HOST})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_entries_lan_advertised(client, auth):
    """b8b3：entries 下发本机局域网地址现值——App 历史候选全失败时的
    自动再发现来源。只允许 RFC1918 私网 + 8765 端口 + 去重 + 上限 4。"""
    _set_public(auth, f"https://{PRIMARY_HOST}", f"https://{ALT_HOST}")
    r = client.get("/api/app/entries", headers={"Host": LAN_HOST})
    assert r.status_code == 200
    # 本文件末尾的用例须把 settings 清回空（沿用 test_entries_null_when_unset
    # 的"文件级清场"约定：后续文件的用例依赖 netlocs 为空的兜底分支）
    _set_public(auth, "", "")
    r = client.get("/api/app/entries", headers={"Host": LAN_HOST})
    data = r.json()
    assert data["public"] is None
    assert isinstance(data["lan"], list)
    assert len(data["lan"]) <= 4
    assert len(set(data["lan"])) == len(data["lan"])
    for a in data["lan"]:
        assert a.startswith("https://") and a.endswith(":8765")
        parts = a[len("https://"):-len(":8765")].split(".")
        assert len(parts) == 4 and all(p.isdigit() for p in parts)
        o = [int(p) for p in parts]
        assert o[0] == 10 or (o[0] == 172 and 16 <= o[1] <= 31)             or (o[0] == 192 and o[1] == 168), a


def test_lan_addresses_filter(monkeypatch):
    """过滤逻辑单测（注入固定 getaddrinfo，双引擎行为一致）：只留 RFC1918、
    去重、保持顺序、上限 4。"""
    import socket as _socket
    from app.api import appver

    def fake_getaddrinfo(host, port, family):
        rows = [("eth", (), None, "", ("192.168.1.100", 0)),
                ("eth", (), None, "", ("192.168.1.100", 0)),   # 重复
                ("wl", (), None, "", ("169.254.9.9", 0)),      # 链路本地
                ("eth", (), None, "", ("8.8.8.8", 0)),         # 公网
                ("eth", (), None, "", ("10.0.0.5", 0)),
                ("eth", (), None, "", ("172.20.1.1", 0)),
                ("eth", (), None, "", ("fe80::1", 0)),         # 非 IPv4 结构
                ("v", (), None, "", ("172.32.0.1", 0))]        # 172 越界
        return rows

    monkeypatch.setattr(appver.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(appver.socket, "AF_INET", _socket.AF_INET)
    got = appver._lan_addresses()
    assert got == ["https://192.168.1.100:8765", "https://10.0.0.5:8765",
                   "https://172.20.1.1:8765"]


def test_lan_addresses_getaddrinfo_oserror(monkeypatch):
    from app.api import appver

    def boom(*a, **k):
        raise OSError("no net")
    monkeypatch.setattr(appver.socket, "getaddrinfo", boom)
    assert appver._lan_addresses() == []
