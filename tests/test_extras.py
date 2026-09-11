"""2026-09-01 批次四小项回归：HEIC 缩略图/预览、WebDAV 独立密码、
事件推送 Webhook（通知 bot）、admin 成员视角预览。

约定同 test_v35：conftest 已建唯一 admin；多账号用邀请码 redeem 现场建。
WebDAV Basic 认证一律用无 Cookie 的干净客户端（_dav_auth 会话优先，
带 Cookie 会绕过 Basic 分支——test_multiuser 里踩过这个语义）。
"""
import base64
import io
import os
import threading
import time

import pytest

from conftest import ADMIN_PASS, ADMIN_USER, MEMBER_PASS

from app.db import _db
from app.main import app
import app.api.photos as system_mod


def _rand() -> str:
    return os.urandom(3).hex()


def _bare(client):
    """无 Cookie 的干净客户端。"""
    return client.__class__(app, base_url="https://testserver")


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


def _member_session(client, username: str, password: str = MEMBER_PASS):
    c = _bare(client)
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return c


def _basic(user: str, pw: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


# ── ① HEIC 缩略图 / 预览 / 元数据 / 照片时间线 ──────────────────


def _make_heic() -> bytes:
    pytest.importorskip("pillow_heif")
    from PIL import Image
    from pillow_heif import register_heif_opener
    register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (180, 60, 60)).save(buf, format="HEIF")
    return buf.getvalue()


