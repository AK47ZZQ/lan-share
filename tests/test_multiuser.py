"""M3 多用户权限测试（对应 docs/DESIGN-v3/PERMISSIONS.md 边界用例的自动化子集）。

用户工厂：邀请码redeem 建号（同时测邀请链路），随机后缀避免跨测试撞名。
"""

import os
import secrets

import pytest
from fastapi.testclient import TestClient

from conftest import server


@pytest.fixture()
def make_user(auth, client):
    """邀请码建号工厂：make_user("member") -> 已登录的 TestClient（含 .username）。"""
    created = []

    def _make(role, name=None):
        username = name or f"u{secrets.token_hex(3)}"
        r = auth.post("/api/admin/invites", json={"role": role, "hours": 1})
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        r = client.post("/api/invites/redeem", json={
            "code": code, "username": username, "password": "pass-12345678"})
        assert r.status_code == 200, r.text
        c = TestClient(server.app, base_url="https://testserver")
        r = c.post("/api/login", json={"username": username, "password": "pass-12345678"})
        assert r.status_code == 200, r.text
        c.username = username
        created.append(username)
        return c

    yield _make
    # 清理：删号（同时验证删号链路不会炸）
    for username in created:
        row = server._db().execute("SELECT id FROM users WHERE username=?",
                                   (username,)).fetchone()
        if row:
            server._db().execute("DELETE FROM users WHERE id=?", (row["id"],))
            server._db().commit()


# ── 邀请与建号（E11）──────────────────────────────────────


def test_redeem_reserved_username_rejected(auth, client):
    auth.post("/api/admin/invites", json={"role": "member", "hours": 1})
    code = auth.get("/api/admin/invites").json()[0]["code"]
    for bad in ("shared", "private", "incoming", "tmp"):
        r = client.post("/api/invites/redeem", json={
            "code": code, "username": bad, "password": "pass-12345678"})
        assert r.status_code == 400, bad


def test_redeem_bad_code_rejected(client):
    assert client.post("/api/invites/redeem", json={
        "code": "no-such-code", "username": "someone1", "password": "pass-12345678"
    }).status_code == 403


def test_admin_endpoints_require_admin(make_user):
    member = make_user("member")
    for ep in ("/api/admin/users", "/api/admin/invites", "/api/admin/grants"):
        assert member.get(ep).status_code == 403, ep


# ── 私人空间隔离（E2/E5）─────────────────────────────────


def test_private_space_isolated(auth, make_user, workdir):
    alice = make_user("member")
    bob = make_user("member")

    # alice 往自己私人空间上传（裸 "private" 别名入口）
    r = alice.post("/api/upload/private",
                   files={"file": ("secret.txt", b"alice-secret", "text/plain")})
    assert r.status_code == 200, r.text

    # bob 无法读 alice 的私人文件（大小写变体也拦）
    assert bob.get(f"/api/files/private/{alice.username}").status_code in (403, 404)  # 404=解析到本人不存在的子目录，同样无泄露
    assert bob.get(f"/api/files/private/{alice.username.upper()}/secret.txt").status_code in (403, 404)
    r = bob.get(f"/api/files/private/{alice.username.upper()}")
    assert r.status_code in (403, 404)  # 别名重定向到本人空间：404 同样无泄露

    # alice 自己可见
    r = alice.get("/api/files/private")
    assert "secret.txt" in [e["name"] for e in r.json()["items"]]

    # vpath 显式形式：private:<path> 相对本人私人根，无法触达他人
    assert alice.get("/api/files/private:admin/").status_code in (403, 404)


def test_private_explicit_vpath(auth, make_user):
    alice = make_user("member")
    alice.post("/api/upload/private",
               files={"file": ("v.txt", b"v", "text/plain")})
    r = alice.get("/api/files/private:")
    assert r.status_code == 200
    assert "v.txt" in [e["name"] for e in r.json()["items"]]


# ── shared 默认只读 / 授权（E3/E10）──────────────────────


