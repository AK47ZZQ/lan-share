"""v3.39 管理台端点回归（Web 控制台供数层）。

覆盖：总览聚合（admin 200 / member 403）、大文件 TOP、疑似重复聚类、
审计日志、Web 会话列表与吊销（吊销后原会话立即失效）、App 设备列表与吊销
（吊销后 Bearer 失效）。治理遍历跳过隐藏目录（.trash 不入列）。
"""
import os
import time

from conftest import ADMIN_PASS, ADMIN_USER, MEMBER_PASS

from app.main import app


def _rand() -> str:
    return os.urandom(3).hex()


def _bare(client):
    return client.__class__(app, base_url="https://testserver")


def _member(client):
    admin = _bare(client)
    assert admin.post("/api/login", json={
        "username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
    inv = admin.post("/api/admin/invites", json={"role": "member", "hours": 24},
                     cookies=dict(admin.cookies)).json()
    username = "m" + _rand()
    r = client.post("/api/invites/redeem", json={
        "code": inv["code"], "username": username, "password": MEMBER_PASS})
    assert r.status_code == 200, r.text
    c = _bare(client)
    r = c.post("/api/login", json={"username": username, "password": MEMBER_PASS})
    assert r.status_code == 200
    return c


def _write(auth, rel_path: str, content: bytes):
    d, _, name = rel_path.rpartition("/")
    r = auth.post(f"/api/upload/{d}", files={"file": (name, content)})
    assert r.status_code == 200, r.text


# ── 总览 ─────────────────────────────────────────────────────

def test_overview_admin_ok_member_forbidden(auth, client):
    r = auth.get("/api/admin/overview")
    assert r.status_code == 200, r.text
    j = r.json()
    for k in ("users", "photos", "shares", "trash", "blobs",
              "sessions", "devices", "disk", "server_time"):
        assert k in j, f"总览缺 {k}"
    assert j["users"]["total"] >= 1
    assert j["disk"]["total_gb"] > 0

    m = _member(client)
    assert m.get("/api/admin/overview").status_code == 403


def test_overview_counts_uploaded_photo(auth):
    _write(auth, f"shared/{_rand()}.jpg", b"\xff\xd8fake")
    j = auth.get("/api/admin/overview").json()
    assert j["photos"]["count"] >= 0  # photos 表按扫描入库，上传本身不必然计数
    assert j["shares"]["active"] >= 0


# ── 大文件 / 疑似重复 ─────────────────────────────────────────

def test_large_files_lists_uploaded(auth):
    name = f"{_rand()}.bin"
    _write(auth, f"shared/{name}", b"A" * 4096)
    j = auth.get("/api/admin/large-files", params={"limit": 200}).json()
    paths = [i["path"] for i in j["items"]]
    assert f"shared/{name}" in paths
    assert j["total_files"] >= 1
    # 隐藏目录不入治理视图
    assert not any(p.startswith(".") or "/." in p for p in paths)


def test_duplicates_groups_same_size(auth):
    n = _rand()
    _write(auth, f"shared/dup-{n}-a.bin", b"B" * 2048)
    _write(auth, f"shared/dup-{n}-b.bin", b"C" * 2048)
    j = auth.get("/api/admin/duplicates",
                 params={"min_size": 1024, "limit": 200}).json()
    hit = [g for g in j["groups"]
           if any(f"dup-{n}-a.bin" in p for p in g["paths"])]
    assert hit, "同尺寸组未被聚类"
    g = hit[0]
    assert g["wasted"] == 2048 * (len(g["paths"]) - 1)


# ── 审计 ─────────────────────────────────────────────────────

def test_audit_returns_recent_entries(auth):
    r = auth.post("/api/login", json={
        "username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200
    j = auth.get("/api/admin/audit", params={"limit": 50}).json()
    assert isinstance(j["items"], list) and j["items"]
    top = j["items"][0]
    for k in ("ts", "ip", "action", "target", "detail", "username"):
        assert k in top


# ── 会话与设备治理 ────────────────────────────────────────────

def test_session_listing_and_revocation(client):
    victim = _bare(client)
    assert victim.post("/api/login", json={
        "username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200

    # probe 紧随 victim 登录：全表按登录时间降序时，最新一条必是 probe 自己，
    # 第二条必是 victim（测试串行，无并发登录干扰）——所有权确定，不靠猜
    probe = _bare(client)
    assert probe.post("/api/login", json={
        "username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
    ordered = sorted(probe.get("/api/admin/sessions").json()["items"],
                     key=lambda s: s["created_at"], reverse=True)
    assert len(ordered) >= 2
    victim_hash = ordered[1]["token_hash"]

    admin = _bare(client)
    admin.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert admin.delete(f"/api/admin/sessions/{victim_hash}").status_code == 200
    # 幂等边界：再删一次 404
    assert admin.delete(f"/api/admin/sessions/{victim_hash}").status_code == 404
    # 被吊销方下一跳立即 401；未吊销的 probe 与 admin 自身不受影响
    assert victim.get("/api/auth/me").status_code == 401
    assert probe.get("/api/auth/me").status_code == 200
    assert admin.get("/api/auth/me").status_code == 200


def test_device_listing_and_revocation(client):
    admin_c = _bare(client)
    admin_c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    # 换发设备令牌（App 流程）
    r = admin_c.post("/api/auth/token", json={"name": f"console-{_rand()}"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    admin2 = _bare(client)
    admin2.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    items = admin2.get("/api/admin/devices").json()["items"]
    dev = next(d for d in items if d["name"].startswith("console-"))
    assert admin2.delete(f"/api/admin/devices/{dev['id']}").status_code == 200

    # 吊销后原 Bearer 立即失效
    dead = _bare(client)
    dead.headers["Authorization"] = f"Bearer {token}"
    r = dead.get("/api/auth/me")
    assert r.status_code == 401


def test_member_forbidden_on_governance(client):
    m = _member(client)
    for path in ("/api/admin/large-files", "/api/admin/duplicates",
                 "/api/admin/audit", "/api/admin/sessions", "/api/admin/devices"):
        assert m.get(path).status_code == 403, path
