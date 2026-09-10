"""知识库 M-K0 测试：链接解析/同名裁决/YAML 归一化/缓存指纹/护栏/权限/布局。

必测项来自设计文档 v2 定稿 §8（对抗审查修正的回归钉）：
rename 后指纹失效、同名冲突 casefold 裁决、YAML 隐式类型归一化、
ghost 无泄露、外链 visible 过滤、2000 护栏明确报错。
"""
import os
import secrets
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import SHARE_ROOT
from app.db import _db
from app.main import app
from app.services import kb as kb_engine
# 注意：必须 `from conftest import`（与 pytest 加载的 conftest 同一模块对象）。
# `from tests.conftest import` 会经 namespace package 把 conftest 顶层代码
# 再执行一遍——重建临时 SHARE_ROOT 并改写 env，运行期读 env 的测试全灭。
from conftest import ADMIN_PASS, ADMIN_USER, MEMBER_PASS2


# ── 夹具 ──────────────────────────────────────────────────

def _mk(workdir: str, rel: str, text: str) -> str:
    """在 workdir 下造笔记文件，返回 SHARE_ROOT 相对路径（notes 列表口径）。"""
    p = SHARE_ROOT / workdir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return f"{workdir}/{rel}"


def _graph(auth, workdir: str) -> dict:
    r = auth.get("/api/kb/graph", params={"path": workdir})
    assert r.status_code == 200, r.text
    return r.json()


def _node(g: dict, name: str) -> dict | None:
    for n in g["nodes"]:
        if n["name"] == name:
            return n
    return None


def _edge(g: dict, src_name: str, dst_name: str) -> dict | None:
    ids = {n["id"]: n["name"] for n in g["nodes"]}
    for s, d, w in g["edges"]:
        if ids.get(s) == src_name and ids.get(d) == dst_name:
            return {"src": s, "dst": d, "w": w}
    return None


@pytest.fixture()
def make_user(auth, client):
    """邀请码建号工厂（test_multiuser 同款）：make_user("member") -> 已登录客户端。"""
    created = []

    def _make(role, name=None):
        username = name or f"u{secrets.token_hex(3)}"
        r = auth.post("/api/admin/invites", json={"role": role, "hours": 1})
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        r = client.post("/api/invites/redeem", json={
            "code": code, "username": username, "password": MEMBER_PASS2})
        assert r.status_code == 200, r.text
        c = TestClient(app, base_url="https://testserver")
        r = c.post("/api/login", json={"username": username, "password": MEMBER_PASS2})
        assert r.status_code == 200, r.text
        c.username = username
        created.append(username)
        return c

    yield _make
    for username in created:
        row = _db().execute("SELECT id FROM users WHERE username=?",
                            (username,)).fetchone()
        if row:
            _db().execute("DELETE FROM users WHERE id=?", (row["id"],))
            _db().commit()


# ── 图基础：链接/去重权重/孤岛 ────────────────────────────

def test_graph_basic_links_weight_orphan(auth, workdir):
    _mk(workdir, "a.md", "[[b]]\n[[b]]\n[[c]]\n")
    _mk(workdir, "b.md", "反向 [[a]]\n")
    _mk(workdir, "c.md", "终点\n")
    _mk(workdir, "lonely.md", "谁也不链\n")
    g = _graph(auth, workdir)
    assert g["stats"]["nodes"] == 4
    e = _edge(g, "a.md", "b.md")
    assert e is not None and e["w"] == 2          # 重复链接去重 + 权重
    a = _node(g, "a.md")
    assert a["out"] == 2 and a["ind"] == 1
    lonely = _node(g, "lonely.md")
    assert lonely["orphan"] is True and a["orphan"] is False
    assert all("x" in n and "y" in n for n in g["nodes"])   # 预布局坐标随图下发


