"""家庭共创批次测试（B1 相册共创 / B3 数据导出；B2 照片互动已下线）。"""
import io
import json
import os
import secrets
import zipfile

import pytest


@pytest.fixture()
def member_name():
    """带随机后缀的用户名：整个会话共用一个临时数据根，固定名会跨测试撞车。"""
    return "social-" + secrets.token_hex(3)


@pytest.fixture()
def member(auth, member_name):
    """建一个测试成员并返回登录态客户端。"""
    r = auth.post("/api/admin/invites", json={"role": "member", "hours": 1})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    c = auth.__class__(auth.app if hasattr(auth, "app") else __import__("server").app,
                       base_url="https://testserver")
    r = c.post("/api/invites/redeem", json={"code": code, "username": member_name,
                                            "password": "social-pass-123"})
    assert r.status_code == 200, r.text
    c.post("/api/login", json={"username": member_name, "password": "social-pass-123"})
    return c


@pytest.fixture()
def photo_path(member_name):
    """造一张成员的照片（incoming 子树，走可读路径）。"""
    p = os.path.join(os.environ["SHARE_ROOT"], "incoming", member_name)
    os.makedirs(p, exist_ok=True)
    name = "pic-abc.jpg"
    with open(os.path.join(p, name), "wb") as f:
        f.write(b"\xff\xd8fakejpeg")
    return f"incoming/{member_name}/{name}"


# ─── B2 照片互动（评论/表情）─────
# 已随功能下线移除（9.6-P2-13，2026-09-09）：端点删除，测试同步下线；
# 相册共创/数据导出测试不受影响。

# ─── B1 相册共创 ──────────────────────────────────────────

def test_album_creator_recorded(member, member_name):
    member.post("/api/albums", json={"title": "记录创建者", "month": "2026-05"})
    hit = next(a for a in member.get("/api/albums").json() if a["title"] == "记录创建者")
    assert hit["created_by"] == member_name


def test_album_member_can_create_and_delete_own(member, auth):
    r = member.post("/api/albums", json={"title": "外婆生日", "month": "2026-08"})
    assert r.status_code == 200, r.text

    # v0.14 起专辑不再按月绑定：同月可建多本（旧的 409 查重已随
    # "月份书签"语义一并废弃）
    r = auth.post("/api/albums", json={"title": "admin 抢注", "month": "2026-08"})
    assert r.status_code == 200

    # 创建者本人可删
    r = member.post("/api/albums", json={"title": "春游", "month": "2026-03"})
    assert r.status_code == 200
    aid = next(a for a in member.get("/api/albums").json() if a["title"] == "春游")["id"]
    r = member.delete(f"/api/albums/{aid}")
    assert r.status_code == 200


def test_album_member_cannot_delete_others(member, auth):
    auth.post("/api/admin/albums", json={"title": "admin 的专辑", "month": "2025-12"})
    hit = next(a for a in member.get("/api/albums").json() if a["month"] == "2025-12")
    r = member.delete(f"/api/albums/{hit['id']}")
    assert r.status_code == 403
    r = member.post("/api/albums", json={"title": "x", "month": "2025-13"})
    assert r.status_code == 400                          # 非法月份


# ─── v0.14 专辑条目（文件夹式勾选）────────────────────────

def test_album_items_crud_and_photo_filter(member, photo_path):
    r = member.post("/api/albums", json={"title": "暑假精选"})
    assert r.status_code == 200 and r.json()["id"], r.text
    aid = r.json()["id"]

    # 列表瘦身：count/cover，不携带全量 items
    lst = next(a for a in member.get("/api/albums").json() if a["id"] == aid)
    assert lst["count"] == 0 and "cover" not in lst and "items" not in lst

    # 勾选加入自己的照片 → 详情可见 → /api/photos?album= 过滤生效
    r = member.put(f"/api/albums/{aid}/items", json={"add": [photo_path]})
    assert r.status_code == 200 and r.json()["items"] == [photo_path], r.text
    detail = member.get(f"/api/albums/{aid}").json()
    assert detail["items"] == [photo_path]
    r = member.get("/api/photos", params={"album": aid})
    assert r.status_code == 200, r.text
    assert photo_path in [i["path"] for i in r.json()["items"]]

    # 重复 add 幂等（保序去重）
    r = member.put(f"/api/albums/{aid}/items", json={"add": [photo_path]})
    assert r.json()["items"] == [photo_path]
    # 列表此时有 cover
    lst = next(a for a in member.get("/api/albums").json() if a["id"] == aid)
    assert lst["count"] == 1 and lst["cover"] == photo_path

    # 移出
    r = member.put(f"/api/albums/{aid}/items", json={"remove": [photo_path]})
    assert r.json()["items"] == []

    r = member.delete(f"/api/albums/{aid}")
    assert r.status_code == 200
    assert member.get(f"/api/albums/{aid}").status_code == 404
    r = member.get("/api/photos", params={"album": aid})
    assert r.status_code == 404                          # 专辑删了过滤即 404


def test_album_items_acl_and_validation(member, auth, photo_path):
    # admin 建的专辑，成员不能改条目（只能 admin 或创建者）
    r = auth.post("/api/albums", json={"title": "admin 挑的"})
    assert r.status_code == 200
    aid = r.json()["id"]
    r = member.put(f"/api/albums/{aid}/items", json={"add": [photo_path]})
    assert r.status_code == 403

    # 别人空间的照片收不进来（不可见 404 防探测）
    r = member.post("/api/albums", json={"title": "我的精选"})
    myaid = r.json()["id"]
    r = member.put(f"/api/albums/{myaid}/items",
                   json={"add": ["private/admin/secret.jpg"]})
    assert r.status_code == 404

    # 不存在的专辑 404
    r = member.put("/api/albums/al-nope/items", json={"add": [photo_path]})
    assert r.status_code == 404

    # 标题校验
    assert member.post("/api/albums", json={"title": ""}).status_code == 400
    assert member.post("/api/albums", json={"title": "x" * 41}).status_code == 400
    assert member.post("/api/albums", json={"title": "t", "month": "2025-1"}).status_code == 400


def test_album_month_bookmark_still_filters(member, photo_path):
    """旧月份书签型专辑（有 month 无 items）在 /api/photos?album= 下不误伤。"""
    r = member.post("/api/albums", json={"title": "月份书签", "month": "1999-01"})
    aid = r.json()["id"]
    r = member.get("/api/photos", params={"album": aid})
    assert r.status_code == 200 and r.json()["items"] == []


# ─── B3 数据导出 ──────────────────────────────────────────

def test_export_member_data(auth, member, member_name, photo_path):
    r = auth.post("/api/admin/export", json={"username": member_name})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["files"] >= 1

    r = auth.get(j["url"])
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert f"incoming/{member_name}/pic-abc.jpg" in z.namelist()
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["member"]["username"] == member_name
    assert manifest["stats"]["files"] == j["files"]
    assert "共享空间" in manifest["scope_note"]

    # 二次下载：票据一次性
    r = auth.get(j["url"])
    assert r.status_code == 403


def test_export_acl_and_missing(auth, member):
    r = member.post("/api/admin/export", json={"username": "admin"})
    assert r.status_code in (401, 403)                   # 非管理员不可导出
    r = auth.post("/api/admin/export", json={"username": "ghost-user"})
    assert r.status_code == 404
