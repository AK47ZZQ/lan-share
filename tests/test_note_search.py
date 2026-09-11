"""笔记正文全文索引与 /api/search 扩展（B4）回归。

覆盖计划要求的四条语义：索引建立/更新（变更与消失增量）/搜索命中（含
snippet 与 kind）/可见性越权过滤。索引引擎无关（普通表 + LIKE，小写镜像
归一两引擎大小写差异），SQLite 与 PG 分支同跑。
"""
import os
import time

from conftest import ADMIN_PASS, ADMIN_USER, MEMBER_PASS
from test_extras import _admin, _member_session, _mk_member, _rand

from app.main import app
from app.services import note_index
from app.store import note_search as ns_store

# conftest 建的 SHARE_ROOT 隔离目录（fixture 侧 os.environ 已钉死）
from app.config import SHARE_ROOT


def _write_note(rel: str, text: str) -> None:
    p = SHARE_ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    # mtime_ns 粒度随文件系统，显式拨指针保证"变更"可被增量识别
    t = time.time() + 1
    os.utime(p, (t, t))


def _q(client, q: str):
    return client.get("/api/search", params={"q": q})


def test_note_index_build_update_delete(client):
    """建立 → 变更/消失增量 → 索引表状态收敛。"""
    username = f"nt{_rand()}"
    _mk_member(client, username, MEMBER_PASS)
    c = _member_session(client, username, MEMBER_PASS)
    _write_note(f"private/{username}/笔记/成长.md", "# 2026 秋季规划 全家旅行")
    _write_note(f"private/{username}/笔记/旧.txt", "早期草稿")

    stats = note_index.refresh_index()
    assert stats["indexed"] >= 2
    rows = {r["path"] for r in ns_store.load_index()}
    assert f"private/{username}/笔记/成长.md" in rows
    assert f"private/{username}/笔记/旧.txt" in rows

    # 变更：正文换掉（mtime 前移保证增量识别）+ 消失：删除旧.txt
    _write_note(f"private/{username}/笔记/成长.md", "新的主题：钓鱼装备清单")
    (SHARE_ROOT / f"private/{username}/笔记/旧.txt").unlink()
    note_index.refresh_index()
    rows = {r["path"] for r in ns_store.load_index()}
    assert f"private/{username}/笔记/旧.txt" not in rows
    mirror = {r["path"]: r["content_lower"]
              for r in ns_store.search_snippets("钓鱼装备", 10)}
    assert mirror.get(f"private/{username}/笔记/成长.md") == "新的主题：钓鱼装备清单"


def test_search_hits_note_body_with_kind_and_snippet(client):
    username = f"nt{_rand()}"
    _mk_member(client, username, MEMBER_PASS)
    c = _member_session(client, username, MEMBER_PASS)
    _write_note(f"private/{username}/笔记/钓鱼.md",
                "周末计划：整理钓鱼装备，买新鱼竿。")
    note_index.refresh_index()

    r = _q(c, "鱼竿")
    assert r.status_code == 200
    hits = [x for x in r.json() if x.get("kind") == "note"]
    assert hits, r.json()
    h = hits[0]
    assert h["path"] == f"private/{username}/笔记/钓鱼.md"
    assert "鱼竿" in h["snippet"]
    # 文件名也含关键词的文件命中 kind=file（形状兼容：存量键不缺失）
    assert all(set(("name", "path", "size", "modified")) <= set(x) for x in r.json())


def test_note_search_respects_visibility(client):
    """越权铁律：他人 private 下的笔记命中也不可见（铁律 41）。"""
    owner = f"nto{_rand()}"
    _mk_member(client, owner, MEMBER_PASS)
    _write_note(f"private/{owner}/笔记/机密.md", "独家暗号芝麻开门")
    note_index.refresh_index()

    viewer = f"ntv{_rand()}"
    _mk_member(client, viewer, MEMBER_PASS)
    c = _member_session(client, viewer, MEMBER_PASS)
    r = _q(c, "芝麻开门")
    assert r.status_code == 200
    assert all("/机密.md" not in x["path"] for x in r.json())

    # 本人可见；admin **同样不可见**——visible() 的隐私铁律：private 只对
    # 本人开放（admin 仅 incoming 全见），搜索不得成为 private 的旁路
    own = _member_session(client, owner, MEMBER_PASS)
    assert any("/机密.md" in x["path"] for x in _q(own, "芝麻开门").json())
    admin = _admin(client)
    assert all("/机密.md" not in x["path"] for x in _q(admin, "芝麻开门").json())


def test_search_still_finds_files_by_name(client):
    """文件名子串命中语义保留（向后兼容回归）。"""
    c = _admin(client)
    _write_note(f"private/{ADMIN_USER}/预算表2026.txt", "数字占位")
    r = _q(c, "预算表")
    assert r.status_code == 200
    assert any(x["kind"] == "file" and "预算表2026" in x["name"] for x in r.json())
