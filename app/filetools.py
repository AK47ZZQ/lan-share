"""文件工具：路径安全、文件名清洗、目录树、上传落盘。"""
import hashlib
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile

from app.config import MAX_UPLOAD_SIZE, SHARE_ROOT
from app.db import _db, _db_lock

# ─── 文件工具 ─────────────────────────────────────────────

_heif_registered = False


def ensure_heif_support():
    """让 PIL 能解码 HEIC/HEIF（iPhone「高效」模式默认照片格式）。幂等；
    pillow-heif 缺失时静默降级（仅损失 HEIC 缩略图/预览，不影响其它功能）。"""
    global _heif_registered
    if _heif_registered:
        return
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        _heif_registered = True
    except ImportError:
        pass


def human_size(size_bytes: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def safe_path(rel: str) -> Path:
    """防目录穿越 + 禁止访问点前缀隐藏路径（.trash/.meta.db 等）。"""
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".") for p in parts):
        raise HTTPException(status_code=403, detail="Forbidden")
    target = SHARE_ROOT.joinpath(*parts) if parts else SHARE_ROOT
    root = os.path.normcase(str(SHARE_ROOT.absolute()))
    tgt = os.path.normcase(str(target.absolute()))
    if tgt != root and not tgt.startswith(root + os.sep):
        raise HTTPException(status_code=403, detail="Forbidden")
    return target


def clean_filename(name: str | None) -> str:
    # Windows 文件名非法字符全集：/ \ : * ? " < > | 与控制符——手机端（Linux FS）
    # 传来的文件名可含后者，漏洗会在落盘时 OSError 500
    n = re.sub(r'[/\\:*?"<>|\x00-\x1f]', '_', name or 'unnamed')
    n = n.strip().rstrip('. ')                              # 结尾点/空格（NTFS 会静默剥离）
    if not n:
        n = 'unnamed'
    if n.startswith('.'):
        n = '_' + n
    if _RESERVED_NAME.match(n):
        n = '_' + n
    return n[:200] or 'unnamed'


_RESERVED_NAME = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$', re.I)


def dir_tree(path: Path) -> dict:
    items = []
    if not path.exists():
        return {"path": "", "items": []}
    for entry in sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.name.startswith("."):
            continue
        # iterdir 与 stat 之间条目可能被并发删除（R1-P2-2）：一次竞态 OSError
        # 不能让整个目录列表 500
        try:
            stat = entry.stat()
            is_dir = entry.is_dir()
            is_file = entry.is_file()
        except OSError:
            continue
        rel = str(entry.relative_to(SHARE_ROOT)).replace("\\", "/")
        items.append({
            "name": entry.name,
            "path": rel,
            "is_dir": is_dir,
            "size": stat.st_size if is_file else None,
            "size_human": human_size(stat.st_size) if is_file else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "ext": entry.suffix.lower() if is_file else None,
        })
    rel_path = str(path.relative_to(SHARE_ROOT)).replace("\\", "/")
    return {"path": rel_path if rel_path != "." else "", "items": items}


def move_to_trash(target: Path, orig_path: str):
    """把文件/目录移入回收站并登记（界面删除、WebDAV 删除/覆盖、上传覆盖共用）。"""
    trash_root = SHARE_ROOT / ".trash"
    trash_root.mkdir(exist_ok=True)
    trash_path = trash_root / f"{time.time_ns()}_{target.name}"
    try:
        os.replace(target, trash_path)
    except OSError:
        shutil.move(str(target), str(trash_path))
    size = None
    if trash_path.is_file():
        size = trash_path.stat().st_size
    with _db_lock:
        _db().execute(
            "INSERT INTO trash(orig_path, trash_path, name, size, deleted_at) VALUES(?,?,?,?,?)",
            (orig_path, f".trash/{trash_path.name}", target.name, size, time.time()))
        _db().commit()


async def save_upload(file: UploadFile, dest: Path, quota_user=None) -> dict:
    """单请求直传。先写 .uploading 临时文件、完成后 os.replace 原子落位；
    临时名带唯一后缀，两个并发同名单请求（如分片超时重试与原请求并发）
    不会同时打开同一个 tmp 交错写坏；旧版本在替换前进回收站。

    quota_user 传入时在落位前按真实占用校验配额（R1-P1-2）：starlette 对
    multipart 文件部分的 UploadFile.size 恒为 0，端点预检无法可信计价；此刻
    tmp 已含完整内容且位于目标目录树内，配额扫描已计入其字节，传 0 即精确。"""
    if dest.is_dir():
        raise HTTPException(status_code=409, detail="同名目录已存在")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.uploading-{os.getpid()}-{time.time_ns()}")
    size = 0
    sha256 = hashlib.sha256()
    overflow = False
    try:
        async with aiofiles.open(tmp, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    overflow = True   # Windows 上打开中的文件不能 unlink，关句柄后再清
                    break
                sha256.update(chunk)
                await f.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if overflow:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=413,
                            detail=f"File too large (max {human_size(MAX_UPLOAD_SIZE)})")
    # 落位前重查（TOCTOU 收窄，红队 P2）：长传期间同名目录可能被并发建出，
    # 只回收文件顶替——目录绝不能被静默移进回收站
    if dest.is_dir():
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="同名目录已存在")
    if quota_user is not None:
        try:
            from app.spaces import enforce_quota
            enforce_quota(quota_user, dest, 0)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    if dest.exists():  # 覆盖上传：旧版本进回收站，不直接丢失
        move_to_trash(dest, str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"))
    os.replace(tmp, dest)
    return {
        "name": dest.name,
        "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
        "size": size,
        "size_human": human_size(size),
        "sha256": sha256.hexdigest(),
    }


def _file_dict(f: Path) -> dict:
    stat = f.stat()
    return {
        "name": f.name,
        "path": str(f.relative_to(SHARE_ROOT)).replace("\\", "/"),
        "size": stat.st_size,
        "size_human": human_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "ext": f.suffix.lower(),
    }


# ─── 认证 API ─────────────────────────────────────────────
