"""review 2026-09-02 修复批回归测试：P0-1 /files 收编、P0-2 大小写变体、
P1-1 回收站越权恢复、P1-2 凭据重置吊销设备 Token、P2-1 分块超发、P2-5 遗留分享、
TOTP 动态码防重放。"""

import os
import secrets
import time as _time

import pyotp
import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_PASS, MEMBER_PASS2
from app.config import SHARE_ROOT
from app.db import _db, _db_lock
from app.main import app


@pytest.fixture()
def make_user(auth, client):
    """邀请码建号工厂（与 test_multiuser 同款）：make_user("member") -> 已登录客户端。"""
    created = []

    def _make(role, name=None):
        username = name or f"u{secrets.token_hex(3)}"
        r = auth.post("/api/admin/invites", json={"role": role, "hours": 1})
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        r = client.post("/api/invites/redeem", json={
            "code": code, "username": username, "password": MEMBER_PASS2})
        assert r.status_code == 200, r.text
        c = TestClient(app, base_url="https://testserver")
        r = c.post("/api/login", json={"username": username, "password": MEMBER_PASS2})
        assert r.status_code == 200, r.text
        c.username = username
        created.append(username)
        return c

    yield _make
    for username in created:
        row = _db().execute("SELECT id FROM users WHERE username=?",
                                   (username,)).fetchone()
        if row:
            _db().execute("DELETE FROM users WHERE id=?", (row["id"],))
            _db().commit()


# ── P0-2：特殊段大小写变体 ─────────────────────────────────


def _plant_private_file(username: str, name: str, content: str):
    d = os.path.join(SHARE_ROOT, "private", username)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(content)


def test_case_variant_private_blocked_for_member(auth, make_user):
    """修复前 Private/<他人>/ 落入 shared 默认可读 → 跨用户越权；修复后必须不可读。"""
    _plant_private_file(auth.username, "secret-r2.txt", "top-secret-r2")
    member = make_user("member")
    for variant in ("Private", "private", "PRIVATE"):
        r = member.get(f"/preview/{variant}/{auth.username}/secret-r2.txt")
        assert r.status_code != 200, variant
        assert "top-secret-r2" not in r.text
        r = member.get(f"/api/files/{variant}/{auth.username}")
        assert r.status_code != 200, variant
    # 本人正路仍可用
    r = auth.get(f"/preview/private/{auth.username}/secret-r2.txt")
    assert r.status_code == 200 and "top-secret-r2" in r.text


def test_case_variant_incoming_blocked_for_member(auth, make_user):
    d = os.path.join(SHARE_ROOT, "incoming", auth.username)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "inc-r2.txt"), "w", encoding="utf-8") as f:
        f.write("inc-secret")
    member = make_user("member")
    r = member.get(f"/api/files/Incoming/{auth.username}")
    assert r.status_code != 200
    assert "inc-secret" not in r.text


# ── P0-1：/files 下载端点走权限模型 ────────────────────────


def test_files_route_requires_auth(client):
    assert client.get("/files/anything.txt").status_code == 401


