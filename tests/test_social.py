"""家庭共创批次（B1 相册共创 / B2 照片互动 / B3 数据导出）测试。"""
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


# ─── B2 照片互动 ──────────────────────────────────────────

def test_comment_roundtrip(member, photo_path):
    r = member.post("/api/photo/comments", json={"path": photo_path, "body": "拍得真好"})
    assert r.status_code == 200, r.text
    r = member.get("/api/photo/comments", params={"path": photo_path})
    items = r.json()["comments"]
    assert len(items) == 1 and items[0]["body"] == "拍得真好"
    assert items[0]["mine"] is True

    # 删除自己的评论
    r = member.delete(f"/api/photo/comments/{items[0]['id']}")
    assert r.status_code == 200
    assert member.get("/api/photo/comments", params={"path": photo_path}).json()["comments"] == []


def test_comment_validation_and_acl(member, photo_path):
    r = member.post("/api/photo/comments", json={"path": photo_path, "body": ""})
    assert r.status_code == 400
    r = member.post("/api/photo/comments", json={"path": photo_path, "body": "x" * 501})
    assert r.status_code == 400
    # 他人空间不可见 → 404（不暴露存在性）
    r = member.post("/api/photo/comments", json={"path": "private/admin/secret.jpg", "body": "hi"})
    assert r.status_code == 404
    # 不存在的路径同样 404
    r = member.get("/api/photo/comments", params={"path": "incoming/nowhere/none.jpg"})
    assert r.status_code == 404


def test_reaction_toggle(member, member_name, photo_path):
    r = member.post("/api/photo/reactions", json={"path": photo_path, "emoji": "❤️"})
    assert r.status_code == 200 and r.json()["on"] is True
    r = member.post("/api/photo/reactions", json={"path": photo_path, "emoji": "❤️"})
    assert r.json()["on"] is False                       # 再点一次=取消
    r = member.get("/api/photo/reactions", params={"path": photo_path})
    assert r.json()["reactions"][0]["count"] == 0 and r.json()["mine"] == []

    member.post("/api/photo/reactions", json={"path": photo_path, "emoji": "😂"})
    r = member.get("/api/photo/reactions", params={"path": photo_path})
    j = r.json()
    by = {x["emoji"]: x for x in j["reactions"]}
    assert by["😂"]["count"] == 1 and by["😂"]["names"] == [member_name]
    assert j["mine"] == ["😂"]


def test_reaction_whitelist(member, photo_path):
    r = member.post("/api/photo/reactions", json={"path": photo_path, "emoji": "🎉"})
    assert r.status_code == 400


def test_comment_acl_admin_delete(auth, member, photo_path):
    member.post("/api/photo/comments", json={"path": photo_path, "body": "待管理删除"})
    cid = member.get("/api/photo/comments", params={"path": photo_path}).json()["comments"][0]["id"]
    auth.post("/api/photo/comments", json={"path": photo_path, "body": "admin 也评论"})
    admin_cid = auth.get("/api/photo/comments", params={"path": photo_path}).json()["comments"][0]["id"]
    r = member.delete(f"/api/photo/comments/{admin_cid}")
    assert r.status_code == 403                          # 不能删别人的
    r = auth.delete(f"/api/photo/comments/{cid}")
    assert r.status_code == 200                          # admin 可删任何人的


# ─── B1 相册共创 ──────────────────────────────────────────

def test_album_creator_recorded(member, member_name):
    member.post("/api/albums", json={"title": "记录创建者", "month": "2026-05"})
    hit = next(a for a in member.get("/api/albums").json() if a["title"] == "记录创建者")
    assert hit["created_by"] == member_name


def test_album_member_can_create_and_delete_own(member, auth):
    r = member.post("/api/albums", json={"title": "外婆生日", "month": "2026-08"})
    assert r.status_code == 200, r.text

    # 同月重复 → 409
    r = auth.post("/api/albums", json={"title": "admin 抢注", "month": "2026-08"})
    assert r.status_code == 409

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
