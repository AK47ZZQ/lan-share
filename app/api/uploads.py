"""分块上传协议 + 直传（07 审查批次 3A：从 files.py 按域拆出）。

init/state/chunk/finish 断点续传全流程、秒传（blob 指纹去重）、multipart 直传。
分片大小协议上限与阶梯见 app.config（单一事实源）。
"""
import hashlib
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.config import (MAX_CHUNK_SIZE, MAX_CHUNKED_SIZE, SHARE_ROOT, UPLOAD_SESSION_TTL,
                        chunk_size_for)
from app.services import cleanup
from app.db import _db, _db_lock
from app.roles import ADMIN
from app.security import audit, client_ip, require_user
from app.filetools import clean_filename, human_size, move_to_trash, save_upload, to_rel
from app.spaces import enforce_quota, resolve, sample_readable
from app.store import users as users_store
from app.api.photos import photos_scan_bump

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


def _session_owner_guard(row, user):
    """分块上传会话只能由创建者（或 admin）续传/收尾。

    ⚠️ fail-closed 收紧（v1.0.0 终审，2026-09-13）：原写法
    `if row["user_id"] and row["user_id"] != user["user_id"] and role != ADMIN`
    在 `user_id` 为 NULL 时**放行任何人**——而该列是**后加的**（schema.py 走
    ALTER TABLE 迁移，列可空），历史行即 NULL；任何登录用户都能续传/收尾这类
    会话（把别人的在途上传写坏或提前 finish 掉，DoS）。实际影响有限（会话短命
    且有陈旧清扫，NULL 行基本不复存在），但授权守卫不该 fail-open：
    **无主会话只许 admin 收尾**（admin 本就全权，且 finish 内 owner 会回落为
    请求者本人，不产生越权写）。"""
    if user["role"] == ADMIN:
        return
    if not row["user_id"] or row["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")


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


# ── 同步 DB 助手（11 号复审批次 3，P2-2）──────────────────────
# 上传四端点都是 async（要 await 请求体流），元数据库访问是同步的：
# 在 async 里直接调 _db() = 事件循环内做 PG 往返，单进程 uvicorn 下阻塞全体请求。
# 统一抽成同步函数、由 run_in_threadpool 执行。
def _session_usage(user_id: int):
    """在途会话数与未完成总量（防配额绕过限流）。"""
    return _db().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(total_size),0) AS total "
        "FROM upload_sessions WHERE user_id=?", (user_id,)).fetchone()


def _get_session(sid: str):
    return _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()


def _get_blob_sample(sha256: str):
    return _db().execute("SELECT sample_path FROM blobs WHERE sha256=?", (sha256,)).fetchone()


def _create_session(sid: str, dest_dir: str, filename: str, total_size: int,
                    sha256: str | None, user_id: int) -> None:
    with _db_lock:
        _db().execute(
            "INSERT INTO upload_sessions(id, dest_dir, filename, total_size, sha256, "
            "created_at, user_id) VALUES(?,?,?,?,?,?,?)",
            (sid, dest_dir, filename, total_size, sha256, time.time(), user_id))
        _db().commit()


def _drop_session(sid: str) -> None:
    with _db_lock:
        _db().execute("DELETE FROM upload_sessions WHERE id=?", (sid,))
        _db().commit()


def _chunk_dir(sid: str) -> Path:
    return SHARE_ROOT / ".chunks" / sid


