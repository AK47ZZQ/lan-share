"""知识库引擎（M-K0）：双链解析 + 图谱索引 + 服务端预布局。

设计规格：data/笔记/知识库与图谱功能设计方案（待确认）.md（v2 定稿）。
知识库 = 笔记的第三种视图（方案 A）：本模块只索引现有 .md/.txt/.html 文件，
零新存储；图谱根 = 调用方传入的目录，多库天然支持。

关键决定（审查定稿）：
- 缓存失效指纹 = sha256(排序后的 (相对路径, size, mtime_ns) 列表)——
  rename/move 不改 mtime 与文件数，"max(mtime)+文件数"抓不住改名。
- 同名/大小写：全部 casefold 匹配（服务端跑 Windows/NTFS）；撞名裁决
  同目录 > 最短相对路径 > 字典序，确定性保证同库同解析。
- PyYAML safe_load 后必须归一化：date/datetime → ISO 字符串（否则炸 JSON
  序列化/指纹），tags/aliases 非列表 → 包成列表。
- 布局持久化在 <root>/.kb/layout.json（点前缀，notes/files/本引擎扫描都跳过），
  重建增量初始化：老节点不动、新节点落邻居旁，杜绝"加一篇全场洗牌"。
- 幽灵节点只从"无路径分隔符的未命中目标"生成（链接文本本就在可读笔记里，
  无泄露面）；带路径的目标：根内解析，根外文件出口处过 visible() 才建外链
  节点（外链暴露"根外文件存在性"，必须按请求者过滤），否则整体丢弃。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from sqlite3 import Row
from typing import Any

import yaml
from fastapi import HTTPException

from app.config import SHARE_ROOT
from app.filetools import NOTE_EXTS
from app.spaces import visible

MAX_NODES = 2000
_SUMMARY_LEN = 160          # 节点摘要长度（App 卡片/agent 上下文共用）
_FM_LIMIT = 64 * 1024       # frontmatter 识别上限（顺带盖住 YAML anchor 炸弹）
_SCAN_LIMIT = 2 * 1024 * 1024   # 单文件链接扫描上限（防病态大文件拖垮重建）
_CTX_LIMIT = 20 * 1024      # context 端点全文上限
_EXCERPT_LEN = 140          # 反链摘录长度
_CACHE_CAP = 4              # 进程内图谱缓存 LRU 上限（path 由调用方传，键空间无界）
_LAYOUT_DIR = ".kb"
_LAYOUT_FILE = "layout.json"

# ─── 解析正则 ─────────────────────────────────────────────
# [[目标]] / [[目标#锚]] / [[目标|别名]] / [[目标#锚|别名]]；目标不含 []|# 与换行
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]+?)(?:#[^\[\]|]*?)?(?:\|([^\[\]\n]+?))?\]\]")
# 标准 md 链接指向本地笔记（排除 URL/绝对路径；含空格或括号的路径不支持——
# 与 Obsidian 的宽容度一致，外部导入库的极少数场景）
_MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\((?!https?://)([^()\s]+?\.(?:md|markdown|txt))\)",
                         re.IGNORECASE)
# 内联标签：行首或空白后的 #词（\w 含 CJK）；URL 片段因前置非空白天然不命中
_TAG_RE = re.compile(r"(?:^|(?<=\s))#([\w/\-]+)", re.MULTILINE)

Node = dict[str, Any]
Edge = list[Any]  # [src_id, dst_id, weight]


# ─── frontmatter 解析与归一化 ─────────────────────────────

def _as_str_list(v: Any) -> list[str]:
    """tags/aliases 归一化：str 按逗号拆（YAML 单值形态），列表逐项转字符串。"""
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    if isinstance(v, (list, tuple)):
        out: list[str] = []
        for item in v:
            if isinstance(item, (date, datetime)):
                out.append(item.isoformat())
            elif isinstance(item, (str, int, float, bool)):
                out.append(str(item))
        return out
    return []


def _fm_span(text: str) -> tuple[int, int] | None:
    """frontmatter 字节区间（含起止线），无/畸形 → None。"""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    after = end + 4
    if after < len(text) and text[after] not in "\r\n":
        return None   # "----" 之类不是 frontmatter 结束线
    return (0, after)


def _fm_suspicious(raw: str) -> bool:
    """病态 YAML 预检：连续内联括号深度 >64 直接跳过解析。

    合法 frontmatter 不会有 64 层内联嵌套；5000 层 `[[[[…` 的解析要花
    秒级 CPU（RecursionError 才停），2000 文件最坏会把构建锁占住半小时。
    """
    run = 0
    for ch in raw:
        if ch in "[{":
            run += 1
            if run > 64:
                return True
        elif ch in "]}":
            run = 0
    return False


def parse_frontmatter(text: str) -> dict[str, Any]:
    """解析文件头 YAML frontmatter；缺失/损坏/非 dict 一律按无元数据处理。

    归一化（审查 P1-3）：YAML 隐式类型是实操坑——`created: 2026-09-10` 出来是
    date 对象、`tags: 学习方法` 出来是 str；不归一化会在序列化/比较时炸。
    """
    span = _fm_span(text)
    if span is None:
        return {}
    raw = text[3:span[1]].rsplit("\n---", 1)[0].strip("\r\n")[:_FM_LIMIT]
    if _fm_suspicious(raw):
        return {}
    try:
        data = yaml.safe_load(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in data.items():
        key = str(k)
        if isinstance(v, (date, datetime)):
            out[key] = v.isoformat()
        elif key in ("tags", "aliases"):
            out[key] = _as_str_list(v)
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[key] = v
        # 嵌套 dict/list 不进图谱元数据（约定层只承诺标量字段）
    return out


def _strip_fm(text: str) -> str:
    span = _fm_span(text)
    if span is None:
        return text
    return text[span[1]:].lstrip("\r\n")


def extract_tags(fm: dict[str, Any], body: str) -> list[str]:
    """frontmatter tags + 内联 #标签，去重保序。"""
    tags: list[str] = []
    for t in fm.get("tags", []):
        if isinstance(t, str) and t and t not in tags:
            tags.append(t)
    for m in _TAG_RE.finditer(body):
        t = m.group(1).rstrip("/-_")
        if t and t not in tags:
            tags.append(t)
    return tags


