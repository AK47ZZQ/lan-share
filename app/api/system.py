"""系统任务：定时清理（tmp/回收站/上传会话/zip/DB）+ 照片时间线 + 通用 QR。"""
import asyncio
import io
import json
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import qrcode
import qrcode.image.svg
from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.config import (CLEANUP_INTERVAL, SHARE_ROOT, TMP_MAX_AGE_DAYS, TRASH_DAYS,
                        UPLOAD_SESSION_TTL, ZIP_TTL)
from app.db import _db, _db_lock
from app.security import audit, preview_user, require_user, require_admin
from app.spaces import resolve, visible
from app.api.files import _chunk_dir, _zips, _zips_lock
from app.api.share import _tickets, _tickets_lock
from app.filetools import ensure_heif_support
from app.main import app

ensure_heif_support()   # HEIC/HEIF 解码注册（照片时间线 EXIF/元数据需要）

@app.on_event("startup")
async def start_cleanup():
    def _run_all():
        _cleanup_tmp()
        _cleanup_trash()
        _cleanup_uploads()
        _cleanup_zips()
        _cleanup_db()

    async def cleanup_loop():
        import logging
        while True:
            # 清理任务含全树 rglob（同步 IO），必须放线程池——直接 await 会周期性
            # 冻住整个事件循环（review 2026-09-02 P2-4）
            # 单轮异常必须吞掉记日志：清理内部对竞态（文件在 iterdir 与 stat/unlink
            # 之间被并发删掉）不设防，一次 OSError 逸出就会杀死 while 循环——
            # 此后到重启为止所有定时清理（回收站过期/上传会话/zip/DB）全部停摆
            try:
                await run_in_threadpool(_run_all)
            except Exception:
                logging.exception("cleanup round failed")
            await asyncio.sleep(CLEANUP_INTERVAL)

    try:
        await run_in_threadpool(_run_all)
    except Exception:
        import logging
        logging.exception("first cleanup round failed")
    asyncio.create_task(cleanup_loop())


def _cleanup_tmp():
    tmp = SHARE_ROOT / "tmp"
    if not tmp.exists():
        return
    cutoff = time.time() - TMP_MAX_AGE_DAYS * 86400
    for f in tmp.rglob("*"):
        try:
            if not f.is_file() or f.stat().st_mtime >= cutoff:
                continue
            f.unlink(missing_ok=True)
            if f.parent != tmp and not any(f.parent.iterdir()):
                f.parent.rmdir()
        except OSError:
            continue   # 竞态（并发删/占用）跳过，下轮再收
    # 孤儿上传临时文件（崩溃残留）：超过 1 天的 .uploading/.dav-uploading/.dedup/.tmp-* 一律回收
    orphan_cutoff = time.time() - 86400
    for f in SHARE_ROOT.rglob("*"):
        if f.is_file() and (f.name.endswith((".uploading", ".dav-uploading", ".dedup"))
                            or f.name.startswith(".tmp-")) \
                and f.stat().st_mtime < orphan_cutoff:
            f.unlink(missing_ok=True)
    # 缩略图缓存：30 天未再生成（即 30 天没被访问命中缺失）的档位回收，防慢性磁盘泄漏
    thumbs = SHARE_ROOT / ".thumbs"
    if thumbs.exists():
        stale = time.time() - 30 * 86400
        for f in thumbs.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < stale:
                    f.unlink(missing_ok=True)
            except OSError:
                continue


def _cleanup_trash():
    trash_root = SHARE_ROOT / ".trash"
    if not trash_root.exists():
        return
    cutoff = time.time() - TRASH_DAYS * 86400
    for f in trash_root.iterdir():
        try:
            if f.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue   # 与用户"清空回收站"并发：条目已消失，下轮自然收敛
        if f.is_dir():
            shutil.rmtree(f, ignore_errors=True)
        else:
            f.unlink(missing_ok=True)
    with _db_lock:
        _db().execute("DELETE FROM trash WHERE deleted_at < ?", (cutoff,))
        _db().commit()


def _cleanup_uploads():
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


def _cleanup_zips():
    zdir = SHARE_ROOT / ".zips"
    if zdir.exists():
        cutoff = time.time() - ZIP_TTL * 2
        for f in zdir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    with _zips_lock:
        now = time.time()
        for k in [k for k, v in _zips.items() if v["exp"] < now]:
            del _zips[k]