def sweep_sessions() -> None:
    """过期上传会话回收（TTL 见 UPLOAD_SESSION_TTL）+ 孤儿分块目录清理。
    （原 system._cleanup_uploads，归位到状态所有者；经 cleanup 注册调度）"""
    with _db_lock:
        stale = _db().execute("SELECT id FROM upload_sessions WHERE created_at < ?",
                              (time.time() - UPLOAD_SESSION_TTL,)).fetchall()
        if stale:
            _db().execute("DELETE FROM upload_sessions WHERE created_at < ?",
                          (time.time() - UPLOAD_SESSION_TTL,))
            _db().commit()
    for r in stale:
        shutil.rmtree(_chunk_dir(r["id"]), ignore_errors=True)
    # 孤儿分块目录（无会话记录）
    chunks_root = SHARE_ROOT / ".chunks"
    if chunks_root.exists():
        known = {r["id"] for r in _db().execute("SELECT id FROM upload_sessions").fetchall()}
        for d in chunks_root.iterdir():
            if d.is_dir() and d.name not in known:
                shutil.rmtree(d, ignore_errors=True)


cleanup.register_sweep("upload_sessions", sweep_sessions)

@router.post("/api/upload/init")
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
    row = await run_in_threadpool(_session_usage, _user["user_id"])
    if row["n"] >= 8:
        raise HTTPException(status_code=429, detail="上传会话过多，请先完成或取消在途任务")
    if row["total"] + body.size > MAX_CHUNKED_SIZE:
        raise HTTPException(status_code=429,
                            detail=f"未完成上传总量超限（≤{human_size(MAX_CHUNKED_SIZE)}）")
    enforce_quota(_user, dest, body.size)

    # 秒传：库里有同 sha256 内容（服务端算的，可信），直接硬链接
    if body.sha256:
        row = await run_in_threadpool(_get_blob_sample, body.sha256)
        if row:
            sample = Path(row["sample_path"])
            # 秒传越权防线（对抗审查 CRIT-1，Dedup Oracle）：blobs 是全局表，
            # "服务端存有该内容"≠"当前用户可读"。样例不可读（他人 private/
            # incoming 子树等）就降级走正常分片——攻击者只知道 sha+size，
            # 没有真实字节，finish 时服务端重算 sha 不符必失败，不泄漏内容。
            # 同内容早退分支在可读判定之后，语义不变。
            readable = sample.is_file() and sample_readable(_user, sample)
            if readable:
                # 配额计价必须用样例真实大小（对抗审查 F5）：body.size 是客户端
                # 自报值，与样例内容可以毫无关系——自报小 size 过预检后链接大
                # 样例即绕过配额。overwrite 场景旧 dest 已先进回收站（移出配额
                # 树），此刻计价即落位后的真实占用；quota 拒绝时旧文件在回收站
                # 可找回（与 _dav_put 的计价次序一致）。
                sample_size = sample.stat().st_size
                try:
                    if dest.is_dir():
                        # 与直传/finish 同款守卫（红队 P2-1）：缺了它，秒传会把
                        # 整个同名目录树静默移进回收站再用 1 字节文件顶位
                        raise HTTPException(status_code=409, detail="同名目录已存在")
                    if dest.exists():
                        try:
                            # 同目录重传同名文件：sample 即 dest 本身，挪进回收站
                            # 会导致下面 os.link 找不到源（WinError 2）——已是同
                            # 内容，直接判定秒传完成，不动旧文件（无新增字节，
                            # 不再计价）
                            if os.path.samestat(os.stat(dest), os.stat(sample)):
                                audit("upload_instant", to_rel(dest),
                                      human_size(sample_size), client_ip(request))
                                return {"instant": True, "path": to_rel(dest),
                                        "size": sample_size, "size_human": human_size(sample_size)}
                        except OSError:
                            pass
                        move_to_trash(dest, to_rel(dest))
                    enforce_quota(_user, dest, sample_size)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.link(sample, dest)
                    audit("upload_instant", to_rel(dest),
                          human_size(sample_size), client_ip(request))
                    return {"instant": True, "path": to_rel(dest),
                            "size": sample_size, "size_human": human_size(sample_size)}
                except OSError as e:
                    import logging
                    logging.warning("instant upload hardlink failed: %s", e)

    sid = secrets.token_urlsafe(16)
    await run_in_threadpool(_chunk_dir(sid).mkdir, parents=True, exist_ok=True)
    await run_in_threadpool(_create_session, sid, body.dest_dir, dest.name,
                            body.size, body.sha256, _user["user_id"])
    return {"instant": False, "session": sid, "chunk_size": chunk_size_for(body.size),
            "max_chunk_size": MAX_CHUNK_SIZE, "received": []}