def test_alias_and_casefold(auth, workdir):
    _mk(workdir, "feynman.md",
        "---\naliases: [Feynman Technique, 费曼技巧]\n---\n正文\n")
    _mk(workdir, "note.md", "读 [[feynman technique]] 和 [[FEYNMAN]] 和 [[费曼技巧]]\n")
    g = _graph(auth, workdir)
    assert g["stats"]["ghosts"] == 0              # 别名+大小写全部命中，无幽灵
    assert _edge(g, "note.md", "feynman.md") is not None


def test_ghost_name_only_and_pathed_dropped(auth, workdir):
    _mk(workdir, "a.md", "[[待写的笔记]] 与 [[不存在/路径.md]] 与 [x](没有/这个.md)\n")
    g = _graph(auth, workdir)
    ghosts = [n for n in g["nodes"] if n["ghost"]]
    assert [n["name"] for n in ghosts] == ["待写的笔记"]   # 带路径未命中不建幽灵
    ghost = ghosts[0]
    assert ghost["path"] == "" and ghost["id"] == "ghost:待写的笔记"
    assert _edge(g, "a.md", "待写的笔记") is not None


def test_collision_policy(auth, workdir):
    """同名裁决（P1-2）：同目录 > 最短相对路径 > 字典序，且确定性。"""
    _mk(workdir, "深度/很深/笔记 MOC.md", "我是最深的\n")
    _mk(workdir, "浅/笔记 MOC.md", "我是较短的\n")
    _mk(workdir, "同目录/笔记 MOC.md", "源头\n[[笔记 MOC]]\n")
    g = _graph(auth, workdir)
    e = _edge(g, "笔记 MOC.md", "笔记 MOC.md")     # 同目录自链命中自己
    assert e is not None and e["src"] == e["dst"]
    # 从别处（根目录）链 → 同目录无候选 → 最短相对路径（浅/）胜出
    _mk(workdir, "根.md", "[[笔记 MOC]]\n")
    g2 = _graph(auth, workdir)
    shallow = next(n for n in g2["nodes"]
                   if n["name"] == "笔记 MOC.md" and n["category"] == "浅")
    hit = _edge(g2, "根.md", "笔记 MOC.md")
    assert hit is not None and hit["dst"] == shallow["id"]


def test_md_link_resolution(auth, workdir):
    _mk(workdir, "a.md", "见 [B 笔记](./b.md) 与 [C](sub/c.md)\n")
    _mk(workdir, "b.md", "x\n")
    _mk(workdir, "sub/c.md", "y\n")
    g = _graph(auth, workdir)
    assert _edge(g, "a.md", "b.md") is not None
    assert _edge(g, "a.md", "c.md") is not None


def test_tags_inline_frontmatter_and_url_safe(auth, workdir):
    _mk(workdir, "a.md",
        "---\ntags: [学习方法, 输出]\n---\n# 这是标题（# 后带空格不是标签）\n"
        "正文 #内联标签 看 https://example.com/#fragment 不算\n")
    g = _graph(auth, workdir)
    a = _node(g, "a.md")
    assert set(a["tags"]) == {"学习方法", "输出", "内联标签"}


def test_wikilink_anchor_stripped(auth, workdir):
    _mk(workdir, "a.md", "[[b#第三节]] 与 [[b#锚|别名链接]]\n")
    _mk(workdir, "b.md", "x\n")
    g = _graph(auth, workdir)
    assert g["stats"]["ghosts"] == 0
    e = _edge(g, "a.md", "b.md")
    assert e is not None and e["w"] == 2


# ── frontmatter 归一化（P1-3）────────────────────────────

def test_frontmatter_normalization(auth, workdir):
    _mk(workdir, "a.md",
        "---\ntitle: T\ncreated: 2026-09-10\ntags: 单值\n"
        "aliases: A1\nscore: 5\nok: true\n---\n正文\n")
    fm = kb_engine.parse_frontmatter(
        (SHARE_ROOT / workdir / "a.md").read_text(encoding="utf-8"))
    assert fm["created"] == "2026-09-10"          # date → ISO 字符串
    assert fm["tags"] == ["单值"]                  # str → list
    assert fm["aliases"] == ["A1"]
    assert fm["score"] == 5 and fm["ok"] is True


