"""WebDAV LOCK/UNLOCK 语义回归（2026-09-19 对抗审查 F1 后补：lock 特性此前零覆盖）。

要点：
- LOCK 要求写权（RFC 4918 §9.10）：只读 member 曾可借 read 通道 LOCK 整个
  共享根（resolve("", "read") = SHARE_ROOT），令全体写者（含 admin）
  PUT/MOVE/DELETE 423 长达 1h 且可无限续期 = 一发即中的写 DoS。
- 排他锁语义：他人持锁写 → 423；出示 token → 放行；owner 可续期/解锁。
"""
from conftest import MEMBER_PASS
from test_extras import _admin, _member_session, _mk_member, _rand


def _uid(username: str) -> int:
    from app.db import _db
    return _db().execute("SELECT id FROM users WHERE username=?",
                         (username,)).fetchone()["id"]


def _grant(auth, username: str, path: str, can_write: bool):
    r = auth.post("/api/admin/grants", json={
        "user_id": _uid(username), "path": path,
        "can_read": True, "can_write": can_write})
    assert r.status_code == 200, r.text


def test_dav_readonly_member_cannot_lock_shared_root(auth):
    """F1 主证：只读 member LOCK 共享根必须被拒（写权不足），而非拿到全库写锁。"""
    username = "dvlk" + _rand()
    _mk_member(auth, username, MEMBER_PASS)
    member = _member_session(auth, username, MEMBER_PASS)
    r = member.request("LOCK", "/dav/")
    assert r.status_code == 403, r.text
    # 锁表必须为空——不能出现"403 之外仍登记了锁"的半状态
    from app.api import webdav
    assert webdav._locks == {}


def test_dav_readonly_member_cannot_lock_shared_dir(auth, workdir):
    username = "dvlk" + _rand()
    _mk_member(auth, username, MEMBER_PASS)
    _grant(auth, username, workdir, can_write=False)
    member = _member_session(auth, username, MEMBER_PASS)
    r = member.request("LOCK", f"/dav/{workdir}/")
    assert r.status_code == 403, r.text


def test_dav_lock_blocks_other_writer_then_token_release(auth, workdir):
    """合法流：两个持写权用户，A LOCK 后 B 写 423，出示 token 后放行。"""
    a_name, b_name = "dvlka" + _rand(), "dvlkb" + _rand()
    _mk_member(auth, a_name, MEMBER_PASS)
    _mk_member(auth, b_name, MEMBER_PASS)
    _grant(auth, a_name, workdir, can_write=True)
    _grant(auth, b_name, workdir, can_write=True)
    a = _member_session(auth, a_name, MEMBER_PASS)
    b = _member_session(auth, b_name, MEMBER_PASS)

    fname = f"lk{_rand()}.txt"
    r = a.request("LOCK", f"/dav/{workdir}/{fname}")
    assert r.status_code == 200, r.text
    token = r.headers.get("lock-token", "").strip("<>")

    # B 无 token 写 → 423
    rb = b.put(f"/dav/{workdir}/{fname}", content=b"x")
    assert rb.status_code == 423, rb.text
    # B 出示 A 的 token（RFC 允许按 token 提交）→ 放行
    rb2 = b.put(f"/dav/{workdir}/{fname}", content=b"x",
                headers={"If": f"<{token}>"})
    assert rb2.status_code in (200, 201, 204), rb2.text

    # A 解锁（错误 token 409 / 正确 204）后 B 无 token 也能写
    rw = a.request("UNLOCK", f"/dav/{workdir}/{fname}",
                   headers={"Lock-Token": "<opaquelocktoken:wrong>"})
    assert rw.status_code == 409
    rok = a.request("UNLOCK", f"/dav/{workdir}/{fname}",
                    headers={"Lock-Token": f"<{token}>"})
    assert rok.status_code == 204
    rb3 = b.put(f"/dav/{workdir}/{fname}", content=b"y")
    assert rb3.status_code in (200, 201, 204), rb3.text


def test_dav_lock_owner_refresh_reuses_token(auth):
    username = "dvlk" + _rand()
    _mk_member(auth, username, MEMBER_PASS)
    a = _member_session(auth, username, MEMBER_PASS)
    fname = f"selflk{_rand()}.txt"
    r1 = a.request("LOCK", f"/dav/private/{username}/{fname}")
    assert r1.status_code == 200, r1.text
    token1 = r1.headers.get("lock-token", "").strip("<>")
    # owner 重复 LOCK = 续期，沿用原 token（RFC 4918 §9.10.2）
    r2 = a.request("LOCK", f"/dav/private/{username}/{fname}")
    assert r2.status_code == 200
    assert r2.headers.get("lock-token", "").strip("<>") == token1
    # owner 自己写不受自己锁影响
    rput = a.put(f"/dav/private/{username}/{fname}", content=b"mine")
    assert rput.status_code in (200, 201, 204), rput.text


def test_dav_non_owner_cannot_unlock(auth, workdir):
    a_name, b_name = "dvlua" + _rand(), "dvlub" + _rand()
    _mk_member(auth, a_name, MEMBER_PASS)
    _mk_member(auth, b_name, MEMBER_PASS)
    _grant(auth, a_name, workdir, can_write=True)
    _grant(auth, b_name, workdir, can_write=True)
    a = _member_session(auth, a_name, MEMBER_PASS)
    b = _member_session(auth, b_name, MEMBER_PASS)
    fname = f"ul{_rand()}.txt"
    r = a.request("LOCK", f"/dav/{workdir}/{fname}")
    assert r.status_code == 200
    token = r.headers.get("lock-token", "").strip("<>")
    # B 出示偷来的 token 解锁 → 403（只有 owner 能解）
    r2 = b.request("UNLOCK", f"/dav/{workdir}/{fname}",
                   headers={"Lock-Token": f"<{token}>"})
    assert r2.status_code == 403, r2.text
    _admin(auth).request("UNLOCK", f"/dav/{workdir}/{fname}",
                         headers={"Lock-Token": f"<{token}>"})


def test_dav_lock_on_ancestor_dir_blocks_descendant_write(auth, workdir):
    """depth 语义（_conflicting_lock 沿祖先链上溯）：目录上的锁阻挡其子孙的写。"""
    a_name, b_name = "dvanc" + _rand(), "dvanb" + _rand()
    _mk_member(auth, a_name, MEMBER_PASS)
    _mk_member(auth, b_name, MEMBER_PASS)
    _grant(auth, a_name, workdir, can_write=True)
    _grant(auth, b_name, workdir, can_write=True)
    a = _member_session(auth, a_name, MEMBER_PASS)
    b = _member_session(auth, b_name, MEMBER_PASS)
    sub = f"{workdir}/anc{_rand()}"
    assert a.request("MKCOL", f"/dav/{sub}").status_code in (200, 201)
    # A 锁子目录；B 写该目录**内部**的文件 → 423（锁在目标自身，最基本情形）
    r = a.request("LOCK", f"/dav/{sub}/")
    assert r.status_code == 200
    token = r.headers.get("lock-token", "").strip("<>")
    rb = b.put(f"/dav/{sub}/inner.txt", content=b"x")
    assert rb.status_code == 423, rb.text
    # A 解锁后 B 可写
    assert a.request("UNLOCK", f"/dav/{sub}/",
                     headers={"Lock-Token": f"<{token}>"}).status_code == 204
    assert b.put(f"/dav/{sub}/inner.txt", content=b"x").status_code in (200, 201, 204)
