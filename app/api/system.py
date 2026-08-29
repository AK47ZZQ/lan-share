"""系统任务：定时清理（tmp/回收站/上传会话/zip/DB）+ 照片时间线 + 通用 QR。"""
import asyncio
import io
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import qrcode
import qrcode.image.svg  # noqa: F401 子模块必须显式导入，SvgPathImage 才存在
from fastapi import Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.config import (CLEANUP_INTERVAL, SHARE_ROOT, TMP_MAX_AGE_DAYS, TRASH_DAYS,
                        UPLOAD_SESSION_TTL, ZIP_TTL)
from app.db import _db, _db_lock
from app.security import audit, require_user, require_admin
from app.spaces import visible
from app.api.files import _chunk_dir, _zips, _zips_lock
from app.api.share import _tickets, _tickets_lock
from app.main import app

@app.on_event("startup")
async def start_cleanup():
    async def cleanup_loop():
        while True:
            _cleanup_tmp()
            _cleanup_trash()
            _cleanup_uploads()
            _cleanup_zips()
            _cleanup_db()
            await asyncio.sleep(CLEANUP_INTERVAL)

    _cleanup_tmp()
    _cleanup_trash()
    _cleanup_uploads()
    _cleanup_zips()
    _cleanup_db()
    asyncio.create_task(cleanup_loop())


def _cleanup_tmp():
    tmp = SHARE_ROOT / "tmp"
    if not tmp.exists():
        return
    cutoff = time.time() - TMP_MAX_AGE_DAYS * 86400
    for f in tmp.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            if f.parent != tmp and not any(f.parent.iterdir()):
                f.parent.rmdir()
    # 孤儿上传临时文件（崩溃残留）：超过 1 天的 .uploading/.dav-uploading 一律回收
    orphan_cutoff = time.time() - 86400
    for f in SHARE_ROOT.rglob("*"):
        if f.is_file() and f.name.endswith((".uploading", ".dav-uploading", ".dedup")) \
                and f.stat().st_mtime < orphan_cutoff:
            f.unlink(missing_ok=True)


def _cleanup_trash():
    trash_root = SHARE_ROOT / ".trash"
    if not trash_root.exists():
        return
    cutoff = time.time() - TRASH_DAYS * 86400
    for f in trash_root.iterdir():
        if f.stat().st_mtime < cutoff:
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
        _db().execute("DELETE FROM shares WHERE expires_at < ?", (now,))
        _db().execute("DELETE FROM notifications WHERE read_at IS NOT NULL AND read_at < ?",
                      (now - 30 * 86400,))
        _db().commit()
    with _tickets_lock:
        for k in [k for k, v in _tickets.items() if v["exp"] < now]:
            del _tickets[k]

# ─── 照片时间线 ───────────────────────────────────────────

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_last_photo_scan = 0.0
_photo_scan_lock = threading.Lock()


def _photo_taken_at(f: Path) -> str:
    """优先 EXIF DateTimeOriginal，其次 EXIF DateTime，最后文件修改时间。返回 'YYYY-MM-DD'。"""
    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
    if f.suffix.lower() not in (".jpg", ".jpeg", ".webp", ".bmp"):
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


