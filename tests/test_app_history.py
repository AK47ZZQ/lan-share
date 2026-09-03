"""2026-09-04 App v0.9.0 批次服务端回归：/api/app/history 更新历史 +
QR 中心云朵 logo（/api/qr 与分享码同源）。

约定同 test_appbatch：conftest 已建唯一 admin；dist 夹具自建自清。
"""
import os

import pytest

from conftest import ADMIN_PASS, ADMIN_USER

import server  # noqa: E402  (conftest 已先设置 SHARE_ROOT 并导入)


def _rand() -> str:
    return os.urandom(3).hex()


def _bare(client):
    return client.__class__(server.app, base_url="https://testserver")


def _admin(client):
    c = _bare(client)
    r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200
    return c


@pytest.fixture
def dist_dir():
    d = os.path.join(server.SHARE_ROOT, "App安装包")
    os.makedirs(d, exist_ok=True)
    vj = os.path.join(d, "versions.json")
    backup = open(vj, "rb").read() if os.path.exists(vj) else None
    yield vj
    if backup is not None:
        open(vj, "wb").write(backup)
    elif os.path.exists(vj):
        os.unlink(vj)


def _write_history(vj, payload):
    import json as _json
    with open(vj, "w", encoding="utf-8") as f:
        f.write(_json.dumps(payload, ensure_ascii=False))


def test_history_requires_login(client):
    r = client.get("/api/app/history")
    assert r.status_code in (401, 307)


def test_history_empty_when_missing(client, dist_dir):
    if os.path.exists(dist_dir):
        os.unlink(dist_dir)
    r = _admin(client).get("/api/app/history")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_history_entries_parsed(client, dist_dir):
    _write_history(dist_dir, {"items": [
        {"version": "0.8.14", "build": 36, "date": "2026-09-04", "notes": "a"},
        {"version": "0.8.5", "build": 27, "notes": "无日期也行"},
        {"version": "坏条目没有build"},
        "不是对象也跳过",
    ]})
    r = _admin(client).get("/api/app/history")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert items[0]["version"] == "0.8.14" and items[0]["build"] == 36
    assert items[1]["notes"] == "无日期也行" and items[1]["date"] == ""


def test_history_malformed_file_returns_empty(client, dist_dir):
    with open(dist_dir, "w", encoding="utf-8") as f:
        f.write("not-json{")
    r = _admin(client).get("/api/app/history")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_qr_has_center_cloud_logo(client):
    """QR 出图带中心云朵 logo（嵌套 svg + evenodd path），ECC 升到 Q。"""
    c = _admin(client)
    r = c.get("/api/qr", params={"text": "https://192.168.1.100:8765/up"})
    assert r.status_code == 200
    assert "image/svg" in r.headers["content-type"]
    assert b'x="36%"' in r.content and b"fill-rule" in r.content


def test_share_qr_same_logo(client):
    """分享二维码同样带 logo（同源助手）。自建文件→分享→删，绝不碰真实数据。"""
    c = _admin(client)
    probe = os.path.join(server.SHARE_ROOT, "qr-logo-e2e.txt")
    with open(probe, "w", encoding="utf-8") as f:
        f.write("qr-logo-test")
    try:
        r = c.post("/api/share", data={"path": "qr-logo-e2e.txt",
                                       "expires_hours": 24})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        r = c.get(f"/api/share/{token}/qr")
        assert r.status_code == 200
        assert b'x="36%"' in r.content
    finally:
        if os.path.exists(probe):
            os.unlink(probe)
