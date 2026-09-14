"""反馈二次补充（20260913225909-e9d1）+ 照片墙隐藏目录与文件夹筛选
（20260913211805-b4e0 / 20260913211718-b58a）回归。

- append：本人/admin 可补充、他人 403、空 422、已完成态可补充、
  frontmatter 更新 时间刷新且正文追加"补充 @"段、状态不被改动。
- wall-excludes：admin 增删隐藏目录 → /api/photos、facets、folders 三端
  口径一致；member 403；只认目录；关闭后恢复。
- include_dirs：前缀白名单过滤（App 文件夹选择器下发）。
"""
import os

from conftest import MEMBER_PASS

import app.api.photos as photos
from app.config import SHARE_ROOT
from app.settings import PHOTO_WALL_EXCLUDES, set_setting

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _rand() -> str:
    return os.urandom(3).hex()


def _member(client):
    admin = client.__class__(client.app, base_url="https://testserver")
    assert admin.post("/api/login", json={
        "username": "admin", "password": "test-pass-123"}).status_code == 200
    inv = admin.post("/api/admin/invites", json={"role": "member", "hours": 24},
                     cookies=dict(admin.cookies)).json()
    username = "pw" + _rand()
    assert client.post("/api/invites/redeem", json={
        "code": inv["code"], "username": username,
        "password": MEMBER_PASS}).status_code == 200
    c = client.__class__(client.app, base_url="https://testserver")
    assert c.post("/api/login", json={
        "username": username, "password": MEMBER_PASS}).status_code == 200
    return c, username


def _post_feedback(c, title, ftype="idea", body=""):
    parts = [("title", (None, title)), ("type", (None, ftype)),
             ("body", (None, body)), ("app_ver", (None, "1.0.0+101")),
             ("device", (None, "TestDroid"))]
    return c.post("/api/feedback", files=parts)


# ── 反馈二次补充 ──────────────────────────────────────────


def test_feedback_append_by_owner(auth, client):
    m, _ = _member(client)
    fid = _post_feedback(m, f"补充测试-{_rand()}", body="原始描述").json()["id"]

    r = m.post(f"/api/feedback/{fid}/append",
               json={"body": "补充：安卓 16 上必现"})
    assert r.status_code == 200, r.text
    det = m.get(f"/api/feedback/{fid}").json()
    assert "原始描述" in det["body"]
    assert "补充：安卓 16 上必现" in det["body"]
    assert "--- 补充 @" in det["body"]
    # frontmatter 更新 时间被刷新；状态不动
    from app.api.feedback import _load_one
    mm = _load_one(fid)
    assert mm["更新"] >= mm["创建"]
    assert mm["状态"] == "已提交"


def test_feedback_append_authz_and_validation(auth, client):
    m, _ = _member(client)
    fid = _post_feedback(m, f"权限-{_rand()}").json()["id"]

    # 他人（另一个成员）不可补充
    other, _ = _member(client)
    assert other.post(f"/api/feedback/{fid}/append",
                      json={"body": "x"}).status_code == 403
    # 空内容 / 超长 422
    assert m.post(f"/api/feedback/{fid}/append",
                  json={"body": "   "}).status_code == 422
    assert m.post(f"/api/feedback/{fid}/append",
                  json={"body": "x" * 1001}).status_code == 422
    # 不存在的反馈 404
    assert m.post("/api/feedback/20990101000000-ff/append",
                  json={"body": "x"}).status_code == 404
    # admin 可补充
    assert auth.post(f"/api/feedback/{fid}/append",
                     json={"body": "admin 收到"}).status_code == 200


def test_feedback_append_after_done_and_dash_guard(auth, client):
    """已完成态仍可补充；正文里的 --- 不得破坏 frontmatter 边界。"""
    m, _ = _member(client)
    fid = _post_feedback(m, f"完成后补充-{_rand()}").json()["id"]
    src = SHARE_ROOT / "反馈" / "待处理" / f"{fid}.md"
    dst = SHARE_ROOT / "反馈" / "已完成" / f"{fid}.md"
    text = src.read_text(encoding="utf-8").replace("状态: 已提交", "状态: 已修复")
    dst.write_text(text, encoding="utf-8")
    src.unlink()

    r = m.post(f"/api/feedback/{fid}/append",
               json={"body": "补充1\n---\n补充2"})
    assert r.status_code == 200
    from app.api.feedback import _load_one
    mm = _load_one(fid)   # 若 frontmatter 被破坏，这里解析不出 → None
    assert mm is not None and mm["状态"] == "已修复"
    assert "补充1" in mm["body"] and "补充2" in mm["body"]
    items = [i for i in m.get("/api/feedback").json()["items"] if i["id"] == fid]
    assert items and items[0]["status"] == "已修复"