def _scan_photos():
    """增量扫描图片库：新/变更文件提取拍摄日期，消失的文件清理条目。60s 内不重复扫。"""
    global _last_photo_scan
    now = time.time()
    if now - _last_photo_scan < 60:
        return
    with _photo_scan_lock:
        if time.time() - _last_photo_scan < 60:
            return
        seen = set()
        updates = []
        for f in SHARE_ROOT.rglob("*"):
            rel_parts = f.relative_to(SHARE_ROOT).parts
            if any(p.startswith(".") for p in rel_parts):
                continue
            if not f.is_file() or f.suffix.lower() not in PHOTO_EXTS:
                continue
            stat = f.stat()
            rel = str(f.relative_to(SHARE_ROOT)).replace("\\", "/")
            seen.add(rel)
            row = _db().execute("SELECT mtime_ns, size FROM photos WHERE path=?", (rel,)).fetchone()
            if row and row["mtime_ns"] == stat.st_mtime_ns and row["size"] == stat.st_size:
                continue
            updates.append((rel, _photo_taken_at(f), stat.st_mtime_ns, stat.st_size))
        with _db_lock:
            if updates:
                _db().executemany(
                    "INSERT INTO photos(path, taken_at, mtime_ns, size) VALUES(?,?,?,?) "
                    "ON CONFLICT(path) DO UPDATE SET taken_at=excluded.taken_at, "
                    "mtime_ns=excluded.mtime_ns, size=excluded.size", updates)
                _db().execute("DELETE FROM photos WHERE mtime_ns=0")
            gone = [r["path"] for r in _db().execute("SELECT path FROM photos").fetchall()
                    if r["path"] not in seen]
            if gone:
                _db().executemany("DELETE FROM photos WHERE path=?", [(g,) for g in gone])
            _db().commit()
        _last_photo_scan = time.time()


def _photo_uploader(rel: str) -> str:
    """拍摄者维度：按路径归属推导（private/<u>/、incoming/<u>/ 归属本人，其余算共享）。"""
    parts = rel.split("/")
    if len(parts) > 1 and parts[0] in ("private", "incoming"):
        return parts[1]
    return "共享空间"


@app.get("/api/photos")
def list_photos(limit: int = Query(default=120, le=500), offset: int = Query(default=0, ge=0),
                month: str | None = Query(default=None),
                uploader: str | None = Query(default=None),
                _user=Depends(require_user)):
    """照片时间线：按拍摄日期倒序（EXIF 优先）。仅显示对当前用户可见的照片。
    可按 month=YYYY-MM 与 uploader 过滤（共享相册双维度浏览）。"""
    _scan_photos()
    all_rows = _db().execute(
        "SELECT path, taken_at FROM photos ORDER BY taken_at DESC, path").fetchall()
    rows = [r for r in all_rows if visible(_user, r["path"].split("/"))]
    if month:
        rows = [r for r in rows if (r["taken_at"] or "")[:7] == month]
    if uploader:
        rows = [r for r in rows if _photo_uploader(r["path"]) == uploader]
    total = len(rows)
    rows = rows[offset:offset + limit]
    return {
        "total": total,
        "items": [{
            "path": r["path"],
            "name": r["path"].split("/")[-1],
            "taken": r["taken_at"],
            "month": (r["taken_at"] or "")[:7],
            "uploader": _photo_uploader(r["path"]),
            "thumb": f"/thumb/{r['path']}",
        } for r in rows],
    }


@app.get("/api/photos/facets")
def photo_facets(_user=Depends(require_user)):
    """相册筛选维度：月份与拍摄者分布（对当前用户可见范围统计）。"""
    _scan_photos()
    rows = _db().execute("SELECT path, taken_at FROM photos ORDER BY taken_at DESC").fetchall()
    months: dict[str, int] = {}
    uploaders: dict[str, int] = {}
    for r in rows:
        if not visible(_user, r["path"].split("/")):
            continue
        m = (r["taken_at"] or "")[:7]
        if len(m) == 7:
            months[m] = months.get(m, 0) + 1
        u = _photo_uploader(r["path"])
        uploaders[u] = uploaders.get(u, 0) + 1
    return {
        "months": [{"month": m, "count": c} for m, c in sorted(months.items(), reverse=True)],
        "uploaders": sorted(
            [{"name": u, "count": c} for u, c in uploaders.items()],
            key=lambda x: -x[1]),
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


@app.get("/api/qr")
async def generic_qr(text: str = Query(..., min_length=1, max_length=512),
                     _user=Depends(require_user)):
    """任意文本的 QR 码 SVG（登录后可用，如手机上传页二维码）。"""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


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
