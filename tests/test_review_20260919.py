"""2026-09-19 对抗审查批次回归（F3：照片墙可见性下推的引擎分歧防线）。

F3 根因：/api/photos 的 member/admin 可见性下推 SQL 用 LIKE 'private/%' 判
私人段——SQLite 的 LIKE 只对 ASCII 折叠大小写（测试绿），**PG 的 LIKE 大小写
敏感**（生产）。photos 表的 path 按磁盘实际大小写入库：根目录若存在大小写
变体（'Private'），下推条件在 PG 上会把他人 private 照片当 shared 放行。
本测试直插大小写变体毒行，在**两个引擎**上都必须不可见（下推只是预过滤，
纯内存的 visible() 是最终防线）。
"""
import time

import pytest
from fastapi.testclient import TestClient

from conftest import MEMBER_PASS2
from app.api import photos as photos_api
from app.config import SHARE_ROOT
from app.db import _db
from app.main import app
from app.store import photos as photos_store

POISON_CAP = "Private/victim/rv-e2e-caseleak.jpg"
POISON_LOW = "private/victim/rv-e2e-lowerleak.jpg"
OWN = "private/{u}/rv-e2e-own.jpg"
SHARED = "rv-e2e-shared.jpg"


@pytest.fixture()
def two_clients(client):
    """(admin_client, member_client, username)，测试结束清理成员与毒行。"""
    a = TestClient(app, base_url="https://testserver")
    r = a.post("/api/login", json={"username": "admin", "password": "test-pass-123"})
    assert r.status_code == 200
    r = a.post("/api/admin/invites", json={"role": "member", "hours": 1})
    code = r.json()["code"]
    uname = "case" + __import__("secrets").token_hex(3)
    assert client.post("/api/invites/redeem", json={
        "code": code, "username": uname, "password": MEMBER_PASS2}).status_code == 200
    m = TestClient(app, base_url="https://testserver")
    assert m.post("/api/login", json={
        "username": uname, "password": MEMBER_PASS2}).status_code == 200
    yield a, m, uname
    row = _db().execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
    if row:
        _db().execute("DELETE FROM users WHERE id=?", (row["id"],))
        _db().commit()


@pytest.fixture()
def poisoned_rows():
    """直插照片表毒行（不落盘文件——把扫描节流钉住防止增量扫描清行）。"""
    uname_holder: list[str] = []

    def _inject(uname: str):
        uname_holder.append(uname)
        photos_store.apply_scan([
            (POISON_CAP, "2026-01-01T00:00:00", 1, 1, 0, time.time()),
            (POISON_LOW, "2026-01-01T00:00:00", 1, 1, 0, time.time()),
            (OWN.format(u=uname), "2026-01-01T00:00:00", 1, 1, 0, time.time()),
            (SHARED, "2026-01-01T00:00:00", 1, 1, 0, time.time()),
        ], [])
        # 钉住节流：/api/photos 不得触发全盘扫描（否则毒行会被"文件已消失"清掉）
        photos_api._last_photo_scan = time.time()
        photos_api._last_force_scan = time.time()

    yield _inject
    _db().execute("DELETE FROM photos WHERE path IN (?,?,?,?)",
                  (POISON_CAP, POISON_LOW, OWN.format(u=uname_holder[0]), SHARED))
    _db().commit()


def test_photo_pushdown_never_leaks_case_variants(client, two_clients, poisoned_rows):
    a, m, uname = two_clients
    poisoned_rows(uname)

    def paths(c):
        return {it["path"] for it in c.get("/api/photos?limit=500").json()["items"]}

    got = paths(m)
    assert POISON_CAP not in got, "大小写变体毒行泄漏给 member"
    assert POISON_LOW not in got, "他人 private（小写）泄漏给 member"
    assert OWN.format(u=uname) in got, "本人 private 照片被误杀"
    assert SHARED in got, "shared 照片被误杀"

    got_a = paths(a)
    assert POISON_CAP not in got_a, "大小写变体毒行泄漏给 admin"
    assert POISON_LOW not in got_a, "他人 private（小写）泄漏给 admin"


