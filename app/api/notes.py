"""笔记专用聚合列表（改进建议 9.6-P1-7 方案 B）。

笔记 = 网盘里的 .md/.txt/.html 普通文件，分类 = 笔记目录下的子文件夹
（嵌套多层）。本模块只提供"带摘要的一次性列表"：App 笔记页一次请求
拿到全部笔记（含分类归属与前 200 字摘要），而新建/重命名/移动/删除
分类与笔记仍复用 files 通道（mkdir/rename/move/delete）——不另立一套
分类存储，Web 端文件页看到的就是同样的目录结构，两套视图永不漂移。

- 摘要：md/txt 取正文前 200 字符；html 先去标签再取（自包含单文件里
  base64 图片很大，只读文件头 8KB 足够，不整读大文件）。
- 搜索 q：命中文件名或内容前 8KB（大小写不敏感）。
- 数量护栏：单目录树最多聚合 2000 篇，防止误选网盘根目录时全盘扫描。
"""
import html as _html
import re
import time
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.config import SHARE_ROOT
from app.filetools import NOTE_EXTS, human_size, to_rel
from app.security import require_user
from app.spaces import resolve, visible

MAX_NOTES = 2000
_SUMMARY_LEN = 200
_HEAD_BYTES = 8 * 1024

_tag_re = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_any_tag_re = re.compile(r"<[^>]+>")
_ws_re = re.compile(r"\s+")


def _summary_of(p, ext: str) -> str:
    """取前 200 字符纯文本摘要；读不到/非 UTF-8 也不让列表 500。"""
    try:
        with open(p, "rb") as f:
            head = f.read(_HEAD_BYTES)
    except OSError:
        return ""
    text = head.decode("utf-8", errors="ignore")
    if ext in (".html", ".htm"):
        text = _tag_re.sub(" ", text)          # script/style 整段去掉
        text = _any_tag_re.sub(" ", text)
        text = _html.unescape(text)
    return _ws_re.sub(" ", text).strip()[:_SUMMARY_LEN]


# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装（07 审查批次 2 消循环）
router = APIRouter()


@router.get("/api/notes/list")
def list_notes(request: Request, path: str = Query(..., min_length=1),
               q: str = Query(default="", max_length=80),
               sort: str = Query(default="modified"),
               _user=Depends(require_user)):
    if sort not in ("modified", "created", "name"):
        raise HTTPException(status_code=400, detail="sort 仅支持 modified/created/name")
    base = resolve(_user, path, "read")
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="笔记目录不存在")
    needle = q.strip().lower()
    items: list[dict[str, object]] = []
    dirs: set[str] = set()      # 分类目录（相对笔记根，正斜杠；含**空目录**）
    for p in base.rglob("*"):
        if len(items) >= MAX_NOTES:
            break
        rel = p.relative_to(base).as_posix()
        try:
            if p.is_dir():
                # 分类目录单独收集（v3.37）：此前分类树只从笔记的 category 字段
                # 聚合——**空分类没有任何笔记，侧栏永远看不到**（实测踩到：
                # 新建分类成功、文件夹也在，笔记侧栏却"没反应"）。
                if rel in ("", "."):
                    continue
                if p.name.startswith(".") or any(
                        part.startswith(".") for part in p.relative_to(base).parts):
                    continue
                dirs.add(rel)
                continue
            if not p.is_file() or p.suffix.lower() not in NOTE_EXTS:
                continue
            stat = p.stat()
        except OSError:
            continue
        if p.name.startswith(".") or any(part.startswith(".") for part in p.relative_to(base).parts):
            continue
        # 可见性过滤（红队 2026-09-10 V1 同款收口）：path=/ 时根会覆盖他人
        # private/incoming——没有这道闸，摘要就泄露他人私人笔记正文
        share_rel = p.relative_to(SHARE_ROOT)
        if not visible(_user, share_rel.parts):
            continue
        # 分类 = 笔记目录下相对子路径（''=根/未分类），正斜杠
        category = rel.rsplit("/", 1)[0] if "/" in rel else ""
        summary = _summary_of(p, p.suffix.lower())
        if needle and needle not in p.name.lower() and needle not in summary.lower():
            continue
        items.append({
            "name": p.name,
            "path": to_rel(p),
            "category": category,
            "ext": p.suffix.lower(),
            "size_human": human_size(stat.st_size),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            "mtime": stat.st_mtime,
            "ctime": stat.st_ctime,
            "summary": summary,
            # 服务器真实存储路径（v3.37，与 files 列表同语义）：手机端"复制路径"
            # 用——跨端与台式 Agent 沟通时直接给绝对路径
            "abs_path": str(p).replace("\\", "/"),
        })
    # 分类清单：路径 + 名称 + **后代笔记数**（含空分类，count=0）。
    # 空分类也下发是本次修复的核心：客户端分类树按"有笔记"聚合会漏掉新建的空分类。
    categories = []
    for d in sorted(dirs):
        cnt = sum(1 for it in items
                  if str(it["category"]) == d
                  or str(it["category"]).startswith(f"{d}/"))
        categories.append({
            "path": d,
            "name": d.rsplit("/", 1)[-1],
            "count": cnt,
        })
    if sort == "name":
        items.sort(key=lambda i: str(i["name"]).lower())
    elif sort == "created":
        items.sort(key=lambda i: -cast(float, i["ctime"]))
    else:
        items.sort(key=lambda i: -cast(float, i["mtime"]))
    # mtime/ctime（秒级 double）保留下发：客户端本地过滤/置顶后需要重排
    return {"total": len(items), "items": items, "categories": categories}
