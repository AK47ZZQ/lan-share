"""反馈通道（v0.28/v3.41）：App「我的」页 → data/反馈/ 落盘 → 电脑端 Agent 直接读写。

设计定稿（头脑风暴三轮）：
- 每条反馈一个 md 文件：frontmatter（编号/类型/状态/备注/标题/版本/设备/用户/
  创建/更新）+ 正文。**状态流转由 Agent 在电脑端直接改文件**（状态: 已提交→
  处理中→已修复/已实装/已拒绝/已暂缓，备注写如 "v0.28.0+74 已实装"），
  列表接口即时反映——无需 DB，也不需要状态回写 API。
- 截图存 反馈/img/{id}-{n}.{ext}，经鉴权端点读取（仅本人与 admin）。
- 权限：登录即可提交（成员也能报 bug）；列表只回自己的，admin 全量（带用户名）。
- 防刷：每人每天 20 条（allow_rate）。

frontmatter 用平铺 "键: 值" 行（不引 YAML 依赖），冒号全半角都认。
"""
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request
from fastapi import UploadFile
from fastapi.responses import FileResponse

from app.config import SHARE_ROOT
from app.security import (ADMIN, allow_rate, audit, client_ip, require_admin,
                          require_user)
from app.store import users as users_store

router = APIRouter()

FB_DIR = "反馈"
IMG_DIR = "img"
TYPES = ("idea", "bug", "other")
_MAX_IMAGES = 3
_MAX_PER_DAY = 20


def _dir() -> Path:
    d = SHARE_ROOT / FB_DIR
    d.mkdir(exist_ok=True)
    return d


def _img_dir() -> Path:
    d = _dir() / IMG_DIR
    d.mkdir(exist_ok=True)
    return d


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _parse(text: str) -> dict | None:
    """解析 frontmatter + 正文；缺"编号"行的文件不进列表（容错手工半成品）。"""
    if not text.startswith("---"):
        return None
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    if not m:
        return None
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line and "：" not in line:
            continue
        parts = re.split(r"[:：]", line, maxsplit=1)
        k = parts[0]
        v = parts[1] if len(parts) > 1 else ""
        meta[k.strip()] = v.strip()
    if not meta.get("编号"):
        return None
    meta["body"] = m.group(2).strip()
    return meta


def _load_one(fid: str) -> dict | None:
    f = _dir() / f"{fid}.md"
    if not f.is_file():
        return None
    return _parse(f.read_text(encoding="utf-8", errors="replace"))


def _list_all() -> list[dict]:
    out = []
    for f in sorted(_dir().glob("*.md"), reverse=True):
        mm = _parse(f.read_text(encoding="utf-8", errors="replace"))
        if mm:
            out.append(mm)
    return out


def _images_of(fid: str) -> list[str]:
    d = _dir() / IMG_DIR
    if not d.is_dir():
        return []
    return sorted(x.name for x in d.glob(f"{fid}-*"))


def _to_item(mm: dict, with_body: bool = False) -> dict:
    fid = mm.get("编号", "")
    item = {
        "id": fid,
        "type": mm.get("类型", "other"),
        "title": mm.get("标题", ""),
        "status": mm.get("状态", "已提交"),
        "note": mm.get("备注", ""),
        "app": mm.get("版本", ""),
        "device": mm.get("设备", ""),
        "user": mm.get("用户", ""),
        "created": mm.get("创建", ""),
        "updated": mm.get("更新") or mm.get("创建", ""),
        "images": [f"/api/feedback/{fid}/img/{name}" for name in _images_of(fid)],
    }
    if with_body:
        item["body"] = mm.get("body", "")
    return item


