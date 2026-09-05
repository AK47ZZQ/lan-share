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
from app.db import _db, _db_lock, is_unique_violation
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
            except Exception as e:
                # 并发双击竞态：UNIQUE 已挡住，视为已点（幂等）。
                # 只吞唯一约束冲突——其它 DB 故障照常上抛，静默丢写入。
                if is_unique_violation(e):
                    on = True
                else:
                    raise
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


# ─── 精选专辑（v0.14 重构）：文件夹式自由勾选 ────────────────
# 模型 {id, title, created_by, created_at, items:[photos.path...]}：
#   建专辑后自己勾选照片/视频进来，也支持"按月全选"批量加入。
# 兼容旧月份书签型专辑（无 items 或 items 为空但有 month）：列表/详情
# 照常返回 month 字段，客户端按旧语义"按月过滤"呈现。
# 权限：guest 只读；编辑条目/删除 = admin 或创建者本人；列表全员可见，
# 条目可见性入队时校验（_visible_photo），查看走 /api/photos?album= 的
# visible 过滤，收进专辑后权限变化的照片自然从视图里消失。

ALBUM_ITEMS_MAX = 500


def _norm_photo_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip("/")


def _album_hit(albums: list[dict], album_id: str) -> dict | None:
    return next((a for a in albums if a.get("id") == album_id), None)


class AlbumBody(BaseModel):
    title: str
    month: str | None = None   # 兼容旧客户端：仍可带 month（月份书签型）
    items: list[str] = []


@app.post("/api/albums")
def create_album(body: AlbumBody, request: Request, _user=Depends(require_user)):
    if _user["role"] == "guest":
        raise HTTPException(status_code=403, detail="访客不能创建专辑")
    title = body.title.strip()
    if not title or len(title) > 40:
        raise HTTPException(status_code=400, detail="专辑标题需为 1~40 字符")
    if body.month:
        if not re.fullmatch(r"\d{4}-\d{2}", body.month):
            raise HTTPException(status_code=400, detail="month 需为 YYYY-MM 格式")
        if not 1 <= int(body.month.split("-")[1]) <= 12:
            raise HTTPException(status_code=400, detail="month 需为有效月份（01-12）")
    if len(body.items) > ALBUM_ITEMS_MAX:
        raise HTTPException(status_code=400,
                            detail=f"一次最多加入 {ALBUM_ITEMS_MAX} 项")
    from app.api.system import _mutate_albums, _load_albums
    # 条目逐个校验可见性（不可见一律 404，防探测）；保序去重
    items: list[str] = []
    for p in body.items:
        norm = _visible_photo(_user, p)
        if norm not in items:
            items.append(norm)
    if len(_load_albums()) >= 50:
        # v0.14 审查 P2：专辑承载策展条目，静默截断=无提示销毁最旧一本，
        # 改为显式拒绝（旧 [:50] 截断只删月份书签尚可容忍）
        raise HTTPException(status_code=400, detail="专辑数量已达上限（50），请先清理不用的")
    album = {"id": f"al-{time.time_ns()}", "title": title,
             "created_by": _user["username"], "created_at": time.time(),
             "items": items}
    if body.month:
        album["month"] = body.month
    if not _mutate_albums(lambda albums: (albums.insert(0, album) or True)):
        raise HTTPException(status_code=500, detail="创建失败")
    audit("album_create", f"{title} ({len(items)} 项)", ip=client_ip(request))
    return {"ok": True, "id": album["id"]}


@app.get("/api/albums/{album_id}")
def get_album(album_id: str, _user=Depends(require_user)):
    from app.api.system import _load_albums, _visible_album_items
    hit = _album_hit(_load_albums(), album_id)
    if not hit:
        raise HTTPException(status_code=404, detail="专辑不存在")
    # 条目按请求者可见性过滤后下发（P1：收进专辑后权限变化的照片，
    # 路径名不能再泄露给无权者；展示数据源 /api/photos?album= 同口径）
    d = dict(hit)
    d["items"] = _visible_album_items(_user, hit.get("items") or [])
    return d


class AlbumItemsBody(BaseModel):
    add: list[str] = []
    remove: list[str] = []


@app.put("/api/albums/{album_id}/items")
def update_album_items(album_id: str, body: AlbumItemsBody, request: Request,
                       _user=Depends(require_user)):
    if _user["role"] == "guest":
        raise HTTPException(status_code=403, detail="访客不能编辑专辑")
    from app.api.system import _mutate_albums
    # add 条目先校验可见性（不可见一律 404 防探测）；保序去重
    add_normed: list[str] = []
    for p in body.add:
        norm = _visible_photo(_user, p)
        if norm not in add_normed:
            add_normed.append(norm)
    remove_set = {_norm_photo_path(p) for p in body.remove}

    err: list[tuple[int, str]] = []
    result: list[str] = []

    def _do(albums: list[dict]) -> bool:
        hit = _album_hit(albums, album_id)
        if not hit:
            err.append((404, "专辑不存在"))
            return False
        if _user["role"] != "admin" and hit.get("created_by") != _user["username"]:
            err.append((403, "只能编辑自己创建的专辑"))
            return False
        items = [i for i in (hit.get("items") or [])]
        if remove_set:
            # 移除不做可见性校验：把已收进来的路径摘出去只是编辑操作
            items = [i for i in items if i not in remove_set]
        for p in add_normed:
            if p not in items:
                items.append(p)
        if len(items) > ALBUM_ITEMS_MAX:
            err.append((400, f"专辑最多 {ALBUM_ITEMS_MAX} 项"))
            return False
        hit["items"] = items
        result.extend(items)
        return True

    if not _mutate_albums(_do):
        code, detail = err[0] if err else (500, "更新失败")
        raise HTTPException(status_code=code, detail=detail)
    audit("album_items",
          f"album:{album_id} +{len(add_normed)}-{len(remove_set)}"
          f"={len(result)}", ip=client_ip(request))
    return {"ok": True, "items": result}


@app.delete("/api/albums/{album_id}")
def delete_album(album_id: str, request: Request, _user=Depends(require_user)):
    from app.api.system import _mutate_albums
    err: list[tuple[int, str]] = []

    def _do(albums: list[dict]) -> bool:
        hit = _album_hit(albums, album_id)
        if not hit:
            err.append((404, "专辑不存在"))
            return False
        if _user["role"] != "admin" and hit.get("created_by") != _user["username"]:
            err.append((403, "只能删除自己创建的专辑"))
            return False
        albums[:] = [a for a in albums if a.get("id") != album_id]
        return True

    if not _mutate_albums(_do):
        code, detail = err[0] if err else (500, "删除失败")
        raise HTTPException(status_code=code, detail=detail)
    audit("album_delete", album_id, ip=client_ip(request))
    return {"ok": True}