def test_frontmatter_broken_tolerated(auth, workdir):
    _mk(workdir, "a.md", "---\ntags: [漏了闭\n---\n正文照常入图 [[b]]\n")
    _mk(workdir, "b.md", "x\n")
    g = _graph(auth, workdir)
    a = _node(g, "a.md")
    assert a is not None and a["tags"] == []       # 损坏按无元数据处理
    assert _edge(g, "a.md", "b.md") is not None


# ── 缓存指纹（P1-1）：rename/move 必须反映到图 ────────────

def test_rename_invalidates_cache(auth, workdir):
    p = _mk(workdir, "旧名.md", "[[目标]]\n")
    _mk(workdir, "目标.md", "x\n")
    g1 = _graph(auth, workdir)
    assert _node(g1, "旧名.md") is not None
    (SHARE_ROOT / p).rename(SHARE_ROOT / workdir / "新名.md")   # mtime/size 不变
    g2 = _graph(auth, workdir)
    assert _node(g2, "旧名.md") is None
    assert _node(g2, "新名.md") is not None


def test_layout_persist_and_incremental(auth, workdir):
    _mk(workdir, "a.md", "[[b]]\n")
    _mk(workdir, "b.md", "x\n")
    g1 = _graph(auth, workdir)
    assert (SHARE_ROOT / workdir / ".kb" / "layout.json").is_file()
    ax1, ay1 = _node(g1, "a.md")["x"], _node(g1, "a.md")["y"]
    _mk(workdir, "新笔记.md", "[[a]] 引出的新节点\n")
    g2 = _graph(auth, workdir)
    a2 = _node(g2, "a.md")
    drift = abs(a2["x"] - ax1) + abs(a2["y"] - ay1)
    assert drift < 120                            # 增量重建：老节点基本不动
    new = _node(g2, "新笔记.md")
    dist = abs(new["x"] - a2["x"]) + abs(new["y"] - a2["y"])
    assert dist < 3 * 130                         # 新节点落在链接对象附近


# ── 护栏/隐藏/扩展名口径 ─────────────────────────────────

def test_hidden_dirs_and_files_skipped(auth, workdir):
    _mk(workdir, ".hid/隐藏.md", "x\n")
    _mk(workdir, ".隐藏笔记.md", "x\n")
    _mk(workdir, "正常.md", "[[b]]\n")
    _mk(workdir, "b.md", "x\n")
    _mk(workdir, "说明.txt", "纯文本也入图 [[正常]]\n")
    _mk(workdir, "忽略.bin", "x\n")
    g = _graph(auth, workdir)
    names = {n["name"] for n in g["nodes"]}
    assert names == {"正常.md", "b.md", "说明.txt"}   # .bin 不入图（NOTES 口径同源）


def test_html_node_without_content_read(auth, workdir):
    _mk(workdir, "日记.html", "<html><body>富文本 <b>笔记</b></body></html>\n")
    g = _graph(auth, workdir)
    h = _node(g, "日记.html")
    assert h is not None and h["summary"] == "" and h["tags"] == []


def test_max_nodes_guard(auth, workdir):
    d = SHARE_ROOT / workdir
    d.mkdir(parents=True, exist_ok=True)
    for i in range(2001):
        (d / f"n{i:05}.md").write_text("x", encoding="utf-8")
    r = auth.get("/api/kb/graph", params={"path": workdir})
    assert r.status_code == 400                   # 明确报错，不静默截断
    assert "2000" in r.json()["detail"]


