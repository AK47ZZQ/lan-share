"""反馈通道回归（v0.28/v3.41）：App 反馈 → data/反馈/ 落盘 → Agent 改文件翻状态。

覆盖：multipart 提交（含截图）、own-only 可见性、admin 全量、图片鉴权读取、
状态由 Agent 直接改 frontmatter 文件后列表即时反映、空标题/超图数拒绝。
"""
import os

from conftest import MEMBER_PASS

from app.config import SHARE_ROOT

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _rand() -> str:
    return os.urandom(3).hex()


def _member(client):
    admin = client.__class__(client.app, base_url="https://testserver")
    assert admin.post("/api/login", json={
        "username": "admin", "password": "test-pass-123"}).status_code == 200
    inv = admin.post("/api/admin/invites", json={"role": "member", "hours": 24},
                     cookies=dict(admin.cookies)).json()
    username = "fb" + _rand()
    assert client.post("/api/invites/redeem", json={
        "code": inv["code"], "username": username,
        "password": MEMBER_PASS}).status_code == 200
    c = client.__class__(client.app, base_url="https://testserver")
    assert c.post("/api/login", json={
        "username": username, "password": MEMBER_PASS}).status_code == 200
    return c, username


def _post_feedback(c, title, ftype="idea", body="", images=0):
    # 表单字段并入 files（(None, value) = 普通字段）：TestClient 对 data+files
    # 混合会走 chunked，被反灌包中间件拒（要求 Content-Length）
    parts = [("title", (None, title)), ("type", (None, ftype)),
             ("body", (None, body)), ("app_ver", (None, "0.28.0+74")),
             ("device", (None, "TestDroid/Android 16"))]
    parts += [("files", (f"s{i}.png", PNG, "image/png")) for i in range(images)]
    return c.post("/api/feedback", files=parts)


def test_feedback_create_list_and_visibility(auth, client):
    m, mname = _member(client)
    title = f"底栏错位-{_rand()}"
    r = _post_feedback(m, title, ftype="bug", body=" steps: 1) 打开文件页 2) 多选",
                       images=2)
    assert r.status_code == 200, r.text
    fid = r.json()["id"]

    # 文件落盘（Agent 工作面）
    fb_file = SHARE_ROOT / "反馈" / f"{fid}.md"
    assert fb_file.is_file()
    text = fb_file.read_text(encoding="utf-8")
    assert "状态: 已提交" in text and f"用户: {mname}" in text
    img = SHARE_ROOT / "反馈" / "img" / f"{fid}-1.png"
    assert img.is_file() and img.read_bytes().startswith(b"\x89PNG")

    # 自己可见；别人（admin 走 own 列表）不混入
    mine = m.get("/api/feedback").json()["items"]
    hit = next(i for i in mine if i["id"] == fid)
    assert hit["status"] == "已提交" and hit["type"] == "bug"
    assert len(hit["images"]) == 2

    # admin 全量接口可见（含用户名）
    allitems = auth.get("/api/admin/feedback").json()["items"]
    assert any(i["id"] == fid and i["user"] == mname for i in allitems)

    # 未登录不可读列表/图片
    assert m.__class__(client.app, base_url="https://testserver") \
        .get("/api/feedback").status_code == 401


def test_feedback_image_authz(auth, client):
    m, _ = _member(client)
    fid = _post_feedback(m, f"带图-{_rand()}", images=1).json()["id"]

    # 本人可读截图
    r = m.get(f"/api/feedback/{fid}/img/{fid}-1.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"

    # 另一个成员不可读、不可看详情
    other, _ = _member(client)
    assert other.get(f"/api/feedback/{fid}/img/{fid}-1.png").status_code == 403
    assert other.get(f"/api/feedback/{fid}").status_code == 403

    # admin 可读
    assert auth.get(f"/api/feedback/{fid}/img/{fid}-1.png").status_code == 200
    # 非法图片名拒绝
    assert m.get(f"/api/feedback/{fid}/img/../../secret.png").status_code in (400, 404)


def test_agent_flips_status_via_file(auth, client):
    """Agent 工作流：直接改 frontmatter 的 状态/备注 → 列表即时反映。"""
    m, _ = _member(client)
    fid = _post_feedback(m, f"想法-{_rand()}", ftype="idea").json()["id"]

    f = SHARE_ROOT / "反馈" / f"{fid}.md"
    text = f.read_text(encoding="utf-8")
    text = text.replace("状态: 已提交", "状态: 已实装") \
               .replace("备注:", "备注: v0.28.0+74 已实装")
    f.write_text(text, encoding="utf-8")

    hit = next(i for i in m.get("/api/feedback").json()["items"]
               if i["id"] == fid)
    assert hit["status"] == "已实装"
    assert hit["note"] == "v0.28.0+74 已实装"


def test_feedback_validation(auth):
    assert _post_feedback(auth, "   ").status_code == 422          # 空标题
    assert _post_feedback(auth, "超图", images=4).status_code == 422  # 超 3 张
    assert auth.get("/api/feedback/20990101000000-ff").status_code == 404