def extract_links(body: str) -> tuple[list[str], list[str]]:
    """→ (wikilink 目标列表[锚/别名已剥], md 相对链接目标列表)。"""
    links = [m.group(1).strip() for m in _WIKILINK_RE.finditer(body)]
    mdlinks = [m.group(1) for m in _MD_LINK_RE.finditer(body)]
    return [lnk for lnk in links if lnk], mdlinks


def _summary_of(body: str) -> str:
    return re.sub(r"\s+", " ", body).strip()[:_SUMMARY_LEN]


# ─── 扫描 ─────────────────────────────────────────────────

class _Entry:
    """单个笔记文件的扫描产物（建图第一遍的中间态）。"""
    __slots__ = ("abs", "rel_root", "rel_share", "name", "stem", "category",
                 "ext", "fm", "tags", "aliases", "links", "summary")

    def __init__(self, abs_: Path, rel_root: str, rel_share: str, name: str,
                 category: str, ext: str):
        self.abs = abs_
        self.rel_root = rel_root       # 相对图谱根（posix）
        self.rel_share = rel_share     # 相对 SHARE_ROOT（posix，节点 id/path）
        self.name = name
        self.stem = name.rsplit(".", 1)[0] if "." in name else name
        self.category = category       # 相对图谱根的父目录，''=根
        self.ext = ext
        self.fm: dict[str, Any] = {}
        self.tags: list[str] = []
        self.aliases: list[str] = []
        self.links: list[str] = []
        self.summary = ""


class KbTooMany(Exception):
    """笔记数量超护栏（API 层转 400，明确报错不静默截断）。"""


def _read_head(p: Path, limit: int) -> str:
    try:
        with open(p, "rb") as f:
            data = f.read(limit)
    except OSError:
        return ""
    return data.decode("utf-8", errors="ignore")


def _read_bytes_head(p: Path, limit: int) -> bytes:
    try:
        with open(p, "rb") as f:
            return f.read(limit)
    except OSError:
        return b""


def _scan(root: Path) -> list[_Entry]:
    """rglob 扫描笔记文件；隐藏目录/文件跳过（.kb 布局文件天然不可见）。"""
    entries: list[_Entry] = []
    for p in root.rglob("*"):
        try:
            if not p.is_file() or p.suffix.lower() not in NOTE_EXTS:
                continue
            rel = p.relative_to(root)
        except (OSError, ValueError):
            continue
        parts = rel.parts
        if any(seg.startswith(".") for seg in parts):
            continue
        if len(entries) >= MAX_NODES:
            raise KbTooMany()
        rel_root = rel.as_posix()
        rel_share = (root.relative_to(SHARE_ROOT) / rel).as_posix()
        category = rel_root.rsplit("/", 1)[0] if "/" in rel_root else ""
        e = _Entry(p, rel_root, rel_share, p.name, category, p.suffix.lower())
        if e.ext in (".md", ".markdown", ".txt"):
            text = _read_head(p, _SCAN_LIMIT)
            e.fm = parse_frontmatter(text)
            body = _strip_fm(text)
            wikilinks, mdlinks = extract_links(body)
            e.links = wikilinks + mdlinks
            e.tags = extract_tags(e.fm, body)
            e.summary = _summary_of(body)
            al = e.fm.get("aliases")
            if isinstance(al, list):
                e.aliases = [str(a) for a in al]
        # .html/.htm：一个字节不读（自包含单文件里 base64 图片动辄几 MB），
        # 链接/摘要/元数据留空，仅作节点
        entries.append(e)
    return entries


def _scan_dirs(root: Path) -> list[str]:
    """分类目录清单（相对图谱根，正斜杠，含**空目录**，隐藏目录跳过）。

    独立于笔记扫描：空分类没有任何笔记文件，只看 entries 会漏掉——
    mkdir 后图谱/侧栏看不到新分类（实测踩到，指纹已含目录故缓存会正确失效）。
    """
    out: list[str] = []
    for p in root.rglob("*"):
        try:
            if not p.is_dir():
                continue
            rel = p.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if rel in ("", "."):
            continue
        if any(seg.startswith(".") for seg in rel.split("/")):
            continue
        out.append(rel)
    out.sort()
    return out


def _fingerprint(root: Path) -> str:
    """失效指纹：排序 (relpath, size, mtime_ns) 列表哈希。

    统计笔记口径内的文件**与分类目录**（与 _scan 同过滤）——rename/move 改
    relpath、编辑改 mtime、增删改数量，全覆盖；非笔记文件变化与图谱无关，
    不掺和。目录必须计入（v3.37）：分类枢纽节点入图后，**空分类**的建/删/改名
    不改变任何笔记文件，漏掉目录会导致图谱不重建、新分类永远不出现（实测踩到）。
    """
    rows: list[str] = []
    for p in root.rglob("*"):
        try:
            rel = p.relative_to(root)
            if rel.as_posix() in ("", "."):
                continue
            parts = rel.parts
            if any(seg.startswith(".") for seg in parts):
                continue
            if p.is_dir():
                rows.append(f"d\x00{rel.as_posix()}")
                continue
            if p.suffix.lower() not in NOTE_EXTS:
                continue
            st = p.stat()
        except (OSError, ValueError):
            continue
        rows.append(f"{rel.as_posix()}\x00{st.st_size}\x00{st.st_mtime_ns}")
    rows.sort()
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


