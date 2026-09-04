"""照片互动：评论 + 表情回应（B2）与精选专辑共创（B1）。

- 评论/回应按 photo_path（photos.path，相对 SHARE_ROOT）挂在照片上，
  可见性复用 spaces.visible——能看见照片的人才留得下互动，回收站/隐藏路径天然隔离。
- 表情走固定白名单（五枚），一对一" toggled"语义：同一人对同一照片同一表情
  再点一次即取消（UNIQUE 约束兜底）。
- 被互动的照片按路径归属推导拍摄者（复用 system._photo_uploader），站内通知，
  自己对自己照片的互动不打扰。
- 精选专辑从"管理员专属"放开为"家庭共创"：任何在用成员可建（记录创建者），
  admin 或创建者本人可删；数据仍是 settings JSON，不迁表。
"""
import re
import time

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.config import SHARE_ROOT
from app.db import _db, _db_lock
from app.security import audit, client_ip, require_user
from app.spaces import visible
from app.main import app

EMOJIS = ("❤️", "👍", "😂", "😮", "🥺")


def _visible_photo(user, path: str) -> str:
    """校验 path 对 user 可见并归一为 photos.path 形态；不可见一律 404（不区分
    存在性防探测）。"""
    rel = (path or "").replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p]
    if not parts or not visible(user, parts):
        raise HTTPException(status_code=404, detail="照片不存在")
    return "/".join(parts)


# ─── 评论 ──────────────────────────────────────────────────

class CommentBody(BaseModel):
    path: str
    body: str


@app.get("/api/photo/comments")
def list_comments(request: Request, path: str = Query(..., min_length=1),
                  _user=Depends(require_user)):
    user = _user
    norm = _visible_photo(user, path)
    rows = _db().execute(
        "SELECT c.id, c.body, c.created_at, c.user_id, u.username "
        "FROM photo_comments c LEFT JOIN users u ON u.id = c.user_id "
        "WHERE c.photo_path=? ORDER BY c.id DESC LIMIT 200", (norm,)).fetchall()
    return {"comments": [{
        "id": r["id"], "user": r["username"] or "已注销", "body": r["body"],
        "created_at": r["created_at"],
        "mine": r["user_id"] == user["user_id"],
    } for r in rows]}


@app.post("/api/photo/comments")
def create_comment(body: CommentBody, request: Request, _user=Depends(require_user)):
    text = body.body.strip()
    if not text or len(text) > 500:
        raise HTTPException(status_code=400, detail="评论需为 1~500 字符")
    norm = _visible_photo(_user, body.path)
    with _db_lock:
        cur = _db().execute(
            "INSERT INTO photo_comments(photo_path, user_id, body, created_at) "
            "VALUES(?,?,?,?) RETURNING id",
            (norm, _user["user_id"], text, time.time()))
        cid = cur.fetchone()["id"]
        _db().commit()
    audit("photo_comment", norm[:120], ip=client_ip(request))
    _notify_owner(norm, _user["username"], f"💬 评论了你的照片：{text[:40]}")
    return {"ok": True, "id": cid}


@app.delete("/api/photo/comments/{cid}")
def delete_comment(cid: int, request: Request, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM photo_comments WHERE id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="评论不存在")
    if row["user_id"] != _user["user_id"] and _user["role"] != "admin":
        raise HTTPException(status_code=403, detail="只能删除自己的评论")
    with _db_lock:
        _db().execute("DELETE FROM photo_comments WHERE id=?", (cid,))
        _db().commit()
    audit("photo_comment_del", row["photo_path"][:120], ip=client_ip(request))
    return {"ok": True}


# ─── 表情回应 ──────────────────────────────────────────────

class ReactionBody(BaseModel):
    path: str
    emoji: str


@app.get("/api/photo/reactions")
def list_reactions(request: Request, path: str = Query(..., min_length=1),
                   _user=Depends(require_user)):
    norm = _visible_photo(_user, path)
    rows = _db().execute(
        "SELECT r.emoji, r.user_id, u.username FROM photo_reactions r "
        "LEFT JOIN users u ON u.id = r.user_id WHERE r.photo_path=?", (norm,)).fetchall()
    counts: dict[str, int] = {}
    names: dict[str, list[str]] = {}
    mine: list[str] = []
    for r in rows:
        counts[r["emoji"]] = counts.get(r["emoji"], 0) + 1
        names.setdefault(r["emoji"], []).append(r["username"] or "已注销")
        if r["user_id"] == _user["user_id"]:
            mine.append(r["emoji"])
    return {"emojis": list(EMOJIS),
            "reactions": [{"emoji": e, "count": counts.get(e, 0),
                           "names": names.get(e, [])} for e in EMOJIS],
            "mine": mine}


