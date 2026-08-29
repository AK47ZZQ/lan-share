"""v3.5 功能回归：定向分享/站内通知、共享相册维度+精选专辑、TOTP 两步验证。

约定：conftest 里 setup 建了唯一 admin（ADMIN_USER）。需要第二个账号时用
邀请码 redeem 现场建 member（同时覆盖邀请链路）。
"""

import io
import os

import pyotp

from conftest import ADMIN_PASS, ADMIN_USER

import server  # noqa: E402  (conftest 已先设置 SHARE_ROOT 并导入)


def _rand() -> str:
    return os.urandom(3).hex()


def _member_session(client, username: str, password: str = "member-pass-123"):
    c = client.__class__(server.app, base_url="https://testserver")
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return c


def _admin(client):
    c = client.__class__(server.app, base_url="https://testserver")
    r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200
    return c


def _mk_member(client, username: str, password: str = "member-pass-123"):
    """邀请码建号（覆盖邀请链路），返回可登录凭据。"""
    admin = _admin(client)
    inv = admin.post("/api/admin/invites", json={"role": "member", "hours": 24},
                     cookies=dict(admin.cookies)).json()
    r = client.post("/api/invites/redeem", json={
        "code": inv["code"], "username": username, "password": password})
    assert r.status_code == 200, r.text


# ── 定向分享 + 站内通知 ───────────────────────────────────

def test_targeted_share_flow(client, workdir):
    uname = "tsharer-" + _rand()
    _mk_member(client, uname)
    admin = _admin(client)
    member = _member_session(client, uname)
    member2 = _member_session(client, uname)  # 同一成员第二个会话

    # admin 在 shared 建文件并定向分享给 tsharer
    assert admin.post("/api/mkdir/shared-target", cookies=dict(admin.cookies)).status_code == 200
    f = io.BytesIO(b"hello target")
    r = admin.post("/api/upload/shared-target", files={"file": ("secret.txt", f)},
                   cookies=dict(admin.cookies))
    assert r.status_code == 200, r.text
    r = admin.post("/api/share", data={
        "path": "shared-target/secret.txt", "expires_hours": "2", "target": uname},
        cookies=dict(admin.cookies))
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    # 匿名访问 → 404（不泄露存在性），detail 带定向标识
    r = client.get(f"/api/share/meta/{token}")
    assert r.status_code == 404
    assert "定向" in r.json()["detail"]

    # 目标成员登录 → meta 可见且无需提取码；直接 prepare 下载
    r = member.get(f"/api/share/meta/{token}")
    assert r.status_code == 200 and r.json()["targeted"] is True
    assert r.json()["needs_password"] is False
    r = member.post(f"/api/share/{token}/prepare", json={})
    assert r.status_code == 200 and "url" in r.json()

    # 目标成员收到站内通知
    r = member2.get("/api/notifications")
    assert r.status_code == 200
    assert r.json()["unread"] >= 1
    assert any(n["type"] == "share" and n["link"] == f"/s/{token}" for n in r.json()["items"])
    # 标记已读
    assert member2.post("/api/notifications/read").status_code == 200
    assert member2.get("/api/notifications").json()["unread"] == 0

    # 分享管理列表带 target 字段
    r = admin.get("/api/shares")
    assert r.json()[token]["target"] == uname

    # 非目标成员不可见
    oname = "otheruser-" + _rand()
    assert client.post("/api/invites/redeem", json={
        "code": admin.post("/api/admin/invites", json={"role": "member", "hours": 1},
                           cookies=dict(admin.cookies)).json()["code"],
        "username": oname, "password": "other-pass-123"}).status_code == 200
    other = _member_session(client, oname, "other-pass-123")
    assert other.get(f"/api/share/meta/{token}").status_code == 404


def test_targeted_share_rejects_unknown_member(client, workdir):
    admin = _admin(client)
    r = admin.post("/api/share", data={
        "path": "whatever", "expires_hours": "2", "target": "no-such-user"})
    assert r.status_code in (404, 400)


# ── 共享相册：维度 + 精选专辑 ─────────────────────────────

def test_photos_month_uploader_and_albums(client, workdir):
    admin = _admin(client)
    admin.post("/api/mkdir/pics-" + _rand(), cookies=dict(admin.cookies))
    f = io.BytesIO(b"\x89PNG\r\n\x1a\nfake")
    admin.post("/api/upload/pics-" + _rand(), files={"file": ("a.png", f)},
               cookies=dict(admin.cookies))
    r = admin.get("/api/photos")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "照片扫描应有条目"
    # 时间线全量条目都带维度字段
    for it in items:
        assert len(it["month"]) == 7 and it["month"][4] == "-"
        assert it["uploader"]
    # incoming/<用户>/ 与共享目录的归属维度正确
    mine = [it for it in items if it["name"] == "a.png"]
    assert mine and mine[0]["uploader"] == "共享空间"
    others = [it for it in items if it["path"].startswith("incoming/")]
    if others:
        assert others[0]["uploader"] == others[0]["path"].split("/")[1]

    facets = admin.get("/api/photos/facets").json()
    assert facets["months"] and facets["uploaders"]

    # 精选专辑：管理员增删；普通用户可读不可写
    month = facets["months"][0]["month"]
    r = admin.post("/api/admin/albums", json={"title": "全家福", "month": month})
    assert r.status_code == 200
    albums = admin.get("/api/albums").json()
    assert any(a["title"] == "全家福" for a in albums)
    dup = admin.post("/api/admin/albums", json={"title": "重复", "month": month})
    assert dup.status_code == 409
    bad = admin.post("/api/admin/albums", json={"title": "坏", "month": "2026/08"})
    assert bad.status_code == 400
    album_id = albums[0]["id"]
    vname = "albumview-" + _rand()
    _mk_member(client, vname)
    member = _member_session(client, vname)
    assert member.get("/api/albums").status_code == 200
    assert member.post("/api/admin/albums", json={"title": "x", "month": month}).status_code == 403
    assert admin.delete(f"/api/admin/albums/{album_id}").status_code == 200


