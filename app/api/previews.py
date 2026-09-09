"""文件预览与缩略图（07 审查批次 3A：从 files.py 按域拆出）。

/preview（含 ?w= 中图档量化）、/thumb（PIL/ffmpeg，ETag 协商缓存）、
像素炸弹拦截。媒体扩展名集合引用 filetools 单一事实源。
"""
import hashlib
import mimetypes
import os
import subprocess
import threading

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.config import SHARE_ROOT
from app.security import require_user
from app.filetools import IMG_EXTS, VID_EXTS, ensure_heif_support
from app.spaces import resolve

ensure_heif_support()   # HEIC/HEIF 解码注册（iPhone 默认照片格式）

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


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


@router.get("/preview/{path:path}")
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
                    img: Image.Image = Image.open(target)
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
# ─── 缩略图 ───────────────────────────────────────────────
# IMG_EXTS/VID_EXTS 单一事实源在 filetools（07 审查 §5.1-3），与照片时间线共用


@router.get("/thumb/{path:path}")
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