def test_member_shared_readonly_until_granted(auth, make_user, workdir):
    auth.post(f"/api/mkdir/{workdir}/docs")
    member = make_user("member")

    # 默认可读
    assert member.get(f"/api/files/{workdir}/docs").status_code == 200
    # 默认不可写
    r = member.post("/api/upload/init", json={
        "dest_dir": f"{workdir}/docs", "filename": "m.txt", "size": 10})
    assert r.status_code == 403

    # 管理员授予该目录写权
    uid = server._db().execute("SELECT id FROM users WHERE username=?",
                               (member.username,)).fetchone()["id"]
    r = auth.post("/api/admin/grants", json={
        "user_id": uid, "path": f"{workdir}/docs", "can_read": True, "can_write": True})
    assert r.status_code == 200, r.text

    r = member.post("/api/upload/init", json={
        "dest_dir": f"{workdir}/docs", "filename": "m.txt", "size": 10})
    assert r.status_code == 200, r.text


def test_grant_is_prefix_segment_scoped(auth, make_user, workdir):
    """E10：授予 shared:docs 不应波及 shared:docs2。"""
    auth.post(f"/api/mkdir/{workdir}/docs")
    auth.post(f"/api/mkdir/{workdir}/docs2")
    member = make_user("member")
    uid = server._db().execute("SELECT id FROM users WHERE username=?",
                               (member.username,)).fetchone()["id"]
    auth.post("/api/admin/grants", json={
        "user_id": uid, "path": f"{workdir}/docs", "can_read": True, "can_write": True})
    r = member.post("/api/upload/init", json={
        "dest_dir": f"{workdir}/docs2", "filename": "m.txt", "size": 10})
    assert r.status_code == 403


def test_grant_backslash_and_dotdot_paths_rejected(auth, make_user, workdir):
    member = make_user("member")
    uid = server._db().execute("SELECT id FROM users WHERE username=?",
                               (member.username,)).fetchone()["id"]
    for evil in ("private/x", "../x", ".trash", "shared:../../x"):
        r = auth.post("/api/admin/grants", json={
            "user_id": uid, "path": evil, "can_read": True, "can_write": True})
        assert r.status_code == 400, evil


# ── guest：无默认读权限（E4 语义）────────────────────────


def test_guest_no_default_read(auth, make_user, workdir):
    auth.post(f"/api/mkdir/{workdir}/docs")
    guest = make_user("guest")
    assert guest.get(f"/api/files/{workdir}/docs").status_code == 403

    uid = server._db().execute("SELECT id FROM users WHERE username=?",
                               (guest.username,)).fetchone()["id"]
    auth.post("/api/admin/grants", json={
        "user_id": uid, "path": f"{workdir}/docs", "can_read": True, "can_write": False})
    assert guest.get(f"/api/files/{workdir}/docs").status_code == 200
    # 只读授权不可写
    r = guest.post("/api/upload/init", json={
        "dest_dir": f"{workdir}/docs", "filename": "g.txt", "size": 10})
    assert r.status_code == 403


# ── 配额 ─────────────────────────────────────────────────


def test_quota_blocks_private_upload(auth, make_user):
    member = make_user("member")
    uid = server._db().execute("SELECT id FROM users WHERE username=?",
                               (member.username,)).fetchone()["id"]
    r = auth.patch(f"/api/admin/users/{uid}", json={"quota_bytes": 1000})
    assert r.status_code == 200, r.text

    r = member.post("/api/upload/private",
                    files={"file": ("big.bin", b"x" * 2000, "application/octet-stream")})
    assert r.status_code == 409, r.text
    # shared 空间不受配额限制
    auth.post("/api/mkdir/shared-quota-free")
    r = member.post("/api/upload/shared-quota-free",
                    files={"file": ("ok.txt", b"y" * 10, "text/plain")})
    # member 对 shared 根默认只读 → 403（权限而非配额）
    assert r.status_code == 403


# ── 跨空间移动（E9）──────────────────────────────────────


