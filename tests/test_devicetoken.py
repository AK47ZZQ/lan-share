"""M-A0 设备 Token 回归：会话换 Bearer Token、设备列表/吊销、滑动续期、
过期与停用账号失效、设备隔离与校验（APP-DESIGN §3）。

Bearer 与 Cookie 并存：require_user 先查 Cookie 再查 Bearer；浏览器不携带
Authorization 头，故 Bearer 路径无 CSRF 面；审计用户名由中间件同等注入。
"""
import os
import time

from conftest import ADMIN_PASS, ADMIN_USER, MEMBER_PASS

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


def _mk_member(client, username: str, password: str = MEMBER_PASS):
    admin = _admin(client)
    inv = admin.post("/api/admin/invites", json={"role": "member", "hours": 24},
                     cookies=dict(admin.cookies)).json()
    r = client.post("/api/invites/redeem", json={
        "code": inv["code"], "username": username, "password": password})
    assert r.status_code == 200, r.text


def test_device_token_full_lifecycle(client):
    admin = _admin(client)
    bare = _bare(client)

    # 会话（Cookie）换设备 Token：明文只出现这一次
    r = admin.post("/api/auth/token", json={"name": "小米 14"})
    assert r.status_code == 200, r.text
    data = r.json()
    tok = data["token"]
    assert tok and data["device_id"] > 0 and data["name"] == "小米 14"

    # Bearer 无 Cookie 访问业务端点（业务端点 + 身份探测）
    h = {"Authorization": f"Bearer {tok}"}
    assert bare.get("/api/files/", headers=h).status_code == 200
    me = bare.get("/api/auth/me", headers=h).json()
    assert me["username"] == ADMIN_USER and "role" in me

    # 设备列表：Cookie 会话可见设备但 current=false；Bearer 会话 current=true
    devs = admin.get("/api/auth/devices").json()["devices"]
    assert len(devs) == 1 and devs[0]["name"] == "小米 14"
    assert devs[0]["current"] is False and "token" not in devs[0]
    devs = bare.get("/api/auth/devices", headers=h).json()["devices"]
    assert len(devs) == 1 and devs[0]["current"] is True

    # Bearer 自己吊销自己 → 之后 401
    assert bare.delete(f"/api/auth/devices/{devs[0]['id']}", headers=h).status_code == 200
    assert bare.get("/api/files/", headers=h).status_code == 401
    assert bare.get("/api/auth/devices", headers=h).status_code == 401

    # 审计：签发与吊销都留痕
    actions = {r["action"] for r in server._db().execute(
        "SELECT action FROM audit_log WHERE action LIKE 'device%'").fetchall()}
    assert {"device_token_create", "device_revoke"} <= actions


def test_device_token_expiry_and_disabled_account(client):
    admin = _admin(client)
    bare = _bare(client)

    tok = admin.post("/api/auth/token", json={"name": "d2"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert bare.get("/api/files/", headers=h).status_code == 200

    # 手工置为已过期 → 401
    server._db().execute("UPDATE device_tokens SET expires_at=? WHERE name='d2'",
                         (time.time() - 1,))
    server._db().commit()
    assert bare.get("/api/files/", headers=h).status_code == 401

    # 停用账号 → 该账号所有 Bearer 即刻失效（JOIN users 过滤）
    tok2 = admin.post("/api/auth/token", json={"name": "d3"}).json()["token"]
    h2 = {"Authorization": f"Bearer {tok2}"}
    assert bare.get("/api/files/", headers=h2).status_code == 200
    uid = server._db().execute(
        "SELECT id FROM users WHERE username=?", (ADMIN_USER,)).fetchone()["id"]
    try:
        server._db().execute("UPDATE users SET is_active=0 WHERE id=?", (uid,))
        server._db().commit()
        assert bare.get("/api/files/", headers=h2).status_code == 401
    finally:
        server._db().execute("UPDATE users SET is_active=1 WHERE id=?", (uid,))
        server._db().commit()
    assert bare.get("/api/files/", headers=h2).status_code == 200


def test_device_token_sliding_renewal(client):
    admin = _admin(client)
    bare = _bare(client)
    tok = admin.post("/api/auth/token", json={"name": "slide"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    # 距过期只剩 10 天（< 90-1 天窗口）→ 一次请求滑动续期回 ~90 天
    server._db().execute(
        "UPDATE device_tokens SET expires_at=? WHERE name='slide'",
        (time.time() + 10 * 86400,))
    server._db().commit()
    assert bare.get("/api/files/", headers=h).status_code == 200
    row = server._db().execute(
        "SELECT expires_at FROM device_tokens WHERE name='slide'").fetchone()
    assert row["expires_at"] > time.time() + 80 * 86400


def test_device_token_rotation_same_name(client):
    """同名设备重复签发 = 轮换（重装/重复登录不产生僵尸设备）。"""
    admin = _admin(client)
    bare = _bare(client)
    r1 = admin.post("/api/auth/token", json={"name": "轮换机"}).json()
    r2 = admin.post("/api/auth/token", json={"name": "轮换机"}).json()
    assert r1["device_id"] == r2["device_id"] and r1["token"] != r2["token"]
    # 旧 token 失效、新 token 可用；同名设备仍只有一台
    assert bare.get("/api/files/", headers={
        "Authorization": f"Bearer {r1['token']}"}).status_code == 401
    assert bare.get("/api/files/", headers={
        "Authorization": f"Bearer {r2['token']}"}).status_code == 200
    same = [d for d in admin.get("/api/auth/devices").json()["devices"]
            if d["name"] == "轮换机"]
    assert len(same) == 1


def test_device_token_isolation_and_validation(client):
    admin = _admin(client)
    bare = _bare(client)

    # 校验：空白 / 控制字符 / 超长拒收；未登录 401
    assert admin.post("/api/auth/token", json={"name": "   "}).status_code == 400
    assert admin.post("/api/auth/token", json={"name": "x" * 65}).status_code == 400
    assert bare.post("/api/auth/token", json={"name": "匿名"}).status_code == 401

    # 设备按账号隔离：成员看不到 admin 的设备，也吊销不了
    uname = "tokuser-" + _rand()
    _mk_member(client, uname)
    member = _bare(client)
    assert member.post("/api/login", json={
        "username": uname, "password": MEMBER_PASS}).status_code == 200
    member.post("/api/auth/token", json={"name": "成员手机"})
    admin.post("/api/auth/token", json={"name": "admin 手机"})
    admin_dev = next(d for d in admin.get("/api/auth/devices").json()["devices"]
                     if d["name"] == "admin 手机")
    assert [d["name"] for d in member.get("/api/auth/devices").json()["devices"]] == ["成员手机"]
    assert member.delete(f"/api/auth/devices/{admin_dev['id']}").status_code == 404
    # admin 的设备不受影响
    assert "admin 手机" in [d["name"] for d in admin.get("/api/auth/devices").json()["devices"]]