def test_reset_pair_codes_is_scope_precise():
    """F4 回归：reset_pair_codes 只清配对码 scope，不得殃及 zip/分享票据。

    此前误用 ttl_state.clear_all() 整表清空——函数名撒谎；conftest 每个测试
    间隔都调它，跨域票据的共存场景在测试里永远无法出现。"""
    from app.api.devices import reset_pair_codes
    from app.store import ttl_state as ts

    ts.put(ts.SCOPE_PAIR_CODE, "123456", "1", 60)
    ts.put(ts.SCOPE_ZIP_TICKET, "rk-zip-tkt", "whatever.zip", 60)
    ts.put(ts.SCOPE_SHARE_TICKET, "rk-share-tkt", "sharetoken", 60)
    try:
        reset_pair_codes()
        assert ts.get(ts.SCOPE_PAIR_CODE, "123456") is None
        assert ts.get(ts.SCOPE_ZIP_TICKET, "rk-zip-tkt") == "whatever.zip"
        assert ts.get(ts.SCOPE_SHARE_TICKET, "rk-share-tkt") == "sharetoken"
    finally:
        ts.delete(ts.SCOPE_ZIP_TICKET, "rk-zip-tkt")
        ts.delete(ts.SCOPE_SHARE_TICKET, "rk-share-tkt")


def test_instant_upload_quota_prices_real_sample_size(client, workdir):
    """F5：秒传配额计价必须用样例真实大小，不信客户端自报 size。

    member 配额 1KB；共享区有 10KB 文件。自报 size=10 + 该文件 sha256 发起
    私人空间秒传——修复前：过配额预检后直接硬链 10KB 样例 = 配额绕过。"""
    import hashlib
    import os as _os

    from fastapi.testclient import TestClient

    uname = "quota" + __import__("secrets").token_hex(3)
    a = TestClient(app, base_url="https://testserver")
    assert a.post("/api/login", json={
        "username": "admin", "password": "test-pass-123"}).status_code == 200
    assert client.post("/api/invites/redeem", json={
        "code": a.post("/api/admin/invites", json={"role": "member", "hours": 1}).json()["code"],
        "username": uname, "password": MEMBER_PASS2}).status_code == 200
    m = TestClient(app, base_url="https://testserver")
    assert m.post("/api/login", json={
        "username": uname, "password": MEMBER_PASS2}).status_code == 200
    uid = _db().execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()["id"]
    try:
        # admin 在共享区放 10KB 真实文件
        payload = _os.urandom(10_000)
        sha = hashlib.sha256(payload).hexdigest()
        assert a.post(f"/api/upload/{workdir}",
                      files={"file": ("qsrc.bin", payload, "application/octet-stream")}
                      ).status_code == 200
        # member 配额 1KB
        assert a.patch(f"/api/admin/users/{uid}",
                       json={"quota_bytes": 1000}).status_code == 200
        # 自报 size=10 秒传进私人空间：必须 409 配额拒绝，而不是硬链成功
        r = m.post("/api/upload/init", json={
            "dest_dir": "private", "filename": "qleak.bin",
            "size": 10, "sha256": sha})
        assert r.status_code == 409, f"配额被自报 size 绕过：{r.status_code} {r.text}"
        assert not r.json().get("instant")
    finally:
        row = _db().execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
        if row:
            _db().execute("DELETE FROM users WHERE id=?", (row["id"],))
            _db().commit()
        import shutil as _sh
        _sh.rmtree(SHARE_ROOT / "private" / uname, ignore_errors=True)


def test_zip_rejects_share_root_target(auth):
    """F6：打包目标是共享根（paths=[""] 或 "/"）必须 400 拒绝。

    修复前：resolve("")=SHARE_ROOT 通过存在性检查，zip 循环里
    f.relative_to(t.parent) 抛 ValueError → 500，且全树 rglob 白烧 IO、
    .zips 留孤儿文件（要等 sweep 2×TTL 才回收）。"""
    r = auth.post("/api/zip", json={"paths": [""]})
    assert r.status_code == 400, f"{r.status_code} {r.text}"
    assert not (SHARE_ROOT / ".zips").exists() or \
        not any((SHARE_ROOT / ".zips").iterdir()), "根目录打包不该留下孤儿 zip"
