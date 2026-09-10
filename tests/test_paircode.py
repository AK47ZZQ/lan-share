"""配对码登录回归：已登录方生成一次性 6 位码，手机免密换设备 Token。

安全面：3 分钟时效 + 一码一用 + 按 IP 限速防爆破 + 未登录不可生成。
凭据面：与 /api/auth/token 同规则（同名轮换、设备列表可见、可吊销）。
"""
import os
import time

import pytest

from conftest import ADMIN_PASS, ADMIN_USER

from app.db import _db
from app.main import app
import app.api.devices as _devices


@pytest.fixture()
def clean_devices(client):
    """配对码签出的设备按账号落库（session 级共享），用后清空，
    免得串进 test_devicetoken 的"设备数"断言。"""
    yield
    admin = _admin(client)
    for d in admin.get("/api/auth/devices").json()["devices"]:
        admin.delete(f"/api/auth/devices/{d['id']}")


def _bare(client):
    return client.__class__(app, base_url="https://testserver")


def _admin(client):
    c = _bare(client)
    r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200
    return c


def test_pair_code_full_flow(client, clean_devices):
    admin = _admin(client)
    phone = _bare(client)

    # 生成配对码（Bearer 与 Cookie 会话均可，这里走 Cookie）
    r = admin.post("/api/pair/code")
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert len(code) == 6 and code.isdigit() and r.json()["expires_in"] == 180

    # 手机免密 claim 换设备 Token → Bearer 直接可用
    r = phone.post("/api/pair/claim", json={"code": code, "name": "长辈的手机"})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    assert tok and r.json()["name"] == "长辈的手机"
    me = phone.get("/api/auth/me", headers={
        "Authorization": f"Bearer {tok}"}).json()
    assert me["username"] == ADMIN_USER

    # 一码一用：同一码第二次 claim 即 401
    assert phone.post("/api/pair/claim", json={"code": code}).status_code == 401

    # 签出的设备在设备列表可见、可吊销
    devs = admin.get("/api/auth/devices").json()["devices"]
    dev = next(d for d in devs if d["name"] == "长辈的手机")
    assert admin.delete(f"/api/auth/devices/{dev['id']}").status_code == 200
    assert phone.get("/api/auth/me", headers={
        "Authorization": f"Bearer {tok}"}).status_code == 401


def test_pair_code_expiry(client):
    admin = _admin(client)
    code = admin.post("/api/pair/code").json()["code"]
    # 手工置过期（不改产品行为，白盒拨时钟）
    _devices._pair_codes[code] = (_devices._pair_codes[code][0], time.time() - 1)
    assert _bare(client).post("/api/pair/claim",
                              json={"code": code}).status_code == 401


def test_pair_code_single_per_user(client, clean_devices):
    """同一账号同时只有最新一枚码：重新生成即作废旧码。"""
    admin = _admin(client)
    old = admin.post("/api/pair/code").json()["code"]
    new = admin.post("/api/pair/code").json()["code"]
    assert old != new
    phone = _bare(client)
    assert phone.post("/api/pair/claim", json={"code": old}).status_code == 401
    assert phone.post("/api/pair/claim", json={"code": new}).status_code == 200


def test_pair_code_validation_and_authz(client):
    admin = _admin(client)
    bare = _bare(client)

    # 未登录不可生成；未登录 claim 是正常路径（无 Cookie）
    assert bare.post("/api/pair/code").status_code == 401

    # 格式校验：非数字 / 长度错 / 空白
    assert bare.post("/api/pair/claim", json={"code": "abcdef"}).status_code == 400
    assert bare.post("/api/pair/claim", json={"code": "12345"}).status_code == 400
    assert bare.post("/api/pair/claim", json={"code": " 12 45 "}).status_code in (400, 401)

    # 限速：5 次/分钟/IP，第 6 次拒绝（防 6 位码爆破的主闸）
    for _ in range(5):
        bare.post("/api/pair/claim", json={"code": "000000"})
    assert bare.post("/api/pair/claim", json={"code": "000000"}).status_code == 429

    # 审计留痕
    actions = {r["action"] for r in _db().execute(
        "SELECT action FROM audit_log WHERE action LIKE 'pair%'").fetchall()}
    assert {"pair_code_create", "pair_claim", "pair_claim_fail"} <= actions


def test_pair_claim_rotates_same_name(client, clean_devices):
    """配对码与 /api/auth/token 签发同规则：同名设备轮换，不产生僵尸设备。"""
    admin = _admin(client)
    bare = _bare(client)
    t1 = admin.post("/api/auth/token", json={"name": "轮换手机"}).json()
    code = admin.post("/api/pair/code").json()["code"]
    t2 = bare.post("/api/pair/claim",
                   json={"code": code, "name": "轮换手机"}).json()
    assert t1["device_id"] == t2["device_id"] and t1["token"] != t2["token"]
    assert bare.get("/api/auth/me", headers={
        "Authorization": f"Bearer {t1['token']}"}).status_code == 401
    assert bare.get("/api/auth/me", headers={
        "Authorization": f"Bearer {t2['token']}"}).status_code == 200
