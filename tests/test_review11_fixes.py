"""11 号独立复审（2026-09-10）修复批的针对性回归测试。

每项问题一条可执行断言——把"已修复"从叙述变成证据。
"""
import os
import urllib.request

import pytest

from test_extras import _admin, _bare, _member_session, _mk_member

from app.api.notify import (_ValidatingRedirectHandler,
                            assert_public_webhook_url)


def _rand() -> str:
    return os.urandom(3).hex()


# ─── P2-4 · Webhook SSRF：重定向逐跳复校验 ─────────────────

def test_webhook_redirect_to_loopback_is_rejected():
    """公网域名 302 到 127.0.0.1 —— 修复前 urlopen 会静默跟随（SSRF 绕过）。"""
    h = _ValidatingRedirectHandler()
    req = urllib.request.Request("https://hooks.example.com/x")
    with pytest.raises(ValueError):
        h.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1:9/x")


def test_webhook_redirect_to_private_range_is_rejected():
    h = _ValidatingRedirectHandler()
    req = urllib.request.Request("https://hooks.example.com/x")
    with pytest.raises(ValueError):
        h.redirect_request(req, None, 302, "Found", {}, "http://192.168.1.100/hook")


def test_webhook_literal_internal_targets_rejected():
    """初始化校验：字面内网/回环/链路本地一律拒绝。"""
    for bad in ("http://127.0.0.1:9/x", "http://192.168.1.100/x",
                "http://[::1]/x", "http://169.254.1.1/x", "ftp://example.com/x"):
        with pytest.raises(ValueError):
            assert_public_webhook_url(bad)


def test_webhook_public_host_allowed():
    """公网域名放行（不因加固而误伤正常渠道）。"""
    assert_public_webhook_url("https://ntfy.sh/mytopic")


def test_webhook_open_seam_uses_validating_opener():
    """守卫：出网出口必须挂着逐跳校验 handler。

    防止将来有人"顺手"把 `_safe_opener` 改回裸 `urllib.request.urlopen`——
    那样重定向校验会被静默绕过，且没有任何测试会红。
    """
    import app.api.notify as notify
    assert any(isinstance(h, notify._ValidatingRedirectHandler)
               for h in notify._safe_opener.handlers), \
        "_safe_opener 未挂 _ValidatingRedirectHandler，重定向 SSRF 校验失效"


# ─── P2-5 · WebDAV 真锁（原实现返回 token 却不记状态 = 假锁）──

def test_dav_options_advertises_class_two(auth):
    """LOCK 现在是真锁，因此 OPTIONS 声明 DAV: 1, 2 才不算撒谎。"""
    r = auth.request("OPTIONS", "/dav/")
    assert r.status_code == 200
    classes = {s.strip() for s in r.headers["dav"].split(",")}
    assert classes == {"1", "2"}, f"DAV 能力声明异常: {r.headers['dav']}"


def test_dav_lock_blocks_other_user_then_owner_releases(client):
    """完整锁生命周期：他人写入被 423 拦下 → 非锁主 UNLOCK 被拒 → 锁主释放后放行。"""
    d = f"davlock-{_rand()}"
    admin = _admin(client)
    assert admin.post(f"/api/mkdir/{d}").status_code == 200
    assert admin.post(f"/api/upload/{d}", files={"file": ("f.txt", b"v1")}).status_code == 200

    uname = f"davlocku-{_rand()}"
    _mk_member(client, uname)
    member = _member_session(client, uname)

    dav = f"/dav/{d}/f.txt"
    r = member.request("LOCK", dav)
    assert r.status_code == 200, r.text
    token = r.headers["lock-token"].strip("<>")
    assert token.startswith("opaquelocktoken:")

    # ① 他人（admin）未出示 token 的写入 → 423 Locked（修复前会静默成功并互踩）
    r = admin.request("PUT", dav, content=b"v2-admin")
    assert r.status_code == 423, f"锁未生效，实际 {r.status_code}"

    # ② 非锁主不能 UNLOCK
    r = admin.request("UNLOCK", dav, headers={"Lock-Token": f"<{token}>"})
    assert r.status_code == 403

    # ③ 锁主释放后写入放行
    assert member.request("UNLOCK", dav,
                          headers={"Lock-Token": f"<{token}>"}).status_code == 204
    assert admin.request("PUT", dav, content=b"v3").status_code in (201, 204)


def test_dav_lock_is_per_user_not_shared(client):
    """同一用户的新会话不算冲突（锁按 username 归属，不按会话）。"""
    import app.api.webdav as wd
    assert callable(wd.reset_dav_locks)
    wd.reset_dav_locks()
    assert wd._conflicting_lock({"username": "u1"}, wd.SHARE_ROOT / "x") is None



