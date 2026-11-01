"""备份的本机接口（控制面板专用，只允许 127.0.0.1 访问）。

为什么单独开一组接口：

1. 托盘是 WebView2 + C# 桥接，它访问 `127.0.0.1:8766` 时没有 session cookie，
   走不了 `/api/admin/*` 那套鉴权。同目录的 `local_config.py` 已经立了这个
   先例（`/local/public-url`），这里沿用。
2. 备份目录必须能由用户选 —— 默认落在数据目录里意味着**盘坏时源和备份一起没**，
   这是长跑系统最不该有的设计。让用户指到另一块盘，才是真正的备份。

安全边界：`_require_local` 只放行环回地址。即使有人从局域网访问，
这些接口也不会响应。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.local_config import _require_local
from app.services import backup as backup_svc

router = APIRouter()


class BackupDirBody(BaseModel):
    dir: str = ""


class BackupRunBody(BaseModel):
    # 立即备份时可选地顺手改目录（控制面板上是「选好目录 → 点立即备份」）
    dir: str | None = None


def _describe() -> dict:
    """当前备份配置 + 上次结果。控制面板靠这一个响应渲染整张卡片。"""
    from app.config import BACKUP_KEEP

    dest = backup_svc.backup_dir()
    default_dir = None
    try:
        from app.config import BACKUP_DIR as _DEF
        default_dir = str(_DEF)
    except Exception:
        pass

    exists = dest.is_dir()
    items: list[dict] = []
    total = 0
    if exists:
        try:
            files = [f for f in dest.iterdir() if f.is_file()]
            # 总量按全部文件算 —— items 只取最近 8 个是为了显示，
            # 但 total_bytes 报「前 8 个之和」就是在撒谎。
            for f in files:
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
            files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            for f in files[:8]:
                try:
                    st = f.stat()
                except OSError:
                    continue
                items.append({"name": f.name, "size": st.st_size, "mtime": st.st_mtime})
        except OSError:
            pass

    last = None
    try:
        last = backup_svc.last_status()
    except Exception:
        pass

    return {
        "dir": str(dest),
        "default_dir": default_dir,
        "is_default": str(dest) == (default_dir or ""),
        "exists": exists,
        # 备份目录和数据目录在同一个盘上时，前端要醒目提示
        "same_volume_as_data": _same_volume(dest),
        "keep": BACKUP_KEEP,
        "files": items,
        "total_bytes": total,
        "last": last,
    }


def _same_volume(dest: Path) -> bool:
    """备份目录是否和数据目录同一卷。同一个盘 = 盘坏一起没。"""
    try:
        from app.config import SHARE_ROOT
        return Path(dest).drive.lower() == Path(SHARE_ROOT).drive.lower()
    except Exception:
        return False


@router.get("/local/backup")
async def get_backup(request: Request):
    _require_local(request)
    return _describe()


@router.post("/local/backup/dir")
async def set_backup_dir(request: Request, body: BackupDirBody):
    _require_local(request)
    try:
        backup_svc.set_backup_dir(body.dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _describe()


@router.post("/local/backup/run")
async def run_backup(request: Request, body: BackupRunBody | None = None):
    """立即备份一次。

    用线程池跑，因为这个函数内部是同步阻塞的（pg_dump 子进程）。
    直接在 async 函数里同步调用会把整个事件循环卡住 —— 备份 20GB 的时候
    全站都会没响应。
    """
    _require_local(request)
    if body is not None and body.dir is not None:
        try:
            backup_svc.set_backup_dir(body.dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    from starlette.concurrency import run_in_threadpool
    result = await run_in_threadpool(backup_svc.run_backup_once)
    return {"result": result, **(_describe())}
