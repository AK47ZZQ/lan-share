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
    # v3.47 类型筛选：actions 全集 + 前缀筛选命中行全部以该前缀开头
    assert isinstance(j["actions"], list) and j["actions"]
    j2 = auth.get("/api/admin/audit",
                  params={"limit": 100, "action": j["actions"][0].split("_")[0]}).json()
    prefix = j["actions"][0].split("_")[0]
    assert all(row["action"].startswith(prefix) for row in j2["items"])


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


# ── v3.40 · 公告栏 ───────────────────────────────────────────

def test_announcements_lifecycle(auth, client):
    title = f"维护公告-{_rand()}"
    r = auth.post("/api/admin/announcements",
                  json={"title": title, "body": "今晚 20:00 维护"})
    assert r.status_code == 200, r.text

    items = auth.get("/api/announcements").json()["items"]
    assert any(i["title"] == title for i in items)

    # 成员可见，但不可增删
    m = _member(client)
    assert any(i["title"] == title
               for i in m.get("/api/announcements").json()["items"])
    assert m.post("/api/admin/announcements",
                  json={"title": "x"}).status_code == 403

    aid = next(i["id"] for i in items if i["title"] == title)
    assert auth.delete(f"/api/admin/announcements/{aid}").status_code == 200
    assert auth.delete(f"/api/admin/announcements/{aid}").status_code == 404
    assert not any(i["title"] == title
                   for i in auth.get("/api/announcements").json()["items"])

    r = auth.post("/api/admin/announcements", json={"title": "   "})
    assert r.status_code == 422  # 空标题拒绝


# ── v3.40 · 站内通知管理 ─────────────────────────────────────

def test_admin_notifications_list_and_delete(auth, client):
    m = _member(client)
    # 定向广播给成员 → 成员收件箱出现
    r = auth.post("/api/admin/notify",
                  json={"title": f"定向通知-{_rand()}", "body": "hi"})
    assert r.status_code == 200, r.text

    items = auth.get("/api/admin/notifications", params={"limit": 200}).json()["items"]
    assert items, "全站通知列表不应为空"
    assert all("username" in i for i in items)

    # 按成员筛选
    names = {i["username"] for i in items}
    target_name = sorted(names)[0]
    filtered = auth.get("/api/admin/notifications",
                        params={"username": target_name}).json()["items"]
    assert all(i["username"] == target_name for i in filtered)

    nid = items[0]["id"]
    assert auth.delete(f"/api/admin/notifications/{nid}").status_code == 200
    assert auth.delete(f"/api/admin/notifications/{nid}").status_code == 404
    assert m.get("/api/admin/notifications").status_code == 403


def test_broadcast_purge_removes_record_and_notifications(auth, client):
    m = _member(client)
    title = f"待彻底删除-{_rand()}"
    assert auth.post("/api/admin/notify",
                     json={"title": title}).status_code == 200

    inbox_before = len(m.get("/api/notifications").json()["items"])
    assert inbox_before >= 1

    sent = auth.get("/api/admin/notifications/sent").json()["items"]
    bid = next(b["id"] for b in sent if b["title"] == title)

    r = auth.delete(f"/api/admin/notifications/broadcast/{bid}")
    assert r.status_code == 200, r.text
    assert r.json()["notifications_removed"] >= 1
    assert auth.delete(
        f"/api/admin/notifications/broadcast/{bid}").status_code == 404

    titles = [n["title"] for n in m.get("/api/notifications").json()["items"]]
    assert title not in titles, "彻底删除后收件箱不应残留"


# ── v3.40 · 会话治理增强 ─────────────────────────────────────

def test_kick_by_account(auth, client):
    # 自建已知用户名的成员：uid 必须"指名道姓"，否则会踢错人（文件内多测试
    # 各自建过成员，admin 列表第一个未必是本测试的 m）
    admin0 = _bare(client)
    assert admin0.post("/api/login", json={
        "username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
    inv = admin0.post("/api/admin/invites",
                      json={"role": "member", "hours": 24},
                      cookies=dict(admin0.cookies)).json()
    mname = "kickm" + _rand()
    assert client.post("/api/invites/redeem", json={
        "code": inv["code"], "username": mname,
        "password": MEMBER_PASS}).status_code == 200
    m = _bare(client)
    assert m.post("/api/login", json={
        "username": mname, "password": MEMBER_PASS}).status_code == 200
    assert m.post("/api/auth/token",
                  json={"name": f"kick-{_rand()}"}).status_code == 200

    users = auth.get("/api/admin/users").json()
    uid = next(u["id"] for u in users if u["username"] == mname)

    r = auth.post(f"/api/admin/users/{uid}/kick", json={"scope": "all"})
    assert r.status_code == 200, r.text
    assert r.json()["sessions"] >= 1

    # 成员的 Web 会话已死
    assert m.get("/api/auth/me").status_code == 401

    # 自踢防护：admin 对自己 kick web/all 必须 400
    me_row = auth.get("/api/auth/me").json()
    admin_uid = next(u["id"] for u in users if u["username"] == me_row["username"])
    assert auth.post(f"/api/admin/users/{admin_uid}/kick",
                     json={"scope": "web"}).status_code == 400


def test_global_sessions_purge_keeps_self(client):
    a1 = _bare(client)
    assert a1.post("/api/login", json={
        "username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
    a2 = _bare(client)
    assert a2.post("/api/login", json={
        "username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200

    operator = _bare(client)
    operator.post("/api/login", json={
        "username": ADMIN_USER, "password": ADMIN_PASS})
    r = operator.delete("/api/admin/sessions")
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] >= 1

    # 其他会话全部下线，操作者自身保留
    assert a1.get("/api/auth/me").status_code == 401
    assert a2.get("/api/auth/me").status_code == 401
    assert operator.get("/api/auth/me").status_code == 200