def test_perf_cold_build_300_nodes(auth, workdir):
    """性能冒烟：300 节点链式库冷构建秒级（门禁机放宽到 20s 防抖）。"""
    d = SHARE_ROOT / workdir
    d.mkdir(parents=True, exist_ok=True)
    for i in range(300):
        (d / f"n{i:03}.md").write_text(f"[[n{(i + 1) % 300:03}]]\n", encoding="utf-8")
    t0 = time.time()
    r = auth.get("/api/kb/graph", params={"path": workdir})
    dt = time.time() - t0
    assert r.status_code == 200
    assert dt < 20, f"300 节点冷构建耗时 {dt:.1f}s"


# ── 权限（与笔记同源 + 外链 visible 过滤）────────────────

def test_unauth_401(client, workdir):
    assert client.get("/api/kb/graph", params={"path": workdir}).status_code == 401
    assert client.get("/api/kb/names", params={"path": workdir}).status_code == 401
    assert client.post("/api/kb/reindex", params={"path": workdir}).status_code == 401


def test_member_read_ok_reindex_forbidden(auth, make_user, workdir):
    _mk(workdir, "a.md", "[[b]]\n")
    _mk(workdir, "b.md", "x\n")
    member = make_user("member")
    r = member.get("/api/kb/graph", params={"path": workdir})
    assert r.status_code == 200
    assert member.post("/api/kb/reindex", params={"path": workdir}).status_code == 403


def test_external_link_visible_filter(auth, make_user, workdir):
    """外链存在性跨权过滤：admin 可见自己私人空间的被链笔记，member 不可见。"""
    _mk(workdir, "共享.md", "链到 [[../private/admin/私人笔记.md]]\n")
    priv = SHARE_ROOT / "private" / "admin"
    priv.mkdir(parents=True, exist_ok=True)
    target = priv / "私人笔记.md"
    target.write_text("私有的\n", encoding="utf-8")
    try:
        g_admin = _graph(auth, workdir)
        ext = [n for n in g_admin["nodes"] if n["external"]]
        assert len(ext) == 1 and ext[0]["path"].endswith("私人笔记.md")
        member = make_user("member")
        r = member.get("/api/kb/graph", params={"path": workdir})
        assert r.status_code == 200
        assert [n for n in r.json()["nodes"] if n["external"]] == []   # member 不可见
    finally:
        target.unlink(missing_ok=True)


def test_file_not_in_root_rejected(auth, workdir):
    _mk(workdir, "a.md", "x\n")
    other = f"t-{secrets.token_hex(4)}"
    outside = _mk(other, "外部.md", "x\n")
    r = auth.get("/api/kb/backlinks", params={"path": workdir, "file": outside})
    assert r.status_code == 400


# ── backlinks / neighbors / names / context ──────────────