# ── TOTP 两步验证 ─────────────────────────────────────────

def test_totp_full_flow(client):
    admin = _admin(client)
    # 未启用时 me.totp_enabled=False
    assert admin.get("/api/auth/me").json()["totp_enabled"] is False
    # enable 之前 setup 必须先行
    assert admin.post("/api/auth/totp/enable", json={"code": "123456"}).status_code == 400
    secret = admin.post("/api/auth/totp/setup", json={}).json()["secret"]
    # 错误验证码 → 400
    assert admin.post("/api/auth/totp/enable", json={"code": "000000"}).status_code == 400
    code = pyotp.TOTP(secret).now()
    assert admin.post("/api/auth/totp/enable", json={"code": code}).status_code == 200
    assert admin.get("/api/auth/me").json()["totp_enabled"] is True

    # 登录：密码正确也只返回 totp_required，不发会话
    r = admin.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.json().get("totp_required") is True
    assert not r.cookies.get("ls_session")

    # 错误动态码 → 401；正确动态码 → 发会话
    assert admin.post("/api/login/totp", json={
        "username": ADMIN_USER, "password": ADMIN_PASS, "code": "000000"}).status_code == 401
    r = admin.post("/api/login/totp", json={
        "username": ADMIN_USER, "password": ADMIN_PASS, "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200 and r.cookies.get("ls_session")

    # 关闭需要原密码；关闭后登录恢复一步
    assert admin.post("/api/auth/totp/disable", json={"old_password": "wrong-pass"}).status_code == 403
    assert admin.post("/api/auth/totp/disable", json={"old_password": ADMIN_PASS}).status_code == 200
    r = admin.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.json().get("ok") is True

    # admin 重置成员密码会同步清 TOTP
    mname = "totpmember-" + _rand()
    _mk_member(client, mname)
    member = _member_session(client, mname)
    msecret = member.post("/api/auth/totp/setup", json={}).json()["secret"]
    member.post("/api/auth/totp/enable", json={"code": pyotp.TOTP(msecret).now()})
    uid = [u for u in admin.get("/api/admin/users").json() if u["username"] == mname][0]["id"]
    assert admin.post(f"/api/admin/users/{uid}/password", json={"value": "reset-pass-123"}).status_code == 200
    c = client.__class__(server.app, base_url="https://testserver")
    r = c.post("/api/login", json={"username": mname, "password": "reset-pass-123"})
    assert r.json().get("ok") is True  # 未要求 TOTP → 已被清除


# ── 引擎中立 SQL（PG 兼容预防回归）────────────────────────

def test_username_case_insensitive_unique(client):
    """原 COLLATE NOCASE 查重已改 LOWER()=LOWER()——行为需保持。"""
    admin = _admin(client)
    inv = admin.post("/api/admin/invites", json={"role": "member", "hours": 1},
                     cookies=dict(admin.cookies)).json()
    case_name = "CaseUser-" + _rand()
    r = client.post("/api/invites/redeem", json={
        "code": inv["code"], "username": case_name, "password": "case-pass-123"})
    assert r.status_code == 200
    inv2 = admin.post("/api/admin/invites", json={"role": "member", "hours": 1},
                      cookies=dict(admin.cookies)).json()
    r = client.post("/api/invites/redeem", json={
        "code": inv2["code"], "username": case_name.lower(), "password": "case-pass-456"})
    assert r.status_code == 409  # 大小写变体仍视为占用


def test_photo_meta_endpoint(client, workdir):
    import io as _io
    from PIL import Image
    admin = _admin(client)
    buf = _io.BytesIO()
    Image.new("RGB", (640, 480), (200, 10, 10)).save(buf, "PNG")
    r = admin.post("/api/upload/meta-test", files={"file": ("m.png", buf.getvalue())})
    assert r.status_code == 200
    r = admin.get("/api/photo/meta", params={"path": "meta-test/m.png"})
    assert r.status_code == 200
    d = r.json()
    assert (d["width"], d["height"]) == (640, 480)
    assert admin.get("/api/photo/meta", params={"path": "meta-test/missing.png"}).status_code == 404
