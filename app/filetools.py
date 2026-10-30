"""文件工具：路径安全、文件名清洗、目录树、上传落盘。"""
import hashlib
import os
import re
import shutil
import threading
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


_83_ALIAS_RE = re.compile(r"~[0-9a-f]{1,4}(?:$|\.)", re.I)


def ensure_disk_room(needed: int = 0) -> None:
    """写盘前余量预检（v3.67.4 移植自分叉批 P1-⑦）：数据盘与 PG 同盘，DAV PUT
    （20GB 级）/ 分片收块把盘写满的瞬间会连带打挂生产库。余量低于
    MIN_FREE_BYTES（或低于本次写入需求）→ 507，客户端稍后重试。TOCTOU 窗口
    仍在（预检后他人写入），但封住"单请求吃满盘"的主向量。"""
    from app.config import MIN_FREE_BYTES, SHARE_ROOT
    free = shutil.disk_usage(str(SHARE_ROOT)).free
    if free < MIN_FREE_BYTES + max(0, needed):
        raise HTTPException(status_code=507, detail="Insufficient disk space")


def safe_path(rel: str) -> Path:
    """防目录穿越 + 禁止访问点前缀隐藏路径（.trash/.meta.db 等）+ 8.3 短名别名。

    8.3 别名（P0，2026-09-27 自主审查真机实证）：本卷启用了短名生成，
    `.meta.db`→`META~1.DB`、`.trash`→`TRASH~1`、`.backups`→`BACKUP~1`——
    短名不以点开头，只拦点前缀时成员可经 `GET /api/files/META~1.DB`
    下载元数据库（口令哈希+会话令牌=全账号接管）。凡含 `~1`~`~ffff`
    形态后缀的段一律 403；副作用：正常文件名含 `name~1.ext` 形态也被拒
    （家庭 NAS 里可接受，比关卷级 8.3 生成温和）。"""
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".") or _83_ALIAS_RE.search(p) for p in parts):
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


# 同一路径的"移入回收站"必须串行。trash 目标名带 time_ns() 唯一后缀，两路并发
# 删除同一文件会算出**不同**的目标名：A 的 os.replace 在飞时，B 先拿到
# PermissionError（Windows 上源要稍后才消失）、判"源还在"而退化成 shutil.move
# 复制路径 —— 结果同一份内容登记出多行，且有的行指向不存在的文件
#（D5 删除侧，实测 [404,200,200,404,404] 与坏行）。
# 本服务是单进程多线程，条带锁足以封死该窗口；固定 32 条带
# 不随路径数增长、无需清理；用 RLock 是为了同线程重入（上传覆盖内部会再调一次）不死锁。
_TRASH_STRIPES = tuple(threading.RLock() for _ in range(32))

# .trash 树的全操作锁（v3.67.2 移植自分叉批 P2-6）：_TRASH_STRIPES 只串行
# "源路径→.trash"方向；恢复/单条删除/清空/定时清理（api/trash 与 cleanup）
# 此前**完全不持锁**地动 .trash——admin 清空（rmtree 整树）与成员恢复的
# shutil.move 交错，Windows 下 rename 失败退化成 copytree，会把被删掉一半的
# 目录树"恢复成功"（静默截断）。凡"从 .trash 里取东西/删东西"一律持本锁；
# 单进程部署，进程内 RLock 足够。
TRASH_OPS_LOCK = threading.RLock()


def _trash_stripe(target: Path) -> threading.RLock:
    return _TRASH_STRIPES[hash(os.path.normcase(str(target))) % len(_TRASH_STRIPES)]


def move_to_trash(target: Path, orig_path: str):
    """把文件/目录移入回收站并登记（界面删除、WebDAV 删除/覆盖、上传覆盖共用）。

    按源路径串行（见 _TRASH_STRIPES）：rename 与登记必须是一个不可分割的单位，
    否则并发同名删除会互相把对方的成功当成自己的失败。
    锁序固定：TRASH_OPS_LOCK 外层、条带锁内层（RLock 同线程重入安全）。
    """
    with TRASH_OPS_LOCK:
        with _trash_stripe(target):
            _move_to_trash_locked(target, orig_path)


def _move_to_trash_locked(target: Path, orig_path: str) -> None:
    trash_root = SHARE_ROOT / ".trash"
    trash_root.mkdir(exist_ok=True)
    trash_path = trash_root / f"{time.time_ns()}_{target.name}"
    try:
        os.replace(target, trash_path)
    except OSError:
        # 这个 fallback 的本意是"同盘 rename 被拒（跨设备/权限）→ 退化成搬移"。
        # 但它也会接住"目标刚被别人并发移走"（两个同名并发删除），此时
        # shutil.move 对不存在的源再抛 FileNotFoundError 冒成 500。
        # 判存在 + 搬移之间还有窗口（Windows 上对方 move 中途会先抛
        # PermissionError 再让源消失），所以搬移本身也要收口。
        try:
            if not target.exists():
                raise HTTPException(status_code=404, detail="文件已被并发移走") from None
            shutil.move(str(target), str(trash_path))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="文件已被并发移走") from None
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
    # 文件名本身已截到 200，但叠上目录深度 + .uploading 临时后缀后总长可超
    # Windows MAX_PATH(260) → aiofiles.open 抛 FileNotFoundError 冒成 500。
    # 落盘前一次判定，给它一个用户能照做的 400（放浅一层/改短名字）。
    # 只限 NT：POSIX 上限是 4096/255，用同一个阈值会误拒合法长名。
    if os.name == "nt" and len(str(tmp)) > 259:
        raise HTTPException(
            status_code=400,
            detail="文件名过长：叠加目录深度与临时后缀后超出系统路径上限，"
                   "请放浅一层目录或改短文件名")
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
