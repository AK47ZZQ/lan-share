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


def test_search_like_wildcards_are_literal(client):
    """LIKE 通配符（%/_）按字面匹配而非通配（store 统一 ESCAPE 转义）。"""
    username = f"ntw{_rand()}"
    _mk_member(client, username, MEMBER_PASS)
    _write_note(f"private/{username}/笔记/pct.md", "进度 100% 完成 的主题")
    _write_note(f"private/{username}/笔记/under.md", "snake_case 命名风格")
    note_index.refresh_index()
    c = _member_session(client, username, MEMBER_PASS)
    # "% 完" 若被当通配符会命中任意以"完"结尾的行；字面语义只命中 100%
    hits = [x for x in _q(c, "100% 完").json() if x.get("kind") == "note"]
    assert hits and "pct.md" in hits[0]["path"]
    # 下划线按字面
    hits2 = [x for x in _q(c, "snake_case").json() if x.get("kind") == "note"]
    assert hits2 and "under.md" in hits2[0]["path"]


def test_search_html_notes_hit_text_not_markup(client):
    """html 笔记镜像去标签：命中的是可读文本，markup 噪声不参与命中。"""
    username = f"nth{_rand()}"
    _mk_member(client, username, MEMBER_PASS)
    _write_note(f"private/{username}/笔记/页.html",
                "<html><body><h1>家庭公约</h1><script>var x=1;</script>"
                "<p>每周大扫除一次</p></body></html>")
    note_index.refresh_index()
    c = _member_session(client, username, MEMBER_PASS)
    assert any("/页.html" in x["path"] for x in _q(c, "大扫除").json())
    # script 内容已剥离：不命中
    assert not any("/页.html" in x["path"] for x in _q(c, "var x").json())


def test_refresh_index_caps_at_max_notes(client, monkeypatch):
    """数量护栏：单轮聚合上限 MAX_NOTES（防误选网盘根目录全盘扫描）。"""
    from app.services import note_index as ni
    seen_counts = []

    real_scan = ni.note_search_store.apply_scan

    def spy(upserts, gone):
        seen_counts.append(len(upserts))
        real_scan(upserts, gone)

    monkeypatch.setattr(ni.note_search_store, "apply_scan", spy)
    for i in range(ni.MAX_NOTES + 5):
        _write_note(f"private/{ADMIN_USER}/bulk/n{i}.md", f"bulk {i}")
    ni.refresh_index()
    assert seen_counts and seen_counts[0] <= ni.MAX_NOTES


def test_refresh_index_scales_to_max_notes(client):
    """规模压测：MAX_NOTES 篇笔记的增量索引一轮完成（家庭规模性能基线）。

    在隔离的 pytest tmp 环境构造满额文件（SHARE_ROOT 由 conftest 钉到临时
    目录，不触碰生产数据目录），断言耗时上限——超限说明 LIKE 全表扫策略
    在目标规模失效，该换引擎而非默默变慢。
    """
    import time as _t
    from app.services import note_index as ni
    for i in range(ni.MAX_NOTES):
        d = SHARE_ROOT / f"private/{ADMIN_USER}/bulk"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"n{i}.md").write_text(f"# 批量 {i}\n正文 {i} 的内容。", encoding="utf-8")
    t0 = _t.perf_counter()
    st = ni.refresh_index()
    elapsed = _t.perf_counter() - t0
    assert st["indexed"] == ni.MAX_NOTES
    assert elapsed < 30, f"2000 篇索引耗时 {elapsed:.1f}s 超预算"
    # 二轮全命中（已同步）：应远快于首轮（无写入，仅比对）
    t1 = _t.perf_counter()
    ni.refresh_index()
    steady = _t.perf_counter() - t1
    assert steady < elapsed, "稳态轮次应不慢于首轮"