def test_files_route_permission_model(auth, make_user, workdir):
    p = os.path.join(SHARE_ROOT, workdir, "pub-r2.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("shared-hello")
    member = make_user("member")
    r = member.get(f"/files/{workdir}/pub-r2.txt")
    assert r.status_code == 200 and r.content == b"shared-hello"
    # 他人 private：入口重定向到本人空间 → 404/403，总之读不到内容
    _plant_private_file(auth.username, "sec-r2.txt", "x")
    r = member.get(f"/files/private/{auth.username}/sec-r2.txt")
    assert r.status_code in (403, 404)
    assert member.get("/files/.meta.db").status_code == 403


# ── P1-1：回收站越权恢复 ───────────────────────────────────


def test_restore_other_private_trash_denied(make_user):
    alice = make_user("member")
    bob = make_user("member")
    r = alice.post("/api/upload/private:a-r2.txt",
                   files={"file": ("a-r2.txt", b"alice-secret")})
    assert r.status_code == 200, r.text
    assert alice.delete("/api/files/private:a-r2.txt").status_code == 200
    rows = alice.get("/api/trash").json()
    tid = next(x["id"] for x in rows if x["name"] == "a-r2.txt")
    # bob 枚举 id 恢复 alice 的条目：修复前会 200 并落进 bob 空间
    r = bob.post(f"/api/trash/{tid}/restore")
    assert r.status_code == 403, r.text
    # bob 的回收站列表也看不到 alice 的条目
    assert tid not in [x["id"] for x in bob.get("/api/trash").json()]


def test_legacy_vpath_trash_member_denied(make_user):
    """存量 vpath 形态（private:<名>，无用户段）条目：成员不可见不可恢复。"""
    member = make_user("member")
    tp = os.path.join(SHARE_ROOT, ".trash", "9999999999_legacy-r2.txt")
    os.makedirs(os.path.dirname(tp), exist_ok=True)
    with open(tp, "w", encoding="utf-8") as f:
        f.write("x")
    with _db_lock:
        _db().execute(
            "INSERT INTO trash(orig_path, trash_path, name, size, deleted_at) VALUES(?,?,?,?,?)",
            ("private:legacy-r2.txt", ".trash/9999999999_legacy-r2.txt",
             "legacy-r2.txt", 1, _time.time()))
        _db().commit()
    tid = _db().execute(
        "SELECT id FROM trash WHERE orig_path='private:legacy-r2.txt'").fetchone()["id"]
    try:
        assert tid not in [x["id"] for x in member.get("/api/trash").json()]
        assert member.post(f"/api/trash/{tid}/restore").status_code == 403
    finally:
        with _db_lock:
            _db().execute("DELETE FROM trash WHERE id=?", (tid,))
            _db().commit()
        os.unlink(tp)


# ── P1-2：凭据重置/停用吊销设备 Token ──────────────────────


def _mk_token(c):
    r = c.post("/api/auth/token", json={"name": f"dev-{os.urandom(2).hex()}"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(_c, tok):
    """无 Cookie 裸客户端：只验 Bearer 本身（改密会重发会话 Cookie，会掩盖 401）。"""
    bare = TestClient(app, base_url="https://testserver")
    return bare.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})


def test_admin_password_reset_revokes_device_token(auth, make_user):
    u = make_user("member")
    tok = _mk_token(u)
    assert _bearer(u, tok).status_code == 200
    uid = _db().execute("SELECT id FROM users WHERE username=?",
                               (u.username,)).fetchone()["id"]
    r = auth.post(f"/api/admin/users/{uid}/password", json={"value": "brand-new-pass-1"})
    assert r.status_code == 200, r.text
    assert _bearer(u, tok).status_code == 401


def test_deactivate_revokes_device_token_and_stays_dead(auth, make_user):
    u = make_user("member")
    tok = _mk_token(u)
    uid = _db().execute("SELECT id FROM users WHERE username=?",
                               (u.username,)).fetchone()["id"]
    assert auth.patch(f"/api/admin/users/{uid}",
                      json={"is_active": False}).status_code == 200
    assert _bearer(u, tok).status_code == 401
    # 重新启用后旧 Token 不得复活
    assert auth.patch(f"/api/admin/users/{uid}",
                      json={"is_active": True}).status_code == 200
    assert _bearer(u, tok).status_code == 401


def test_self_password_change_revokes_device_token(make_user):
    u = make_user("member")
    tok = _mk_token(u)
    r = u.post("/api/auth/password",
               json={"old_password": MEMBER_PASS2, "new_password": "changed-pass-1"})
    assert r.status_code == 200, r.text
    assert _bearer(u, tok).status_code == 401


# ── P2-1：分块超发即时拒绝 ─────────────────────────────────


def test_chunk_overflow_rejected(auth):
    r = auth.post("/api/upload/init",
                  json={"dest_dir": "", "filename": "ov-r2.bin", "size": 10})
    assert r.status_code == 200, r.text
    sid = r.json()["session"]
    r = auth.post(f"/api/upload/chunk/{sid}?index=0", content=b"X" * 64)
    assert r.status_code == 400


# ── P2-5：v2 遗留（无主）分享仅 admin 可见可管 ─────────────


def test_legacy_ownerless_share_admin_only(auth, make_user):
    with _db_lock:
        _db().execute(
            "INSERT INTO shares(token, path, name, size, created_at, expires_at, owner_id) "
            "VALUES(?,?,?,?,?,?,NULL)",
            ("legacy-r2-tok", "legacy.txt", "legacy.txt", 1, _time.time(),
             _time.time() + 3600))
        _db().commit()
    try:
        member = make_user("member")
        assert "legacy-r2-tok" not in member.get("/api/shares").json()
        assert "legacy-r2-tok" in auth.get("/api/shares").json()
        assert member.delete("/api/shares/legacy-r2-tok").status_code == 403
        assert member.patch("/api/shares/legacy-r2-tok",
                            json={"expires_hours": 5}).status_code == 403
    finally:
        with _db_lock:
            _db().execute("DELETE FROM shares WHERE token=?", ("legacy-r2-tok",))
            _db().commit()


# ── P3：TOTP 动态码防重放 ──────────────────────────────────


def test_totp_code_replay_blocked(auth):
    r = auth.post("/api/auth/totp/setup")
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    code = pyotp.TOTP(secret).now()
    assert auth.post("/api/auth/totp/enable", json={"code": code}).status_code == 200
    try:
        c = TestClient(app, base_url="https://testserver")
        body = {"username": auth.username, "password": ADMIN_PASS, "code": code}
        r1 = c.post("/api/login/totp", json=body)
        assert r1.status_code == 200, r1.text
        # 同一 30s 窗口内的动态码重放必须拒绝
        r2 = c.post("/api/login/totp", json=body)
        assert r2.status_code == 401
    finally:
        auth.post("/api/auth/totp/disable", json={"old_password": ADMIN_PASS})
