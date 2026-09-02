"""恢复码回归（两步验证第二因子兜底）：开通生成 10 枚、登录一次性消费、
重新生成作废旧码、admin 仅解除不动密码。库存只存 SHA256，明文仅生成响应一次。
"""
import json
import os

import pyotp
import pytest

from conftest import ADMIN_PASS, ADMIN_USER, MEMBER_PASS

import server  # noqa: E402  (conftest 已先设置 SHARE_ROOT 并导入)
import app.api.auth as _auth  # noqa: E402


@pytest.fixture(autouse=True)
def _totp_cleanup():
    """users 表是会话级共享的（conftest 只清内存态）：用例中途失败也会把
    admin 的两步验证状态擦干净，免得串进后续用例（login 变两步 → 401）。"""
    yield
    with server._db_lock:
        server._db().execute(
            "UPDATE users SET totp_secret=NULL, totp_enabled=0, totp_rc=NULL "
            "WHERE username=?", (ADMIN_USER,))
        server._db().commit()


def _rand() -> str:
    return os.urandom(3).hex()


def _bare(client):
    return client.__class__(server.app, base_url="https://testserver")


def _admin(client):
    c = _bare(client)
    r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200
    return c


def _mk_member(client, username: str, password: str = MEMBER_PASS):
    admin = _admin(client)
    inv = admin.post("/api/admin/invites", json={"role": "member", "hours": 24},
                     cookies=dict(admin.cookies)).json()
    r = client.post("/api/invites/redeem", json={
        "code": inv["code"], "username": username, "password": password})
    assert r.status_code == 200, r.text


def _enable(c, username: str, password: str):
    """给指定账号开通两步验证，返回 (secret, recovery_codes)。"""
    secret = c.post("/api/auth/totp/setup", json={}).json()["secret"]
    r = c.post("/api/auth/totp/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def test_enable_returns_ten_unique_hashes_not_plaintext(client):
    admin = _admin(client)
    _, codes = _enable(admin, ADMIN_USER, ADMIN_PASS)
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(_auth._RC_RE.match(c) for c in codes)
    # 库内只存 SHA256：明文不入库，哈希可对上
    row = server._db().execute(
        "SELECT totp_rc FROM users WHERE username=?", (ADMIN_USER,)).fetchone()
    stored = json.loads(row["totp_rc"])
    assert codes[0] not in stored and len(stored) == 10
    assert _auth._rc_hash(codes[0]) in stored
    # me 暴露剩余数
    assert admin.get("/api/auth/me").json()["totp_rc_left"] == 10


def test_login_with_recovery_code_single_use(client):
    admin = _admin(client)
    secret, codes = _enable(admin, ADMIN_USER, ADMIN_PASS)
    # 恢复码顶替动态码登录成功；4-4 连字符 + 大写 + 首尾空白均可归一
    pretty = f" {codes[0][:4].upper()}-{codes[0][4:]} "
    r = _bare(client).post("/api/login/totp", json={
        "username": ADMIN_USER, "password": ADMIN_PASS, "code": pretty})
    assert r.status_code == 200 and r.cookies.get("ls_session")
    # 同一枚码再用 → 401（一次性）
    r2 = _bare(client).post("/api/login/totp", json={
        "username": ADMIN_USER, "password": ADMIN_PASS, "code": codes[0]})
    assert r2.status_code == 401
    # 剩余 9；动态码本身仍可用
    assert admin.get("/api/auth/me").json()["totp_rc_left"] == 9
    r3 = _bare(client).post("/api/login/totp", json={
        "username": ADMIN_USER, "password": ADMIN_PASS, "code": pyotp.TOTP(secret).now()})
    assert r3.status_code == 200
    # 关闭两步验证后恢复码一并清零
    assert admin.post("/api/auth/totp/disable",
                      json={"old_password": ADMIN_PASS}).status_code == 200
    assert admin.get("/api/auth/me").json()["totp_rc_left"] == 0


def test_recovery_code_miss_and_garbage_counted_as_fail(client):
    admin = _admin(client)
    _enable(admin, ADMIN_USER, ADMIN_PASS)
    bare = _bare(client)
    # 未下发的合法格式码 → 401
    assert bare.post("/api/login/totp", json={
        "username": ADMIN_USER, "password": ADMIN_PASS,
        "code": "deadbeef"}).status_code == 401
    # 非法格式 → 401（计入按账号防爆破）
    assert bare.post("/api/login/totp", json={
        "username": ADMIN_USER, "password": ADMIN_PASS,
        "code": "zz!!"}).status_code == 401
    # 已用过的码复用 → 401
    codes = json.loads(server._db().execute(
        "SELECT totp_rc FROM users WHERE username=?", (ADMIN_USER,)).fetchone()["totp_rc"])
    assert len(codes) == 10  # 两次失败均未消费库存


def test_rc_regen_requires_password_invalidates_old(client):
    admin = _admin(client)
    # 未启用 → 400
    assert admin.post("/api/auth/totp/rc",
                      json={"old_password": ADMIN_PASS}).status_code == 400
    _, old_codes = _enable(admin, ADMIN_USER, ADMIN_PASS)
    # 错密码 → 403
    assert admin.post("/api/auth/totp/rc",
                      json={"old_password": "wrong-pass"}).status_code == 403
    r = admin.post("/api/auth/totp/rc", json={"old_password": ADMIN_PASS})
    assert r.status_code == 200
    new_codes = r.json()["recovery_codes"]
    assert len(new_codes) == 10 and not set(new_codes) & set(old_codes)
    # 旧码作废，新码可用
    assert _bare(client).post("/api/login/totp", json={
        "username": ADMIN_USER, "password": ADMIN_PASS,
        "code": old_codes[1]}).status_code == 401
    assert _bare(client).post("/api/login/totp", json={
        "username": ADMIN_USER, "password": ADMIN_PASS,
        "code": new_codes[0]}).status_code == 200


def test_admin_totp_disable_surgical(client):
    """admin 仅解除两步验证：不动密码、不吊销会话；恢复码一并清除。"""
    admin = _admin(client)
    mname = "rcadm-" + _rand()
    _mk_member(client, mname)
    member = client.__class__(server.app, base_url="https://testserver")
    assert member.post("/api/login", json={
        "username": mname, "password": MEMBER_PASS}).status_code == 200
    _enable(member, mname, MEMBER_PASS)
    uid = [u for u in admin.get("/api/admin/users").json()
           if u["username"] == mname][0]["id"]
    assert [u for u in admin.get("/api/admin/users").json()
            if u["username"] == mname][0]["totp_enabled"] is True
    # 未启用账号 → 400；非 admin → 403
    assert member.post(f"/api/admin/users/{uid}/totp-disable").status_code == 403
    # admin 解除成功
    assert admin.post(f"/api/admin/users/{uid}/totp-disable").status_code == 200
    # 成员原密码直接一步登录（未吊销会话不受影响；密码未变），TOTP 状态清空
    r = _bare(client).post("/api/login", json={"username": mname, "password": MEMBER_PASS})
    assert r.json().get("ok") is True
    fresh = _bare(client)
    fresh.post("/api/login", json={"username": mname, "password": MEMBER_PASS})
    assert fresh.get("/api/auth/me").json()["totp_enabled"] is False
    assert fresh.get("/api/auth/me").json()["totp_rc_left"] == 0
