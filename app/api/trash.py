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
from app.roles import ADMIN
from app.security import audit, client_ip, require_admin, require_user
from app.store import trash as trash_store
from app.filetools import human_size, to_rel
from app.spaces import resolve, visible

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


_VPATH_RE = re.compile(r"^(shared|private):(.*)$", re.I)


# ── 同步 DB 助手（11 号复审批次 3，P2-2）──────────────────────
# 本模块路由都是 async（要 await 文件系统操作），而元数据库访问是同步的：
# 直接在 async 里调 DB 会在事件循环内做 PG 往返，单进程 uvicorn 下阻塞全体
# 请求。统一抽成同步函数、由 run_in_threadpool 执行——与项目既有规范一致
#（中间件与其它路由都这么做，此前这几处是漏网的）。
def _select_all_trash():
    return trash_store.list_all()


def _select_trash(trash_id: int):
    return trash_store.get(trash_id)


def _drop_trash_row(trash_id: int) -> None:
    trash_store.drop(trash_id)


def _clear_trash() -> None:
    trash_store.clear()


def _ensure_can_touch(user: dict, row) -> None:
    """恢复/删除共用的逐条可见性判定（review 2026-09-02 P1-1 收编）：
    历史入口 vpath 形态（归属不可判定）仅 admin；其余按空间可见性。
    不通过直接 403。"""
    parts = _trash_rel_parts(row["orig_path"])
    if parts is None:
        if user["role"] != ADMIN:
            raise HTTPException(status_code=403, detail="Forbidden")
    elif not visible(user, parts):
        raise HTTPException(status_code=403, detail="Forbidden")


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
    rows = await run_in_threadpool(_select_all_trash)
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
    row = await run_in_threadpool(_select_trash, trash_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    # 与删除入口同款可见性校验（review 2026-09-02 P1-1）：缺了它，成员可枚举
    # trash id 把他人 private/incoming 的回收站条目"恢复"进自己空间
    _ensure_can_touch(_user, row)
    trash_path = SHARE_ROOT / row["trash_path"]
    if not trash_path.exists():
        await run_in_threadpool(_drop_trash_row, trash_id)
        raise HTTPException(status_code=404, detail="Trash file missing")
    dest = resolve(_user, row["orig_path"], "write")
    if dest.exists():
        # 冲突重命名必须连"恢复名"一起查（2026-09-19 对抗审查 F2）：只查主名时，
        # 第二个同 orig_path 条目的恢复会在「f (恢复).txt」已存在时走 shutil.move
        # 的 copy2 回退（Windows os.rename 必抛）——静默覆盖上一份恢复副本
        # （bug·27ce"rename 静默替换"同族）。逐个后缀找到空位，宁多勿覆盖。
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while True:
            tag = "" if n == 1 else f" {n}"   # 首个冲突保持旧名「(恢复)」，向后兼容
            cand = dest.with_name(f"{stem} (恢复{tag}){suffix}")
            if not cand.exists():
                dest = cand
                break
            n += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    await run_in_threadpool(shutil.move, str(trash_path), str(dest))
    await run_in_threadpool(_drop_trash_row, trash_id)
    audit("restore", to_rel(dest), ip=client_ip(request))
    return {"ok": True, "path": to_rel(dest)}


@router.delete("/api/trash/{trash_id}")
async def delete_trash_item(request: Request, trash_id: int, _user=Depends(require_user)):
    row = await run_in_threadpool(_select_trash, trash_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    _ensure_can_touch(_user, row)
    trash_path = SHARE_ROOT / row["trash_path"]
    # rmtree 大目录（数万文件）可达分钟级，进线程池防冻结事件循环（R1-P2-8）
    if trash_path.is_dir():
        await run_in_threadpool(shutil.rmtree, trash_path, True)
    else:
        await run_in_threadpool(trash_path.unlink, True)
    await run_in_threadpool(_drop_trash_row, trash_id)
    audit("trash_delete", row["name"], ip=client_ip(request))
    return {"ok": True}


@router.delete("/api/trash")
async def empty_trash(request: Request, _admin=Depends(require_admin)):
    trash_root = SHARE_ROOT / ".trash"
    if trash_root.exists():
        # 同 delete_trash_item：rmtree 进线程池（R1-P2-8）
        await run_in_threadpool(shutil.rmtree, trash_root, True)
    await run_in_threadpool(_clear_trash)
    audit("trash_empty", ip=client_ip(request))
    return {"ok": True}
