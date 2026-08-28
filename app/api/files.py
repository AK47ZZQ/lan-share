"""文件 API：列表/注册指纹/分块上传/直传/建目录/移动/回收站/预览/zip/重命名/缩略图/搜索/磁盘。"""
import hashlib
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import (Depends, File, HTTPException, Query, Request, UploadFile)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.config import (CHUNK_SIZE, MAX_UPLOAD_SIZE, TRASH_DAYS, UPLOAD_SESSION_TTL,
                        ZIP_TTL, SHARE_ROOT)
from app.db import _db, _db_lock
from app.security import audit, client_ip, require_user, require_admin
from app.filetools import (_file_dict, clean_filename, dir_tree, human_size, safe_path,
                           save_upload)
from app.spaces import enforce_quota, resolve, sync_grants_on_move, visible
from app.main import app


def _session_owner_guard(row, user):
    """分块上传会话只能由创建者（或 admin）续传/收尾。"""
    if row["user_id"] and row["user_id"] != user["user_id"] and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")


def _under_private(path: Path, username: str) -> bool:
    try:
        rel = path.relative_to(SHARE_ROOT)
    except ValueError:
        return False
    return (rel.parts and rel.parts[0] == "private"
            and len(rel.parts) > 1
            and rel.parts[1].lower() == username.lower())


def _cross_space_guard(user, src: Path, dest: Path):
    """E9：成员不得把文件移入/移出私人空间与共享空间之间（admin 不限）。
    私人空间内部的移动不受限。"""
    if user["role"] == "admin":
        return
    src_priv = _under_private(src, user["username"])
    dest_priv = _under_private(dest, user["username"])
    if src_priv != dest_priv:
        raise HTTPException(status_code=400,
                            detail="共享空间与私人空间之间不能移动（可复制后删除）")

@app.get("/api/files/{path:path}")
@app.get("/api/files")
async def list_files(path: str = "", _user=Depends(require_user)):
    target = resolve(_user, path, "read")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Directory not found")
    if target.is_file():
        raise HTTPException(status_code=400, detail="Not a directory")
    return dir_tree(target)


def register_blob(path: Path, sha: str, size: int) -> bool:
    """登记内容指纹；发现同内容旧文件则用 NTFS 硬链接去重（返回是否发生了去重）。

    用 link→临时名 再原子 replace 的方式，避免 unlink 后失败导致新文件丢失的窗口。"""
    row = _db().execute("SELECT sample_path FROM blobs WHERE sha256=?", (sha,)).fetchone()
    if row and row["sample_path"] != str(path):
        sample = Path(row["sample_path"])
        if sample.is_file():
            tmp_link = path.with_name(path.name + ".dedup")
            try:
                os.link(sample, tmp_link)
                os.replace(tmp_link, path)
                return True
            except OSError:
                tmp_link.unlink(missing_ok=True)  # 跨卷/权限失败则保留独立副本
    with _db_lock:
        _db().execute(
            "INSERT INTO blobs(sha256, size, sample_path, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(sha256) DO UPDATE SET sample_path=excluded.sample_path",
            (sha, size, str(path), time.time()))
        _db().commit()
    return False


# ─── 分块上传 / 断点续传 / 秒传 ────────────────────────────

class UploadInitBody(BaseModel):
    dest_dir: str = ""
    filename: str
    size: int
    sha256: Optional[str] = None  # 客户端预计算（≤100MB 才算）；finish 时服务端必校验


def _chunk_dir(sid: str) -> Path:
    return SHARE_ROOT / ".chunks" / sid


@app.post("/api/upload/init")
async def upload_init(request: Request, body: UploadInitBody, _user=Depends(require_user)):
    dest_dir = resolve(_user, body.dest_dir, "write")
    dest = dest_dir / clean_filename(body.filename)
    if body.size <= 0:
        raise HTTPException(status_code=400, detail="Invalid size")
    enforce_quota(_user, dest, body.size)

    # 秒传：库里有同 sha256 内容（服务端算的，可信），直接硬链接
    if body.sha256:
        row = _db().execute("SELECT sample_path FROM blobs WHERE sha256=?", (body.sha256,)).fetchone()
        if row:
            sample = Path(row["sample_path"])
            if sample.is_file():
                try:
                    if dest.exists():
                        dest.unlink()
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.link(sample, dest)
                    audit("upload_instant", str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
                          human_size(body.size), client_ip(request))
                    return {"instant": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
                            "size": body.size, "size_human": human_size(body.size)}
                except OSError:
                    pass  # 硬链接失败则走正常分块上传

    sid = secrets.token_urlsafe(16)
    _chunk_dir(sid).mkdir(parents=True, exist_ok=True)
    with _db_lock:
        _db().execute(
            "INSERT INTO upload_sessions(id, dest_dir, filename, total_size, sha256, "
            "created_at, user_id) VALUES(?,?,?,?,?,?,?)",
            (sid, body.dest_dir, dest.name, body.size, body.sha256, time.time(),
             _user["user_id"]))
        _db().commit()
    return {"instant": False, "session": sid, "chunk_size": CHUNK_SIZE, "received": []}


