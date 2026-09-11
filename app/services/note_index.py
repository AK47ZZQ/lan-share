"""笔记正文全文索引（B4）：网盘 .md/.txt/.html 的维护型小写镜像。

## 为什么是"惰性增量扫描"而非写端点挂钩子

笔记就是网盘里的普通文件（见 api/notes.py 模型说明），新建/编辑/删除/改名/
移动全部复用 files 通用通道——没有"保存"这个可挂钩子的语义点；在 upload/
rename/move/delete 四个端点分别挂增量更新既侵入又极易漏（move 的路径同步、
半写文件的 mtime 竞态）。这里沿用 photos 扫描的成熟模式：/api/search 触发时
按 mtime_ns+size 比对增量 upsert、消失删行——**收敛性等价于写时更新**（一次
请求内先增量再查询，读到的永远是新状态），且天然覆盖一切写路径。

## 边界

- 索引范围：NOTE_EXTS 文件、隐藏目录排除、单轮上限 MAX_NOTES（同 notes 聚合
  护栏），正文读头 8KB（与笔记摘要同深读，html 去标签后镜像——搜索命中的是
  可读文本而非标记噪声）。
- SQL 一律走 app/store/note_search.py（表主人）；本模块只做文件遍历与镜像。
- 越权铁律：/api/search 的笔记命中按 visible(user, parts) 过滤后才返回。
"""
import html as _html
import re
import threading
import time

from app.config import SHARE_ROOT
from app.filetools import NOTE_EXTS, to_rel
from app.spaces import visible
from app.store import note_search as note_search_store

MAX_NOTES = 2000
_HEAD_BYTES = 8 * 1024
_SNIPPET_CTX = 60          # 命中片段上下文字符数（前后各 60）
_scan_lock = threading.Lock()

_tag_re = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_any_tag_re = re.compile(r"<[^>]+>")
_ws_re = re.compile(r"\s+")


def _mirror_text(raw: bytes, ext: str) -> str:
    """正文 → 去空白小写镜像（html 先去标签/反转义）。"""
    text = raw.decode("utf-8", errors="ignore")
    if ext in (".html", ".htm"):
        text = _tag_re.sub(" ", text)
        text = _any_tag_re.sub(" ", text)
        text = _html.unescape(text)
    return _ws_re.sub(" ", text).strip().lower()


def refresh_index() -> dict:
    """增量维护索引：新/变更 upsert、消失删行。返回统计（测试/巡检用）。"""
    with _scan_lock:
        known = {r["path"]: (r["mtime_ns"], r["size"])
                 for r in note_search_store.load_index()}
        seen: set[str] = set()
        upserts = []
        for f in SHARE_ROOT.rglob("*"):
            rel_parts = f.relative_to(SHARE_ROOT).parts
            if any(p.startswith(".") for p in rel_parts):
                continue
            if not f.is_file() or f.suffix.lower() not in NOTE_EXTS:
                continue
            if len(seen) >= MAX_NOTES:
                break
            try:
                st = f.stat()
                with open(f, "rb") as fp:
                    head = fp.read(_HEAD_BYTES)
            except OSError:
                continue
            rel = to_rel(f)
            seen.add(rel)
            if rel in known and known[rel] == (st.st_mtime_ns, st.st_size):
                continue
            upserts.append((rel, _mirror_text(head, f.suffix.lower()),
                            st.st_mtime_ns, st.st_size))
        gone = [p for p in known if p not in seen]
        note_search_store.apply_scan(upserts, gone)
        return {"indexed": len(seen), "updated": len(upserts), "gone": len(gone)}


def search_notes(user, q: str, limit: int = 20) -> list[dict]:
    """笔记正文全文命中（大小写不敏感）。返回 file_dict 兼容形状 +
    kind="note" + snippet。可见性过滤在返回前收口（越权铁律）。"""
    refresh_index()
    needle = q.strip().lower()
    if not needle:
        return []
    rows = note_search_store.search_snippets(needle, limit * 3)   # 余量给可见性过滤
    out = []
    for r in rows:
        rel = r["path"]
        parts = rel.split("/")
        if not visible(user, parts):
            continue
        pos = r["content_lower"].find(needle)
        start = max(0, pos - _SNIPPET_CTX)
        snippet = ("…" if start else "") + r["content_lower"][start:pos + len(needle) + _SNIPPET_CTX]
        try:
            st = (SHARE_ROOT / rel).stat()
        except OSError:
            continue
        out.append({
            "name": parts[-1],
            "path": rel,
            "size": st.st_size,
            "size_human": _human(st.st_size),
            "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
            "ext": "." + rel.rsplit(".", 1)[-1].lower() if "." in parts[-1] else "",
            "kind": "note",
            "snippet": snippet,
        })
        if len(out) >= limit:
            break
    return out


def _human(n: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"
