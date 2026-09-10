"""精选专辑（07 审查批次 3B：system.py 管理侧与 social.py 成员侧合流）。

专辑存储层（settings JSON + _mutate_albums 锁内读-改-写）此前住在
system.py，成员侧增删住在 social.py，两边函数体内互相反向 import——
"改 A 必须改 B"的教科书案例（07 §3.2）；现在一个域一个家。

模型 {id, title, created_by, created_at, items:[photos.path...]}，
兼容旧月份书签型（month 有值、items 空）。guest 只读；编辑/删除 =
admin 或创建者本人；条目入库时校验可见性（防探测）。
"""
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.db import _db, _db_lock
from app.roles import ADMIN, GUEST
from app.security import audit, client_ip, preview_user, require_admin, require_user
from app.spaces import visible
from app.settings import FEATURED_ALBUMS

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


# ─── 精选专辑（月份集合，管理员维护，全员可见）──────────────

def load_albums() -> list[dict]:
    row = _db().execute("SELECT value FROM settings WHERE key=?", (FEATURED_ALBUMS,)).fetchone()
    if not row or not row["value"]:
        return []
    import json
    try:
        data = json.loads(row["value"])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_albums(albums: list[dict]):
    import json
    with _db_lock:
        _db().execute(
            "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (FEATURED_ALBUMS, json.dumps(albums, ensure_ascii=False)))
        _db().commit()


def rename_album_owner(old: str, new: str):
    """用户名变更后同步精选专辑归属（R2-P2-3）：否则改名者丢失自己专辑的
    编辑/删除权（created_by 比对落空）。走 _mutate_albums 的锁内读-改-写，
    消灭与并发编辑专辑的丢更新竞态（R2-P2-5）。"""
    changed = []

    def _do(albums: list[dict]) -> bool:
        for a in albums:
            if a.get("created_by") == old:
                a["created_by"] = new
                changed.append(True)
        return bool(changed)

    _mutate_albums(_do)


def _mutate_albums(mutator) -> bool:
    """精选专辑整表"读-改-写"原子化（v0.14 审查 P2）。

    load_albums()→改→_save_albums() 三步之间不持锁时，两个设备并发
    勾选/删除会整份互相覆盖丢更新；专辑条目现在是用户策展数据，必须
    全程持 _db_lock。_save_albums 自己也拿这把锁（Lock 不可重入），
    所以这里绕开它直接写库。mutator(albums) 就地修改，返回 False 表示
    未命中（不落库）。"""
    import json
    with _db_lock:
        row = _db().execute(
            "SELECT value FROM settings WHERE key=?", (FEATURED_ALBUMS,)).fetchone()
        try:
            albums = json.loads(row["value"]) if row and row["value"] else []
            if not isinstance(albums, list):
                albums = []
        except Exception:
            albums = []
        if not mutator(albums):
            return False
        _db().execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (FEATURED_ALBUMS, json.dumps(albums, ensure_ascii=False)))
        _db().commit()
        return True


def _visible_album_items(user, items: list) -> list[str]:
    """按请求者可见性过滤专辑条目：收进专辑后权限变化（转入 private/
    回收站）的照片不应向无权者泄露路径名（v0.14 审查 P1，防探测边界
    与照片互动一致）。"""
    return [p for p in items if isinstance(p, str) and visible(user, p.split("/"))]


@router.get("/api/albums")
def list_albums(request: Request, _user=Depends(require_user)):
    # v0.14：items 不随列表下发（50 专辑 × 500 路径会把时间线首页拖肥），
    # 列表只给 count/cover，全量条目走 GET /api/albums/{id}。
    # cover/count 都按请求者可见性过滤（P1/P2：不可见路径名不下发）
    user = preview_user(request, _user)
    out = []
    for a in load_albums():
        items = _visible_album_items(user, a.get("items") or [])
        d = {k: v for k, v in a.items() if k != "items"}
        d["count"] = len(items)
        if items:
            d["cover"] = items[0]
        out.append(d)
    return out


class AdminAlbumBody(BaseModel):
    title: str
    month: str


@router.post("/api/admin/albums")
def admin_create_album(body: AdminAlbumBody, _admin=Depends(require_admin)):
    title = body.title.strip()
    if not title or len(title) > 40:
        raise HTTPException(status_code=400, detail="专辑标题需为 1~40 字符")
    if not re.fullmatch(r"\d{4}-\d{2}", body.month):
        raise HTTPException(status_code=400, detail="month 需为 YYYY-MM 格式")
    err: list[tuple[int, str]] = []

    def _do(albums: list[dict]) -> bool:
        # 重复/上限检查必须锁内做（06 审查 P1-1）：裸 _load+_save 两步之间
        # 不持锁，双管理员并发创建互相覆盖丢更新；上限显式拒绝而非静默截断
        # （与 social.py 成员侧同款语义）
        if any(a.get("month") == body.month for a in albums):
            err.append((409, "该月份已是精选专辑"))
            return False
        if len(albums) >= 50:
            err.append((400, "专辑数量已达上限（50），请先清理不用的"))
            return False
        albums.insert(0, {"id": f"al-{time.time_ns()}", "title": title, "month": body.month})
        return True

    if not _mutate_albums(_do):
        code, detail = err[0] if err else (500, "创建失败")
        raise HTTPException(status_code=code, detail=detail)
    audit("album_create", f"{title} ({body.month})")
    return {"ok": True}


@router.delete("/api/admin/albums/{album_id}")
def admin_delete_album(album_id: str, _admin=Depends(require_admin)):
    err: list[tuple[int, str]] = []

    def _do(albums: list[dict]) -> bool:
        if not any(a.get("id") == album_id for a in albums):
            err.append((404, "专辑不存在"))
            return False
        albums[:] = [a for a in albums if a.get("id") != album_id]
        return True

    if not _mutate_albums(_do):
        code, detail = err[0] if err else (500, "删除失败")
        raise HTTPException(status_code=code, detail=detail)
    audit("album_delete", album_id)
    return {"ok": True}


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




@router.post("/api/albums")
def create_album(body: AlbumBody, request: Request, _user=Depends(require_user)):
    if _user["role"] == GUEST:
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
    hit = _album_hit(load_albums(), album_id)
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
    if _user["role"] == GUEST:
        raise HTTPException(status_code=403, detail="访客不能编辑专辑")
    # 长度预检（R1-P3-3）：与 create_album 同款上限，避免可见性校验循环跑完才限长
    if len(body.add) + len(body.remove) > 2 * ALBUM_ITEMS_MAX:
        raise HTTPException(status_code=400,
                            detail=f"一次最多操作 {2 * ALBUM_ITEMS_MAX} 项")
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
        if _user["role"] != ADMIN and hit.get("created_by") != _user["username"]:
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
    err: list[tuple[int, str]] = []

    def _do(albums: list[dict]) -> bool:
        hit = _album_hit(albums, album_id)
        if not hit:
            err.append((404, "专辑不存在"))
            return False
        if _user["role"] != ADMIN and hit.get("created_by") != _user["username"]:
            err.append((403, "只能删除自己创建的专辑"))
            return False
        albums[:] = [a for a in albums if a.get("id") != album_id]
        return True

    if not _mutate_albums(_do):
        code, detail = err[0] if err else (500, "删除失败")
        raise HTTPException(status_code=code, detail=detail)
    audit("album_delete", album_id, ip=client_ip(request))
    return {"ok": True}