def _cleanup_db():
    now = time.time()
    with _db_lock:
        _db().execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        _db().execute("DELETE FROM device_tokens WHERE expires_at < ?", (now,))
        _db().execute("DELETE FROM shares WHERE expires_at < ?", (now,))
        _db().execute("DELETE FROM notifications WHERE read_at IS NOT NULL AND read_at < ?",
                      (now - 30 * 86400,))
        _db().commit()
    with _tickets_lock:
        for k in [k for k, v in _tickets.items() if v["exp"] < now]:
            del _tickets[k]

# ─── 照片时间线 ───────────────────────────────────────────

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
# 视频纳入照片时间线（App v0.6.0）：备份本就支持视频，此前时间线只扫图片，
# 手机备份的视频在照片墙完全不可见。类型由扩展名推导（media_type），不建列。
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".3gp", ".webm", ".avi", ".mkv"}
_last_photo_scan = 0.0
_last_force_scan = 0.0
_photo_scan_lock = threading.Lock()


def _media_type(rel: str) -> str:
    return "video" if Path(rel).suffix.lower() in VIDEO_EXTS else "photo"


_FFPROBE_FALLBACKS = ("C:/env/ffmpeg/bin/ffprobe.exe",)   # NSSM 服务 PATH 常不带 ffmpeg


def _ffprobe_exe() -> str | None:
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    for p in _FFPROBE_FALLBACKS:
        if Path(p).is_file():
            return p
    return None


