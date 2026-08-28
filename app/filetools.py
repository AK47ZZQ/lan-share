"""文件工具：路径安全、文件名清洗、目录树、上传落盘。"""
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile

from app.config import MAX_UPLOAD_SIZE, SHARE_ROOT

# ─── 文件工具 ─────────────────────────────────────────────

def human_size(size_bytes: int) -> str:
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


def clean_filename(name: str) -> str:
    n = re.sub(r'[/\\:\x00-\x1f]', '_', name or 'unnamed')  # 分隔符/冒号(NTFS ADS)/控制符
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
        stat = entry.stat()
        rel = str(entry.relative_to(SHARE_ROOT)).replace("\\", "/")
        items.append({
            "name": entry.name,
            "path": rel,
            "is_dir": entry.is_dir(),
            "size": stat.st_size if entry.is_file() else None,
            "size_human": human_size(stat.st_size) if entry.is_file() else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "ext": entry.suffix.lower() if entry.is_file() else None,
        })
    rel_path = str(path.relative_to(SHARE_ROOT)).replace("\\", "/")
    return {"path": rel_path if rel_path != "." else "", "items": items}


async def save_upload(file: UploadFile, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    sha256 = hashlib.sha256()
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_SIZE:
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413,
                                    detail=f"File too large (max {human_size(MAX_UPLOAD_SIZE)})")
            sha256.update(chunk)
            await f.write(chunk)
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