# ─── 建图 ─────────────────────────────────────────────────

class _Built:
    """构建产物（缓存持有）。graph 为用户无关主体（外链边单列）；ext_cand
    外链候选与 ghost_srcs 幽灵溯源都在出口按请求者可见性过滤——
    外链暴露"根外文件存在性"、幽灵在根=SHARE_ROOT 时可能源自他人私人
    笔记（红队 V1/V2），两者都必须按 visible() 收口。"""
    __slots__ = ("fp", "graph", "excerpts", "ext_edges", "ext_cand", "ghost_srcs",
                 "folders", "folder_members", "root_rel")

    def __init__(self, fp: str, graph: dict[str, Any],
                 excerpts: dict[tuple[str, str], str],
                 ext_edges: list[Edge], ext_cand: dict[str, str],
                 ghost_srcs: dict[str, list[str]],
                 folders: list[dict[str, Any]] | None = None,
                 folder_members: dict[str, list[str]] | None = None,
                 root_rel: str = ""):
        self.fp = fp
        self.graph = graph
        self.excerpts = excerpts    # (目标 id, 源 id) → 摘录
        self.ext_edges = ext_edges  # [源 id, ext id, weight]
        self.ext_cand = ext_cand    # ext id → 根外文件相对 SHARE_ROOT 路径
        self.ghost_srcs = ghost_srcs  # ghost id → 链接它的源节点 id 列表
        # v3.37 分类枢纽：folder id → 全部后代笔记 id（可见性判定用：
        # 任一后代可见 ⇒ 该分类节点可见，父级随子级自动可见）
        self.folder_members = folder_members or {}
        self.folders = folders or []
        # 图谱根相对 SHARE_ROOT 的路径（""=根本身）——空分类枢纽的可见性判定用
        self.root_rel = root_rel


def _pick_entry(cands: list[_Entry], src: _Entry) -> _Entry:
    """撞名裁决：同目录 > 最短相对路径 > 字典序（审查 P1-2，确定性）。"""
    same = [c for c in cands if c.category == src.category]
    pool = same if same else cands
    return min(pool, key=lambda c: (len(c.rel_root), c.rel_root.casefold()))


def _norm_rel(t: str) -> str:
    t = t.replace("\\", "/").strip()
    while t.startswith("./"):
        t = t[2:]
    return t


def _share_rel_of(root: Path, joined: str) -> str | None:
    """根内相对路径（允许 ../ 越根）→ SHARE_ROOT 相对 posix；越出 SHARE_ROOT
    或目标不是文件 → None。外链候选的唯一出口（存在性判定在此，可见性在出口）。"""
    phys = Path(os.path.normpath(str(root / joined)))
    try:
        rel = phys.resolve().relative_to(SHARE_ROOT.resolve())
    except (OSError, ValueError):
        return None
    if not phys.is_file():
        return None
    return rel.as_posix()


def _resolve_target(
    raw: str, src: _Entry, root: Path,
    by_path: dict[str, int], by_name: dict[str, list[_Entry]],
    by_alias: dict[str, list[_Entry]],
) -> tuple[str, Any] | None:
    """解析一个链接目标 → ("node", idx) | ("ghost", name) | ("ext", share_rel) | None。

    顺序（定稿）：aliases → 文件名（撞名裁决）→ 相对路径（相对源目录 → 相对根）。
    带路径分隔符的目标不产生 ghost（未命中即外链候选/丢弃），防根外探测。
    """
    t = _norm_rel(raw)
    if not t:
        return None
    cf = t.casefold()
    if "/" not in t:
        cands = by_alias.get(cf)
        if cands:
            return ("node", by_path[_pick_entry(cands, src).rel_root.casefold()])
        cands = by_name.get(cf)
        if cands:
            return ("node", by_path[_pick_entry(cands, src).rel_root.casefold()])
        return ("ghost", t)
    # 带路径：先根内路径匹配（相对源目录 → 相对根）
    for base in (src.category, ""):
        joined = f"{base}/{t}" if base else t
        norm = os.path.normpath(joined).replace("\\", "/")
        if norm.startswith("..") or os.path.isabs(norm):
            continue
        idx = by_path.get(norm.casefold())
        if idx is not None:
            return ("node", idx)
    # 根外存在性：候选必须是 SHARE_ROOT 内的真实文件（越界丢弃），出口再过 visible()
    for base in (src.category, ""):
        joined = f"{base}/{t}" if base else t
        rel = _share_rel_of(root, joined)
        if rel is not None:
            return ("ext", rel)
    return None