@router.get("/api/upload/state/{sid}")
async def upload_state(sid: str, _user=Depends(require_user)):
    """断点续传：查询已收到的块序号。"""
    row = await run_in_threadpool(_get_session, sid)
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    _session_owner_guard(row, _user)
    d = _chunk_dir(sid)
    received = sorted(int(p.stem.split("-")[1]) for p in d.glob("part-*")) if d.exists() else []
    return {"session": sid, "chunk_size": chunk_size_for(row["total_size"]),
            "max_chunk_size": MAX_CHUNK_SIZE, "received": received}


@router.post("/api/upload/chunk/{sid}")
async def upload_chunk(request: Request, sid: str, index: int = Query(...),
                       _user=Depends(require_user)):
    row = await run_in_threadpool(_get_session, sid)
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
    重新 init，重缴会话数/总量/配额三道闸，R1-P2-6）。同步函数，调用方自行
    决定是否进线程池。"""
    shutil.rmtree(d, ignore_errors=True)   # 1024 分片上限，亚秒级
    _drop_session(sid)


@router.post("/api/upload/finish/{sid}")
async def upload_finish(request: Request, sid: str, _user=Depends(require_user)):
    row = await run_in_threadpool(_get_session, sid)
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
        owner = users_store.get(row["user_id"])
        if not owner:
            raise HTTPException(status_code=403, detail="Forbidden")
    dest_dir = resolve(owner, row["dest_dir"] or "", "write")
    dest = dest_dir / row["filename"]
    if dest.is_dir():
        # 与直传同款守卫：同名目录存在时不能拿文件去顶替（否则整个目录被静默移进回收站）
        raise HTTPException(status_code=409, detail="同名目录已存在")
    # 重组前配额预检（2026-09-13 审查 U1）：重组要写出满额 tmp——配额缩水/
    # 并发写满后注定超限的 finish 不该白烧最长几十分钟重组 IO 与双倍磁盘
    # （超水位期这是现实约束）；落位前的正式校验仍保留（配额 used 可在此
    # 间隙被并发改变，双保险）
    enforce_quota(owner, dest, row["total_size"])
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

    try:
        enforce_quota(owner, dest, size)
    except BaseException:
        # 重组后的校验失败同样要清 tmp（U1b）：此前 409 直接上抛，
        # .uploading-* 残留私人目录最长 1 天才被孤儿回收——配额超限者
        # 磁盘占用翻倍（水位期不可接受）
        tmp.unlink(missing_ok=True)
        raise
    # 落位前重查（TOCTOU 收窄，红队 P2）：分片重组可达数十分钟，期间同名
    # 目录可能被并发建出——目录绝不能被静默移进回收站
    if dest.is_dir():
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="同名目录已存在")
    if dest.exists():  # 覆盖上传：旧版本进回收站，不直接丢失
        move_to_trash(dest, to_rel(dest))
    os.replace(tmp, dest)
    deduped = register_blob(dest, real_sha, size)
    shutil.rmtree(d, ignore_errors=True)
    await run_in_threadpool(_drop_session, sid)
    rel = to_rel(dest)
    audit("upload", rel, human_size(size), client_ip(request))
    # v0.20：新文件落盘后清零照片扫描节流——下一次照片页请求必扫，
    # 二维码/网页/App 上传的图片不再等 60s 普通节流
    photos_scan_bump()
    return {"ok": True, "path": rel, "size": size, "size_human": human_size(size),
            "sha256": real_sha, "deduped": deduped}
@router.post("/api/upload/{path:path}")
@router.post("/api/upload")
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
    photos_scan_bump()
    return {"ok": True, "deduped": deduped, **result}
