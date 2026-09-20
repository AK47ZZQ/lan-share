"""消融实验补钉（2026-09-20）：给"改坏了测试还全绿"的安全不变量补上回归钉。

来源：`python scripts/ablate-security-core.py` 首轮——12 条安全不变量里 6 条
被改坏后 436 项测试全绿（M3/M4/M5/M8/M10/M11）。本文件逐条补钉，编号与消融
编号对齐，下一轮 `--only M3,M4,...` 直接复测。

钉在 spaces/safe_path 单元层而不是 HTTP 层（奥卡姆）：消融改的就是这一层的
表达式，同层探针最短路径定位；不经 DB 往返与磁盘存在性，SQLite/PG 同构。

vpath 口径（本轮踩到、写死在此以免再犯）：`shared:`/`private:` 用**冒号**，
裸路径本身就等价 shared——所以 resolve 传 "photos" 才得到 vkey "shared/photos"，
传 "shared/photos" 会得到 "shared/shared/photos"。后者仍能 403，于是"看起来过了"
但实际没碰到被测分支：M8 第一版就是这么假绿的。
"""

import secrets
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from conftest import MEMBER_PASS2
from app.config import SHARE_ROOT
from app.db import _db
from app.filetools import safe_path
from app.main import app
from app.spaces import (invalidate_grants_cache, resolve, sample_readable,
                        sync_grants_on_move, visible, _clean_parts)

APP_DIR = "App安装包"


@pytest.fixture()
def make_user(auth, client):
    created = []

    def _make(role):
        username = f"pin{secrets.token_hex(3)}"
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


def row_of(username):
    """users 行 + require_user 的别名列（resolve/visible 读 user["user_id"]，表里是 id）。
    用 dict：两种引擎同构，且 keys() 行为一致。"""
    r = _db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    d = {k: r[k] for k in r.keys()}
    d.setdefault("user_id", d.get("id"))
    return d


def grant(username, path, can_read=1, can_write=0, expires_at=None):
    _db().execute(
        "INSERT INTO grants(user_id, path, can_read, can_write, created_by, created_at, "
        "expires_at) VALUES(?,?,?,?,?,?,?)",
        (_db().execute("SELECT id FROM users WHERE username=?",
                       (username,)).fetchone()["id"],
         path, can_read, can_write, 1, time.time(), expires_at))
    _db().commit()
    invalidate_grants_cache(None)


# ── M3：_clean_parts 拒绝 ".." 与点前缀（虚拟路径层防线）────────

def test_M3_clean_parts_rejects_dotdot():
    with pytest.raises(HTTPException) as ei:
        _clean_parts("a/../../etc")
    assert ei.value.status_code == 403


def test_M3_clean_parts_rejects_hidden_segment():
    with pytest.raises(HTTPException) as ei:
        _clean_parts("a/.trash")
    assert ei.value.status_code == 403


def test_M3_clean_parts_accepts_ordinary_path():
    assert _clean_parts("a/b") == ["a", "b"]
    assert _clean_parts("/a//b/") == ["a", "b"]      # 空段/多余斜杠不算穿越
    assert _clean_parts("a\\b") == ["a", "b"]        # 反斜杠归一为分隔符


def test_M3_resolve_traversal_denied_end_to_end():
    """对外语义：穿越必须 403（_clean_parts 与 safe_path 两道都守得住）。"""
    anon = {"username": "nobody", "role": "member", "user_id": 1}
    for bad in ("private:../../etc", "../../../../Windows/win.ini"):
        with pytest.raises(HTTPException) as ei:
            resolve(anon, bad, "read")
        assert ei.value.status_code == 403


# ── M4：grant 到期即失效 ──────────────────────────────────────

def test_M4_expired_grant_denies_read(make_user):
    guest = make_user("guest")
    grant(guest.username, "shared/photos", can_read=1, expires_at=time.time() - 10)
    with pytest.raises(HTTPException) as ei:
        resolve(row_of(guest.username), "photos", "read")
    assert ei.value.status_code == 403


def test_M4_future_grant_allows_read(make_user):
    """反向护栏：到期判定不得写成"永远过期"，否则本钉会以错误的方式常绿。"""
    guest = make_user("guest")
    grant(guest.username, "shared/photos", can_read=1, expires_at=time.time() + 3600)
    assert resolve(row_of(guest.username), "photos", "read") == SHARE_ROOT / "photos"


def test_M4_null_expiry_means_no_expiry(make_user):
    guest = make_user("guest")
    grant(guest.username, "shared/photos", can_read=1, expires_at=None)
    assert resolve(row_of(guest.username), "photos", "read") == SHARE_ROOT / "photos"


def test_M4_expired_grant_denies_write(make_user):
    member = make_user("member")
    grant(member.username, "shared/docs", can_read=1, can_write=1,
          expires_at=time.time() - 1)
    with pytest.raises(HTTPException) as ei:
        resolve(row_of(member.username), "docs/new.txt", "write")
    assert ei.value.status_code == 403


# ── M5：聚合视图逐条判可见性（根级 resolve ≠ 逐节点，§6 教训）────

def test_M5_visible_denies_guest_without_grant(make_user):
    guest = make_user("guest")
    assert visible(row_of(guest.username), ["photos", "a.jpg"]) is False


def test_M5_visible_allows_guest_only_within_granted_prefix(make_user):
    guest = make_user("guest")
    grant(guest.username, "shared/photos", can_read=1)
    r = row_of(guest.username)
    assert visible(r, ["photos", "a.jpg"]) is True
    assert visible(r, ["photos"]) is True
    assert visible(r, ["music", "b.mp3"]) is False      # 不越授予范围
    assert visible(r, ["photossecret", "b.jpg"]) is False   # 段边界，非裸前缀