def build_graph(root: Path, fp: str | None = None, persist: bool = True) -> _Built:
    """构建图谱索引。fp=已算好的失效指纹（省一次全量 rglob）；
    persist=False 时布局只留进程内（只读请求者不得向共享目录落盘——红队 V4）。"""
    entries = _scan(root)
    nodes: list[Node] = []
    by_path: dict[str, int] = {}
    by_name: dict[str, list[_Entry]] = {}
    by_alias: dict[str, list[_Entry]] = {}
    for e in entries:
        idx = len(nodes)
        nodes.append({
            "id": e.rel_share, "path": e.rel_share, "name": e.name,
            "category": e.category, "tags": e.tags, "aliases": e.aliases,
            "out": 0, "ind": 0, "ghost": False, "external": False,
            "orphan": False, "summary": e.summary, "x": 0.0, "y": 0.0,
        })
        by_path[e.rel_root.casefold()] = idx
        by_name.setdefault(e.stem.casefold(), []).append(e)
        for al in e.aliases:
            by_alias.setdefault(al.casefold(), []).append(e)

    edge_w: dict[tuple[str, str], int] = {}
    ext_w: dict[tuple[str, str], int] = {}
    excerpts: dict[tuple[str, str], str] = {}
    excerpt_seen: set[str] = set()          # 每个目标只记首条摘录（首源获胜）
    ghosts: dict[str, Node] = {}
    ghost_srcs: dict[str, list[str]] = {}
    ext_cand: dict[str, str] = {}

    for e in entries:
        src_node = nodes[by_path[e.rel_root.casefold()]]
        if not e.links:
            continue
        # 摘录用正文：每个文件只读一次（红队 V3：逐目标重读 2MB 文件
        # 会把冷构建放大到小时级）
        body = _strip_fm(_read_head(e.abs, _SCAN_LIMIT)) \
            if e.ext in (".md", ".markdown", ".txt") else ""
        body_lines = body.splitlines() if body else []
        for raw in e.links:
            hit = _resolve_target(raw, e, root, by_path, by_name, by_alias)
            if hit is None:
                continue
            kind, val = hit
            if kind == "node":
                dst_id: str = nodes[val]["id"]
            elif kind == "ghost":
                gid = f"ghost:{val}"
                if gid not in ghosts:
                    ghosts[gid] = {
                        "id": gid, "path": "", "name": val, "category": "",
                        "tags": [], "aliases": [], "out": 0, "ind": 0,
                        "ghost": True, "external": False, "orphan": False,
                        "summary": "", "x": 0.0, "y": 0.0,
                    }
                ghost_srcs.setdefault(gid, []).append(src_node["id"])
                dst_id = gid
            else:  # external：出口按 visible() 物化
                ext_id = f"ext:{val}"
                ext_cand[ext_id] = val
                dst_id = ext_id
            key = (src_node["id"], dst_id)
            if kind == "ext":
                ext_w[key] = ext_w.get(key, 0) + 1
            else:
                edge_w[key] = edge_w.get(key, 0) + 1
                if dst_id not in excerpt_seen and body_lines:
                    line = ""
                    for ln in body_lines:
                        if raw in ln:
                            line = ln.strip()[:_EXCERPT_LEN]
                            break
                    if line:
                        excerpts[(dst_id, src_node["id"])] = line
                        excerpt_seen.add(dst_id)

    for gid in sorted(ghosts):
        nodes.append(ghosts[gid])

    # ── 分类枢纽节点（v3.37）：分类=笔记目录下的子文件夹（含**空目录**），
    # 自动挂载为其下笔记的枢纽——新建分类/新笔记在下次指纹变更时自动入图，
    # 无需手工连边。枢纽边**只用于布局聚类**（把同分类笔记拉到一起），
    # 不进 g["edges"]：度数/反链/孤岛语义保持"知识链接"纯净，不被目录
    # 归属污染（否则每篇有分类的笔记都不再是孤岛，"孤岛"功能失效）。
    folders: dict[str, dict[str, Any]] = {}
    folder_members: dict[str, list[str]] = {}
    for dpath in _scan_dirs(root):
        parts = [seg for seg in dpath.split("/") if seg]
        for i in range(len(parts)):
            sub = "/".join(parts[:i + 1])
            fid = f"folder:{sub}"
            if fid not in folders:
                folders[sub] = {
                    "id": fid, "name": parts[i], "path": sub,
                    "parent": "/".join(parts[:i]), "x": 0.0, "y": 0.0,
                    # 与笔记节点同构的字段（布局/物化代码按统一形状访问）
                    "category": "/".join(parts[:i]), "tags": [], "aliases": [],
                    "out": 0, "ind": 0, "ghost": False, "external": False,
                    "orphan": False, "summary": "", "kind": "folder",
                }
                folder_members[fid] = []
    for e in entries:
        parts = [seg for seg in e.category.split("/") if seg]
        for i in range(len(parts)):
            folder_members[f"folder:{'/'.join(parts[:i + 1])}"].append(e.rel_share)
    folder_edges: list[Edge] = []
    for sub in sorted(folders):
        parent = folders[sub]["parent"]
        if parent:
            folder_edges.append([f"folder:{parent}", f"folder:{sub}", 1])
    for e in entries:
        if e.category:
            folder_edges.append([f"folder:{e.category}", e.rel_share, 1])
    folder_list = []
    for sub in sorted(folders):
        n = dict(folders[sub])
        n["count"] = len(folder_members[n["id"]])
        nodes.append(n)
        folder_list.append(dict(n))   # 顶层清单与节点同构（count 一并下发）
    try:
        root_rel = root.relative_to(SHARE_ROOT).as_posix()
    except ValueError:
        root_rel = ""

    edges: list[Edge] = [[s, d, w] for (s, d), w in sorted(edge_w.items())]
    ext_edges: list[Edge] = [[s, d, w] for (s, d), w in sorted(ext_w.items())]
    deg_out: dict[str, int] = {}
    deg_in: dict[str, int] = {}
    for s, d, _w in edges:
        deg_out[s] = deg_out.get(s, 0) + 1
        deg_in[d] = deg_in.get(d, 0) + 1
    for n in nodes:
        n["out"] = deg_out.get(n["id"], 0)
        n["ind"] = deg_in.get(n["id"], 0)
        n["orphan"] = n["out"] == 0 and n["ind"] == 0
    for n in nodes:
        if n.get("kind") == "folder":
            # 枢纽的度 = 目录归属边（子分类 + 成员笔记）；孤岛语义只对笔记成立
            n["out"] = sum(1 for s, _d, _w in folder_edges if s == n["id"])
            n["ind"] = sum(1 for _s, d, _w in folder_edges if d == n["id"])
            n["orphan"] = False

    graph = {"nodes": nodes, "edges": edges,
             "folders": folder_list,
             "stats": {"nodes": len(nodes), "edges": len(edges),
                       "ghosts": sum(1 for n in nodes if n["ghost"]),
                       "orphans": sum(1 for n in nodes if n["orphan"])},
             "builtAt": time.time()}
    _layout(nodes, edges + folder_edges, root, persist=persist)
    if fp is None:
        fp = _fingerprint(root)
    return _Built(fp, graph, excerpts, ext_edges, ext_cand, ghost_srcs,
                  folders=folder_list, folder_members=folder_members,
                  root_rel=root_rel)


