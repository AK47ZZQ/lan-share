"""M0 基线：现有行为的回归网（拆包/PG/Vue 升级的安全网）。

覆盖 docs/DEVELOPMENT.md 回归清单的核心后端链路 + 对抗审查历史教训
（safe_path 穿越、防爆破锁定、重名防覆盖）。
"""

import hashlib
import os

import pytest

from conftest import ADMIN_PASS, ADMIN_USER


# ── 认证 ─────────────────────────────────────────────────


def test_auth_state_after_setup(client):
    r = client.get("/api/auth/state")
    assert r.status_code == 200
    assert r.json()["needs_setup"] is False  # conftest 已建管理员


def test_setup_rejected_when_admin_exists(client):
    r = client.post("/api/setup", json={"username": "second", "password": "test-pass-456"})
    assert r.status_code == 403


def test_login_wrong_password_401(client):
    assert client.post(
        "/api/login", json={"username": ADMIN_USER, "password": "wrong-pass"}
    ).status_code == 401


def test_me_requires_session(client):
    assert client.get("/api/auth/me").status_code == 401


def test_authcheck_forward_auth(client, auth):
    # 未登录 → 401（Caddy forward_auth 依赖此行为拦 /files/*）
    assert client.get("/authcheck").status_code == 401
    # 登录后 → 200
    assert auth.get("/authcheck").status_code == 200


def test_logout_revokes_session(auth):
    assert auth.get("/api/auth/me").status_code == 200
    assert auth.post("/api/logout").status_code == 200
    assert auth.get("/api/auth/me").status_code == 401


def test_login_bruteforce_locks_account(client):
    """历史教训：5 次失败锁 15 分钟，锁定期内正确密码也拒绝。"""
    for _ in range(5):
        r = client.post("/api/login", json={"username": ADMIN_USER, "password": "wrong"})
        assert r.status_code == 401
    r = client.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 429  # 锁定期内正确密码同样拒绝


# ── 文件基础操作 ─────────────────────────────────────────


def test_mkdir_list_rename(auth, workdir):
    assert auth.post(f"/api/mkdir/{workdir}/docs").status_code == 200

    r = auth.get(f"/api/files/{workdir}")
    assert r.status_code == 200
    assert "docs" in [e["name"] for e in r.json()["items"]]

    r = auth.post("/api/rename", json={"path": f"{workdir}/docs", "new_name": "docsv2"})
    assert r.status_code == 200, r.text
    r = auth.get(f"/api/files/{workdir}")
    assert "docsv2" in [e["name"] for e in r.json()["items"]]


