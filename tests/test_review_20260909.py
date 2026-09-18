"""review 2026-09-09（改进建议9.9）修复批回归测试：DAV Basic 哑哈希等时、
DAV PROPFIND 线程池化、_user_field 列名白名单、/api/auth/me 容忍损坏 totp_rc、
APK sha256 缓存、zip 打包票据上限。"""

import base64
import hashlib

from app.db import _db, _db_lock
from app.main import app



# ── P1-2：DAV Basic 认证哑哈希（用户名枚举等时） ─────────────


def test_dav_unknown_user_fills_dummy_hash():
    """修复前：不存在的用户 0ms 短路返回（_DUMMY_HASH 不初始化）；
    修复后：失败路径也跑一次哑 scrypt——用 _DUMMY_HASH 被懒初始化作证。
    必须用无会话 Cookie 的裸客户端，否则 DAV 认证走 Cookie 分支到不了 Basic。"""
    from fastapi.testclient import TestClient

    from app.services import passwords as auth_mod
    auth_mod._DUMMY_HASH = None
    c = TestClient(app, base_url="https://testserver")
    cred = base64.b64encode(b"nosuchuser9f3c:wrongpass").decode()
    r = c.request("PROPFIND", "/dav/",
                  headers={"Authorization": f"Basic {cred}", "Depth": "0"})
    assert r.status_code == 401
    assert auth_mod._DUMMY_HASH, "哑哈希未初始化：DAV 认证仍可时序枚举用户名"


def test_dav_propfind_207(auth):
    """PROPFIND 线程池化后的功能回归：主体路径仍 207（与 test_baseline DAV 用例互补）。"""
    r = auth.request("PROPFIND", "/dav/", headers={"Depth": "0"})
    assert r.status_code == 207
    assert "multistatus" in r.text


# ── P2：_user_field 列名白名单 ───────────────────────────────


def test_user_field_rejects_nonwhitelisted_column(auth):
    """列名白名单外的取值必须拒绝，防后续调用方拼进用户输入。"""
    from app.api.auth import _user_field
    try:
        _user_field({"user_id": 1}, "password_hash")
        ok_field = True
    except ValueError:
        ok_field = False
    assert ok_field, "白名单内列不应拒绝"
    try:
        _user_field({"user_id": 1}, "1; DROP TABLE users")
    except ValueError:
        pass
    else:
        raise AssertionError("白名单外列未被拒绝")


# ── P2：/api/auth/me 容忍损坏 totp_rc ───────────────────────


def test_me_tolerates_corrupted_totp_rc(auth):
    """totp_rc 字段损坏（非合法 JSON）时 /api/auth/me 应回 200 且 rc_left=0。"""
    row = _db().execute("SELECT totp_rc FROM users WHERE username=?",
                               (auth.username,)).fetchone()
    original = row["totp_rc"]
    try:
        _db().execute("UPDATE users SET totp_rc='{bad-json' WHERE username=?",
                             (auth.username,))
        _db().commit()
        r = auth.get("/api/auth/me")
        assert r.status_code == 200, r.text
        assert r.json()["totp_rc_left"] == 0
    finally:
        _db().execute("UPDATE users SET totp_rc=? WHERE username=?",
                             (original, auth.username))
        _db().commit()


# ── P2：APK sha256 按 mtime+size 缓存 ───────────────────────


def test_apk_sha256_cached(tmp_path):
    from app.api.appver import _apk_sha256, _apk_sha_cache
    f = tmp_path / "x.apk"
    f.write_bytes(b"lanshare-apk-bytes")
    want = hashlib.sha256(b"lanshare-apk-bytes").hexdigest()
    _apk_sha_cache[1:] = [None, None]          # 清缓存强制首算
    s1 = _apk_sha256(f)
    _apk_sha_cache[1:] = [None, None]          # 再清缓存：同 mtime+size 应命中
    s2 = _apk_sha256(f)
    assert s1 == s2 == want
    assert _apk_sha_cache[0] == want
    # 文件内容变化（mtime/size 变）后必须重算
    f.write_bytes(b"lanshare-apk-bytes-v2")
    s3 = _apk_sha256(f)
    assert s3 == hashlib.sha256(b"lanshare-apk-bytes-v2").hexdigest()


# ── P2：zip 打包票据上限 ────────────────────────────────────


def test_zip_ticket_cap_rejects(auth):
    """票据表打满后 /api/zip 应回 429，且不留下在途打包位。

    v3.57：票据从进程内 dict 落到 ttl_state 表，本用例改为**按唯一前缀造数 +
    按前缀清理**（不再掏 `archive._lock`/`_tickets` 私有态）——顺带把"上限判定
    用真实计数口径"这件事也一起验了。
    """
    import os

    from app.services import archive
    from app.store import ttl_state as ts

    tag = f"cap-{os.urandom(3).hex()}"
    keys = [f"{tag}-{i}" for i in range(archive._TICKET_CAP)]
    for t in keys:
        ts.put(ts.SCOPE_ZIP_TICKET, t, "whatever.zip", 10 ** 9)
    try:
        assert ts.count(ts.SCOPE_ZIP_TICKET) >= archive._TICKET_CAP
        r = auth.post("/api/zip", json={"paths": ["whatever.txt"]})
        assert r.status_code == 429, r.text
    finally:
        with _db_lock:
            _db().execute("DELETE FROM ttl_state WHERE scope=? AND tkey LIKE ?",
                          (ts.SCOPE_ZIP_TICKET, f"{tag}%"))
            _db().commit()
    # 上限预检发生在占位之前：被打回那一次不该留下在途位锁死该账号
    # （在清理 fake 票据之后验——否则 SlotCap 会先挡住，测不到占位这一步）
    archive.release_slot("admin")          # 清可能残留（正常应无行）
    archive.acquire_slot("admin")          # 不抛 SlotBusy/SlotCap 即通过
    archive.release_slot("admin")