# ─── 布局（确定性力仿真 + 持久化增量）──────────────────────

_SPACING = 130.0      # 期望边长/节点间距（视觉标定基准）
_REP_RADIUS = 260.0   # 斥力截断半径（网格法只算近邻）
_REP_CAND_CAP = 24    # 单节点单迭代斥力候选上限（极端稠密时保性能）
_FULL_ITERS = 150
_INC_ITERS = 50
_INC_TEMP = 8.0      # 增量重建低温：老节点几乎不动，新节点局部落位
                     # （无阻尼 FR 有"轨道游走"倾向，高温增量重建等于全场洗牌）


def _layout_path(root: Path) -> Path:
    return root / _LAYOUT_DIR / _LAYOUT_FILE


def load_layout(root: Path) -> dict[str, list[float]]:
    try:
        data = json.loads(_layout_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pos = data.get("pos") if isinstance(data, dict) else None
    if not isinstance(pos, dict):
        return {}
    out: dict[str, list[float]] = {}
    for k, v in pos.items():
        if isinstance(k, str) and isinstance(v, list) and len(v) == 2 \
                and all(isinstance(x, (int, float)) and math.isfinite(x) for x in v):
            out[k] = [float(v[0]), float(v[1])]
    return out


def save_layout(root: Path, pos: dict[str, list[float]]) -> None:
    """原子写入布局；目录只读（member 写 shared）时静默放弃——布局仅进程内。"""
    try:
        d = root / _LAYOUT_DIR
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(d), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"v": 1, "pos": pos}, f, ensure_ascii=False)
            os.replace(tmp, _layout_path(root))
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass


