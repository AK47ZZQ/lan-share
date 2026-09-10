"""回收站（07 审查批次 3A：从 files.py 按域拆出）。

列表/恢复/单条物理删除/清空。orig_path 的 vpath 兼容解析与
逐条可见性判定（review 2026-09-02 P1-1）也在本模块。
"""
import re
import shutil
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.config import SHARE_ROOT, TRASH_DAYS
from app.db import _db, _db_lock
from app.roles import ADMIN
from app.security import audit, client_ip, require_admin, require_user
from app.filetools import human_size, to_rel
from app.spaces import resolve, visible

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


_VPATH_RE = re.compile(r"^(shared|private):(.*)$", re.I)


def _trash_rel_parts(orig_path: str) -> list[str] | None:
    """回收站 orig_path → 物理路径段（供 visible() 判归属）。历史入口存过
    vpath 形态（"private:<名>"，无用户段，归属不可判定）→ 返回 None，
    此类条目仅 admin 可见/可操作（review 2026-09-02 P1-1）。"""
    raw = (orig_path or "").replace("\\", "/")
    m = _VPATH_RE.match(raw)
    if m:
        if m.group(1).lower() == "private":
            return None
        raw = m.group(2)
    return [s for s in raw.split("/") if s not in ("", ".")]


@router.get("/api/trash")
async def list_trash(_user=Depends(require_user)):
    rows = _db().execute("SELECT * FROM trash ORDER BY deleted_at DESC").fetchall()
    is_admin = _user["role"] == ADMIN
    items = []
    for r in rows:
        parts = _trash_rel_parts(r["orig_path"])
        if parts is None:
            if not is_admin:
                continue
        elif not visible(_user, parts):
            continue
        items.append({
            "id": r["id"],
            "name": r["name"],
            "orig_path": r["orig_path"],
            "size": r["size"],
            "size_human": human_size(r["size"]) if r["size"] else "-",
            "deleted_at": datetime.fromtimestamp(r["deleted_at"]).isoformat(),
            "days_left": max(0, TRASH_DAYS - int((time.time() - r["deleted_at"]) / 86400)),
        })
    return items


@router.post("/api/trash/{trash_id}/restore")
async def restore_trash(request: Request, trash_id: int, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    # 与删除入口同款可见性校验（review 2026-09-02 P1-1）：缺了它，成员可枚举
    # trash id 把他人 private/incoming 的回收站条目"恢复"进自己空间
    parts = _trash_rel_parts(row["orig_path"])
    if parts is None:
        if _user["role"] != ADMIN:
            raise HTTPException(status_code=403, detail="Forbidden")
    elif not visible(_user, parts):
        raise HTTPException(status_code=403, detail="Forbidden")
    trash_path = SHARE_ROOT / row["trash_path"]
    if not trash_path.exists():
        with _db_lock:
            _db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
            _db().commit()
        raise HTTPException(status_code=404, detail="Trash file missing")
    dest = resolve(_user, row["orig_path"], "write")
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        dest = dest.with_name(f"{stem} (恢复){suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(trash_path), str(dest))
    with _db_lock:
        _db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
        _db().commit()
    audit("restore", to_rel(dest), ip=client_ip(request))
    return {"ok": True, "path": to_rel(dest)}


@router.delete("/api/trash/{trash_id}")
async def delete_trash_item(request: Request, trash_id: int, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    parts = _trash_rel_parts(row["orig_path"])
    if parts is None:
        if _user["role"] != ADMIN:
            raise HTTPException(status_code=403, detail="Forbidden")
    elif not visible(_user, parts):
        raise HTTPException(status_code=403, detail="Forbidden")
    trash_path = SHARE_ROOT / row["trash_path"]
    # rmtree 大目录（数万文件）可达分钟级，进线程池防冻结事件循环（R1-P2-8）
    if trash_path.is_dir():
        await run_in_threadpool(shutil.rmtree, trash_path, True)
    else:
        trash_path.unlink(missing_ok=True)
    with _db_lock:
        _db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
        _db().commit()
    audit("trash_delete", row["name"], ip=client_ip(request))
    return {"ok": True}


@router.delete("/api/trash")
async def empty_trash(request: Request, _admin=Depends(require_admin)):
    trash_root = SHARE_ROOT / ".trash"
    if trash_root.exists():
        # 同 delete_trash_item：rmtree 进线程池（R1-P2-8）
        await run_in_threadpool(shutil.rmtree, trash_root, True)
    with _db_lock:
        _db().execute("DELETE FROM trash")
        _db().commit()
    audit("trash_empty", ip=client_ip(request))
    return {"ok": True}