@router.post("/api/feedback")
async def feedback_create(request: Request,
                          title: str = Form(...),
                          type: str = Form("idea"),
                          body: str = Form(""),
                          app_ver: str = Form(""),
                          device: str = Form(""),
                          files: list[UploadFile] = File(default=[]),
                          _user=Depends(require_user)):
    user = users_store.get_username(_user["user_id"]) or f"#{_user['user_id']}"
    if not allow_rate(f"fb:{_user['user_id']}", _MAX_PER_DAY, 86400):
        raise HTTPException(status_code=429, detail="今天反馈太多了，明天再来")
    title = title.strip()
    if not title or len(title) > 80:
        raise HTTPException(status_code=422, detail="标题必填且不超过 80 字")
    if type not in TYPES:
        type = "other"
    if len(files) > _MAX_IMAGES:
        raise HTTPException(status_code=422, detail=f"截图最多 {_MAX_IMAGES} 张")

    fid = time.strftime("%Y%m%d%H%M%S") + "-" + os.urandom(2).hex()
    now = _now_str()
    for i, up in enumerate(files, 1):
        ext = Path(up.filename or "s.jpg").suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        data = await up.read()
        if len(data) > 12 * 1024 * 1024:
            raise HTTPException(status_code=422, detail="单张截图不能超过 12MB")
        (_img_dir() / f"{fid}-{i}{ext}").write_bytes(data)

    md = (
        "---\n"
        f"编号: {fid}\n"
        f"类型: {type}\n"
        f"状态: 已提交\n"
        f"备注:\n"
        f"标题: {title}\n"
        f"版本: {app_ver.strip()[:40]}\n"
        f"设备: {device.strip()[:80]}\n"
        f"用户: {user}\n"
        f"创建: {now}\n"
        f"更新: {now}\n"
        "---\n\n"
        f"{body.strip()[:2000]}\n"
    )
    (_dir() / f"{fid}.md").write_text(md, encoding="utf-8")
    audit("feedback.create", target=title[:40],
          detail=f"type={type} images={len(files)}", ip=client_ip(request))
    return {"ok": True, "id": fid}


@router.get("/api/feedback")
def feedback_list(_user=Depends(require_user)):
    """自己的反馈列表；admin 全量（带用户名，前端按需展示）。"""
    is_admin = _user["role"] == ADMIN
    me = users_store.get_username(_user["user_id"])
    items = []
    for mm in _list_all():
        if not is_admin and mm.get("用户") != me:
            continue
        items.append(_to_item(mm))
    return {"items": items}


@router.get("/api/feedback/{fid}")
def feedback_detail(fid: str, _user=Depends(require_user)):
    mm = _load_one(fid)
    if mm is None:
        raise HTTPException(status_code=404, detail="反馈不存在")
    is_admin = _user["role"] == ADMIN
    me = users_store.get_username(_user["user_id"])
    if not is_admin and mm.get("用户") != me:
        raise HTTPException(status_code=403, detail="只能查看自己的反馈")
    return _to_item(mm, with_body=True)


@router.get("/api/feedback/{fid}/img/{name}")
def feedback_image(fid: str, name: str, _user=Depends(require_user)):
    mm = _load_one(fid)
    if mm is None:
        raise HTTPException(status_code=404, detail="反馈不存在")
    is_admin = _user["role"] == ADMIN
    me = users_store.get_username(_user["user_id"])
    if not is_admin and mm.get("用户") != me:
        raise HTTPException(status_code=403, detail="只能查看自己的反馈截图")
    safe = Path(name).name
    if not re.fullmatch(rf"{re.escape(fid)}-\d+\.(jpg|jpeg|png|webp)", safe):
        raise HTTPException(status_code=400, detail="非法图片名")
    p = _img_dir() / safe
    if not p.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    media = ("image/png" if safe.endswith(".png")
             else "image/webp" if safe.endswith(".webp") else "image/jpeg")
    return FileResponse(p, media_type=media)


@router.get("/api/admin/feedback")
def feedback_admin_list(_admin=Depends(require_admin)):
    """admin 全量视角（含用户名），Web 管理台后续可挂。"""
    return {"items": [_to_item(mm) for mm in _list_all()]}