def _simulate(xs: list[float], ys: list[float],
              pairs: list[tuple[int, int, int]], iters: int,
              max_temp: float = 42.0,
              movable: set[int] | None = None) -> None:
    """Fruchterman-Reingold 变体：网格近距斥力 O(n·k) + 弹簧引力 + 弱向心力。

    纯 Python 预算：2000 节点冷构建秒级。无随机数——初始相位由排序序决定，
    同库同布局（确定性要求）。max_temp 调低即"温柔重建"。
    movable 非 None 时只动这些节点（增量重建：老节点冻结为静态锚，
    新节点局部松弛落位——全局耦合下老节点被多侧对拉，调温度救不了漂移）。
    """
    n = len(xs)
    if n == 0:
        return
    if n == 1:
        xs[0], ys[0] = 0.0, 0.0
        return
    active = range(n) if movable is None else sorted(movable)
    k2 = _SPACING * _SPACING
    r2 = _REP_RADIUS * _REP_RADIUS
    disp_x = [0.0] * n
    disp_y = [0.0] * n
    for it in range(iters):
        temp = max(2.0, max_temp * (1.0 - it / iters))
        grid: dict[tuple[int, int], list[int]] = {}
        for i in range(n):
            grid.setdefault((int(xs[i] // _REP_RADIUS), int(ys[i] // _REP_RADIUS)),
                            []).append(i)
        max_move = 0.0
        for i in active:
            dx = dy = 0.0
            cx = int(xs[i] // _REP_RADIUS)
            cy = int(ys[i] // _REP_RADIUS)
            tried = 0
            for gx in (cx - 1, cx, cx + 1):
                for gy in (cy - 1, cy, cy + 1):
                    bucket = grid.get((gx, gy))
                    if not bucket:
                        continue
                    xi = xs[i]
                    yi = ys[i]
                    for j in bucket:
                        if j == i or tried >= _REP_CAND_CAP:
                            continue
                        tried += 1
                        ddx = xi - xs[j]
                        ddy = yi - ys[j]
                        d2 = ddx * ddx + ddy * ddy
                        if d2 > r2 or d2 == 0.0:
                            continue
                        # k/d 归一：d=k 处斥力=1，与弹簧 d²/k²（d=k 处=1）平衡，
                        # 连边对自然停在 k 间距（经典 FR 的 k²/d 比弹簧强 k 倍，
                        # 平衡点被推到截断半径外，增量加节点会推走整个邻域）
                        f = _SPACING / math.sqrt(d2)
                        dx += ddx * f
                        dy += ddy * f
            disp_x[i] = dx
            disp_y[i] = dy
        for a, b, w in pairs:
            am = movable is None or a in movable
            bm = movable is None or b in movable
            if not am and not bm:
                continue
            ddx = xs[b] - xs[a]
            ddy = ys[b] - ys[a]
            d = math.sqrt(ddx * ddx + ddy * ddy)
            if d == 0.0:
                continue
            f = min(d * d / k2, 6.0) * min(1.0 + w * 0.25, 2.5)
            ux = ddx / d * f
            uy = ddy / d * f
            if am:
                disp_x[a] += ux
                disp_y[a] += uy
            if bm:
                disp_x[b] -= ux
                disp_y[b] -= uy
        for i in active:
            mx = disp_x[i] - xs[i] * 0.004
            my = disp_y[i] - ys[i] * 0.004
            md = math.sqrt(mx * mx + my * my)
            if md > temp:
                mx = mx / md * temp
                my = my / md * temp
            xs[i] += mx
            ys[i] += my
            move = abs(mx) + abs(my)
            if move > max_move:
                max_move = move
        if max_move < 0.4:
            break


def _layout(nodes: list[Node], edges: list[Edge], root: Path,
            persist: bool = True) -> None:
    """填 x/y：持久化布局优先（增量），新节点落已链接邻居旁或黄金角螺旋。
    persist=False（只读请求者触发重建）布局只留进程内，不向目录写 .kb/
    （红队 V4：服务进程有 FS 写权，"目录只读静默放弃"兜不住 shared 目录）。"""
    prev = load_layout(root)
    idx_of = {n["id"]: i for i, n in enumerate(nodes)}
    ids = sorted(idx_of)
    xs = [0.0] * len(nodes)
    ys = [0.0] * len(nodes)
    fresh: list[int] = []
    for nid in ids:
        i = idx_of[nid]
        p = prev.get(nid)
        if p is not None:
            xs[i], ys[i] = p[0], p[1]
        else:
            fresh.append(i)
    golden = math.pi * (3.0 - math.sqrt(5.0))
    if not prev:
        # 冷构建：黄金角螺旋初始相位（排序序 → 确定性）
        for r, i in enumerate(fresh):
            rad = _SPACING * 0.9 * math.sqrt(r + 1.0)
            ang = r * golden
            xs[i] = math.cos(ang) * rad
            ys[i] = math.sin(ang) * rad
        iters = _FULL_ITERS
    else:
        # 增量：新节点放已链接老节点的质心旁（无邻居则绕老图质心外圈）
        fresh_set = set(fresh)
        old = [i for i in range(len(nodes)) if i not in fresh_set]
        link_map: dict[int, list[int]] = {}
        for s, d, _w in edges:
            si, di = idx_of.get(s), idx_of.get(d)
            if si is not None and di is not None:
                link_map.setdefault(si, []).append(di)
                link_map.setdefault(di, []).append(si)
        if old:
            ocx = sum(xs[i] for i in old) / len(old)
            ocy = sum(ys[i] for i in old) / len(old)
        else:
            ocx = ocy = 0.0
        for r, i in enumerate(fresh):
            nbrs = [j for j in link_map.get(i, []) if j not in fresh_set]
            if nbrs:
                # 偏移落位：与邻居重合时斥力/弹簧都失效（d=0 被跳过），成对游走
                # 会拖动老节点——初始就错开 0.7 倍间距，让力立即生效
                xs[i] = sum(xs[j] for j in nbrs) / len(nbrs) \
                    + math.cos(r * golden) * _SPACING * 0.7
                ys[i] = sum(ys[j] for j in nbrs) / len(nbrs) \
                    + math.sin(r * golden) * _SPACING * 0.7
            else:
                rad = _SPACING * (1.0 + r * 0.15)
                xs[i] = ocx + math.cos(r * golden) * rad
                ys[i] = ocy + math.sin(r * golden) * rad
        iters = _INC_ITERS
    pairs: list[tuple[int, int, int]] = []
    for s, d, w in edges:
        si, di = idx_of.get(s), idx_of.get(d)
        if si is not None and di is not None and si != di:
            pairs.append((si, di, int(w)))
    if prev:
        _simulate(xs, ys, pairs, iters, max_temp=_INC_TEMP,
                  movable=set(fresh))
    else:
        _simulate(xs, ys, pairs, iters, max_temp=42.0)
    pos: dict[str, list[float]] = {}
    for i, n in enumerate(nodes):
        n["x"] = round(xs[i], 2)
        n["y"] = round(ys[i], 2)
        pos[n["id"]] = [n["x"], n["y"]]
    if persist:
        save_layout(root, pos)


# ─── 缓存（LRU + 每根构建锁）──────────────────────────────

_cache: OrderedDict[str, _Built] = OrderedDict()
_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def reset_cache() -> None:
    """测试专用：清空图谱缓存与构建锁。"""
    with _registry_lock:
        _cache.clear()
        _locks.clear()


def _key_of(root: Path) -> str:
    return os.path.normcase(str(root))


def _lock_for(key: str) -> threading.Lock:
    with _registry_lock:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _get_built(root: Path, force: bool, persist: bool = True) -> _Built:
    key = _key_of(root)
    with _lock_for(key):
        fp = _fingerprint(root)   # 命中要指纹，未命中构建也要——只算一次
        hit = _cache.get(key)
        if not force and hit is not None and hit.fp == fp:
            _cache.move_to_end(key)
            return hit
        built = build_graph(root, fp=fp, persist=persist)
        with _registry_lock:
            _cache[key] = built
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_CAP:
                stale, _b = _cache.popitem(last=False)
                lock = _locks.get(stale)
                # 锁字典只增不减会慢性泄漏；只清"无人持锁"的淘汰键
                # （有人等着的不动，最坏多一次重复构建，无害）
                if lock is not None and not lock.locked():
                    _locks.pop(stale, None)
        return built


def _vis_plan(built: _Built, user: Row) -> tuple[set[str], set[str]]:
    """（可见节点 id 集, 可保留幽灵 id 集）。

    红队 V1/V2 收口：图谱根高于请求者可见边界（如 path=/）时，扫描会
    覆盖他人 private/incoming——所有出口必须按 visible() 过滤节点；
    幽灵节点的名字来自源笔记正文，只有存在**可见源**的幽灵才可下发。
    """
    vis: set[str] = set()
    for n in built.graph["nodes"]:
        if n["ghost"] or n.get("kind") == "folder":
            continue
        if visible(user, str(n["path"]).split("/")):
            vis.add(n["id"])
    # 分类枢纽：任一后代笔记可见 ⇒ 可见（members 已含全部后代，
    # 父级随子级自动成立，无需再传播）。**空分类没有成员可供判定**，
    # 必须按目录自身的物理归属过 visible()——path=/ 时它会落在他人
    # private/incoming 下，直接放行会泄露"他人目录存在性"（安全测试抓到）。
    rr = built.root_rel
    for n in built.graph["nodes"]:
        if n.get("kind") != "folder":
            continue
        members = built.folder_members.get(n["id"], [])
        if members:
            if any(m in vis for m in members):
                vis.add(n["id"])
        else:
            dir_rel = f"{rr}/{n['path']}" if rr else str(n["path"])
            if visible(user, [p for p in dir_rel.split("/") if p]):
                vis.add(n["id"])
    ghosts_ok = {gid for gid, srcs in built.ghost_srcs.items()
                 if any(s in vis for s in srcs)}
    return vis, ghosts_ok


def _materialize(built: _Built, user: Row) -> dict[str, Any]:
    """出口物化：按请求者可见性过滤节点/幽灵/外链边。

    全部可见且无外链候选时零拷贝快路径；否则浅拷贝并重算度数——
    外链参与后 orphan 判定会变，度数必须基于过滤后的边重算。
    """
    g = built.graph
    vis, ghosts_ok = _vis_plan(built, user)
    allowed: dict[str, str] = {}
    for ext_id, share_rel in built.ext_cand.items():
        if visible(user, share_rel.split("/")):
            allowed[ext_id] = share_rel
    keep_all = len(vis) + len(ghosts_ok) == len(g["nodes"])
    # 快路径仅限"无外链候选且全可见"——外链节点只存在于物化产物，
    # 有 ext_cand 时 admin 也要走拷贝路径才能拿到外链节点
    if not built.ext_cand and keep_all:
        return g
    nodes = [dict(n) for n in g["nodes"]
             if n["id"] in vis or (n["ghost"] and n["id"] in ghosts_ok)]
    have = {n["id"] for n in nodes}
    edges = [list(e) for e in g["edges"] if e[0] in have and e[1] in have]
    for s, d, w in built.ext_edges:
        if d in allowed and s in have:
            edges.append([s, d, w])
    deg_out: dict[str, int] = {}
    deg_in: dict[str, int] = {}
    for s, d, _w in edges:
        deg_out[s] = deg_out.get(s, 0) + 1
        deg_in[d] = deg_in.get(d, 0) + 1
    for n in nodes:
        n["out"] = deg_out.get(n["id"], 0)
        n["ind"] = deg_in.get(n["id"], 0)
        n["orphan"] = n["out"] == 0 and n["ind"] == 0
    # 外链节点：绕主图质心外圈排布（黄金角，确定性）
    cx = sum(n["x"] for n in nodes) / max(1, len(nodes))
    cy = sum(n["y"] for n in nodes) / max(1, len(nodes))
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for r, ext_id in enumerate(sorted(allowed)):
        share_rel = allowed[ext_id]
        rad = _SPACING * 3.0 * (1.0 + 0.12 * (r // 12))
        ang = r * golden
        nodes.append({
            "id": ext_id, "path": share_rel,
            "name": share_rel.rsplit("/", 1)[-1],
            "category": "", "tags": [], "aliases": [],
            "out": 0, "ind": deg_in.get(ext_id, 0), "ghost": False,
            "external": True, "orphan": False, "summary": "",
            "x": round(cx + math.cos(ang) * rad, 2),
            "y": round(cy + math.sin(ang) * rad, 2),
        })
    folders = [f for f in g.get("folders", []) if f["id"] in vis]
    return {"nodes": nodes, "edges": edges, "folders": folders,
            "stats": {"nodes": len(nodes), "edges": len(edges),
                      "ghosts": sum(1 for n in nodes if n["ghost"]),
                      "orphans": sum(1 for n in nodes if n["orphan"])},
            "builtAt": g["builtAt"]}


def get_graph(user: Row, root: Path, persist: bool = True) -> dict[str, Any]:
    return _materialize(_get_built(root, force=False, persist=persist), user)


def rebuild(user: Row, root: Path) -> dict[str, Any]:
    # reindex 已过 write 权限，重建必然允许落盘
    return _materialize(_get_built(root, force=True, persist=True), user)


def reset_kb_state() -> None:
    """测试夹具入口（conftest 惯例：官方 reset 函数，不直掏私有字典）。"""
    reset_cache()


# ─── 查询辅助（backlinks / neighbors / names / context）───

def _node_by_path(g: dict[str, Any], file_rel: str) -> Node | None:
    """按 SHARE_ROOT 相对路径找节点（casefold，Windows 文件系统语义）。"""
    want = os.path.normpath(file_rel).replace("\\", "/").casefold()
    for n in g["nodes"]:
        if not n["ghost"] and not n["external"] and not n.get("kind") == "folder" \
                and str(n["path"]).casefold() == want:
            return n
    return None


def backlinks(user: Row, root: Path, file_rel: str,
              persist: bool = True) -> dict[str, Any]:
    built = _get_built(root, force=False, persist=persist)
    g = built.graph
    target = _node_by_path(g, file_rel)
    if target is None:
        raise HTTPException(status_code=404, detail="笔记不存在")
    tid = target["id"]
    vis, ghosts_ok = _vis_plan(built, user)
    nodes_by_id = {n["id"]: n for n in g["nodes"]}
    back: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    for s, d, _w in g["edges"]:
        if d == tid and s in vis and s in nodes_by_id:
            src = nodes_by_id[s]
            back.append({"path": src["path"], "name": src["name"],
                         "excerpt": built.excerpts.get((tid, s), ""),
                         "ghost": False})
        elif s == tid and d in nodes_by_id \
                and (d in vis or d in ghosts_ok):
            dst = nodes_by_id[d]
            out.append({"path": str(dst["path"]), "name": dst["name"],
                        "ghost": bool(dst["ghost"])})
    back.sort(key=lambda x: str(x["name"]).casefold())
    out.sort(key=lambda x: str(x["name"]).casefold())
    return {"backlinks": back, "outgoing": out}


def names(user: Row, root: Path, persist: bool = True) -> list[dict[str, Any]]:
    """编辑器 [[ 补全的轻量数据源（全图 JSON 太重）。"""
    built = _get_built(root, force=False, persist=persist)
    vis, ghosts_ok = _vis_plan(built, user)
    out = [{"name": n["name"], "aliases": n["aliases"], "path": n["path"],
            "category": n["category"], "ghost": n["ghost"]}
           for n in built.graph["nodes"]
           if n["id"] in vis or (n["ghost"] and n["id"] in ghosts_ok)]
    out.sort(key=lambda x: str(x["name"]).casefold())
    return out


def neighbors(user: Row, root: Path, file_rel: str, depth: int,
              persist: bool = True) -> dict[str, Any]:
    """以某篇笔记为中心的子图（depth ≤2，大图局部展开 / agent 爬行）。"""
    depth = max(1, min(depth, 2))
    built = _get_built(root, force=False, persist=persist)
    g = built.graph
    center = _node_by_path(g, file_rel)
    if center is None:
        raise HTTPException(status_code=404, detail="笔记不存在")
    vis, ghosts_ok = _vis_plan(built, user)
    keep_all = vis | ghosts_ok
    adj: dict[str, list[str]] = {}
    for s, d, _w in g["edges"]:
        if s not in keep_all or d not in keep_all:
            continue
        adj.setdefault(s, []).append(d)
        adj.setdefault(d, []).append(s)
    keep = {center["id"]}
    frontier = {center["id"]}
    for _ in range(depth):
        nxt: set[str] = set()
        for nid in frontier:
            nxt |= set(adj.get(nid, []))
        nxt -= keep
        keep |= nxt
        frontier = nxt
    nodes = [n for n in g["nodes"] if n["id"] in keep]
    edges = [e for e in g["edges"] if e[0] in keep and e[1] in keep]
    return {"nodes": nodes, "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges),
                      "ghosts": sum(1 for n in nodes if n["ghost"]),
                      "orphans": sum(1 for n in nodes if n["orphan"])},
            "builtAt": g["builtAt"]}


def context(user: Row, root: Path, file_rel: str,
            persist: bool = True) -> dict[str, Any]:
    """agent 专用：本篇全文（≤20KB **字节**，truncated 标志）+ 一跳邻居/反链摘要。"""
    built = _get_built(root, force=False, persist=persist)
    g = built.graph
    center = _node_by_path(g, file_rel)
    if center is None:
        raise HTTPException(status_code=404, detail="笔记不存在")
    # 字节级截断（review P1-2）：按字符切 3 字节 CJK 会到 60KB
    raw_bytes = _read_bytes_head(SHARE_ROOT / str(center["path"]),
                                 _CTX_LIMIT + 1)
    truncated = len(raw_bytes) > _CTX_LIMIT
    content = raw_bytes[:_CTX_LIMIT].decode("utf-8", errors="ignore")
    vis, ghosts_ok = _vis_plan(built, user)
    nodes_by_id = {n["id"]: n for n in g["nodes"]}
    outgoing: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []
    for s, d, _w in g["edges"]:
        if s == center["id"] and d in nodes_by_id \
                and (d in vis or d in ghosts_ok):
            n = nodes_by_id[d]
            outgoing.append({"path": str(n["path"]), "name": n["name"],
                             "summary": n["summary"], "ghost": bool(n["ghost"])})
        elif d == center["id"] and s in vis and s in nodes_by_id:
            n = nodes_by_id[s]
            incoming.append({"path": n["path"], "name": n["name"],
                             "summary": n["summary"], "ghost": False})
    outgoing.sort(key=lambda x: str(x["name"]).casefold())
    incoming.sort(key=lambda x: str(x["name"]).casefold())
    return {
        "note": {"path": center["path"], "name": center["name"],
                 "tags": center["tags"], "aliases": center["aliases"],
                 "content": content, "truncated": truncated,
                 # 服务器真实存储路径（v3.37）：图谱节点卡片"复制路径"用，
                 # 手机端把它发给台式 Agent 即可定位同一文件
                 "abs_path": str(SHARE_ROOT / str(center["path"])).replace("\\", "/")},
        "links": outgoing, "backlinks": incoming,
        "builtAt": g["builtAt"],
    }
