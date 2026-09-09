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

from fastapi import Depends, HTTPException, Query, Request

from app.config import SHARE_ROOT
from app.filetools import human_size
from app.security import require_user
from app.spaces import resolve
from app.main import app

NOTE_EXTS = {".md", ".markdown", ".txt", ".html", ".htm"}
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


@app.get("/api/notes/list")
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
    for p in base.rglob("*"):
        if len(items) >= MAX_NOTES:
            break
        try:
            if not p.is_file() or p.suffix.lower() not in NOTE_EXTS:
                continue
            stat = p.stat()
        except OSError:
            continue
        if p.name.startswith(".") or any(part.startswith(".") for part in p.relative_to(base).parts):
            continue
        # 分类 = 笔记目录下相对子路径（''=根/未分类），正斜杠
        rel = p.relative_to(base).as_posix()
        category = rel.rsplit("/", 1)[0] if "/" in rel else ""
        summary = _summary_of(p, p.suffix.lower())
        if needle and needle not in p.name.lower() and needle not in summary.lower():
            continue
        items.append({
            "name": p.name,
            "path": str(p.relative_to(SHARE_ROOT)).replace("\\", "/"),
            "category": category,
            "ext": p.suffix.lower(),
            "size_human": human_size(stat.st_size),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            "mtime": stat.st_mtime,
            "ctime": stat.st_ctime,
            "summary": summary,
        })
    if sort == "name":
        items.sort(key=lambda i: str(i["name"]).lower())
    elif sort == "created":
        items.sort(key=lambda i: -cast(float, i["ctime"]))
    else:
        items.sort(key=lambda i: -cast(float, i["mtime"]))
    # mtime/ctime（秒级 double）保留下发：客户端本地过滤/置顶后需要重排
    return {"total": len(items), "items": items}