@app.post("/api/photo/reactions")
def toggle_reaction(body: ReactionBody, request: Request, _user=Depends(require_user)):
    if body.emoji not in EMOJIS:
        raise HTTPException(status_code=400, detail="不支持的表情")
    norm = _visible_photo(_user, body.path)
    with _db_lock:
        hit = _db().execute(
            "SELECT id FROM photo_reactions WHERE photo_path=? AND user_id=? AND emoji=?",
            (norm, _user["user_id"], body.emoji)).fetchone()
        if hit:
            _db().execute("DELETE FROM photo_reactions WHERE id=?", (hit["id"],))
            on = False
        else:
            try:
                _db().execute(
                    "INSERT INTO photo_reactions(photo_path, user_id, emoji, created_at) "
                    "VALUES(?,?,?,?)", (norm, _user["user_id"], body.emoji, time.time()))
                on = True
            except Exception:
                # 并发双击竞态：UNIQUE 已挡住，视为已点（幂等）
                on = True
        _db().commit()
    audit("photo_react", f"{'on' if on else 'off'} {body.emoji} {norm[:100]}",
          ip=client_ip(request))
    if on:
        _notify_owner(norm, _user["username"], f"{body.emoji} 回应了你的照片")
    return {"ok": True, "on": on}


# ─── 拍摄者站内通知 ────────────────────────────────────────

def _notify_owner(photo_path: str, actor: str, text: str) -> None:
    """照片归属人收站内通知；归属不明（共享空间）或本人操作则跳过。"""
    import app.api.system as _sys
    users = _sys._known_users()
    owner = _sys._photo_uploader(photo_path, users)
    if owner in (None, "共享空间", actor):
        return
    row = _db().execute(
        "SELECT id FROM users WHERE username=? AND is_active=1", (owner,)).fetchone()
    if not row:
        return
    with _db_lock:
        _db().execute(
            "INSERT INTO notifications(user_id, type, title, body, link, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (row["id"], "photo", f"{actor} 与照片互动", text,
             f"/photos?path={photo_path}", time.time()))
        _db().commit()


# ─── 精选专辑共创（B1）：成员可建，admin/创建者可删 ─────────

class AlbumBody(BaseModel):
    title: str
    month: str


@app.post("/api/albums")
def create_album(body: AlbumBody, request: Request, _user=Depends(require_user)):
    title = body.title.strip()
    if not title or len(title) > 40:
        raise HTTPException(status_code=400, detail="专辑标题需为 1~40 字符")
    if not re.fullmatch(r"\d{4}-\d{2}", body.month):
        raise HTTPException(status_code=400, detail="month 需为 YYYY-MM 格式")
    mm = int(body.month.split("-")[1])
    if not 1 <= mm <= 12:
        raise HTTPException(status_code=400, detail="month 需为有效月份（01-12）")
    from app.api.system import _load_albums, _save_albums
    albums = _load_albums()
    if any(a.get("month") == body.month for a in albums):
        raise HTTPException(status_code=409, detail="该月份已有专辑")
    albums.insert(0, {"id": f"al-{time.time_ns()}", "title": title,
                      "month": body.month, "created_by": _user["username"]})
    _save_albums(albums[:50])
    audit("album_create", f"{title} ({body.month})", ip=client_ip(request))
    return {"ok": True, "albums": albums}


@app.delete("/api/albums/{album_id}")
def delete_album(album_id: str, request: Request, _user=Depends(require_user)):
    from app.api.system import _load_albums, _save_albums
    albums = _load_albums()
    hit = next((a for a in albums if a.get("id") == album_id), None)
    if not hit:
        raise HTTPException(status_code=404, detail="专辑不存在")
    if _user["role"] != "admin" and hit.get("created_by") != _user["username"]:
        raise HTTPException(status_code=403, detail="只能删除自己创建的专辑")
    _save_albums([a for a in albums if a.get("id") != album_id])
    audit("album_delete", album_id, ip=client_ip(request))
    return {"ok": True}
