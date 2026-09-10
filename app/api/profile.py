"""用户资料 API（07 小尾巴：从 auth.py 按域拆出）。

昵称只是显示层：登录/路径/权限仍一律用 username；头像按 uid 存进
SHARE_ROOT/.avatars（点前缀目录不进照片扫描与文件列表）。
"""
import io
import os
import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import SHARE_ROOT
from app.security import allow_rate, audit, client_ip, require_user
from app.store import users as users_store
from app.api.auth import uid

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


_NICK_MAX = 32


def _clean_nickname(raw: str) -> str:
    nick = (raw or "").strip()
    if len(nick) > _NICK_MAX:
        raise HTTPException(status_code=422,
                            detail=f"昵称最长 {_NICK_MAX} 个字符")
    if any(ord(c) < 32 for c in nick):
        raise HTTPException(status_code=422, detail="昵称不能包含控制字符")
    return nick


def _avatar_dir():
    d = SHARE_ROOT / ".avatars"
    d.mkdir(parents=True, exist_ok=True)
    return d


class ProfileBody(BaseModel):
    nickname: str


@router.patch("/api/auth/profile")
async def update_profile(body: ProfileBody, request: Request,
                         user=Depends(require_user)):
    nick = _clean_nickname(body.nickname)

    def _save() -> None:
        users_store.set_nickname(uid(user), nick)

    await run_in_threadpool(_save)
    audit("profile_nickname", user["username"], nick or "(清空)",
          client_ip(request))
    return {"ok": True, "nickname": nick}


@router.post("/api/auth/avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...),
                        user=Depends(require_user)):
    """上传/替换头像：PIL 解码（60MP 钳制）→ 中心裁方 → 512px JPEG 落盘。
    旧头像按 uid 固定文件名覆盖，历史版本自然消失。"""
    if not allow_rate(f"avatar:{uid(user)}", 6, 60):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="头像文件不能超过 8MB")
    try:
        from PIL import Image, ImageOps
        # lambda 包裹规避 Image.open 重载让 mypy 推不进线程池的 Callable 形参
        img: Image.Image = await run_in_threadpool(
            lambda: Image.open(io.BytesIO(raw)))
        if (img.width or 0) * (img.height or 0) > 60 * 1024 * 1024:
            raise HTTPException(status_code=415, detail="图片尺寸过大")
        img = await run_in_threadpool(ImageOps.exif_transpose, img)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=415, detail="不是有效的图片文件")
    # 中心裁方后缩到 512：圆形头像框不会变形，单文件 <100KB
    side = min(img.size)

    def _bake() -> bytes:
        import io
        try:
            left = (img.width - side) // 2
            top = (img.height - side) // 2
            sq = img.crop((left, top, left + side, top + side)).convert("RGB")
            sq.thumbnail((512, 512))
            buf = io.BytesIO()
            sq.save(buf, "JPEG", quality=88)
            return buf.getvalue()
        except HTTPException:
            raise
        except Exception:
            # 真正的解码发生在 crop/convert/save（Image.open 只读头）：
            # 头合法但数据流损坏的图在此抛 OSError，须归 415 而非 500
            raise HTTPException(status_code=415, detail="不是有效的图片文件")

    data = await run_in_threadpool(_bake)
    dest = _avatar_dir() / f"{uid(user)}.jpg"

    def _write() -> None:
        # tmp 名唯一化（review P3-1）：固定 <uid>.tmp 在并发上传时交错写
        tmp = dest.with_suffix(
            f".{os.getpid()}-{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        users_store.set_avatar_path(uid(user), rel)

    rel = f".avatars/{uid(user)}.jpg"
    await run_in_threadpool(_write)
    audit("profile_avatar", user["username"], ip=client_ip(request))
    return {"ok": True, "avatar_path": rel}


@router.get("/api/auth/avatar/{username}")
def get_avatar(username: str, _user=Depends(require_user)):
    # def（非 async）：DB 往返与磁盘 stat 进线程池，不阻塞事件循环（项目规范）
    row = users_store.get_avatar_path_by_username(username)
    if row is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if "avatar_path" in row.keys() and row["avatar_path"]:
        f = SHARE_ROOT / row["avatar_path"]
        # 纵深防御（review P3-2）：avatar_path 当前只由端点写入常量，但
        # 一旦列值被污染成越界路径，这里就是登录用户的任意文件读取
        try:
            f.resolve().relative_to(_avatar_dir().resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="暂无头像")
        if f.is_file():
            return FileResponse(f, media_type="image/jpeg",
                                headers={"Cache-Control": "private, max-age=300"})
    raise HTTPException(status_code=404, detail="暂无头像")
