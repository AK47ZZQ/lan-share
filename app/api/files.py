"""文件 API：列表/注册指纹/分块上传/直传/建目录/移动/回收站/预览/zip/重命名/缩略图/搜索/磁盘。"""
import hashlib
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import (Depends, File, HTTPException, Query, Request, UploadFile,
                     Response)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.config import (MAX_CHUNKED_SIZE, TRASH_DAYS, ZIP_TTL, SHARE_ROOT, chunk_size_for)
from app.db import _db, _db_lock
from app.security import audit, client_ip, preview_user, require_user, require_admin
from app.filetools import (_file_dict, clean_filename, dir_tree, ensure_heif_support,
                           human_size, move_to_trash, save_upload)
from app.spaces import enforce_quota, resolve, sample_readable, sync_grants_on_move, visible
from app.main import app

ensure_heif_support()   # HEIC/HEIF 解码注册（iPhone 默认照片格式）


def _session_owner_guard(row, user):
    """分块上传会话只能由创建者（或 admin）续传/收尾。"""
    if row["user_id"] and row["user_id"] != user["user_id"] and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")


def _is_own_private_root(user, target: Path) -> bool:
    """目标是否是请求者本人的私人空间根目录（该目录不可删除/重命名/移出）。"""
    try:
        rel = target.relative_to(SHARE_ROOT)
    except ValueError:
        return False
    return bool(rel.parts and rel.parts[0].lower() == "private" and len(rel.parts) == 2
                and rel.parts[1].lower() == user["username"].lower())


def _under_private(path: Path, username: str) -> bool:
    try:
        rel = path.relative_to(SHARE_ROOT)
    except ValueError:
        return False
    return bool(rel.parts and rel.parts[0].lower() == "private"
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

@app.get("/files/{path:path}")
@app.get("/files")
def download_file(path: str = "", _user=Depends(require_user)):
    """静态直连下载（Caddy /files/* 反代至此）：与 /preview 同样走 resolve() 判权。
    Caddyfile 不再直接 file_server 挂 SHARE_ROOT（越权旁路，review 2026-09-02 P0-1）；
    FileResponse 的 filename 参数自带 Content-Disposition: attachment。"""
    target = resolve(_user, path, "read")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="目录请在界面打包下载")
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=mime or "application/octet-stream",
                        filename=target.name)


