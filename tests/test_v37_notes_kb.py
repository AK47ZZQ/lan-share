"""v3.37 笔记增强回归（移动端体验批的服务端配套）。

覆盖块：
1. `/api/notes/list` 下发 `categories`（**含空分类**）与逐条 `abs_path`
   ——修复"新建分类成功但侧栏看不到"（空分类无笔记，旧聚合法永远漏掉）。

（v3.38：知识库/图谱功能整体下线，原第 2/3 块 kb 契约测试一并移除。）
"""
from pathlib import Path

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


def _make_user_local(auth, client):
    """本文件本地造号（invite→redeem→login），调用方负责清理用户行。"""
    import secrets as _secrets
    from fastapi.testclient import TestClient as _TC
    from app.main import app as _app
    username = f"u{_secrets.token_hex(3)}"
    r = auth.post("/api/admin/invites", json={"role": "member", "hours": 1})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = client.post("/api/invites/redeem", json={
        "code": code, "username": username, "password": "Mm123456!"})
    assert r.status_code == 200, r.text
    c = _TC(_app, base_url="https://testserver")
    r = c.post("/api/login", json={"username": username, "password": "Mm123456!"})
    assert r.status_code == 200, r.text
    return c, username


def test_notes_categories_respect_visibility(auth, client):
    """2026-11-08 夜批对抗审查修复钉：分类目录侧补 visible 逐节点收口——
    path=/ 时他人 private 下的分类目录名不得泄漏进 categories。"""
    m, uname = _make_user_local(auth, client)
    try:
        # 直落盘造数据（mkdir 通道经 clean_filename 洗冒号，无法寻址 private）：
        # 成员私有分类 + **他人的**（admin）私有分类——负面断言必须有真实他人数据
        from app.config import SHARE_ROOT as _SR
        import shutil as _shutil
        auth_admin = auth.username
        mine = Path(_SR) / "private" / uname / "笔记" / "成员私密分类"
        others = Path(_SR) / "private" / auth_admin / "笔记" / "管理员私人分类"
        mine.mkdir(parents=True, exist_ok=True)
        (mine / "x.md").write_text("# 秘密", encoding="utf-8")
        others.mkdir(parents=True, exist_ok=True)
        (others / "y.md").write_text("# 管理员的", encoding="utf-8")
        # 成员视角查全树根：自己的 private 分类必须可见（App 分类树依赖），
        # 他人 private 目录名必须不可见（缺陷：目录侧漏 visible 收口）
        r = m.get("/api/notes/list", params={"path": "/"})
        assert r.status_code == 200, r.text
        cats = {c["path"] for c in r.json()["categories"]}
        assert f"private/{uname}/笔记/成员私密分类" in cats, \
            f"成员应能看到自己 private 下的分类: {sorted(cats)[:8]}…"
        assert "private/{}/笔记/管理员私人分类".format(auth_admin) not in cats, \
            f"他人 private 目录名泄漏进 categories: {sorted(c for c in cats if c.startswith('private/'))}"
        own = f"private/{uname}"
        assert not any(c.startswith("private/") and not (c == own or c.startswith(own + "/"))
                       for c in cats), \
            f"他人 private 目录名不得出现在 categories: {sorted(c for c in cats if c.startswith('private/'))}"
    finally:
        import shutil as _shutil
        _shutil.rmtree(Path(_SR) / "private" / uname, ignore_errors=True)
        _shutil.rmtree(Path(_SR) / "private" / auth.username / "笔记" / "管理员私人分类",
                       ignore_errors=True)
        from app.db import _db
        row = _db().execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
        if row:
            _db().execute("DELETE FROM users WHERE id=?", (row["id"],))
            _db().commit()