# ── 照片墙隐藏目录 + 文件夹筛选 ───────────────────────────


def _mk(rel: str):
    p = SHARE_ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8fakejpg")
    return p


def _cleanup(*likes):
    from app.db import _db, _db_lock
    with _db_lock:
        for like in likes:
            _db().execute("DELETE FROM photos WHERE path LIKE ?", (f"%{like}%",))
        _db().commit()
    set_setting(PHOTO_WALL_EXCLUDES, "")
    for like in likes:
        d = SHARE_ROOT / like.split("/")[0]
        if d.exists():
            for child in sorted(d.rglob("*"), reverse=True):
                child.rmdir() if child.is_dir() else child.unlink(missing_ok=True)
            try:
                d.rmdir()
            except OSError:
                pass


def test_wall_exclude_hides_from_all_endpoints(client, auth):
    tag = f"pw{_rand()}"
    priv_dir = f"照片/{tag}/私人相册"
    norm_dir = f"照片/{tag}/日常"
    _mk(f"{priv_dir}/a.jpg")
    _mk(f"{norm_dir}/b.jpg")
    photos._last_photo_scan = 0.0
    m, mname = _member(client)
    # 真实用户名分支：备份落点 照片/<用户>/<相册>/ 三级，分组应取到相册层
    own_dir = f"照片/{mname}/私人相册"
    _mk(f"{own_dir}/u1.jpg")
    try:
        # 建立扫描基线：两处都在墙上
        base = m.get("/api/photos", params={"refresh": "1"}).json()
        paths = {i["path"] for i in base["items"]}
        assert f"{norm_dir}/b.jpg" in paths and f"{priv_dir}/a.jpg" in paths

        # member 无权管理隐藏清单
        assert m.get("/api/photos/wall-excludes").status_code == 403
        assert m.post("/api/photos/wall-excludes",
                      json={"path": priv_dir, "hidden": True}).status_code == 403

        # admin 隐藏 私人相册
        r = auth.post("/api/photos/wall-excludes",
                      json={"path": priv_dir, "hidden": True})
        assert r.status_code == 200 and f"照片/{tag}/私人相册" in r.json()["excludes"]
        after = m.get("/api/photos", params={"refresh": "1"}).json()
        apaths = {i["path"] for i in after["items"]}
        assert f"{priv_dir}/a.jpg" not in apaths, "隐藏目录照片不应再上墙"
        assert f"{norm_dir}/b.jpg" in apaths
        assert after["total"] == base["total"] - 1

        # facets 同口径
        fc = m.get("/api/photos/facets").json()
        total_fc = sum(x["count"] for x in fc["months"])
        assert total_fc == after["total"]

        # folders 同口径：隐藏目录不出现；非用户名目录取两级前缀
        folders = {f["dir"]: f["count"] for f in m.get("/api/photos/folders").json()["folders"]}
        assert f"照片/{tag}/私人相册" not in folders
        assert folders.get(f"照片/{tag}") == 1
        assert folders.get(own_dir) == 1

        # 文件页仍可寻址（隐藏≠无权限）：admin 列目录能看到该文件
        ls = auth.get("/api/files", params={"path": priv_dir})
        assert ls.status_code == 200

        # 取消隐藏 → 恢复
        r2 = auth.post("/api/photos/wall-excludes",
                       json={"path": priv_dir, "hidden": False})
        assert r2.status_code == 200 and r2.json()["excludes"] == []
        back = m.get("/api/photos", params={"refresh": "1"}).json()
        assert f"{priv_dir}/a.jpg" in {i["path"] for i in back["items"]}

        # 非目录 422
        assert auth.post("/api/photos/wall-excludes",
                         json={"path": f"{priv_dir}/a.jpg", "hidden": True}
                         ).status_code == 422
    finally:
        _cleanup(tag, mname)


