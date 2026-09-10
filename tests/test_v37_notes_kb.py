"""v3.37 笔记增强回归（移动端体验批的服务端配套）。

覆盖块：
1. `/api/notes/list` 下发 `categories`（**含空分类**）与逐条 `abs_path`
   ——修复"新建分类成功但侧栏看不到"（空分类无笔记，旧聚合法永远漏掉）。

（v3.38：知识库/图谱功能整体下线，原第 2/3 块 kb 契约测试一并移除。）
"""
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


# ── 分类清单含空分类 + abs_path ──────────────────────────────

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
    """隐藏目录（.trash 等）不进分类清单。"""
    _mk_admin_dir(auth, "笔记")
    _mkdir_under(auth, "笔记", "正常分类")
    (SHARE_ROOT / "笔记" / ".秘密目录").mkdir(parents=True, exist_ok=True)
    j = _notes_list(auth)
    cats = [c["path"] for c in j.get("categories", [])]
    assert "正常分类" in cats
    assert not any(c.startswith(".") or "秘密" in c for c in cats)
