"""精选专辑（B1）。

照片评论/表情回应功能已按改进建议 9.6-P2-13 下线（照片墙回归干净的
浏览体验；photo_comments / photo_reactions 两张表保留历史数据但不再
读写）。本模块现仅承载精选专辑：

- 精选专辑从"管理员专属"放开为"家庭共创"：任何在用成员可建（记录创建者），
  admin 或创建者本人可删；数据仍是 settings JSON，不迁表。
- 照片可见性校验 _visible_photo 复用 spaces.visible——能看见照片的人才
  收得进专辑。
"""
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.security import audit, client_ip, require_user
from app.spaces import visible


def _visible_photo(user, path: str) -> str:
    """校验 path 对 user 可见并归一为 photos.path 形态；不可见一律 404（不区分
    存在性防探测）。"""
    rel = (path or "").replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p]
    if not parts or not visible(user, parts):
        raise HTTPException(status_code=404, detail="照片不存在")
    return "/".join(parts)


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


# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装（07 审查批次 2 消循环）
router = APIRouter()


@router.post("/api/albums")
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
    from app.api.system import _mutate_albums
    # 条目逐个校验可见性（不可见一律 404，防探测）；保序去重
    items: list[str] = []
    for p in body.items:
        norm = _visible_photo(_user, p)
        if norm not in items:
            items.append(norm)
    album = {"id": f"al-{time.time_ns()}", "title": title,
             "created_by": _user["username"], "created_at": time.time(),
             "items": items}
    if body.month:
        album["month"] = body.month
    limit_err = []
    def _do(albums: list) -> bool:
        # 上限检查挪进 mutator（R2-P3-5）：锁外 check-then-act 并发可超限。
        # v0.14 审查 P2：专辑承载策展条目，静默截断=无提示销毁最旧一本，
        # 改为显式拒绝
        if len(albums) >= 50:
            limit_err.append(True)
            return False
        albums.insert(0, album)
        return True
    if not _mutate_albums(_do):
        if limit_err:
            raise HTTPException(status_code=400,
                                detail="专辑数量已达上限（50），请先清理不用的")
        raise HTTPException(status_code=500, detail="创建失败")
    audit("album_create", f"{title} ({len(items)} 项)", ip=client_ip(request))
    return {"ok": True, "id": album["id"]}


@router.get("/api/albums/{album_id}")
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


@router.put("/api/albums/{album_id}/items")
def update_album_items(album_id: str, body: AlbumItemsBody, request: Request,
                       _user=Depends(require_user)):
    if _user["role"] == "guest":
        raise HTTPException(status_code=403, detail="访客不能编辑专辑")
    # 长度预检（R1-P3-3）：与 create_album 同款上限，避免可见性校验循环跑完才限长
    if len(body.add) + len(body.remove) > 2 * ALBUM_ITEMS_MAX:
        raise HTTPException(status_code=400,
                            detail=f"一次最多操作 {2 * ALBUM_ITEMS_MAX} 项")
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


@router.delete("/api/albums/{album_id}")
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