def _ffprobe_meta(path: Path) -> tuple[float | None, str | None, str | None]:
    """视频元数据：(时长秒, 拍摄日期YYYY-MM-DD, 视频编码)。ffprobe 不可用时静默
    降级（三项留空，App 只显示 ▶ 角标）；date 取容器 creation_time（手机拍摄的
    视频 mtime 常是"拷入时间"，比 EXIF 不可用的图片更需要这一层）；编码取第一
    视频流 codec_name，前端据此对 HEVC 等浏览器播不了的格式给下载兜底提示。"""
    exe = _ffprobe_exe()
    if not exe:
        return None, None, None
    try:
        r = subprocess.run(
            [exe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=15)
        if r.returncode != 0:
            return None, None, None
        data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
        fmt = data.get("format") or {}
        dur = float(fmt["duration"]) if fmt.get("duration") else None
        ct = (fmt.get("tags") or {}).get("creation_time")
        taken = str(ct)[:10] if ct else None
        codec = next((s.get("codec_name") for s in data.get("streams") or []
                      if s.get("codec_type") == "video" and s.get("codec_name")), None)
        return dur, taken, codec
    except Exception:
        return None, None, None


def _probe_video_durations(cap: int = 20):
    """补扫视频行缺失的时长/编码。duration 语义：NULL=待补扫（仅视频），0=非视频/
    已处理。图片行入库即写 0；存量 NULL 图片行在此收编，之后补扫查询不再被图片行
    白拉全表。codec 是 v0.11 新列：存量视频行 duration 已有、codec 为 NULL 的也
    一并纳入补扫（探到啥刷啥，互不阻塞）。两段式更新，PG 友好：CASE 里悬空 NULL
    参数会让 psycopg 报 IndeterminateDatatype；taken_at 仅在有值时单独刷。"""
    rows = _db().execute("SELECT path, duration, codec FROM photos "
                         "WHERE duration IS NULL OR codec IS NULL").fetchall()
    if not rows:
        return
    todo, others = [], []
    for r in rows:
        if Path(r["path"]).suffix.lower() in VIDEO_EXTS:
            todo.append(r["path"])
        elif r["duration"] is None:
            others.append(r["path"])   # codec IS NULL 的图片行不重扫，只收编 duration
    updates = []
    for rel in todo[:cap]:
        f = SHARE_ROOT / rel
        if not f.is_file():
            continue   # 留 NULL：下次全量扫描的 gone 清理会删行
        dur, taken, codec = _ffprobe_meta(f)
        if dur is not None or codec:
            updates.append((dur, taken, codec, rel))
    if not others and not updates:
        return
    with _db_lock:
        if others:
            _db().executemany("UPDATE photos SET duration=0 WHERE path=?",
                              [(p,) for p in others])
        if updates:
            _db().executemany("UPDATE photos SET duration=?, codec=? WHERE path=?",
                              [(d, c, p) for (d, t, c, p) in updates])
            taken_updates = [(t, p) for (d, t, c, p) in updates if t]
            if taken_updates:
                _db().executemany("UPDATE photos SET taken_at=? WHERE path=?",
                                  taken_updates)
        _db().commit()


def _photo_taken_at(f: Path) -> str:
    """优先 EXIF DateTimeOriginal，其次 EXIF DateTime，最后文件修改时间。返回 'YYYY-MM-DD'。"""
    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
    if f.suffix.lower() not in (".jpg", ".jpeg", ".webp", ".bmp", ".heic", ".heif"):
        return mtime
    try:
        from PIL import Image
        with Image.open(f) as img:
            exif = img.getexif()
            raw = exif.get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
            if raw:
                return str(raw)[:10].replace(":", "-")
    except Exception:
        pass
    return mtime


def _scan_photos(force: bool = False):
    """增量扫描图片库：新/变更文件提取拍摄日期，消失的文件清理条目。
    普通请求 60s 内不重复扫；force（客户端已知有新照片，如备份刚完成/
    照片页脉搏轮询）走**独立的 5s 节流**——强刷必须真扫，不被其他客户端
    的普通请求顶掉（0.7.2 前共用一个时间戳，网页正刷照片墙时 App 的
    refresh=1 常被 5s 内的普通命中吞掉，强刷扫了个寂寞）。扫描范围是
    SHARE_ROOT 全盘 rglob，新建文件夹里的照片天然覆盖，无固定目录清单。"""
    global _last_photo_scan, _last_force_scan
    now = time.time()
    if force:
        if now - _last_force_scan < 5:
            return
    else:
        if now - _last_photo_scan < 60:
            return
    with _photo_scan_lock:
        if force:
            if time.time() - _last_force_scan < 5:
                return
        else:
            if time.time() - _last_photo_scan < 60:
                return
        seen = set()
        updates = []
        for f in SHARE_ROOT.rglob("*"):
            rel_parts = f.relative_to(SHARE_ROOT).parts
            if any(p.startswith(".") for p in rel_parts):
                continue
            if not f.is_file() or f.suffix.lower() not in PHOTO_EXTS | VIDEO_EXTS:
                continue
            stat = f.stat()
            rel = str(f.relative_to(SHARE_ROOT)).replace("\\", "/")
            seen.add(rel)
            row = _db().execute("SELECT mtime_ns, size FROM photos WHERE path=?", (rel,)).fetchone()
            if row and row["mtime_ns"] == stat.st_mtime_ns and row["size"] == stat.st_size:
                continue
            # 图片行 duration 写 0（非视频即"已处理"），视频留 NULL 交给补扫；
            # 内容变更（mtime 变）时 duration 一并重置，换过的视频会被重新探测
            updates.append((rel, _photo_taken_at(f), stat.st_mtime_ns, stat.st_size,
                            None if f.suffix.lower() in VIDEO_EXTS else 0))
        with _db_lock:
            if updates:
                _db().executemany(
                    "INSERT INTO photos(path, taken_at, mtime_ns, size, duration) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(path) DO UPDATE SET taken_at=excluded.taken_at, "
                    "mtime_ns=excluded.mtime_ns, size=excluded.size, duration=excluded.duration", updates)
                _db().execute("DELETE FROM photos WHERE mtime_ns=0")
            gone = [r["path"] for r in _db().execute("SELECT path FROM photos").fetchall()
                    if r["path"] not in seen]
            if gone:
                _db().executemany("DELETE FROM photos WHERE path=?", [(g,) for g in gone])
            _db().commit()
        _last_photo_scan = time.time()
        _last_force_scan = time.time()
    # 补扫放扫描锁外：ffprobe 批量最坏 20×15s，不占住扫描锁拖慢时间线刷新
    _probe_video_durations()


def _known_users() -> set[str]:
    return {r["username"] for r in _db().execute("SELECT username FROM users")}


def _photo_uploader(rel: str, users: set[str] | None = None) -> str:
    """拍摄者维度：按路径归属推导（private/<u>/、incoming/<u>/ 归属本人；
    照片/手机相册/<u>/ 为旧版 App 备份落点（0.7.0 前），归属 <u>；
    照片|视频/<u>/<相册>/… 为 0.7.0 新落点——第二段必须命中真实用户名
    才判归属，否则手工整理的二级目录（照片/同学&生活&学校/…）会被误判
    成用户；其余算共享。"""
    parts = rel.split("/")
    if len(parts) > 1 and parts[0].lower() in ("private", "incoming"):
        return parts[1]
    if len(parts) > 3 and parts[0] == "照片" and parts[1] == "手机相册":
        return parts[2]
    if (users and len(parts) > 3 and parts[0] in ("照片", "视频")
            and parts[1] in users):
        return parts[1]
    return "共享空间"


@app.get("/api/photos")
def list_photos(request: Request, limit: int = Query(default=120, le=500),
                offset: int = Query(default=0, ge=0),
                month: str | None = Query(default=None),
                uploader: str | None = Query(default=None),
                media_type: str | None = Query(default=None),
                refresh: bool = Query(default=False),
                _user=Depends(require_user)):
    """照片时间线：按拍摄日期倒序（EXIF 优先，视频取容器 creation_time）。
    可按 month=YYYY-MM 与 uploader、media_type=photo|video 过滤。
    refresh=1 时放宽扫描节流（App 在备份完成后带此参数拉最新）。"""
    user = preview_user(request, _user)   # admin 成员视角预览
    _scan_photos(force=refresh)
    users = _known_users()
    all_rows = _db().execute(
        "SELECT path, taken_at, duration, codec FROM photos ORDER BY taken_at DESC, path").fetchall()
    rows = [r for r in all_rows if visible(user, r["path"].split("/"))]
    if month:
        rows = [r for r in rows if (r["taken_at"] or "")[:7] == month]
    if uploader:
        rows = [r for r in rows if _photo_uploader(r["path"], users) == uploader]
    if media_type in ("photo", "video"):
        rows = [r for r in rows if _media_type(r["path"]) == media_type]
    total = len(rows)
    rows = rows[offset:offset + limit]
    return {
        "total": total,
        "items": [{
            "path": r["path"],
            "name": r["path"].split("/")[-1],
            "taken": r["taken_at"],
            "month": (r["taken_at"] or "")[:7],
            "uploader": _photo_uploader(r["path"], users),
            "media_type": _media_type(r["path"]),
            "duration": r["duration"],
            "codec": r["codec"],
            "thumb": f"/thumb/{r['path']}",
        } for r in rows],
    }


@app.get("/api/photos/facets")
def photo_facets(request: Request, _user=Depends(require_user)):
    """相册筛选维度：月份与拍摄者分布（对当前用户可见范围统计）。"""
    user = preview_user(request, _user)   # admin 成员视角预览
    _scan_photos()
    users = _known_users()
    rows = _db().execute("SELECT path, taken_at FROM photos ORDER BY taken_at DESC").fetchall()
    months: dict[str, int] = {}
    uploaders: dict[str, int] = {}
    for r in rows:
        if not visible(user, r["path"].split("/")):
            continue
        m = (r["taken_at"] or "")[:7]
        if len(m) == 7:
            months[m] = months.get(m, 0) + 1
        u = _photo_uploader(r["path"], users)
        uploaders[u] = uploaders.get(u, 0) + 1
    return {
        "months": [{"month": m, "count": c} for m, c in sorted(months.items(), reverse=True)],
        "uploaders": sorted(
            [{"name": u, "count": c} for u, c in uploaders.items()],
            key=lambda x: -x["count"]),
    }


# ─── 精选专辑（月份集合，管理员维护，全员可见）──────────────

_ALBUMS_KEY = "featured_albums"


def _load_albums() -> list[dict]:
    row = _db().execute("SELECT value FROM settings WHERE key=?", (_ALBUMS_KEY,)).fetchone()
    if not row or not row["value"]:
        return []
    import json
    try:
        data = json.loads(row["value"])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_albums(albums: list[dict]):
    import json
    with _db_lock:
        _db().execute(
            "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_ALBUMS_KEY, json.dumps(albums, ensure_ascii=False)))
        _db().commit()


@app.get("/api/albums")
def list_albums(_user=Depends(require_user)):
    return _load_albums()


class AlbumBody(BaseModel):
    title: str
    month: str


@app.post("/api/admin/albums")
def create_album(body: AlbumBody, _admin=Depends(require_admin)):
    title = body.title.strip()
    if not title or len(title) > 40:
        raise HTTPException(status_code=400, detail="专辑标题需为 1~40 字符")
    if not re.fullmatch(r"\d{4}-\d{2}", body.month):
        raise HTTPException(status_code=400, detail="month 需为 YYYY-MM 格式")
    albums = _load_albums()
    if any(a.get("month") == body.month for a in albums):
        raise HTTPException(status_code=409, detail="该月份已是精选专辑")
    albums.insert(0, {"id": f"al-{time.time_ns()}", "title": title, "month": body.month})
    _save_albums(albums[:50])
    audit("album_create", f"{title} ({body.month})")
    return {"ok": True, "albums": albums}


@app.delete("/api/admin/albums/{album_id}")
def delete_album(album_id: str, _admin=Depends(require_admin)):
    albums = _load_albums()
    remaining = [a for a in albums if a.get("id") != album_id]
    if len(remaining) == len(albums):
        raise HTTPException(status_code=404, detail="专辑不存在")
    _save_albums(remaining)
    audit("album_delete", album_id)
    return {"ok": True}


@app.get("/api/photo/meta")
def photo_meta(request: Request, path: str = Query(..., min_length=1),
               _user=Depends(require_user)):
    """图片元数据：原始尺寸 + 拍摄设备（EXIF）。预览信息面板用。"""
    from app.api.files import IMG_EXTS
    user = preview_user(request, _user)   # 与列表接口一致：admin 视角下按成员身份解析
    target = resolve(user, path, "read")
    if not target.is_file() or target.suffix.lower() not in IMG_EXTS:
        raise HTTPException(status_code=404, detail="Not an image")
    width = height = None
    camera = None
    try:
        from PIL import Image
        with Image.open(target) as im:
            width, height = im.size
            exif = im.getexif()
            make = exif.get(271)
            model = exif.get(272)
            def _s(v):
                return v.decode("utf-8", "ignore").strip() if isinstance(v, bytes) else str(v).strip()
            if model:
                camera = f"{_s(make)} {_s(model)}".strip()
    except Exception:
        pass
    return {"width": width, "height": height, "camera": camera}


# QR 中心品牌云朵（Material cloud_outlined，evenodd 镂空；与 App/启动图标同源）
_QR_LOGO_CLOUD = ("M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 "
                  "2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 "
                  "0-2.64-2.05-4.78-4.65-4.96zM19 18H6c-2.21 0-4-1.79-4-4s1.79-4 4-4"
                  "h.71C7.37 7.69 9.48 6 12 6c3.04 0 5.5 2.46 5.5 5.5v.5H19"
                  "c1.66 0 3 1.34 3 3s-1.34 3-3 3z")


def qr_svg_response(text: str) -> Response:
    """QR SVG + 中心云朵 logo。ECC-Q（25% 冗余）覆盖 28% 中心 logo 可靠扫读；
    嵌套 <svg> 用百分比定位，免去解析模块数。share.py 的分享码同源复用。"""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage,
                      error_correction=qrcode.constants.ERROR_CORRECT_Q)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    logo = ('<svg x="36%" y="36%" width="28%" height="28%" viewBox="0 0 512 512">'
            '<rect width="512" height="512" rx="96" fill="#ffffff"/>'
            f'<path transform="translate(166.4,166.4) scale(7.4667)" '
            f'd="{_QR_LOGO_CLOUD}" fill="#5B6CFF" fill-rule="evenodd"/></svg>')
    svg = svg.replace("</svg>", logo + "</svg>")
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/api/qr")
async def generic_qr(text: str = Query(..., min_length=1, max_length=512),
                     _user=Depends(require_user)):
    """任意文本的 QR 码 SVG（登录后可用，如手机上传页二维码）。"""
    return qr_svg_response(text)


@app.get("/healthz")
def healthz():
    """免认证健康探针（M5）：进程存活 + DB 连通 + 数据目录可写 + 磁盘水位。"""
    import shutil as _shutil
    checks = {"db": False, "data_dir": False}
    try:
        _db().execute("SELECT 1").fetchone()
        checks["db"] = True
    except Exception:
        pass
    try:
        probe = SHARE_ROOT / ".healthz-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["data_dir"] = True
    except Exception:
        pass
    du = _shutil.disk_usage(str(SHARE_ROOT))
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        **checks,
        "disk_free_gb": round(du.free / 2**30, 1),
        "disk_total_gb": round(du.total / 2**30, 1),
        "disk_used_pct": round(du.used / du.total * 100, 1),
    }