def test_heic_thumb_preview_meta(client, auth, workdir):
    name = f"p-{_rand()}.heic"
    r = auth.post(f"/api/upload/{workdir}",
                  files={"file": (name, _make_heic(), "image/heic")})
    assert r.status_code == 200, r.text
    rel = r.json()["path"]

    r = auth.get(f"/thumb/{rel}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    r = auth.get(f"/preview/{rel}?w=1280")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    r = auth.get(f"/api/photo/meta?path={rel}")
    assert r.status_code == 200
    assert (r.json()["width"], r.json()["height"]) == (64, 48)


def test_heic_enters_photo_timeline(client, auth, workdir):
    name = f"t-{_rand()}.heic"
    r = auth.post(f"/api/upload/{workdir}",
                  files={"file": (name, _make_heic(), "image/heic")})
    assert r.status_code == 200, r.text
    rel = r.json()["path"]
    system_mod._last_photo_scan = 0   # 白盒重置 60s 扫描节流（必须写 system 模块本体）
    r = auth.get("/api/photos?limit=500")
    assert any(it["path"] == rel for it in r.json()["items"])


# ── ② WebDAV 独立密码 ────────────────────────────────────────


def test_webdav_independent_password_isolation(client):
    uname = "davuser-" + _rand()
    _mk_member(client, uname)
    member = _member_session(client, uname)
    main_pass_hdr = _basic(uname, MEMBER_PASS)
    bare = _bare(client)

    # 基线：未设置独立密码时登录密码可挂载
    assert bare.request("PROPFIND", "/dav/", headers=main_pass_hdr).status_code == 207

    # 当前密码错 → 403；新密码太短 → 400
    r = member.post("/api/auth/webdav-password",
                    json={"current_password": "wrong-pass", "webdav_password": "davpass-123"})
    assert r.status_code == 403
    r = member.post("/api/auth/webdav-password",
                    json={"current_password": MEMBER_PASS, "webdav_password": "short"})
    assert r.status_code == 400

    # 设置成功；状态端点只报布尔
    r = member.post("/api/auth/webdav-password",
                    json={"current_password": MEMBER_PASS, "webdav_password": "davpass-123"})
    assert r.status_code == 200 and r.json() == {"ok": True, "set": True}
    assert member.get("/api/auth/webdav-password").json() == {"set": True}

    # DAV 入口拒收登录密码、认独立密码；网页登录不受影响
    assert bare.request("PROPFIND", "/dav/", headers=main_pass_hdr).status_code == 401
    assert bare.request("PROPFIND", "/dav/",
                        headers=_basic(uname, "davpass-123")).status_code == 207
    assert member.get("/api/auth/me").status_code == 200

    # 清除（空串）后回退登录密码
    r = member.post("/api/auth/webdav-password",
                    json={"current_password": MEMBER_PASS, "webdav_password": ""})
    assert r.status_code == 200 and r.json()["set"] is False
    assert bare.request("PROPFIND", "/dav/", headers=main_pass_hdr).status_code == 207


def test_admin_reset_and_clear_webdav_password(client):
    uname = "davadmin-" + _rand()
    _mk_member(client, uname)
    member = _member_session(client, uname)
    admin = _admin(client)
    bare = _bare(client)

    r = member.post("/api/auth/webdav-password",
                    json={"current_password": MEMBER_PASS, "webdav_password": "oldpass-123"})
    assert r.status_code == 200

    uid = _db().execute(
        "SELECT id FROM users WHERE username=?", (uname,)).fetchone()["id"]
    users = admin.get("/api/admin/users").json()
    assert next(u for u in users if u["id"] == uid)["webdav_set"] is True

    # 管理端重置：旧独立密码失效、新密码生效，成员网页会话保持
    r = admin.post(f"/api/admin/users/{uid}/webdav-password", json={"value": "newdav-12345"})
    assert r.status_code == 200 and r.json()["set"] is True
    assert bare.request("PROPFIND", "/dav/",
                        headers=_basic(uname, "oldpass-123")).status_code == 401
    assert bare.request("PROPFIND", "/dav/",
                        headers=_basic(uname, "newdav-12345")).status_code == 207
    assert member.get("/api/auth/me").status_code == 200

    # 管理端清除 → 回退登录密码
    r = admin.post(f"/api/admin/users/{uid}/webdav-password", json={"value": ""})
    assert r.status_code == 200 and r.json()["set"] is False
    assert bare.request("PROPFIND", "/dav/",
                        headers=_basic(uname, MEMBER_PASS)).status_code == 207


# ── ③ 事件推送 Webhook（通知 bot）────────────────────────────


def test_webhook_config_validation_and_test(auth, monkeypatch):
    # 非 http(s) / 缺占位符都拒收
    assert auth.post("/api/admin/webhook", json={"url": "ftp://x/{title}"}).status_code == 400
    assert auth.post("/api/admin/webhook", json={"url": "https://a.com/hook"}).status_code == 400

    url = "https://push.example.com/key?title={title}&body={body}"
    assert auth.post("/api/admin/webhook", json={"url": url}).json()["url"] == url
    assert auth.get("/api/admin/webhook").json()["url"] == url

    captured = {}

    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_open(req, timeout=0):
        captured["url"] = req.full_url
        return _FakeResp(b"OK")

    monkeypatch.setattr("app.services.webhook._webhook_open", fake_open)
    assert auth.post("/api/admin/webhook/test").status_code == 200
    # 占位符已被替换为 URL 编码内容
    assert "{title}" not in captured["url"] and "title=" in captured["url"]

    # 清空配置 + 清除后测试按钮报错
    assert auth.post("/api/admin/webhook", json={"url": ""}).status_code == 200
    assert auth.get("/api/admin/webhook").json()["url"] == ""
    assert auth.post("/api/admin/webhook/test").status_code == 400


def test_share_and_redeem_trigger_push(client, monkeypatch):
    events = []

    def fake_open(req, timeout=0):
        events.append(req.full_url)

    monkeypatch.setattr("app.services.webhook._webhook_open", fake_open)
    admin = _admin(client)
    assert admin.post("/api/admin/webhook", json={
        "url": "https://push.example.com/hook?title={title}&body={body}"}).status_code == 200

    uname = "pusher-" + _rand()
    _mk_member(client, uname)   # 新成员加入事件

    assert admin.post("/api/mkdir/shared-push", cookies=dict(admin.cookies)).status_code == 200
    assert admin.post("/api/upload/shared-push",
                      files={"file": ("doc.txt", b"x")},
                      cookies=dict(admin.cookies)).status_code == 200
    r = admin.post("/api/share", data={"path": "shared-push/doc.txt", "expires_hours": "1",
                                       "target": uname}, cookies=dict(admin.cookies))
    assert r.status_code == 200, r.text

    deadline = time.time() + 10
    while len(events) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert len(events) >= 2
    time.sleep(0.3)   # 等 worker 池排空，避免 monkeypatch 撤销后真实出网
    # 清空，避免影响同会话后续测试
    assert admin.post("/api/admin/webhook", json={"url": ""}).status_code == 200


# ── ④ admin 成员视角预览（X-As-User，只读）───────────────────


def test_preview_files_as_member(client):
    uname_a = "viewa-" + _rand()
    uname_b = "viewb-" + _rand()
    _mk_member(client, uname_a)
    _mk_member(client, uname_b)
    a = _member_session(client, uname_a)
    b = _member_session(client, uname_b)
    admin = _admin(client)

    fa = f"only-a-{_rand()}.txt"
    fb = f"only-b-{_rand()}.txt"
    assert a.post("/api/upload/private", files={"file": (fa, b"a")}).status_code == 200
    assert b.post("/api/upload/private", files={"file": (fb, b"b")}).status_code == 200

    # admin 默认视角直看 a 的私人目录没问题；换 b 视角后看到的是 b 的私人根
    r = admin.get("/api/files/private/", headers={"X-As-User": uname_b})
    names = [it["name"] for it in r.json()["items"]]
    assert fb in names and fa not in names

    r = admin.get("/api/files/private/", headers={"X-As-User": uname_a})
    names = [it["name"] for it in r.json()["items"]]
    assert fa in names and fb not in names

    # 成员带 X-As-User 头无效（仍看自己）；目标不存在 → 404
    r = b.get("/api/files/private/", headers={"X-As-User": uname_a})
    assert [it["name"] for it in r.json()["items"]] == [fb]
    assert admin.get("/api/files/private/",
                     headers={"X-As-User": "nobody-" + _rand()}).status_code == 404


def test_preview_shares_as_member(client):
    admin = _admin(client)
    uname_a = "viewc-" + _rand()
    _mk_member(client, uname_a)

    assert admin.post("/api/mkdir/shared-view", cookies=dict(admin.cookies)).status_code == 200
    assert admin.post("/api/upload/shared-view",
                      files={"file": ("sv.txt", b"x")},
                      cookies=dict(admin.cookies)).status_code == 200
    assert admin.post("/api/share", data={"path": "shared-view/sv.txt", "expires_hours": "1"},
                      cookies=dict(admin.cookies)).status_code == 200

    assert any(s["name"] == "sv.txt" for s in admin.get("/api/shares").json().values())
    view_a = admin.get("/api/shares", headers={"X-As-User": uname_a}).json()
    assert not any(s["name"] == "sv.txt" for s in view_a.values())


def test_preview_photo_facets_as_member(client):
    admin = _admin(client)
    uname_a = "viewd-" + _rand()
    uname_b = "viewe-" + _rand()
    _mk_member(client, uname_a)
    _mk_member(client, uname_b)
    a = _member_session(client, uname_a)
    b = _member_session(client, uname_b)

    assert a.post("/api/upload/private",
                  files={"file": ("a.jpg", b"not-a-real-jpeg", "image/jpeg")}).status_code == 200
    assert b.post("/api/upload/private",
                  files={"file": ("b.jpg", b"not-a-real-jpeg", "image/jpeg")}).status_code == 200

    system_mod._last_photo_scan = 0   # 节流旗标必须写 system 模块本体（server._xx 只是副本）
    # 设计如此：照片流对 private/<u>/ 只对本人可见，admin 默认视角同样看不到
    names_admin = [u["name"] for u in admin.get("/api/photos/facets").json()["uploaders"]]
    assert uname_a not in names_admin and uname_b not in names_admin

    # 但切换到 a 的视角后，a 能看到自己私人目录的照片维度
    va = admin.get("/api/photos/facets", headers={"X-As-User": uname_a}).json()
    names_a = [u["name"] for u in va["uploaders"]]
    assert uname_a in names_a and uname_b not in names_a


# ── ⑨ /preview Range 断点下载契约（App M-A2 下载续传依赖 206）──────

def test_preview_range_resume_contract(client, auth, workdir):
    admin = _admin(client)
    payload = b"0123456789" * 1000  # 10KB，内容可定位
    assert admin.post("/api/upload", files={"file": ("rng.bin", payload)}).status_code == 200

    # 无 Range：200 全量
    r = admin.get("/preview/rng.bin")
    assert r.status_code == 200 and len(r.content) == len(payload)

    # 带 Range：206 + 精确切片（App 断点续传从 .part 偏移续写的前提）
    r = admin.get("/preview/rng.bin", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.content == payload[100:200]

    # 起点即文件尾：按 RFC 9110 → 416（服务端正确行为；App 侧把 416 视为"已到尾"）
    r = admin.get("/preview/rng.bin", headers={"Range": f"bytes={len(payload)}-"})
    assert r.status_code == 416


# ── ⑩ 手机相册落点的拍摄者归属（App 0.4.2 统一落 照片/手机相册/<u>/）────

def test_photo_uploader_phone_album(client, auth, workdir):
    from app.api.photos import _photo_uploader
    # 新统一落点：照片/手机相册/<u>/file → 归属 <u>
    assert _photo_uploader("照片/手机相册/debug/IMG_1.jpg") == "debug"
    # 共享相册目录不受影响：照片/<自定义相册>/file 仍是共享空间
    assert _photo_uploader("照片/2026 春节/聚餐.jpg") == "共享空间"
    # 旧路径语义保持：incoming/<u>/ 与 private/<u>/ 归属本人；照片根下文件算共享
    assert _photo_uploader("incoming/debug/手机相册/IMG_0.jpg") == "debug"
    assert _photo_uploader("private/admin/a.jpg") == "admin"
    assert _photo_uploader("照片/直接放的.jpg") == "共享空间"


# ── ⑩-b 0.7.0 相册子目录落点的拍摄者归属（照片|视频/<u>/<相册>/…）────

def test_photo_uploader_album_layout(client, auth, workdir):
    from app.api.photos import _photo_uploader
    users = {"debug", "user@example.com"}
    # 新落点：照片/<u>/<相册>/file、视频/<u>/<相册>/file → 归属 <u>
    assert _photo_uploader("照片/debug/LSTest1/a.jpg", users) == "debug"
    assert _photo_uploader("视频/debug/Camera/v.mp4", users) == "debug"
    assert _photo_uploader("照片/user@example.com/未分类/b.jpg", users) \
        == "user@example.com"
    # 第二段不是真实用户名 → 不误判（手工整理的二级目录保持共享）
    assert _photo_uploader("照片/同学&生活&学校/子目录/a.jpg", users) \
        == "共享空间"
    assert _photo_uploader("视频/notauser/Camera/v.mp4", users) == "共享空间"
    # 未知用户（改名后被删的账号残留目录）不归属
    assert _photo_uploader("照片/gone_user/Camera/a.jpg", users) == "共享空间"
    # 旧版落点在带 users 参数时语义不变
    assert _photo_uploader("照片/手机相册/debug/IMG_1.jpg", users) == "debug"