def test_backlinks_excerpt_and_outgoing(auth, workdir):
    _mk(workdir, "源.md", "第一段\n详见 [[目标笔记]] 的说法\n")
    _mk(workdir, "目标笔记.md", "被链的终点，不外链\n")
    r = auth.get("/api/kb/backlinks",
                 params={"path": workdir, "file": f"{workdir}/目标笔记.md"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["backlinks"]) == 1
    bl = data["backlinks"][0]
    assert bl["name"] == "源.md" and "详见" in bl["excerpt"] and "[[目标笔记]]" in bl["excerpt"]
    assert data["outgoing"] == []                  # 终点笔记无出链
    r2 = auth.get("/api/kb/backlinks",
                  params={"path": workdir, "file": f"{workdir}/源.md"})
    out = r2.json()["outgoing"]
    assert [o["name"] for o in out] == ["目标笔记.md"]


def test_neighbors_depth(auth, workdir):
    _mk(workdir, "a.md", "[[b]]\n")
    _mk(workdir, "b.md", "[[c]]\n")
    _mk(workdir, "c.md", "[[d]]\n")
    _mk(workdir, "d.md", "终点\n")
    r1 = auth.get("/api/kb/neighbors",
                  params={"path": workdir, "file": f"{workdir}/a.md", "depth": 1})
    assert r1.status_code == 200
    assert {n["name"] for n in r1.json()["nodes"]} == {"a.md", "b.md"}
    r2 = auth.get("/api/kb/neighbors",
                  params={"path": workdir, "file": f"{workdir}/a.md", "depth": 2})
    assert {n["name"] for n in r2.json()["nodes"]} == {"a.md", "b.md", "c.md"}


def test_names_for_completion(auth, workdir):
    _mk(workdir, "Feynman.md", "---\naliases: [费曼技巧]\n---\nx\n")
    _mk(workdir, "待写.md", "[[还欠一篇]]\n")
    r = auth.get("/api/kb/names", params={"path": workdir})
    assert r.status_code == 200
    names = r.json()
    fey = [n for n in names if n["name"] == "Feynman.md"][0]
    assert fey["aliases"] == ["费曼技巧"]
    ghosts = [n for n in names if n["ghost"]]
    assert [g["name"] for g in ghosts] == ["还欠一篇"]   # 补全面里也提示待写


def test_context_payload_and_truncation(auth, workdir):
    _mk(workdir, "a.md", "# 标题\n\n" + "正文内容。" * 200 + "\n[[b]]\n")
    _mk(workdir, "b.md", "邻居摘要内容\n")
    r = auth.get("/api/kb/context", params={"path": workdir, "file": f"{workdir}/a.md"})
    assert r.status_code == 200
    data = r.json()
    assert data["note"]["truncated"] is False
    assert "[[b]]" in data["note"]["content"]
    assert [n["name"] for n in data["links"]] == ["b.md"]
    # 超 20KB 截断
    big = "# 大\n\n" + "字" * 30000
    _mk(workdir, "big.md", big + "\n")
    r2 = auth.get("/api/kb/context", params={"path": workdir, "file": f"{workdir}/big.md"})
    d2 = r2.json()
    assert d2["note"]["truncated"] is True
    assert len(d2["note"]["content"]) <= 20 * 1024


def test_reindex_admin_ok(auth, workdir):
    _mk(workdir, "a.md", "[[b]]\n")
    _mk(workdir, "b.md", "x\n")
    r = auth.post("/api/kb/reindex", params={"path": workdir})
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["nodes"] == 2


def test_unknown_note_404(auth, workdir):
    _mk(workdir, "a.md", "x\n")
    r = auth.get("/api/kb/backlinks", params={"path": workdir, "file": f"{workdir}/无.md"})
    assert r.status_code == 404


def test_code_fence_links_parsed_v1(auth, workdir):
    """v1 容忍代码块里的 [[ ]]（Obsidian 同款行为），钉住文档承诺。"""
    _mk(workdir, "a.md", "```\n[[代码里的链接]]\n```\n")
    g = _graph(auth, workdir)
    assert [n["name"] for n in g["nodes"] if n["ghost"]] == ["代码里的链接"]


def test_layout_survives_readonly_dir(auth, make_user, workdir):
    """member（shared 只读）打开图谱：布局写盘失败静默降级，端点不 500。"""
    _mk(workdir, "a.md", "[[b]]\n")
    _mk(workdir, "b.md", "x\n")
    member = make_user("member")
    r = member.get("/api/kb/graph", params={"path": workdir})
    assert r.status_code == 200
    assert "x" in r.json()["nodes"][0]


# ── 红队 2026-09-10 修复回归（V1/V2/V4 + 字节截断）────────

def test_root_slash_no_private_leak(auth, make_user, workdir):
    """V1：path=/ 全库扫描时，member 不得看到他人 private/incoming 的
    节点元数据/摘要/名单/反链摘录；notes 列表同款口径。"""
    priv = SHARE_ROOT / "private" / "admin"
    priv.mkdir(parents=True, exist_ok=True)
    secret = priv / "机密笔记.md"
    secret.write_text("绝密内容一号 [[公开锚点]]\n", encoding="utf-8")
    inc = SHARE_ROOT / "incoming" / "admin"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "直传秘密.md").write_text("直传隐私\n", encoding="utf-8")
    _mk(workdir, "公开笔记.md", "公开内容 [[公开锚点]]\n")
    try:
        member = make_user("member")
        g = member.get("/api/kb/graph", params={"path": "/"}).json()
        paths = {n["path"] for n in g["nodes"] if n["path"]}
        assert not any(p.startswith("private/") or p.startswith("incoming/")
                       for p in paths)
        names = member.get("/api/kb/names", params={"path": "/"}).json()
        assert all(not str(n["path"]).startswith(("private/", "incoming/"))
                   for n in names)
        # 反链摘录：admin 私人笔记链到公开笔记，member 看不到这条反链
        bl = member.get("/api/kb/backlinks", params={
            "path": "/", "file": f"{workdir}/公开笔记.md"}).json()
        assert all(not str(b["path"]).startswith("private/") for b in bl["backlinks"])
        # notes 列表同款口径
        nl = member.get("/api/notes/list", params={"path": "/", "sort": "name"}).json()
        assert all(not str(it["path"]).startswith(("private/", "incoming/"))
                   for it in nl["items"])
        # admin 自己可见自己的 private（E8 语义由 visible() 决定）
        ga = auth.get("/api/kb/graph", params={"path": "/"}).json()
        assert any(str(n["path"]) == "private/admin/机密笔记.md"
                   for n in ga["nodes"])
    finally:
        secret.unlink(missing_ok=True)
        (inc / "直传秘密.md").unlink(missing_ok=True)


