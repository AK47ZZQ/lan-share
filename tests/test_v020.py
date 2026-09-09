"""v0.20 批次回归：管理广播推送 / 授权一级目录约束 / 照片入库排序。

- /api/admin/notify：标题必填、目标收敛、站内通知逐人落库；
- 授权只收共享空间一级目录（子目录随父级继承，深路径 400）；
- /api/photos?sort=added：入库时间倒序（二维码/网页上传按 EXIF 落历史
  月份，时间线顶部"看不到新照片"的感知问题）。
"""

import secrets

import pytest

from app.db import _db, _db_lock
from app.config import SHARE_ROOT
from test_multiuser import make_user  # 跨文件复用建号工厂 fixture


# ── /api/admin/notify 广播 ───────────────────────────────────

def test_notify_requires_admin(client, make_user):
    member = make_user("member")
    r = member.post("/api/admin/notify", json={"title": "hi"})
    assert r.status_code == 403


def test_notify_title_required(auth):
    assert auth.post("/api/admin/notify", json={"title": "   "}).status_code == 400


def test_notify_broadcast_all_and_targets(auth, make_user):
    member = make_user("member")
    # 全体：目标数 == 在职用户数
    r = auth.post("/api/admin/notify",
                  json={"title": "全体通知", "body": "今晚停电维护"})
    assert r.status_code == 200, r.text
    sent_all = r.json()["sent"]
    users = auth.get("/api/admin/users").json()
    assert sent_all == len([u for u in users if u.get("is_active", True)])

    # 定向：只有指定的人收到
    uid = next(u["id"] for u in users if u["username"] == member.username)
    r = auth.post("/api/admin/notify",
                  json={"title": "点名", "user_ids": [uid]})
    assert r.status_code == 200 and r.json()["sent"] == 1

    # 站内信箱：member 收到 2 条，type=admin
    inbox = member.get("/api/notifications").json()
    mine = [n for n in inbox["items"] if n["type"] == "admin"]
    assert len(mine) >= 2

    # 不存在的目标 → 404
    r = auth.post("/api/admin/notify", json={"title": "x", "user_ids": [999999]})
    assert r.status_code == 404


# ── 授权 UI 契约（一级目录 + 前缀继承）──────────────────────

def test_grant_first_level_covers_subdirs(auth, make_user, workdir):
    """v0.20 授权重构语义钉死：只授一级目录，子目录自动继承（前缀命中）。"""
    guest = make_user("guest")
    uid = next(u["id"] for u in auth.get("/api/admin/users").json()
               if u["username"] == guest.username)
    assert auth.post(f"/api/mkdir/{workdir}/sub").status_code == 200
    r = auth.post("/api/admin/grants", json={"user_id": uid, "path": workdir})
    assert r.status_code == 200, r.text
    try:
        # 一级目录可见
        assert guest.get(f"/api/files/{workdir}").status_code == 200
        # 子目录随父级继承
        assert guest.get(f"/api/files/{workdir}/sub").status_code == 200
        # 未授权的一级目录依旧不可见
        other = f"t-denied-{secrets.token_hex(3)}"
        assert auth.post(f"/api/mkdir/{other}").status_code == 200
        try:
            assert guest.get(f"/api/files/{other}").status_code == 403
        finally:
            import shutil
            shutil.rmtree(SHARE_ROOT / other, ignore_errors=True)
    finally:
        for g in auth.get("/api/admin/grants").json():
            if g["user_id"] == uid and g["path"] == f"shared/{workdir}":
                auth.delete(f"/api/admin/grants/{g['id']}")


# ── /api/photos sort=added ───────────────────────────────────

def test_photos_sort_added(auth, workdir):
    # 先落两个真实文件并强制扫描入库（扫描的 gone 回收会清掉无文件的行）
    for name in ("old.jpg", "new.jpg"):
        (SHARE_ROOT / workdir / name).write_bytes(b"\xff\xd8fakejpg")
    assert auth.get("/api/photos", params={"refresh": "1"}).status_code == 200
    rows = [r["path"] for r in _db().execute(
        "SELECT path FROM photos WHERE path LIKE ?", (f"{workdir}/%",)).fetchall()]
    assert rows, "扫描未入库（测试前置失败）"
    # 摆拍：old.jpg 拍摄早、入库早；new.jpg 拍摄更早、入库晚——
    # 拍摄序 old 在前，入库序 new 在最前（核心断言）
    with _db_lock:
        _db().execute(
            "UPDATE photos SET taken_at=?, added_at=? WHERE path=?",
            ("2020-01-01", 1000.0, f"{workdir}/old.jpg"))
        _db().execute(
            "UPDATE photos SET taken_at=?, added_at=? WHERE path=?",
            ("2019-01-01", 2000.0, f"{workdir}/new.jpg"))
        _db().commit()
    try:
        taken = [i["path"] for i in auth.get(
            "/api/photos", params={"sort": "taken"}).json()["items"]
            if i["path"].startswith(f"{workdir}/")]
        added = [i["path"] for i in auth.get(
            "/api/photos", params={"sort": "added"}).json()["items"]
            if i["path"].startswith(f"{workdir}/")]
        assert taken.index(f"{workdir}/old.jpg") < taken.index(f"{workdir}/new.jpg")
        assert added[0] == f"{workdir}/new.jpg"
        # added_at 为空的存量行沉底
        with _db_lock:
            _db().execute(
                "UPDATE photos SET added_at=NULL WHERE path=?",
                (f"{workdir}/new.jpg",))
            _db().commit()
        added2 = [i["path"] for i in auth.get(
            "/api/photos", params={"sort": "added"}).json()["items"]
            if i["path"].startswith(f"{workdir}/")]
        assert added2[-1] == f"{workdir}/new.jpg"
    finally:
        with _db_lock:
            _db().execute("DELETE FROM photos WHERE path LIKE ?",
                                 (f"{workdir}/%",))
            _db().commit()


def test_upload_bumps_photo_scan_throttle(auth, workdir):
    import app.api.photos as photos_mod
    photos_mod._last_photo_scan = 12345.0
    photos_mod._last_force_scan = 12345.0
    r = auth.post(
        f"/api/upload?path={workdir}",
        files={"file": ("bump.jpg", b"\xff\xd8fakejpg", "image/jpeg")})
    assert r.status_code == 200, r.text
    assert photos_mod._last_photo_scan == 0.0
    assert photos_mod._last_force_scan == 0.0
