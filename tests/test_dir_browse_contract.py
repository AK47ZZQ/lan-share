"""目录浏览契约测试（bug·a6df 修复批，2026-09-13）。

App 目录选择器（dir_picker_page）与服务端相册页（server_albums_page）依赖
/api/files 的四条行为契约，此前全部靠"碰巧没坏"，本文件逐条钉死防漂移：

1. items 每条**恒有** `is_dir` 布尔字段——App 修复后按 is_dir 过滤，字段
   缺失会被防御性剔除 → 选择器静默空目录（比旧 bug 更隐蔽）；
2. 文件路径请求列表 → 400 "Not a directory"（列表端点对文件的既定语义）；
3. 空路径**不带尾斜杠**（GET /api/files）与带尾斜杠（/api/files/）都 200
   ——FilesApi.browse 与 listDir 两种拼法分用，注释明言勿混改；
4. `+`/中文等特殊字符文件名端到端无损：列表原样返回、preview 可读——
   App 笔记标题实采字符集（bug·a6df 现场文件 7小时+提示词.md）。
"""
import pytest

from app.config import SHARE_ROOT


def _mkdir(c, path):
    r = c.post(f"/api/mkdir/{path}")
    assert r.status_code == 200, r.text


def _put(c, path, content=b"data"):
    # /api/upload/{dir} 语义 = 落到该目录下（文件名取 multipart filename）
    parent, name = path.rsplit("/", 1)
    r = c.post(f"/api/upload/{parent}", files={"file": (name, content)})
    assert r.status_code == 200, r.text


def test_items_contract(auth, workdir):
    _mkdir(auth, workdir)
    _mkdir(auth, f"{workdir}/sub")
    _put(auth, f"{workdir}/note.md")
    r = auth.get(f"/api/files/{workdir}")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "目录列表不应为空"
    for it in items:
        assert isinstance(it["is_dir"], bool), f"is_dir 缺失或非布尔: {it}"
    by_name = {it["name"]: it for it in items}
    assert by_name["sub"]["is_dir"] is True
    assert by_name["note.md"]["is_dir"] is False


def test_file_path_is_400(auth, workdir):
    _mkdir(auth, workdir)
    _put(auth, f"{workdir}/note.md")
    r = auth.get(f"/api/files/{workdir}/note.md")
    assert r.status_code == 400
    assert r.json()["detail"] == "Not a directory"


def test_root_listing_with_and_without_trailing_slash(auth, workdir):
    _mkdir(auth, workdir)
    for url in ("/api/files", "/api/files/"):
        r = auth.get(url)
        assert r.status_code == 200, f"{url} 应 200"
        # 两种拼法返回同一棵树，workdir 目录都在场且 is_dir=True
        by_name = {it["name"]: it for it in r.json()["items"]}
        assert by_name[workdir]["is_dir"] is True
    # 私人空间入口标记只在根列表出现一次（App DirPicker 的 🔒 图标数据源）；
    # 先访问一次私人目录（resolve 自动建目录）让根下 private/ 存在
    assert auth.get("/api/files/private").status_code == 200
    roots = [it for it in auth.get("/api/files").json()["items"]
             if it.get("is_private_root")]
    assert len(roots) == 1
    assert roots[0]["is_dir"] is True


def test_special_char_filename_end_to_end(auth, workdir):
    name = "7小时+提示词.md"
    _put(auth, f"{workdir}/{name}", "正文".encode("utf-8"))
    # 列表原样返回（不转码/不丢字段）
    r = auth.get(f"/api/files/{workdir}")
    by_name = {it["name"]: it for it in r.json()["items"]}
    assert name in by_name and by_name[name]["is_dir"] is False
    # 文件路径请求列表 → 400（不是 404/500：文件存在，只是"不是目录"）
    r = auth.get(f"/api/files/{workdir}/{name}")
    assert r.status_code == 400
    # preview 通道可读（App 文档查看器的实际取文通道）
    r = auth.get(f"/preview/{workdir}/{name}")
    assert r.status_code == 200
    assert r.content == "正文".encode("utf-8")


def test_missing_dir_is_404(auth):
    r = auth.get("/api/files/definitely-not-here-xyz")
    assert r.status_code == 404