def test_M5_visible_denies_other_users_private(make_user):
    member = make_user("member")
    r = row_of(member.username)
    assert visible(r, ["private", member.username, "x.jpg"]) is True
    assert visible(r, ["private", "someone-else", "x.jpg"]) is False


def test_M5_visible_denies_member_others_incoming(make_user):
    member = make_user("member")
    other = make_user("member")
    r = row_of(member.username)
    assert visible(r, ["incoming", member.username, "p.jpg"]) is True
    assert visible(r, ["incoming", other.username, "p.jpg"]) is False


def test_M5_visible_denies_hidden_and_empty():
    r = {"username": "m", "role": "member", "user_id": 1}
    assert visible(r, [".trash", "x"]) is False
    assert visible(r, []) is False


def test_M5_visible_admin_sees_shared_and_incoming_not_others_private(make_user):
    other = make_user("member")
    admin = row_of("admin")
    assert visible(admin, ["shared-anything"]) is True
    assert visible(admin, ["incoming", other.username, "p.jpg"]) is True
    assert visible(admin, ["private", other.username, "x.jpg"]) is False


# ── M8：安装包目录写权 admin-only（恶意 APK → 全体客户端 RCE 面）──

def test_M8_member_with_write_grant_cannot_write_app_dir(make_user):
    """先给足写权，让 403 只能来自 M8 那道守卫——否则测试会因为别的原因绿
    （第一版就是 vkey 拼错，被写权检查顺手 403，守卫根本没被触达）。"""
    member = make_user("member")
    r = row_of(member.username)
    grant(member.username, f"shared/{APP_DIR}", can_read=1, can_write=1)
    with pytest.raises(HTTPException) as ei:
        resolve(r, f"{APP_DIR}/evil.apk", "write")
    assert ei.value.status_code == 403


def test_M8_member_can_still_read_app_dir(make_user):
    """读必须放行——全员"检查更新"依赖它，收紧时不得顺手把读也锁死。"""
    member = make_user("member")
    assert resolve(row_of(member.username), f"{APP_DIR}/version.json", "read") \
        == SHARE_ROOT / APP_DIR / "version.json"


def test_M8_admin_can_write_app_dir(make_user):
    assert resolve(row_of("admin"), f"{APP_DIR}/version.json", "write") \
        == SHARE_ROOT / APP_DIR / "version.json"


def test_M8_other_dir_write_still_honours_grant(make_user):
    """守卫必须只罩住安装包目录，不得把成员的正常写权一并吃掉。"""
    member = make_user("member")
    r = row_of(member.username)
    grant(member.username, "shared/docs", can_read=1, can_write=1)
    assert resolve(r, "docs/a.txt", "write") == SHARE_ROOT / "docs" / "a.txt"


# ── M10：目录改名后 grants 缓存必须失效（R2-P3-8）───────────────

def test_M10_move_invalidates_grants_cache(make_user):
    guest = make_user("guest")
    r = row_of(guest.username)
    grant(guest.username, "shared/alpha", can_read=1)
    resolve(r, "alpha", "read")            # 先把旧行灌进 30s TTL 缓存
    sync_grants_on_move("alpha", "beta")    # 改名：改库 + 失效缓存
    assert resolve(r, "beta", "read") == SHARE_ROOT / "beta"


def test_M10_moved_away_path_no_longer_grants(make_user):
    guest = make_user("guest")
    r = row_of(guest.username)
    grant(guest.username, "shared/alpha", can_read=1)
    resolve(r, "alpha", "read")
    sync_grants_on_move("alpha", "beta")
    with pytest.raises(HTTPException) as ei:
        resolve(r, "alpha", "read")
    assert ei.value.status_code == 403


# ── M7：秒传样例可读判定（Dedup Oracle 封堵）────────────────────

def test_M7_sample_readable_denies_other_users_private(make_user):
    member, other = make_user("member"), make_user("member")
    assert sample_readable(row_of(member.username),
                           SHARE_ROOT / "private" / other.username / "diary.jpg") is False


def test_M7_sample_readable_allows_own_private(make_user):
    member = make_user("member")
    assert sample_readable(row_of(member.username),
                           SHARE_ROOT / "private" / member.username / "diary.jpg") is True


def test_M7_sample_readable_denies_outside_root_and_hidden(make_user):
    r = row_of(make_user("member").username)
    assert sample_readable(r, SHARE_ROOT.parent / "elsewhere.jpg") is False
    assert sample_readable(r, SHARE_ROOT / ".trash" / "x.jpg") is False


def test_M7_sample_readable_member_denied_other_incoming(make_user):
    member, other = make_user("member"), make_user("member")
    r = row_of(member.username)
    assert sample_readable(r, SHARE_ROOT / "incoming" / other.username / "p.jpg") is False
    assert sample_readable(r, SHARE_ROOT / "incoming" / member.username / "p.jpg") is True


# ── safe_path 可达防线（M11 的可测部分）────────────────────────

def test_safe_path_rejects_traversal_and_hidden():
    for bad in ("a/../../etc", ".trash/x", "a/.meta.db"):
        with pytest.raises(HTTPException) as ei:
            safe_path(bad)
        assert ei.value.status_code == 403


def test_safe_path_allows_within_root():
    assert safe_path("photos/a.jpg") == SHARE_ROOT / "photos" / "a.jpg"
    assert safe_path("") == SHARE_ROOT