def test_upload_direct_and_visible_in_list(auth, workdir):
    r = auth.post(f"/api/upload/{workdir}",
                  files={"file": ("hello.txt", b"hello lan-share", "text/plain")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deduped"] is False

    r = auth.get(f"/api/files/{workdir}")
    entry = next(e for e in r.json()["items"] if e["name"] == "hello.txt")
    assert entry["size"] == len(b"hello lan-share")


def test_upload_chunked_roundtrip(auth, workdir):
    """分块上传 → finish 服务端校验 sha → 磁盘内容一致（断点续传的地基）。"""
    payload = os.urandom(300_000)
    sha = hashlib.sha256(payload).hexdigest()

    r = auth.post("/api/upload/init", json={
        "dest_dir": workdir, "filename": "blob.bin",
        "size": len(payload), "sha256": sha})
    assert r.status_code == 200
    body = r.json()
    assert body["instant"] is False
    sid, chunk_size = body["session"], body["chunk_size"]

    for i, off in enumerate(range(0, len(payload), chunk_size)):
        r = auth.post(f"/api/upload/chunk/{sid}?index={i}",
                      content=payload[off:off + chunk_size])
        assert r.status_code == 200, r.text

    assert auth.post(f"/api/upload/finish/{sid}").status_code == 200

    with open(os.path.join(os.environ["SHARE_ROOT"], workdir, "blob.bin"), "rb") as f:
        assert f.read() == payload


def test_upload_instant_dedup(auth, workdir):
    """秒传：同 sha256 内容第二处落点直接硬链接，不重传。"""
    payload = os.urandom(64_000)
    sha = hashlib.sha256(payload).hexdigest()

    r = auth.post("/api/upload/init", json={
        "dest_dir": workdir, "filename": "a.bin", "size": len(payload), "sha256": sha})
    assert r.json()["instant"] is False
    sid = r.json()["session"]
    auth.post(f"/api/upload/chunk/{sid}?index=0", content=payload)
    assert auth.post(f"/api/upload/finish/{sid}").status_code == 200

    r = auth.post("/api/upload/init", json={
        "dest_dir": workdir, "filename": "b.bin", "size": len(payload), "sha256": sha})
    body = r.json()
    assert body["instant"] is True
    assert body["path"].endswith("b.bin")


def test_move_with_conflict_auto_rename(auth, workdir):
    """移动重名自动 (2) 改名，不覆盖（os.rename 原子防覆盖）。"""
    auth.post(f"/api/mkdir/{workdir}/src")
    auth.post(f"/api/mkdir/{workdir}/dst")
    auth.post(f"/api/upload/{workdir}/src",
              files={"file": ("f.txt", b"one", "text/plain")})
    auth.post(f"/api/upload/{workdir}/dst",
              files={"file": ("f.txt", b"two", "text/plain")})

    r = auth.post("/api/move", json={
        "sources": [f"{workdir}/src/f.txt"], "dest_dir": f"{workdir}/dst"})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["ok"] is True
    assert results[0]["dest"].endswith("f (2).txt")

    dst = os.path.join(os.environ["SHARE_ROOT"], workdir, "dst")
    with open(os.path.join(dst, "f.txt"), "rb") as f:
        assert f.read() == b"two"  # 原文件未被覆盖
    with open(os.path.join(dst, "f (2).txt"), "rb") as f:
        assert f.read() == b"one"


def test_move_rejects_into_own_subdir(auth, workdir):
    auth.post(f"/api/mkdir/{workdir}/parent")
    auth.post(f"/api/mkdir/{workdir}/parent/child")
    r = auth.post("/api/move", json={
        "sources": [f"{workdir}/parent"], "dest_dir": f"{workdir}/parent/child"})
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is False


def test_move_requires_sources(auth):
    assert auth.post("/api/move", json={"sources": [], "dest_dir": ""}).status_code == 400


# ── 回收站 ───────────────────────────────────────────────


def test_delete_restore_roundtrip(auth, workdir):
    auth.post(f"/api/upload/{workdir}",
              files={"file": ("doomed.txt", b"bye", "text/plain")})
    assert auth.delete(f"/api/files/{workdir}/doomed.txt").status_code == 200

    r = auth.get("/api/trash")
    item = next(t for t in r.json() if t["name"] == "doomed.txt")
    assert item["orig_path"] == f"{workdir}/doomed.txt"

    r = auth.post(f"/api/trash/{item['id']}/restore")
    assert r.status_code == 200, r.text
    assert os.path.exists(os.path.join(os.environ["SHARE_ROOT"], workdir, "doomed.txt"))


def test_delete_root_rejected(auth):
    assert auth.delete("/api/files/").status_code in (400, 403, 404)


# ── safe_path 边界（历史审查教训）─────────────────────────


@pytest.mark.parametrize("evil", [
    "%2e%2e/.meta.db",
    "%2e%2e%5c%2e%2e%5c.meta.db",
    "sub/%2e%2e/%2e%2e/%2e%2e/.meta.db",
    ".trash",
    ".meta.db",
])
def test_path_traversal_and_hidden_denied(auth, evil):
    assert auth.get(f"/api/files/{evil}").status_code == 403, evil


def test_filename_with_colon_sanitized(auth, workdir):
    """NTFS 备用数据流（冒号）必须被清洗。"""
    r = auth.post(f"/api/upload/{workdir}",
                  files={"file": ("evil:ads.txt", b"x", "text/plain")})
    assert r.status_code == 200
    names = [e["name"] for e in auth.get(f"/api/files/{workdir}").json()["items"]]
    assert all(":" not in n for n in names)


# ── 分享 ─────────────────────────────────────────────────


def test_share_full_flow(auth, client, workdir):
    payload = b"shared content"
    auth.post(f"/api/upload/{workdir}",
              files={"file": ("shareme.txt", payload, "text/plain")})

    r = auth.post("/api/share", data={"path": f"{workdir}/shareme.txt"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    # 匿名可取 meta 与下载（分享链接本就不要求登录）
    meta = client.get(f"/api/share/meta/{token}")
    assert meta.status_code == 200
    assert meta.json()["name"] == "shareme.txt"

    prep = client.post(f"/api/share/{token}/prepare", json={})
    assert prep.status_code == 200, prep.text
    dl = client.get(prep.json()["url"])
    assert dl.status_code == 200
    assert dl.content == payload

    # 撤销（需登录）= 删库行，匿名访问立即 404
    assert auth.delete(f"/api/shares/{token}").status_code == 200
    assert client.get(f"/api/share/meta/{token}").status_code == 404


def test_share_password_lock(auth, workdir):
    """提取码错 5 次锁定 15 分钟（按 token），锁定期内正确密码也拒绝。"""
    auth.post(f"/api/upload/{workdir}",
              files={"file": ("locked.txt", b"x", "text/plain")})
    r = auth.post("/api/share", data={"path": f"{workdir}/locked.txt",
                                      "password": "1234"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    for _ in range(5):
        resp = auth.post(f"/api/share/{token}/prepare", json={"password": "0000"})
        assert resp.status_code == 403
    resp = auth.post(f"/api/share/{token}/prepare", json={"password": "1234"})
    assert resp.status_code == 429  # 锁定期内正确密码同样拒绝


def test_share_download_limit(auth, workdir):
    auth.post(f"/api/upload/{workdir}",
              files={"file": ("limited.txt", b"x", "text/plain")})
    r = auth.post("/api/share", data={"path": f"{workdir}/limited.txt",
                                      "max_downloads": "1"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    prep = auth.post(f"/api/share/{token}/prepare", json={})
    assert auth.get(prep.json()["url"]).status_code == 200
    # 次数在响应发送完成后才计（BackgroundTask），TestClient 会跑完 background
    prep2 = auth.post(f"/api/share/{token}/prepare", json={})
    assert prep2.status_code == 410  # 下载次数已达上限


def test_preview_chinese_filename(auth, workdir):
    """回归：中文文件名预览曾因 Content-Disposition 头 latin-1 编码 500。"""
    name = "截图 测试 中文.txt"
    r = auth.post(f"/api/upload/{workdir}",
                  files={"file": (name, b"content", "text/plain")})
    assert r.status_code == 200, r.text
    r = auth.get(f"/preview/{workdir}/{name}")
    assert r.status_code == 200, f"{r.status_code}: {r.text[:120]}"
    assert r.content == b"content"


def test_share_range_requests_count_once(auth, workdir):
    """Regression: download managers send parallel Range requests; one ticket = one count."""
    import server as _s
    auth.post(f"/api/upload/{workdir}",
              files={"file": ("big.apk", b"x" * 3000, "application/octet-stream")})
    r = auth.post("/api/share", data={"path": f"{workdir}/big.apk", "max_downloads": "3"})
    token = r.json()["token"]
    url = auth.post(f"/api/share/{token}/prepare", json={}).json()["url"]
    for rng in ("bytes=0-999", "bytes=1000-1999", "bytes=2000-2999", "bytes=0-"):
        resp = auth.get(url, headers={"Range": rng})
        assert resp.status_code in (200, 206)
    row = _s._db().execute("SELECT downloads FROM shares WHERE token=?", (token,)).fetchone()
    assert row["downloads"] == 1, "should count exactly once"
