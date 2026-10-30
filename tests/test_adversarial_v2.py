"""对抗式审查 v2（2026-09-20 自主 review 批次）。

靶心：WebDAV 根集合（`/dav` 与 `/dav/`）在 path 为空时**绕过 resolve()**，
直接拿 SHARE_ROOT 当 target（app/api/webdav.py::dav_handler）。

第一性原理：权限模型的不可约契约是「任何文件入口必经 spaces.resolve()」
，guest 的语义是「shared 无默认权限，一切凭 grant」
（spaces.py 模块 docstring / PERMISSIONS.md E4）。根集合也是集合，
不存在"读根不需要授权"的例外——所以 guest PROPFIND /dav/ 的正确答案是 403，
不是 207。

这些用例在修复前应全部变红（证明缺陷真实存在且测试有效），修复后变绿。
"""

import secrets
import time

import pytest
from fastapi.testclient import TestClient

from conftest import MEMBER_PASS2
from app.db import _db
from app.main import app
from app.spaces import invalidate_grants_cache


@pytest.fixture()
def make_user(auth, client):
    """邀请码建号工厂，测试结束按唯一用户名清理（绝不按"列表第一个"定位他人数据）。"""
    created = []

    def _make(role):
        username = f"av2{secrets.token_hex(3)}"
        r = auth.post("/api/admin/invites", json={"role": role, "hours": 1})
        assert r.status_code == 200, r.text
        r = client.post("/api/invites/redeem", json={
            "code": r.json()["code"], "username": username, "password": MEMBER_PASS2})
        assert r.status_code == 200, r.text
        c = TestClient(app, base_url="https://testserver")
        assert c.post("/api/login", json={
            "username": username, "password": MEMBER_PASS2}).status_code == 200
        c.username = username
        created.append(username)
        return c

    yield _make
    for username in created:
        row = _db().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            _db().execute("DELETE FROM users WHERE id=?", (row["id"],))
            _db().commit()
    invalidate_grants_cache(None)


def _grant(user_client, path, can_read=1, can_write=0):
    uid = _db().execute("SELECT id FROM users WHERE username=?",
                        (user_client.username,)).fetchone()["id"]
    _db().execute(
        "INSERT INTO grants(user_id, path, can_read, can_write, created_by, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (uid, path, can_read, can_write, 1, time.time()))
    _db().commit()
    invalidate_grants_cache(uid)


# ── D1：guest 读根集合绕过授权 ────────────────────────────────

def test_guest_propfind_root_is_denied(make_user):
    """guest 无任何 grant 时 PROPFIND /dav/ 必须 403。

    缺陷形态：path 为空 → target=SHARE_ROOT（不经 resolve）→ 207 列出 shared
    顶层全部条目名/大小/mtime，外加 private、incoming 两个集合的存在性。
    """
    guest = make_user("guest")
    assert guest.request("PROPFIND", "/dav/", headers={"Depth": "1"}).status_code == 403


def test_guest_get_root_is_denied(make_user):
    guest = make_user("guest")
    assert guest.request("GET", "/dav/").status_code == 403


def test_guest_proppatch_root_is_denied(make_user):
    """PROPPATCH 虽是无副作用的桩，但"能否到达该资源"仍必须经授权判定。"""
    guest = make_user("guest")
    assert guest.request("PROPPATCH", "/dav/").status_code == 403


def test_guest_dav_without_trailing_slash_denied(make_user):
    """/dav（无斜杠）同样 path="" ——同一分支的第二个入口，必须一起收口。"""
    guest = make_user("guest")
    assert guest.request("PROPFIND", "/dav", headers={"Depth": "1"}).status_code == 403


# ── 反向护栏：修复不得把合法用户的根挂载打死 ──────────────────

def test_admin_propfind_root_still_ok(auth):
    assert auth.request("PROPFIND", "/dav/", headers={"Depth": "1"}).status_code == 207


def test_member_propfind_root_still_ok(make_user):
    member = make_user("member")
    assert member.request("PROPFIND", "/dav/", headers={"Depth": "1"}).status_code == 207


def test_guest_with_root_grant_propfind_ok(make_user):
    """显式授予 shared/ 根读权后必须可列——证明修复是"须经授权"而非"一律禁止"。"""
    guest = make_user("guest")
    _grant(guest, "shared/")
    assert guest.request("PROPFIND", "/dav/", headers={"Depth": "1"}).status_code == 207


def test_guest_subtree_grant_does_not_unlock_root(make_user):
    """子树 grant 不得顺带放开根集合：命中按段前缀，shared/photos 不覆盖 shared/。"""
    guest = make_user("guest")
    _grant(guest, "shared/photos")
    assert guest.request("PROPFIND", "/dav/", headers={"Depth": "1"}).status_code == 403