@app.get("/api/files/{path:path}")
@app.get("/api/files")
def list_files(request: Request, path: str = "", _user=Depends(require_user)):
    user = preview_user(request, _user)   # admin 成员视角预览（X-As-User，只读）
    target = resolve(user, path, "read")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Directory not found")
    if target.is_file():
        raise HTTPException(status_code=400, detail="Not a directory")
    tree = dir_tree(target)
    # 根目录的 private 入口显示为"私人空间"（点击进入的是本人私人目录）
    if target == SHARE_ROOT:
        for item in tree["items"]:
            if item["name"] == "private" and item["is_dir"]:
                item["name"] = "🔒 私人空间"
                item["is_private_root"] = True
    return tree


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
    if body.size > MAX_CHUNKED_SIZE:
        raise HTTPException(status_code=413,
                            detail=f"File too large for chunked upload (max {human_size(MAX_CHUNKED_SIZE)})")
    # 分片真实字节落在 .chunks（配额扫描视野外）：不限流的话一个成员开 N 个
    # 64GB 会话即可绕过配额灌满磁盘（红队 P2）——并发会话与未完成总量双上限
    row = _db().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(total_size),0) AS total "
        "FROM upload_sessions WHERE user_id=?",
        (_user["user_id"],)).fetchone()
    if row["n"] >= 8:
        raise HTTPException(status_code=429, detail="上传会话过多，请先完成或取消在途任务")
    if row["total"] + body.size > MAX_CHUNKED_SIZE:
        raise HTTPException(status_code=429,
                            detail=f"未完成上传总量超限（≤{human_size(MAX_CHUNKED_SIZE)}）")
    enforce_quota(_user, dest, body.size)

    # 秒传：库里有同 sha256 内容（服务端算的，可信），直接硬链接
    if body.sha256:
        row = _db().execute("SELECT sample_path FROM blobs WHERE sha256=?", (body.sha256,)).fetchone()
        if row:
            sample = Path(row["sample_path"])
            # 秒传越权防线（对抗审查 CRIT-1，Dedup Oracle）：blobs 是全局表，
            # "服务端存有该内容"≠"当前用户可读"。样例不可读（他人 private/
            # incoming 子树等）就降级走正常分片——攻击者只知道 sha+size，
            # 没有真实字节，finish 时服务端重算 sha 不符必失败，不泄漏内容。
            # 同内容早退分支在可读判定之后，语义不变。
            readable = sample.is_file() and sample_readable(_user, sample)
            if readable:
                try:
                    if dest.is_dir():
                        # 与直传/finish 同款守卫（红队 P2-1）：缺了它，秒传会把
                        # 整个同名目录树静默移进回收站再用 1 字节文件顶位
                        raise HTTPException(status_code=409, detail="同名目录已存在")
                    if dest.exists():
                        try:
                            # 同目录重传同名文件：sample 即 dest 本身，挪进回收站
                            # 会导致下面 os.link 找不到源（WinError 2）——已是同
                            # 内容，直接判定秒传完成，不动旧文件
                            if os.path.samestat(os.stat(dest), os.stat(sample)):
                                audit("upload_instant", str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
                                      human_size(body.size), client_ip(request))
                                return {"instant": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
                                        "size": body.size, "size_human": human_size(body.size)}
                        except OSError:
                            pass
                        move_to_trash(dest, str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"))
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.link(sample, dest)
                    audit("upload_instant", str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
                          human_size(body.size), client_ip(request))
                    return {"instant": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
                            "size": body.size, "size_human": human_size(body.size)}
                except OSError as e:
                    import logging
                    logging.warning("instant upload hardlink failed: %s", e)

    sid = secrets.token_urlsafe(16)
    _chunk_dir(sid).mkdir(parents=True, exist_ok=True)
    with _db_lock:
        _db().execute(
            "INSERT INTO upload_sessions(id, dest_dir, filename, total_size, sha256, "
            "created_at, user_id) VALUES(?,?,?,?,?,?,?)",
            (sid, body.dest_dir, dest.name, body.size, body.sha256, time.time(),
             _user["user_id"]))
        _db().commit()
    return {"instant": False, "session": sid, "chunk_size": chunk_size_for(body.size), "received": []}


@app.get("/api/upload/state/{sid}")
async def upload_state(sid: str, _user=Depends(require_user)):
    """断点续传：查询已收到的块序号。"""
    row = _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    _session_owner_guard(row, _user)
    d = _chunk_dir(sid)
    received = sorted(int(p.stem.split("-")[1]) for p in d.glob("part-*")) if d.exists() else []
    return {"session": sid, "chunk_size": chunk_size_for(row["total_size"]), "received": received}


@app.post("/api/upload/chunk/{sid}")
async def upload_chunk(request: Request, sid: str, index: int = Query(...),
                       _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    _session_owner_guard(row, _user)
    cs = chunk_size_for(row["total_size"])
    total = (row["total_size"] + cs - 1) // cs
    if index < 0 or index >= total:
        raise HTTPException(status_code=400, detail="Chunk index out of range")
    part = _chunk_dir(sid) / f"part-{index:06d}"
    # 原子写：客户端中断（暂停/断网）时只留下 _tmp_*（不匹配 part-*，续传不会误信半截分片）；
    # 临时名带唯一后缀，同分片并发重试不会交错写坏
    tmp = _chunk_dir(sid) / f"_tmp_{index:06d}-{os.getpid()}-{time.time_ns()}"
    expected = min(cs, row["total_size"] - index * cs)
    size = 0
    overflow = False
    try:
        async with aiofiles.open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > expected:
                    # 超发立即停止接收（不再把整块写完才比对），防滥用端点灌盘
                    overflow = True
                    break
                await f.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if overflow or size != expected:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400,
                            detail=f"Chunk {index}: got {size} bytes, expected {expected}")
    os.replace(tmp, part)
    return {"ok": True, "index": index}


def _destroy_upload_session(sid: str, d: Path):
    """销毁分片会话：分片目录 + DB 记录一并清除（finish 校验失败强制客户端
    重新 init，重缴会话数/总量/配额三道闸，R1-P2-6）。"""
    shutil.rmtree(d, ignore_errors=True)   # 1024 分片上限，亚秒级；调用方异步上下文
    with _db_lock:
        _db().execute("DELETE FROM upload_sessions WHERE id=?", (sid,))
        _db().commit()


@app.post("/api/upload/finish/{sid}")
async def upload_finish(request: Request, sid: str, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    _session_owner_guard(row, _user)
    d = _chunk_dir(sid)
    cs = chunk_size_for(row["total_size"])
    total = (row["total_size"] + cs - 1) // cs
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
    if dest.is_dir():
        # 与直传同款守卫：同名目录存在时不能拿文件去顶替（否则整个目录被静默移进回收站）
        raise HTTPException(status_code=409, detail="同名目录已存在")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.uploading-{os.getpid()}-{time.time_ns()}")

    size = 0
    sha = hashlib.sha256()
    try:
        async with aiofiles.open(tmp, "wb") as out:
            for part in parts:
                async with aiofiles.open(part, "rb") as f:
                    while chunk := await f.read(1024 * 1024):
                        size += len(chunk)
                        sha.update(chunk)
                        await out.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if size != row["total_size"]:
        tmp.unlink(missing_ok=True)
        _destroy_upload_session(sid, d)
        raise HTTPException(status_code=400, detail="Size mismatch")

    real_sha = sha.hexdigest()
    if row["sha256"] and row["sha256"] != real_sha:
        tmp.unlink(missing_ok=True)
        # 会话与全部分片一并销毁（R1-P2-6）：仅删 tmp 时会话保留，攻击者可
        # init 一个 64GB 会话后循环 finish 触发反复全量重组+哈希（磁盘 IO DoS）
        _destroy_upload_session(sid, d)
        raise HTTPException(status_code=400, detail="SHA256 mismatch, please re-upload")

    enforce_quota(owner, dest, size)
    # 落位前重查（TOCTOU 收窄，红队 P2）：分片重组可达数十分钟，期间同名
    # 目录可能被并发建出——目录绝不能被静默移进回收站
    if dest.is_dir():
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="同名目录已存在")
    if dest.exists():  # 覆盖上传：旧版本进回收站，不直接丢失
        move_to_trash(dest, str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"))
    os.replace(tmp, dest)
    deduped = register_blob(dest, real_sha, size)
    shutil.rmtree(d, ignore_errors=True)
    with _db_lock:
        _db().execute("DELETE FROM upload_sessions WHERE id=?", (sid,))
        _db().commit()
    rel = str(dest.relative_to(SHARE_ROOT)).replace("\\", "/")
    audit("upload", rel, human_size(size), client_ip(request))
    # v0.20：新文件落盘后清零照片扫描节流——下一次照片页请求必扫，
    # 二维码/网页/App 上传的图片不再等 60s 普通节流
    from app.api.system import photos_scan_bump
    photos_scan_bump()
    return {"ok": True, "path": rel, "size": size, "size_human": human_size(size),
            "sha256": real_sha, "deduped": deduped}


@app.post("/api/upload/{path:path}")
@app.post("/api/upload")
async def upload_file(request: Request, path: str = "", file: UploadFile = File(...),
                      on_dup: str = Query(default="trash"),
                      _user=Depends(require_user)):
    if on_dup not in ("trash", "overwrite"):
        raise HTTPException(status_code=400, detail="on_dup 仅支持 trash/overwrite")
    clean_name = clean_filename(file.filename)
    dest = resolve(_user, path, "write") / clean_name
    # 真实配额校验移入 save_upload 落位前（R1-P1-2）：starlette multipart 的
    # file.size 恒为 0，预检 `file.size or MAX_UPLOAD_SIZE` 会把所有直传按 2GB
    # 计价，配额 <2GB 的成员任何直传必 409
    result = await save_upload(file, dest, quota_user=_user, on_dup=on_dup)
    deduped = register_blob(dest, result["sha256"], result["size"])
    audit("upload", result["path"], result["size_human"], client_ip(request))
    from app.api.system import photos_scan_bump
    photos_scan_bump()
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
            if _is_own_private_root(_user, target):
                raise HTTPException(status_code=400, detail="私人空间根目录不可移动")
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
            # 重名序号与资源管理器一致：首个冲突即 "name (1)"（0.6.0 起修正，
            # 此前从 (2) 起跳）
            # 目录判定必须在 rename 之前——rename 之后旧路径已不存在，
            # is_dir() 恒 False，授权同步会变成永不执行的死代码
            #（review 2026-09-03 P1：移动目录后 grants.path 全部失联）
            was_dir = target.is_dir()
            try:
                old_rel = str(target.relative_to(SHARE_ROOT)).replace("\\", "/")
            except ValueError:
                old_rel = ""
            placed = None
            final = dest_dir / target.name
            for cand in [final] + [
                    final.with_name(f"{final.stem} ({i}){final.suffix}")
                    for i in range(1, 999)]:
                try:
                    os.rename(target, cand)
                    placed = cand
                    break
                except FileExistsError:
                    continue
            if placed is None:
                raise HTTPException(status_code=409, detail="无法生成不重名的目标名")
            final_rel = str(placed.relative_to(SHARE_ROOT)).replace("\\", "/")
            if was_dir:
                sync_grants_on_move(old_rel, final_rel)
            results.append({"src": src, "ok": True, "dest": final_rel})
            audit("move", old_rel or src, f"→ {final_rel}", client_ip(request))
        except HTTPException as e:
            results.append({"src": src, "ok": False, "error": e.detail})
        except OSError as e:
            results.append({"src": src, "ok": False, "error": f"系统错误（{type(e).__name__}）"})
    return {"ok": True, "results": results}


def _move_to_trash(target: Path, orig_path: str):
    """兼容别名：实现已下沉 filetools.move_to_trash（webdav/system 从此处导入）。"""
    move_to_trash(target, orig_path)


@app.post("/api/copy")
def copy_items(request: Request, body: MoveBody, _user=Depends(require_user)):
    """批量复制到目标目录（App v0.6.0 文件管理补齐）。与 move 的差别：
    复制不删源——源只需读权；文件 copy2、目录 copytree；重名自动 "name (n)"。
    同步 def 跑线程池：copytree 大目录耗时，不阻塞事件循环。"""
    if not body.sources:
        raise HTTPException(status_code=400, detail="未选择要复制的文件")
    if len(body.sources) > 500:
        raise HTTPException(status_code=400, detail="单次最多复制 500 项")
    dest_dir = resolve(_user, body.dest_dir, "write")
    if not dest_dir.is_dir():
        raise HTTPException(status_code=404, detail="目标目录不存在")

    def _tree_size(p: Path) -> int:
        if p.is_dir():
            total = 0
            for f in p.rglob("*"):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    continue
            return total
        return p.stat().st_size

    incoming = 0
    for src in body.sources:
        try:
            # 定价走 resolve 后路径：裸 src 可写 ".." 等越界形态，
            # 直接 rglob 会拿用户输入当文件系统路径遍历（IO 放大 + 配额虚高）
            incoming += _tree_size(resolve(_user, src, "read"))
        except (HTTPException, OSError):
            pass   # 源缺失/无权由下方逐项检查报错
    enforce_quota(_user, dest_dir, incoming)
    results = []
    for src in body.sources:
        try:
            target = resolve(_user, src, "read")
            if not target.exists():
                raise HTTPException(status_code=404, detail="源文件不存在")
            if target == dest_dir or (target.is_dir() and target in dest_dir.parents):
                raise HTTPException(status_code=400, detail="不能复制到自身的子目录")
            final = dest_dir / target.name
            placed = None
            if not final.exists():
                placed = final
            else:
                for i in range(1, 1000):
                    cand = final.with_name(f"{final.stem} ({i}){final.suffix}")
                    if not cand.exists():
                        placed = cand
                        break
            if placed is None:
                raise HTTPException(status_code=409, detail="无法生成不重名的目标名")
            if target.is_dir():
                shutil.copytree(target, placed)
            else:
                shutil.copy2(target, placed)
            final_rel = str(placed.relative_to(SHARE_ROOT)).replace("\\", "/")
            results.append({"src": src, "ok": True, "dest": final_rel})
            audit("copy", str(target.relative_to(SHARE_ROOT)).replace("\\", "/"),
                  f"→ {final_rel}", client_ip(request))
        except HTTPException as e:
            results.append({"src": src, "ok": False, "error": e.detail})
        except OSError as e:
            results.append({"src": src, "ok": False, "error": f"系统错误（{type(e).__name__}）"})
    return {"ok": True, "results": results}


@app.delete("/api/files/{path:path}")
async def delete_file(request: Request, path: str, _user=Depends(require_user)):
    target = resolve(_user, path, "write")
    if _is_own_private_root(_user, target):
        raise HTTPException(status_code=400, detail="私人空间根目录不可删除（里面的文件可以删）")
    if target == SHARE_ROOT:
        raise HTTPException(status_code=400, detail="不能删除根目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    # 回收站登记物理相对路径（此前存请求原样 vpath，visible() 无法判定归属）
    _move_to_trash(target, str(target.relative_to(SHARE_ROOT)).replace("\\", "/"))
    # 审计同样记物理相对路径（R1-P3-1）：vpath 形态（private:x.jpg）进动态流
    # 后 visible() 无法归一，会泄漏到全员可见
    audit("trash", str(target.relative_to(SHARE_ROOT)).replace("\\", "/"), ip=client_ip(request))
    return {"ok": True}


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


@app.get("/api/trash")
async def list_trash(_user=Depends(require_user)):
    rows = _db().execute("SELECT * FROM trash ORDER BY deleted_at DESC").fetchall()
    is_admin = _user["role"] == "admin"
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


@app.post("/api/trash/{trash_id}/restore")
async def restore_trash(request: Request, trash_id: int, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    # 与删除入口同款可见性校验（review 2026-09-02 P1-1）：缺了它，成员可枚举
    # trash id 把他人 private/incoming 的回收站条目"恢复"进自己空间
    parts = _trash_rel_parts(row["orig_path"])
    if parts is None:
        if _user["role"] != "admin":
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
    audit("restore", str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"), ip=client_ip(request))
    return {"ok": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/")}


@app.delete("/api/trash/{trash_id}")
async def delete_trash_item(request: Request, trash_id: int, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    parts = _trash_rel_parts(row["orig_path"])
    if parts is None:
        if _user["role"] != "admin":
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


@app.delete("/api/trash")
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


# 中图档宽度阶梯：?w= 量化到最近的档位，防止任意 w 值撑爆缩略图缓存
PREVIEW_W_LADDER = (320, 480, 640, 960, 1280, 1600, 2048, 2560, 3200, 3840)

# 像素数上限（红队 P1-2 解压炸弹）：PIL 默认只在 >358MP 时才抛错，
# 178-358MP 区间全量解码进内存（330MP RGBA ≈ 1.3GB）——一张 PNG 可打瘫
# 服务端。Image.open 只读 header，宽高检查不触发解码
_IMG_PIXEL_CAP = 60 * 1024 * 1024


def _reject_pixel_bomb(img) -> None:
    if (img.width or 0) * (img.height or 0) > _IMG_PIXEL_CAP:
        raise HTTPException(status_code=415, detail="图片尺寸过大，无法生成预览")


def _snap_w(w: int) -> int:
    for step in PREVIEW_W_LADDER:
        if w <= step:
            return step
    return PREVIEW_W_LADDER[-1]


@app.get("/preview/{path:path}")
async def preview_file(path: str, w: int | None = Query(default=None),
                       _user=Depends(require_user)):
    """文件预览。图片可带 ?w=<宽> 取按宽缩放的中档位（缓存于 .thumbs/，
    w 量化到固定阶梯），公网/移动端省流量；缩放超过中档位后前端自行改拉原图。"""
    target = resolve(_user, path, "read")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    mime, _ = mimetypes.guess_type(str(target))
    # filename 走 starlette 的 RFC5987 编码（中文文件名直接拼头会炸 latin-1）
    if w and target.suffix.lower() in IMG_EXTS:
        w = _snap_w(max(320, min(int(w), 3840)))
        stat = target.stat()
        key = hashlib.md5(
            f"{path}:{stat.st_mtime_ns}:{stat.st_size}:w{w}".encode()).hexdigest()
        cache_dir = SHARE_ROOT / ".thumbs"
        cache_dir.mkdir(exist_ok=True)
        cached = cache_dir / f"{key}.jpg"
        if not cached.exists():
            # PIL 解码/EXIF 矫正/缩放是 CPU 密集同步操作，大图会跑数秒，
            # 必须进线程池，否则照片墙冷缓存时冻结整个事件循环
            def _render_thumb() -> None:
                from PIL import Image, ImageOps
                tmp = cache_dir / f".tmp-{os.getpid()}-{threading.get_ident()}-{key}.jpg"
                try:
                    img = Image.open(target)
                    _reject_pixel_bomb(img)
                    img = ImageOps.exif_transpose(img)   # iPhone 照片靠 EXIF 标记旋转，缩放前矫正
                    img.thumbnail((w, w))
                    img.convert("RGB").save(tmp, "JPEG", quality=82)
                    os.replace(tmp, cached)
                except HTTPException:
                    tmp.unlink(missing_ok=True)
                    raise   # 415 炸弹拦截要保留原状态码，不被吞成 500
                except Exception:
                    tmp.unlink(missing_ok=True)
                    raise HTTPException(status_code=500, detail="Preview error")
            await run_in_threadpool(_render_thumb)
        return FileResponse(cached, media_type="image/jpeg")
    return FileResponse(target, media_type=mime or "application/octet-stream",
                        filename=target.name)


# ─── 批量打包下载 / 重命名 ─────────────────────────────────

class ZipBody(BaseModel):
    paths: list[str]


_zips: dict[str, dict] = {}
_zips_lock = threading.Lock()
_zip_busy: set[str] = set()   # 在途打包的用户名（红队 P2：打包与全局线程池/磁盘共用，
                              # 不限并发则单成员可打瘫全站 + 30min 回收窗内灌满磁盘）


@app.post("/api/zip")
def create_zip(request: Request, body: ZipBody, _user=Depends(require_user)):
    """打包选中文件/目录为 zip，返回短时效下载 URL。"""
    if not body.paths or len(body.paths) > 500:
        raise HTTPException(status_code=400, detail="Invalid paths")
    with _zips_lock:
        if _user["username"] in _zip_busy:
            raise HTTPException(status_code=429, detail="已有打包任务在进行，请稍候")
        _zip_busy.add(_user["username"])
    try:
        targets = [resolve(_user, p, "read") for p in body.paths]
        for t in targets:
            if not t.exists():
                raise HTTPException(status_code=404, detail=f"Not found: {t.name}")

        # 磁盘余量预计价（红队 P2-2）：打包是整树复制且 .zips 不在配额视野内，
        # 串行循环可把磁盘打满；拒绝"预计打包量 > 剩余空间 - 5GB 余量"的请求
        total_in = 0
        for t in targets:
            if t.is_dir():
                for f in t.rglob("*"):
                    try:
                        if f.is_file():
                            total_in += f.stat().st_size
                    except OSError:
                        continue
            else:
                total_in += t.stat().st_size
        free = shutil.disk_usage(str(SHARE_ROOT)).free
        if total_in > free - 5 * 1024 ** 3:
            raise HTTPException(status_code=507,
                                detail="磁盘剩余空间不足，无法打包（需为系统保留 5GB 余量）")

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
    finally:
        with _zips_lock:
            _zip_busy.discard(_user["username"])


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
    if _is_own_private_root(_user, target):
        raise HTTPException(status_code=400, detail="私人空间根目录不可重命名")
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
    try:
        target.rename(dest)
    except FileExistsError:
        raise HTTPException(status_code=400, detail="目标名称已存在")
    except OSError:
        raise HTTPException(status_code=500, detail="重命名失败（目标被占用或系统错误）")
    if old_rel:
        sync_grants_on_move(old_rel, str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"))
    audit("rename", old_rel or body.path, body.new_name, client_ip(request))
    return {"ok": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/")}


# ─── 缩略图 ───────────────────────────────────────────────

IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif"}
VID_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


@app.get("/thumb/{path:path}")
def thumb_file(request: Request, path: str, _user=Depends(require_user)):
    """缩略图（同步端点，跑线程池）。图片用 Pillow，视频用 ffmpeg 抽帧，缓存于 .thumbs/。
    ETag 协商缓存：缓存键含 mtime+size ⇒ 内容寻址，文件被覆盖后浏览器缓存
    自然失效（Caddy 对 /thumb/* 放开 no-store 才能让浏览器吃到 304）。"""
    from PIL import Image
    target = resolve(_user, path, "read")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    ext = target.suffix.lower()
    if ext not in IMG_EXTS | VID_EXTS:
        raise HTTPException(status_code=404, detail="No thumbnail for this type")

    stat = target.stat()
    key = hashlib.md5(f"{path}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()
    etag = f'W/"{key}"'
    # Cache-Control 由 Caddy 对 /thumb/* 统一设置（private, no-cache），
    # 这里只负责 ETag 与 304——两边都设会出重复头
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    cache_dir = SHARE_ROOT / ".thumbs"
    cache_dir.mkdir(exist_ok=True)
    cached = cache_dir / f"{key}.jpg"
    resp_headers = {"ETag": etag}
    if cached.exists():
        return FileResponse(cached, media_type="image/jpeg", headers=resp_headers)

    tmp = cache_dir / f".tmp-{os.getpid()}-{threading.get_ident()}-{key}.jpg"
    try:
        if ext in IMG_EXTS:
            from PIL import Image, ImageOps
            img = Image.open(target)
            _reject_pixel_bomb(img)   # 同 preview：解压炸弹拦截（红队 P1-2）
            # JPEG draft 解码：直接按 1/2、1/4… 尺寸解码再缩放，大图首生成快数倍
            try:
                img.draft("JPEG", (320, 320))
            except (AttributeError, ValueError):
                pass
            rotated = ImageOps.exif_transpose(img)   # 同 preview：EXIF 方向矫正后再缩放
            rotated.thumbnail((320, 320))
            rotated.convert("RGB").save(tmp, "JPEG", quality=80)
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
    return FileResponse(cached, media_type="image/jpeg", headers=resp_headers)


@app.get("/api/search")
def search_files(q: str = Query(..., min_length=1), _user=Depends(require_user)):
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
def recent_files(limit: int = Query(default=20, ge=0, le=100), _user=Depends(require_user)):
    files = []
    for f in SHARE_ROOT.rglob("*"):
        rel_parts = f.relative_to(SHARE_ROOT).parts
        if not visible(_user, rel_parts):
            continue
        if f.is_file():
            files.append(_file_dict(f))
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files[:limit]


# 网盘全量扫描 5 分钟 TTL 缓存（R1-P2-11）：10 万文件级库上每次 /api/disk
# 全树 rglob+stat 数秒；仪表盘/存储详情是高频页面，容忍 5 分钟内的统计滞后
_scan_cache: dict = {"ts": 0.0, "physical": 0, "cats": {}}
_SCAN_TTL = 300.0


def _scan_share_sizes():
    """网盘内容统计：返回 (去重后的物理占用, 各一级目录/根文件的逻辑大小)。
    NTFS 硬链接去重的同一份内容按物理只计一次；隐藏（点前缀）路径全部排除。"""
    if _scan_cache["ts"] and time.time() - _scan_cache["ts"] < _SCAN_TTL:
        return _scan_cache["physical"], _scan_cache["cats"]
    seen = set()
    physical = 0
    categories: dict[str, int] = {}
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
    _scan_cache.update(ts=time.time(), physical=physical, cats=categories)
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
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "share_size": human_size(share_physical),
        "warning": usage.free < 15 * 1024**3,
    }


@app.get("/api/disk/detail")
def disk_detail(user=Depends(require_user)):
    """存储详情：磁盘总况全员可见；分目录构成仅 admin；
    成员返回自己的配额用量（private 空间逻辑字节）。"""
    usage = shutil.disk_usage(str(SHARE_ROOT))
    share_physical, cat_sizes = _scan_share_sizes()
    resp = {
        "total": usage.total, "total_human": human_size(usage.total),
        "used": usage.used, "used_human": human_size(usage.used),
        "free": usage.free, "free_human": human_size(usage.free),
        "warning": usage.free < 15 * 1024**3,
        "share_size": share_physical, "share_size_human": human_size(share_physical),
        "categories": None, "private_used": None, "quota_bytes": None,
    }
    if user["role"] == "admin":
        resp["categories"] = {name: {"size": size, "size_human": human_size(size)}
                              for name, size in cat_sizes.items()}
    else:
        used = 0
        own = SHARE_ROOT / "private" / user["username"]
        if own.exists():
            for f in own.rglob("*"):
                if f.is_file():
                    try:
                        used += f.stat().st_size
                    except OSError:
                        continue
        resp["private_used"] = used
        try:
            resp["quota_bytes"] = user["quota_bytes"]
        except (IndexError, KeyError):
            resp["quota_bytes"] = None
    return resp


# ─── 分享链接 ─────────────────────────────────────────────
# 创建/管理需登录；取件公开（分享的意义就是给没登录的人），密码走 POST body。
