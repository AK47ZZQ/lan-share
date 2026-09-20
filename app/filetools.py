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

# 笔记扩展名单一事实源：notes 列表与 kb 知识库引擎共用（api/notes.py 转引，
# 两套视图口径永不漂移）
NOTE_EXTS = {".md", ".markdown", ".txt", ".html", ".htm"}

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


def to_rel(path: "Path | str") -> str:
    """物理路径 → 相对 SHARE_ROOT 的正斜杠相对路径。

    全项目**唯一**的这层转换点（07/08 审查）：此前
    `str(x.relative_to(SHARE_ROOT)).replace("\\\\", "/")` 在 40 处手写复制，
    且有两处漏了 replace（audit 记录在 Windows 下带反斜杠）。集中后语义
    与原先完全一致（不在 SHARE_ROOT 下仍抛 ValueError，不静默兜底）。
    """
    return str(Path(path).relative_to(SHARE_ROOT)).replace("\\", "/")


# ─── 媒体扩展名（单一事实源，07 审查 §5.1-3）──────────────
# 图片预览/缩略图（api/files.py）与照片时间线扫描（api/system.py）共用同一份。
# 漂移教训：本共同体曾有 .gif（能预览）而照片扫描没有（不进时间线），
# 照片扫描有 .m4v/.3gp 而缩略图没有（时间线有但无缩略图）。
# .svg 有意不收：浏览器直接导航 svg 会执行内嵌脚本，预览端点按内联图片
# 服会有 XSS 面，svg 保持走下载。Web（web/src/filekinds.ts）与 App
# （mobile/lib/data/media_kinds.dart）各自持有一份对齐副本，改动需三端同步。
IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif"}
VID_EXTS = {".mp4", ".mov", ".m4v", ".3gp", ".webm", ".avi", ".mkv"}


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
    if RESERVED_NAME.match(n):
        n = '_' + n
    # 截断必须再剥一次结尾点/空格：上面的 rstrip 发生在 [:200] 之前，
    # 足够长的名字会在第 200 个字符处重新落出点/空格（"aaa….txt" → "aaa…."），
    # 而 NTFS 会静默剥离它们——登记名与磁盘实际名就此分叉。
    return n[:200].rstrip('. ') or 'unnamed'


RESERVED_NAME = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$', re.I)


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
        rel = to_rel(entry)
        items.append({
            "name": entry.name,
            "path": rel,
            "is_dir": is_dir,
            "size": stat.st_size if is_file else None,
            "size_human": human_size(stat.st_size) if is_file else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "ext": entry.suffix.lower() if is_file else None,
            # 服务器真实存储路径（改进建议 9.6-P1-3）：供"复制路径"用，
            # 统一正斜杠；能列出该条目即有读权限，与前端按钮可见性一致
            "abs_path": str(entry).replace("\\", "/"),
        })
    rel_path = to_rel(path)
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


async def save_upload(file: UploadFile, dest: Path, quota_user=None,
                      on_dup: str = "trash") -> dict:
    """单请求直传。先写 .uploading 临时文件、完成后 os.replace 原子落位；
    临时名带唯一后缀，两个并发同名单请求（如分片超时重试与原请求并发）
    不会同时打开同一个 tmp 交错写坏；旧版本在替换前进回收站。

    on_dup 控制同名覆盖行为（改进建议 9.6-P0-12）：默认 "trash" 旧版本进
    回收站（通用上传防丢）；"overwrite" 直接覆盖不留版本——供笔记编辑器
    这类"就地编辑"场景使用，避免每次保存都往回收站塞一个副本。

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
    if dest.exists() and on_dup != "overwrite":
        # 覆盖上传：旧版本进回收站，不直接丢失（on_dup=overwrite 除外）
        move_to_trash(dest, to_rel(dest))
    os.replace(tmp, dest)
    return {
        "name": dest.name,
        "path": to_rel(dest),
        "size": size,
        "size_human": human_size(size),
        "sha256": sha256.hexdigest(),
    }


def file_dict(f: Path) -> dict:
    stat = f.stat()
    return {
        "name": f.name,
        "path": to_rel(f),
        "size": stat.st_size,
        "size_human": human_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "ext": f.suffix.lower(),
    }


# ─── 认证 API ─────────────────────────────────────────────