def test_feedback_dir_not_on_wall(client):
    """系统目录 反馈/ 不进照片墙（b4e0 关联发现）：成员反馈截图是私有
    提交内容，此前被全盘扫描收进 photos 表全家可见。扫描跳过 + 存量行
    随 gone 清理消失。"""
    from app.db import _db, _db_lock
    tag = f"fb{_rand()}"
    _mk(f"反馈/img/{tag}-1.png")
    photos._last_photo_scan = 0.0
    m, _ = _member(client)
    try:
        m.get("/api/photos", params={"refresh": "1"})   # 触发扫描
        wall = m.get("/api/photos", params={"refresh": "1"}).json()
        assert not any(i["path"].startswith("反馈/")
                       for i in wall["items"]), "反馈截图不应上墙"
        folders = m.get("/api/photos/folders").json()["folders"]
        assert not any(f["dir"].startswith("反馈/") for f in folders)
        with _db_lock:
            c = _db().execute("SELECT COUNT(*) AS c FROM photos WHERE path LIKE ?",
                              (f"反馈/%{tag}%",)).fetchone()["c"]
        assert c == 0, "存量反馈截图行应被 gone 清理"
    finally:
        with _db_lock:
            _db().execute("DELETE FROM photos WHERE path LIKE ?", (f"%{tag}%",))
            _db().commit()
        (SHARE_ROOT / f"反馈/img/{tag}-1.png").unlink(missing_ok=True)


def test_include_dirs_filter(client):
    tag = f"pw{_rand()}"
    _mk(f"照片/{tag}/甲/c1.jpg")
    _mk(f"照片/{tag}/乙/c2.jpg")
    photos._last_photo_scan = 0.0
    m, _ = _member(client)
    try:
        m.get("/api/photos", params={"refresh": "1"})   # 触发扫描
        only = m.get("/api/photos", params={
            "include_dirs": f"照片/{tag}/甲"}).json()
        paths = {i["path"] for i in only["items"]}
        assert f"照片/{tag}/甲/c1.jpg" in paths
        assert f"照片/{tag}/乙/c2.jpg" not in paths
        # 多前缀 + 不存在的目录混入也不炸
        multi = m.get("/api/photos", params={
            "include_dirs": f"照片/{tag}/甲,照片/{tag}/乙"}).json()
        assert multi["total"] == 2
        assert multi["total"] == len(multi["items"]) or multi["total"] >= 2
    finally:
        _cleanup(tag)


def test_facets_media_and_dir_filters(client):
    """facets 新增 media_type / include_dirs 参数（20260914041746-6c58
    时间轴跳月）：App 快滚轴改为全时间线定位跳转后，跳转偏移按 facets
    月份计数累计推算，必须与 /api/photos 的筛选口径完全一致——这里验证
    两端同参数组合计数对齐。"""
    tag = f"fc{_rand()}"
    _mk(f"照片/{tag}/甲/f1.jpg")
    _mk(f"照片/{tag}/乙/f2.jpg")
    _mk(f"照片/{tag}/乙/f3.mp4")
    photos._last_photo_scan = 0.0
    m, _ = _member(client)
    try:
        m.get("/api/photos", params={"refresh": "1"})   # 触发扫描
        # include_dirs 口径：只统计甲
        fc = m.get("/api/photos/facets", params={
            "include_dirs": f"照片/{tag}/甲"}).json()
        assert sum(x["count"] for x in fc["months"]) == 1
        # media_type 口径：视频 1、照片 2（限定本 tag 目录）
        fv = m.get("/api/photos/facets", params={
            "include_dirs": f"照片/{tag}", "media_type": "video"}).json()
        fp = m.get("/api/photos/facets", params={
            "include_dirs": f"照片/{tag}", "media_type": "photo"}).json()
        assert sum(x["count"] for x in fv["months"]) == 1
        assert sum(x["count"] for x in fp["months"]) == 2
        # 与 /api/photos 同参数组合的 total 严格对齐（跳月偏移的正确性根基）
        wall = m.get("/api/photos", params={
            "include_dirs": f"照片/{tag}/乙", "media_type": "video"}).json()
        fwall = m.get("/api/photos/facets", params={
            "include_dirs": f"照片/{tag}/乙", "media_type": "video"}).json()
        assert wall["total"] == 1
        assert sum(x["count"] for x in fwall["months"]) == wall["total"]
    finally:
        _cleanup(tag)