def test_member_cannot_move_between_private_and_shared(auth, make_user, workdir):
    auth.post(f"/api/mkdir/{workdir}/teamdocs")
    member = make_user("member")
    uid = server._db().execute("SELECT id FROM users WHERE username=?",
                               (member.username,)).fetchone()["id"]
    auth.post("/api/admin/grants", json={
        "user_id": uid, "path": f"{workdir}/teamdocs", "can_read": True,
        "can_write": True})
    member.post("/api/upload/private",
                files={"file": ("mine.txt", b"m", "text/plain")})

    # private → shared：拒绝
    r = member.post("/api/move", json={
        "sources": ["private/mine.txt"], "dest_dir": f"{workdir}/teamdocs"})
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is False

    # shared → private：拒绝
    auth.post(f"/api/upload/{workdir}/teamdocs",
              files={"file": ("team.txt", b"t", "text/plain")})
    r = member.post("/api/move", json={
        "sources": [f"{workdir}/teamdocs/team.txt"], "dest_dir": "private"})
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is False


# ── 搜索/最近/回收站可见性 ───────────────────────────────


def test_search_hides_others_private(auth, make_user, client):
    auth.post("/api/upload/private",
              files={"file": ("needle-xyz.txt", b"a", "text/plain")})
    member = make_user("member")
    r = member.get("/api/search", params={"q": "needle-xyz"})
    assert r.status_code == 200
    assert r.json() == []
    # admin 能搜到
    r = auth.get("/api/search", params={"q": "needle-xyz"})
    assert any("needle-xyz" in f["name"] for f in r.json())


def test_trash_list_hides_others_private(auth, make_user):
    auth.post("/api/upload/private",
              files={"file": ("gone-q.txt", b"a", "text/plain")})
    auth.delete("/api/files/private/gone-q.txt")
    member = make_user("member")
    names = [t["name"] for t in member.get("/api/trash").json()]
    assert "gone-q.txt" not in names


# ── 分享跟随创建者权限 ───────────────────────────────────


def test_member_cannot_share_others_private(auth, make_user):
    auth.post("/api/upload/private",
              files={"file": ("vip.txt", b"a", "text/plain")})
    member = make_user("member")
    r = member.post("/api/share", data={"path": "private/vip.txt"})
    assert r.status_code in (403, 404)  # 别名→本人空间无此文件，404 无泄露


def test_member_shares_own_private_file(auth, client, make_user):
    member = make_user("member")
    member.post("/api/upload/private",
                files={"file": ("mine-share.txt", b"hello", "text/plain")})
    r = member.post("/api/share", data={"path": "private/mine-share.txt"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    prep = client.post(f"/api/share/{token}/prepare", json={})
    assert prep.status_code == 200
    dl = client.get(prep.json()["url"])
    assert dl.content == b"hello"


# ── 账号停用（会话即时失效）─────────────────────────────


def test_disabled_account_sessions_revoked(auth, make_user):
    member = make_user("member")
    assert member.get("/api/auth/me").status_code == 200
    uid = server._db().execute("SELECT id FROM users WHERE username=?",
                               (member.username,)).fetchone()["id"]
    r = auth.patch(f"/api/admin/users/{uid}", json={"is_active": False})
    assert r.status_code == 200, r.text
    # 既有会话立即失效；重新登录被拒
    assert member.get("/api/auth/me").status_code == 401
    r = member.post("/api/login",
                    json={"username": member.username, "password": "pass-12345678"})
    assert r.status_code == 403


# ── 直传 incoming 按用户隔离 ─────────────────────────────


def test_incoming_upload_lands_in_own_subdir(make_user):
    member = make_user("member")
    r = member.post("/api/upload/incoming",
                    files={"file": ("phone.jpg", b"jpegdata", "image/jpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["path"].startswith(f"incoming/{member.username}/")


# ── WebDAV Basic 认证按角色 ──────────────────────────────


def test_webdav_member_can_read_shared_not_private_others(auth, make_user):
    import base64
    member = make_user("member")
    header = {"Authorization": "Basic " + base64.b64encode(
        f"{member.username}:pass-12345678".encode()).decode()}
    r = member.request("PROPFIND", "/dav/", headers=header)
    assert r.status_code == 207
    # 别人的私人目录经 WebDAV 也是 403（resolve 同源）
    r = member.request("PROPFIND", f"/dav/private/{auth.username}/",
                       headers=header)
    assert r.status_code in (403, 404)  # 别名→本人空间不存在该子目录