def test_ghost_private_source_hidden(auth, make_user, workdir):
    """V2：幽灵名字源自不可见私人笔记时不下发（有可见源才保留）。"""
    priv = SHARE_ROOT / "private" / "admin"
    priv.mkdir(parents=True, exist_ok=True)
    secret = priv / "选题.md"
    secret.write_text("[[待写的绝密选题]]\n", encoding="utf-8")
    _mk(workdir, "公开笔记.md", "[[人人可见的待写]]\n")
    try:
        member = make_user("member")
        g = member.get("/api/kb/graph", params={"path": "/"}).json()
        ghost_names = [n["name"] for n in g["nodes"] if n["ghost"]]
        assert "人人可见的待写" in ghost_names
        assert "待写的绝密选题" not in ghost_names
    finally:
        secret.unlink(missing_ok=True)


def test_readonly_rebuild_writes_no_layout(auth, make_user, workdir):
    """V4：member 只读请求触发重建时，不向共享目录落盘 .kb/layout.json。"""
    _mk(workdir, "a.md", "[[b]]\n")
    _mk(workdir, "b.md", "x\n")
    member = make_user("member")
    r = member.get("/api/kb/graph", params={"path": workdir})
    assert r.status_code == 200
    assert not (SHARE_ROOT / workdir / ".kb" / "layout.json").exists()
    # admin 命中的是 member 建的缓存（同样不落盘）；强制重建（write）才写
    assert auth.post("/api/kb/reindex", params={"path": workdir}).status_code == 200
    assert (SHARE_ROOT / workdir / ".kb" / "layout.json").is_file()


def test_context_truncation_is_byte_level(auth, workdir):
    """review P1-2：CJK 3 字节字符按字节截断，content ≤ 20KB 字节。"""
    _mk(workdir, "大文件.md", "汉" * 30000)   # 90000 字节
    r = auth.get("/api/kb/context",
                 params={"path": workdir, "file": f"{workdir}/大文件.md"})
    assert r.status_code == 200
    data = r.json()
    assert data["note"]["truncated"] is True
    assert len(data["note"]["content"].encode("utf-8")) <= 20 * 1024