@app.get("/api/upload/state/{sid}")
async def upload_state(sid: str, _user=Depends(require_user)):
    """断点续传：查询已收到的块序号。"""
    row = _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    _session_owner_guard(row, _user)
    d = _chunk_dir(sid)
    received = sorted(int(p.stem.split("-")[1]) for p in d.glob("part-*")) if d.exists() else []
    return {"session": sid, "chunk_size": CHUNK_SIZE, "received": received}


@app.post("/api/upload/chunk/{sid}")
async def upload_chunk(request: Request, sid: str, index: int = Query(...),
                       _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    _session_owner_guard(row, _user)
    total = (row["total_size"] + CHUNK_SIZE - 1) // CHUNK_SIZE
    if index < 0 or index >= total:
        raise HTTPException(status_code=400, detail="Chunk index out of range")
    part = _chunk_dir(sid) / f"part-{index:06d}"
    async with aiofiles.open(part, "wb") as f:
        async for chunk in request.stream():
            await f.write(chunk)
    return {"ok": True, "index": index}


@app.post("/api/upload/finish/{sid}")
async def upload_finish(request: Request, sid: str, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    _session_owner_guard(row, _user)
    d = _chunk_dir(sid)
    total = (row["total_size"] + CHUNK_SIZE - 1) // CHUNK_SIZE
    parts = sorted(d.glob("part-*"))
    if len(parts) != total:
        raise HTTPException(status_code=400,
                            detail=f"Missing chunks: {len(parts)}/{total}")

    owner = _user
    if row["user_id"] and row["user_id"] != _user["user_id"]:
        owner = _db().execute("SELECT * FROM users WHERE id=?",
                              (row["user_id"],)).fetchone()
        if not owner:
            raise HTTPException(status_code=403, detail="Forbidden")
    dest_dir = resolve(owner, row["dest_dir"] or "", "write")
    dest = dest_dir / row["filename"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".uploading")

    size = 0
    sha = hashlib.sha256()
    async with aiofiles.open(tmp, "wb") as out:
        for part in parts:
            async with aiofiles.open(part, "rb") as f:
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    sha.update(chunk)
                    await out.write(chunk)
    if size != row["total_size"]:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Size mismatch")

    real_sha = sha.hexdigest()
    if row["sha256"] and row["sha256"] != real_sha:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="SHA256 mismatch, please re-upload")

    enforce_quota(owner, dest, size)
    os.replace(tmp, dest)
    deduped = register_blob(dest, real_sha, size)
    shutil.rmtree(d, ignore_errors=True)
    with _db_lock:
        _db().execute("DELETE FROM upload_sessions WHERE id=?", (sid,))
        _db().commit()
    rel = str(dest.relative_to(SHARE_ROOT)).replace("\\", "/")
    audit("upload", rel, human_size(size), client_ip(request))
    return {"ok": True, "path": rel, "size": size, "size_human": human_size(size),
            "sha256": real_sha, "deduped": deduped}


@app.post("/api/upload/{path:path}")
@app.post("/api/upload")
async def upload_file(request: Request, path: str = "", file: UploadFile = File(...),
                      _user=Depends(require_user)):
    clean_name = clean_filename(file.filename)
    dest = resolve(_user, path, "write") / clean_name
    enforce_quota(_user, dest, file.size or 0)
    result = await save_upload(file, dest)
    deduped = register_blob(dest, result["sha256"], result["size"])
    audit("upload", result["path"], result["size_human"], client_ip(request))
    return {"ok": True, "deduped": deduped, **result}


@app.post("/api/mkdir/{path:path}")
async def create_directory(request: Request, path: str, _user=Depends(require_user)):
    if not path.strip():
        raise HTTPException(status_code=400, detail="Invalid directory name")
    # 逐段清洗（保留名/冒号/结尾点等），再过空间解析（含写权限判定）
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    cleaned = [clean_filename(p) for p in parts]
    target = resolve(_user, "/".join(cleaned), "write")
    target.mkdir(parents=True, exist_ok=True)
    rel = str(target.relative_to(SHARE_ROOT)).replace("\\", "/")
    audit("mkdir", rel, ip=client_ip(request))
    return {"ok": True, "path": rel}


class MoveBody(BaseModel):
    sources: list[str]
    dest_dir: str = ""


@app.post("/api/move")
async def move_items(request: Request, body: MoveBody, _user=Depends(require_user)):
    """批量移动到目标目录。同盘 rename，硬链接去重与秒传指纹不受影响。"""
    if not body.sources:
        raise HTTPException(status_code=400, detail="未选择要移动的文件")
    if len(body.sources) > 500:
        raise HTTPException(status_code=400, detail="单次最多移动 500 项")
    dest_dir = resolve(_user, body.dest_dir, "write")
    if not dest_dir.is_dir():
        raise HTTPException(status_code=404, detail="目标目录不存在")
    results = []
    for src in body.sources:
        try:
            # 移动=从源删除：源也需要写权（不能移走只有读权的共享文件）
            target = resolve(_user, src, "write")
            _cross_space_guard(_user, target, dest_dir)
            if not target.exists():
                raise HTTPException(status_code=404, detail="源文件不存在")
            if target.parent == dest_dir:
                raise HTTPException(status_code=400, detail="已在目标目录")
            if target == dest_dir or (target.is_dir() and target in dest_dir.parents):
                # 只有"目录移进自己的后代"会成环；移到祖先目录是合法操作
                raise HTTPException(status_code=400, detail="不能移动到自身的子目录")
            # os.rename 在 Windows 下目标已存在必抛 FileExistsError（原子防覆盖，
            # 无 _unique_dest 预检 + os.replace 的并发竞态窗口）
            placed = None
            final = dest_dir / target.name
            for i in range(1, 1000):
                cand = final if i == 1 else final.with_name(f"{final.stem} ({i}){final.suffix}")
                try:
                    os.rename(target, cand)
                    placed = cand
                    break
                except FileExistsError:
                    continue
            if placed is None:
                raise HTTPException(status_code=409, detail="无法生成不重名的目标名")
            final_rel = str(placed.relative_to(SHARE_ROOT)).replace("\\", "/")
            if target.is_dir():
                sync_grants_on_move(
                    str(target.relative_to(SHARE_ROOT)).replace("\\", "/"), final_rel)
            results.append({"src": src, "ok": True, "dest": final_rel})
            audit("move", src, f"→ {final_rel}", client_ip(request))
        except HTTPException as e:
            results.append({"src": src, "ok": False, "error": e.detail})
        except OSError as e:
            results.append({"src": src, "ok": False, "error": str(e)})
    return {"ok": True, "results": results}


def _move_to_trash(target: Path, orig_path: str):
    """把文件/目录移入回收站并登记（界面删除与 WebDAV 删除/覆盖共用）。"""
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


@app.delete("/api/files/{path:path}")
async def delete_file(request: Request, path: str, _user=Depends(require_user)):
    target = resolve(_user, path, "write")
    if target == SHARE_ROOT:
        raise HTTPException(status_code=400, detail="不能删除根目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    _move_to_trash(target, path)
    audit("trash", path, ip=client_ip(request))
    return {"ok": True}


@app.get("/api/trash")
async def list_trash(_user=Depends(require_user)):
    rows = _db().execute("SELECT * FROM trash ORDER BY deleted_at DESC").fetchall()
    return [{
        "id": r["id"],
        "name": r["name"],
        "orig_path": r["orig_path"],
        "size": r["size"],
        "size_human": human_size(r["size"]) if r["size"] else "-",
        "deleted_at": datetime.fromtimestamp(r["deleted_at"]).isoformat(),
        "days_left": max(0, TRASH_DAYS - int((time.time() - r["deleted_at"]) / 86400)),
    } for r in rows if visible(_user, r["orig_path"].split("/"))]


@app.post("/api/trash/{trash_id}/restore")
async def restore_trash(request: Request, trash_id: int, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
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
    audit("restore", str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"), ip=client_ip(request))
    return {"ok": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/")}


@app.delete("/api/trash/{trash_id}")
async def delete_trash_item(request: Request, trash_id: int, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    if not visible(_user, row["orig_path"].split("/")):
        raise HTTPException(status_code=403, detail="Forbidden")
    trash_path = SHARE_ROOT / row["trash_path"]
    if trash_path.is_dir():
        shutil.rmtree(trash_path, ignore_errors=True)
    else:
        trash_path.unlink(missing_ok=True)
    with _db_lock:
        _db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
        _db().commit()
    audit("trash_delete", row["name"], ip=client_ip(request))
    return {"ok": True}


@app.delete("/api/trash")
async def empty_trash(request: Request, _admin=Depends(require_admin)):
    trash_root = SHARE_ROOT / ".trash"
    if trash_root.exists():
        shutil.rmtree(trash_root, ignore_errors=True)
    with _db_lock:
        _db().execute("DELETE FROM trash")
        _db().commit()
    audit("trash_empty", ip=client_ip(request))
    return {"ok": True}


@app.get("/preview/{path:path}")
async def preview_file(path: str, _user=Depends(require_user)):
    target = resolve(_user, path, "read")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=mime or "application/octet-stream",
                        headers={"Content-Disposition": f"inline; filename=\"{target.name}\""})


# ─── 批量打包下载 / 重命名 ─────────────────────────────────

class ZipBody(BaseModel):
    paths: list[str]


_zips: dict[str, dict] = {}
_zips_lock = threading.Lock()


@app.post("/api/zip")
def create_zip(request: Request, body: ZipBody, _user=Depends(require_user)):
    """打包选中文件/目录为 zip，返回短时效下载 URL。"""
    if not body.paths or len(body.paths) > 500:
        raise HTTPException(status_code=400, detail="Invalid paths")
    targets = [resolve(_user, p, "read") for p in body.paths]
    for t in targets:
        if not t.exists():
            raise HTTPException(status_code=404, detail=f"Not found: {t.name}")

    zdir = SHARE_ROOT / ".zips"
    zdir.mkdir(exist_ok=True)
    zpath = zdir / f"{secrets.token_urlsafe(8)}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for t in targets:
            if t.is_dir():
                for f in t.rglob("*"):
                    if f.is_file():
                        z.write(f, str(f.relative_to(t.parent)))
            else:
                # 重名时保留路径去重
                arc = t.name
                base = arc.rsplit(".", 1)[0] if "." in arc else arc
                ext = ("." + arc.rsplit(".", 1)[1]) if "." in arc else ""
                n = 1
                while arc in z.namelist():
                    arc = f"{base} ({n}){ext}"
                    n += 1
                z.write(t, arc)
    token = secrets.token_urlsafe(16)
    with _zips_lock:
        _zips[token] = {"path": zpath, "exp": time.time() + ZIP_TTL}
    audit("zip", f"{len(targets)} items", ip=client_ip(request))
    return {"ok": True, "url": f"/api/zip/{token}",
            "name": f"lan-share-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"}


@app.get("/api/zip/{token}")
async def download_zip(token: str, _user=Depends(require_user)):
    with _zips_lock:
        info = _zips.pop(token, None)
    if not info or info["exp"] < time.time():
        raise HTTPException(status_code=403, detail="下载链接已失效，请重新打包")
    zpath: Path = info["path"]
    if not zpath.exists():
        raise HTTPException(status_code=404, detail="Zip missing")
    return FileResponse(zpath, media_type="application/zip",
                        filename=zpath.stem + ".zip",
                        background=BackgroundTask(os.unlink, str(zpath)))


class RenameBody(BaseModel):
    path: str
    new_name: str


@app.post("/api/rename")
async def rename_path(request: Request, body: RenameBody, _user=Depends(require_user)):
    target = resolve(_user, body.path, "write")
    if target == SHARE_ROOT:
        raise HTTPException(status_code=400, detail="不能重命名根目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    dest = target.parent / clean_filename(body.new_name)
    if dest == target:
        return {"ok": True}
    if dest.exists():
        raise HTTPException(status_code=400, detail="目标名称已存在")
    try:
        old_rel = str(target.relative_to(SHARE_ROOT)).replace("\\", "/")
    except ValueError:
        old_rel = ""
    target.rename(dest)
    if old_rel:
        sync_grants_on_move(old_rel, str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"))
    audit("rename", body.path, body.new_name, client_ip(request))
    return {"ok": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/")}


# ─── 缩略图 ───────────────────────────────────────────────

IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VID_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


@app.get("/thumb/{path:path}")
def thumb_file(path: str, _user=Depends(require_user)):
    """缩略图（同步端点，跑线程池）。图片用 Pillow，视频用 ffmpeg 抽帧，缓存于 .thumbs/。"""
    from PIL import Image
    target = resolve(_user, path, "read")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    ext = target.suffix.lower()
    if ext not in IMG_EXTS | VID_EXTS:
        raise HTTPException(status_code=404, detail="No thumbnail for this type")

    stat = target.stat()
    key = hashlib.md5(f"{path}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()
    cache_dir = SHARE_ROOT / ".thumbs"
    cache_dir.mkdir(exist_ok=True)
    cached = cache_dir / f"{key}.jpg"
    if cached.exists():
        return FileResponse(cached, media_type="image/jpeg")

    tmp = cached.with_suffix(".tmp")
    try:
        if ext in IMG_EXTS:
            img = Image.open(target)
            img.thumbnail((320, 320))
            img.convert("RGB").save(tmp, "JPEG", quality=80)
        else:
            # 短视频 -ss 会越界抽不到帧：先试 1s 处，失败退回首帧
            for seek_args in (["-ss", "1"], []):
                tmp.unlink(missing_ok=True)
                r = subprocess.run(
                    ["ffmpeg", "-y"] + seek_args + ["-i", str(target), "-vframes", "1",
                     "-vf", "scale=320:-2", "-f", "image2", str(tmp)],
                    capture_output=True, timeout=25)
                if r.returncode == 0 and tmp.exists():
                    break
            if not tmp.exists():
                raise HTTPException(status_code=404, detail="Thumbnail failed")
        os.replace(tmp, cached)
    except HTTPException:
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Thumbnail error")
    return FileResponse(cached, media_type="image/jpeg")


@app.get("/api/search")
async def search_files(q: str = Query(..., min_length=1), _user=Depends(require_user)):
    results = []
    for f in SHARE_ROOT.rglob("*"):
        rel_parts = f.relative_to(SHARE_ROOT).parts
        if not visible(_user, rel_parts):
            continue
        if f.is_file() and q.lower() in f.name.lower():
            results.append(_file_dict(f))
            if len(results) >= 50:
                break
    return results


@app.get("/api/recent")
async def recent_files(limit: int = Query(default=20, le=100), _user=Depends(require_user)):
    files = []
    for f in SHARE_ROOT.rglob("*"):
        rel_parts = f.relative_to(SHARE_ROOT).parts
        if not visible(_user, rel_parts):
            continue
        if f.is_file():
            files.append(_file_dict(f))
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files[:limit]


def _scan_share_sizes():
    """网盘内容统计：返回 (去重后的物理占用, 各一级目录/根文件的逻辑大小)。
    NTFS 硬链接去重的同一份内容按物理只计一次；隐藏（点前缀）路径全部排除。"""
    seen = set()
    physical = 0
    categories = {}
    for f in SHARE_ROOT.rglob("*"):
        try:
            if not f.is_file():
                continue
            rel = f.relative_to(SHARE_ROOT)
            st = f.stat()
        except OSError:
            continue
        if any(p.startswith(".") for p in rel.parts):
            continue
        key = (st.st_dev, st.st_ino)
        if key not in seen:
            seen.add(key)
            physical += st.st_size
        top = rel.parts[0] if len(rel.parts) > 1 else "(根目录文件)"
        categories[top] = categories.get(top, 0) + st.st_size
    return physical, categories


@app.get("/api/disk")
def disk_info(_user=Depends(require_user)):
    # 同步 def：FastAPI 自动放线程池，rglob 扫描不阻塞事件循环（大目录时尤其重要）
    usage = shutil.disk_usage(str(SHARE_ROOT))
    share_physical, _cats = _scan_share_sizes()
    return {
        "total": human_size(usage.total),
        "used": human_size(usage.used),
        "free": human_size(usage.free),
        "free_bytes": usage.free,
        "share_size": human_size(share_physical),
        "warning": usage.free < 15 * 1024**3,
    }


@app.get("/api/disk/detail")
def disk_detail(_admin=Depends(require_admin)):
    """分目录明细仅 admin（成员可见磁盘总况 /api/disk，见不到各文件夹构成）。"""
    usage = shutil.disk_usage(str(SHARE_ROOT))
    share_physical, cat_sizes = _scan_share_sizes()
    categories = {name: {"size": size, "size_human": human_size(size)}
                  for name, size in cat_sizes.items()}
    return {
        "total": usage.total, "total_human": human_size(usage.total),
        "used": usage.used, "used_human": human_size(usage.used),
        "free": usage.free, "free_human": human_size(usage.free),
        "warning": usage.free < 15 * 1024**3,
        "share_size": share_physical, "share_size_human": human_size(share_physical),
        "categories": categories,
    }


# ─── 分享链接 ─────────────────────────────────────────────
# 创建/管理需登录；取件公开（分享的意义就是给没登录的人），密码走 POST body。
