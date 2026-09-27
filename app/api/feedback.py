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

反馈量增长后的定位成本问题（9-11 主人提出）：**目录即状态**——
反馈/待处理/（已提交）、反馈/进行中/（处理中）、反馈/已完成/（细状态在
frontmatter：已修复/已实装/已拒绝/已暂缓）。Agent 领任务 = ls 待处理/，
状态流转 = 移动文件；另由服务端在每次列表读写时自动重生成 反馈/看板.md
（统计+待办清单），一眼全局。看板.md 不进列表、不进治理视图。
"""
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request
from fastapi import UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import FEEDBACK_MAX_ATTACHMENT_MB, SHARE_ROOT
from app.security import (ADMIN, allow_rate, audit, client_ip, require_admin,
                          require_user)
from app.api import events as events_bus
from app.store import users as users_store

router = APIRouter()


class FeedbackAppendBody(BaseModel):
    body: str

FB_DIR = "反馈"
IMG_DIR = "img"
KANBAN = "看板.md"
# 目录即状态（粗三档）：目录名 → 该目录下的默认状态
STATUS_DIRS = {"待处理": "已提交", "进行中": "处理中"}
DONE_DIR = "已完成"
TYPES = ("idea", "bug", "other")
ATTACH_EXTS = (".jpg", ".jpeg", ".png", ".webp",   # 截图四格式（原行为）
               ".txt", ".md")                      # other·e464：文档白名单
_MAX_ATTACHMENTS = 3        # 件数上限（原 _MAX_IMAGES 更名：附件不再只限截图）
_MAX_PER_DAY = 20


def _dir() -> Path:
    d = SHARE_ROOT / FB_DIR
    d.mkdir(exist_ok=True)
    for sub in (*STATUS_DIRS, DONE_DIR):
        (d / sub).mkdir(exist_ok=True)
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


def _find_file(fid: str) -> Path | None:
    """在三个状态子目录里定位反馈文件（状态流转=移动文件，位置不定）。"""
    for sub in STATUS_DIRS:
        p = _dir() / sub / f"{fid}.md"
        if p.is_file():
            return p
    p = _dir() / DONE_DIR / f"{fid}.md"
    return p if p.is_file() else None


def _load_one(fid: str) -> dict | None:
    p = _find_file(fid)
    return _parse(p.read_text(encoding="utf-8", errors="replace")) if p else None


def _list_all() -> list[dict]:
    """递归三个状态子目录；状态 = 目录派生（已完成目录取 frontmatter 细状态），
    新→旧排序。看板.md 在根目录、不在子目录，天然不进列表。"""
    out = []
    for f in sorted(_dir().rglob("*.md"), reverse=True):
        if f.parent == _dir():
            continue  # 根目录散文件（旧布局/看板）不入列表
        mm = _parse(f.read_text(encoding="utf-8", errors="replace"))
        if not mm:
            continue
        sub = f.parent.name
        if sub in STATUS_DIRS:
            mm["状态"] = STATUS_DIRS[sub]
        elif sub == DONE_DIR and not mm.get("状态"):
            mm["状态"] = "已实装"
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
    if len(files) > _MAX_ATTACHMENTS:
        raise HTTPException(status_code=422, detail=f"附件最多 {_MAX_ATTACHMENTS} 个")

    fid = time.strftime("%Y%m%d%H%M%S") + "-" + os.urandom(2).hex()
    now = _now_str()
    limit = FEEDBACK_MAX_ATTACHMENT_MB * 1024 * 1024
    for i, up in enumerate(files, 1):
        ext = Path(up.filename or "s.jpg").suffix.lower()
        if ext not in ATTACH_EXTS:
            ext = ".jpg"   # 白名单外回落按截图存（原行为）；txt/md 保留原后缀
        # 边读边写（v3.67.4 移植自分叉批 P1-⑤）：原 `await up.read()` 把整个
        # 附件读进内存后才校验 25MB 上限——上限形同虚设，登录态请求可用
        # 巨型附件把进程内存打爆。分块读、超限即断、清掉半截文件
        tmp_path = _img_dir() / f"{fid}-{i}{ext}"
        size = 0
        try:
            with tmp_path.open("wb") as out:
                while chunk := await up.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(
                            status_code=422,
                            detail=f"单个附件不能超过 {FEEDBACK_MAX_ATTACHMENT_MB}MB")
                    out.write(chunk)
        except HTTPException:
            tmp_path.unlink(missing_ok=True)
            raise

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
    (_dir() / "待处理" / f"{fid}.md").write_text(md, encoding="utf-8")
    _regen_kanban()
    # SSE 实时信号：新反馈提醒全体在用 admin（B3；信号语义，客户端自行拉列表）
    events_bus.publish_many(users_store.list_active_admin_ids(), {"kind": "feedback"})
    audit("feedback.create", target=title[:40],
          detail=f"type={type} images={len(files)}", ip=client_ip(request))
    return {"ok": True, "id": fid}


def _regen_kanban() -> None:
    """自动重生成 反馈/看板.md：Agent 读写这一份文件即知全局，
    不必逐条通读。每次列表被查询/新反馈提交时触发（几十条规模零开销）。"""
    items = _list_all()
    buckets: dict[str, list[dict]] = {d: [] for d in (*STATUS_DIRS, DONE_DIR)}
    for mm in items:
        if mm["状态"] in STATUS_DIRS.values():
            sub = next(d for d, st in STATUS_DIRS.items() if st == mm["状态"])
        else:
            sub = DONE_DIR
        buckets[sub].append(mm)
    done_stats: dict[str, int] = {}
    for mm in buckets[DONE_DIR]:
        done_stats[mm["状态"]] = done_stats.get(mm["状态"], 0) + 1
    stat_line = " · ".join(
        f"{d} {len(b)}" for d, b in buckets.items())
    done_line = " / ".join(f"{k} {v}" for k, v in sorted(done_stats.items())) or "空"

    def _row(mm: dict) -> str:
        return (f"- [{mm.get('类型', 'other')}·{mm.get('编号', '')}] "
                f"{mm.get('标题', '')}"
                f"（{mm.get('版本', '')} · {mm.get('用户', '')}）")

    lines = [
        "# 反馈看板（服务端自动生成，勿手编；数据源 = 本目录 md 文件）",
        "",
        "> Agent 工作流：领任务看 `待处理/`；开工把文件移入 `进行中/`；",
        "> 完成后移入 `已完成/` 并改 frontmatter 的 状态（已修复/已实装/已拒绝/已暂缓）+ 备注 + 更新。",
        f"> 统计：{stat_line}（已完成细分：{done_line}）",
        "",
    ]
    red = buckets["待处理"]
    lines.append(f"## 🔴 待处理（{len(red)}）")
    lines += [_row(mm) for mm in red] if red else ["（空）"]
    lines.append("")
    prog = buckets["进行中"]
    lines.append(f"## 🟡 进行中（{len(prog)}）")
    lines += [f"- [{mm.get('状态')}] {mm.get('标题', '')}（{mm.get('编号', '')} · 备注：{mm.get('备注', '') or '无'}）"
              for mm in prog] if prog else ["（空）"]
    lines.append("")
    done = buckets[DONE_DIR][:10]
    lines.append(f"## ✅ 已完成（共 {len(buckets[DONE_DIR])}，最近 {len(done)}）")
    lines += [f"- [{mm.get('状态')}] {mm.get('标题', '')}（{mm.get('编号', '')} · 备注：{mm.get('备注', '') or '无'}）"
              for mm in done] if done else ["（空）"]
    (_dir() / KANBAN).write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    _regen_kanban()
    return {"items": items}


_APPEND_LIMIT_PER_DAY = 30


@router.post("/api/feedback/{fid}/append")
def feedback_append(fid: str, payload: FeedbackAppendBody,
                    request: Request, _user=Depends(require_user)):
    """对已有反馈二次补充（20260913225909-e9d1）：正文末尾追加带时间戳的补充段，
    frontmatter 的 更新 刷新。不动 状态/备注——状态流转仍归 Agent 改文件。
    补充在任何状态都允许（已完成后的补充往往最有价值）。"""
    me = users_store.get_username(_user["user_id"])
    if not allow_rate(f"fb-append:{_user['user_id']}", _APPEND_LIMIT_PER_DAY, 86400):
        raise HTTPException(status_code=429, detail="今天补充太频繁了，明天再来")
    text = payload.body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="补充内容不能为空")
    if len(text) > 1000:
        raise HTTPException(status_code=422, detail="补充内容不能超过 1000 字")
    p = _find_file(fid)
    if p is None:
        raise HTTPException(status_code=404, detail="反馈不存在")
    raw = p.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", raw, re.S)
    if not m:
        raise HTTPException(status_code=500, detail="反馈文件格式异常")
    is_admin = _user["role"] == ADMIN
    if not is_admin and m.group(1) and "用户: " in m.group(1):
        owner = next((ln.split(":", 1)[1].strip()
                      for ln in m.group(1).splitlines()
                      if ln.split(":", 1)[0].strip() == "用户"), "")
        if owner != me:
            raise HTTPException(status_code=403, detail="只能补充自己的反馈")
    now = _now_str()
    fm = re.sub(r"^([ \t]*更新[:：]).*$",
                lambda mm: mm.group(1) + " " + now,
                m.group(1), count=1, flags=re.M)
    # 防注入：补充内容里的 "---" 分隔线会破坏 frontmatter 解析边界——统一替换
    safe_text = re.sub(r"\r?\n-{3,}\s*\r?\n?", "\n", text)
    new_body = m.group(2).rstrip() + f"\n\n--- 补充 @ {now} ---\n{safe_text}\n"
    p.write_text(f"---\n{fm}\n---\n\n{new_body}", encoding="utf-8")
    _regen_kanban()
    # 提醒 admin 有新补充（复用 feedback 信号通道；客户端收到后拉列表）
    events_bus.publish_many(users_store.list_active_admin_ids(), {"kind": "feedback"})
    audit("feedback.append", target=fid,
          detail=f"len={len(text)}", ip=client_ip(request))
    return {"ok": True, "updated": now}


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
