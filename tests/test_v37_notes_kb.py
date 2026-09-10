"""v3.37 笔记/知识库增强回归（移动端体验批的服务端配套）。

覆盖三块新契约：
1. `/api/notes/list` 下发 `categories`（**含空分类**）与逐条 `abs_path`
   ——修复"新建分类成功但侧栏看不到"（空分类无笔记，旧聚合法永远漏掉）；
2. `/api/kb/graph` 下发分类枢纽节点（`kind=folder`，含空目录）——
   图谱自动关联新创建的分类与笔记；
3. 孤岛语义不回归：枢纽边只用于布局聚类，笔记的孤儿判定仍是"知识链接"。
"""
import time

from conftest import MEMBER_PASS

from app.config import SHARE_ROOT


def _mk_admin_dir(auth, name):
    assert auth.post(f"/api/mkdir/{name}").status_code == 200


def _write_note(auth, rel_path: str, content: str):
    d, _, name = rel_path.rpartition("/")
    r = auth.post(f"/api/upload/{d}", files={"file": (name, content.encode("utf-8"))})
    assert r.status_code == 200, r.text


def _notes_list(auth, path="笔记"):
    r = auth.get("/api/notes/list", params={"path": path})
    assert r.status_code == 200, r.text
    return r.json()


def _mkdir_under(auth, base, cat):
    assert auth.post(f"/api/mkdir/{_enc(base + '/' + cat)}").status_code == 200


def _enc(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


# ── 1 · 分类清单含空分类 + abs_path ─────────────────────────

def test_notes_list_reports_empty_categories(auth):
    """新建分类（空目录）必须出现在 categories 里，count=0 —— 问题 3 的根因修复。"""
    _mk_admin_dir(auth, "笔记")
    _mkdir_under(auth, "笔记", "空分类")

    j = _notes_list(auth)
    cats = {c["path"]: c for c in j.get("categories", [])}
    assert "空分类" in cats, f"空分类未下发: {sorted(cats)}"
    assert cats["空分类"]["count"] == 0
    assert cats["空分类"]["name"] == "空分类"

    # 放进一篇笔记后 count 变 1
    _write_note(auth, "笔记/空分类/第一篇.md", "# hello\n")
    j = _notes_list(auth)
    cats = {c["path"]: c for c in j["categories"]}
    assert cats["空分类"]["count"] == 1
    # 笔记条目本身也带分类归属
    item = next(i for i in j["items"] if i["name"] == "第一篇.md")
    assert item["category"] == "空分类"


def test_notes_list_items_carry_abs_path(auth):
    """逐条 abs_path（服务器真实存储路径）——手机端"复制路径"与台式 Agent 沟通用。"""
    _mk_admin_dir(auth, "笔记")
    _write_note(auth, "笔记/abs.md", "x")
    j = _notes_list(auth)
    item = next(i for i in j["items"] if i["name"] == "abs.md")
    assert item["abs_path"].startswith(str(SHARE_ROOT).replace("\\", "/"))
    assert item["abs_path"].endswith("笔记/abs.md")
    assert "\\" not in item["abs_path"]


def test_notes_list_hidden_dirs_not_exposed(auth):
    """隐藏目录（.trash/.kb 等）不进分类清单。"""
    _mk_admin_dir(auth, "笔记")
    _mkdir_under(auth, "笔记", "正常分类")
    (SHARE_ROOT / "笔记" / ".秘密目录").mkdir(parents=True, exist_ok=True)
    j = _notes_list(auth)
    cats = [c["path"] for c in j.get("categories", [])]
    assert "正常分类" in cats
    assert not any(c.startswith(".") or "秘密" in c for c in cats)


# ── 2 · 知识图谱分类枢纽 ─────────────────────────────────────

def _kb_graph(auth, path="笔记"):
    r = auth.get("/api/kb/graph", params={"path": path})
    assert r.status_code == 200, r.text
    return r.json()


def test_kb_graph_contains_folder_hubs(auth):
    """分类（含空目录）作为 kind=folder 枢纽节点自动入图 —— 问题 4 的自动关联。"""
    _mk_admin_dir(auth, "笔记")
    _mkdir_under(auth, "笔记", "图谱分类")
    _write_note(auth, "笔记/图谱分类/A.md", "A")
    _write_note(auth, "笔记/图谱分类/B.md", "[[A]]")
    time.sleep(0.05)

    j = _kb_graph(auth)
    folders = {f["path"]: f for f in j.get("folders", [])}
    assert "图谱分类" in folders, f"分类枢纽未下发: {sorted(folders)}"
    hub_id = folders["图谱分类"]["id"]
    assert hub_id.startswith("folder:")

    node = next(n for n in j["nodes"] if n["id"] == hub_id)
    assert node["kind"] == "folder"
    assert node["count"] == 2          # 后代笔记数
    assert node["orphan"] is False

    # 枢纽边不进 edges（度数/反链/孤岛语义保持"知识链接"纯净）
    assert not any(e[0].startswith("folder:") or e[1].startswith("folder:")
                   for e in j["edges"])


def test_kb_graph_folder_hub_empty_category(auth):
    """空分类也入图（新建分类立即在图谱可见，无需先写笔记）。"""
    _mk_admin_dir(auth, "笔记")
    _mkdir_under(auth, "笔记", "空的图谱分类")
    j = _kb_graph(auth)
    folders = {f["path"] for f in j.get("folders", [])}
    assert "空的图谱分类" in folders


def test_kb_graph_orphan_semantics_preserved(auth):
    """有分类但无 [[链接]] 的笔记仍是孤岛 —— 枢纽边不得污染孤岛判定。"""
    _mk_admin_dir(auth, "笔记")
    _mkdir_under(auth, "笔记", "孤岛分类")
    _write_note(auth, "笔记/孤岛分类/无链接.md", "没有链接")
    time.sleep(0.05)
    j = _kb_graph(auth)
    note = next(n for n in j["nodes"] if n["name"] == "无链接.md")
    assert note["orphan"] is True, "枢纽边污染了孤岛判定"


def test_kb_backlinks_not_polluted_by_folders(auth):
    """反链/出链不得混入分类枢纽。"""
    _mk_admin_dir(auth, "笔记")
    _mkdir_under(auth, "笔记", "反链分类")
    _write_note(auth, "笔记/反链分类/源.md", "[[目标]]")
    _write_note(auth, "笔记/反链分类/目标.md", "t")
    time.sleep(0.05)
    r = auth.get("/api/kb/backlinks",
                 params={"path": "笔记", "file": "笔记/反链分类/目标.md"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert all(not str(b["path"]).startswith("folder:") for b in j["backlinks"])
    assert all(not str(o["path"]).startswith("folder:") for o in j["outgoing"])
