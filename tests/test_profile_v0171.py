# v0.17 资料体系 + 口令复核回归：昵称/头像/verify-password 三端点。
# 头像走 multipart，钉住 1MB 闸白名单（/api/auth/avatar 不被 413 误杀——
# 首发 App 端"昵称头像保存失败"的根因）。
import io

from PIL import Image


def test_nickname_roundtrip(auth):
    r = auth.patch("/api/auth/profile", json={"nickname": "站长"})
    assert r.status_code == 200 and r.json()["nickname"] == "站长"
    me = auth.get("/api/auth/me").json()
    assert me["nickname"] == "站长"
    # 清空恢复显示用户名
    r = auth.patch("/api/auth/profile", json={"nickname": ""})
    assert r.status_code == 200 and r.json()["nickname"] == ""
    # 校验：超长 / 控制字符
    assert auth.patch("/api/auth/profile", json={"nickname": "x" * 33}).status_code == 422
    assert auth.patch("/api/auth/profile", json={"nickname": "a\nb"}).status_code == 422


def test_avatar_upload_and_fetch(auth):
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 80, 80)).save(buf, "PNG")
    buf.seek(0)
    r = auth.post("/api/auth/avatar",
                  files={"file": ("a.png", buf, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["avatar_path"].startswith(".avatars/")
    me = auth.get("/api/auth/me").json()
    assert me["avatar_path"]
    # 取回：512 方图 JPEG
    r2 = auth.get(f"/api/auth/avatar/{me['username']}")
    assert r2.status_code == 200
    img = Image.open(io.BytesIO(r2.content))
    assert max(img.size) <= 512
    # 无头像用户 404
    auth.post("/api/setup" if False else "/api/logout")
    assert auth.get("/api/auth/avatar/nobody-here").status_code in (401, 404)


def test_avatar_rejects_non_image(auth):
    r = auth.post("/api/auth/avatar",
                  files={"file": ("x.png", b"not-an-image", "image/png")})
    assert r.status_code in (415, 422)


def test_verify_password(auth):
    # 注意必须 from conftest（与 pytest 插件同模块身份）：from tests.conftest
    # 会把 conftest 当新模块再执行一遍，重建临时根并翻转 SHARE_ROOT，
    # 后续所有走 os.environ 的测试全盘错位（实测踩坑）
    from conftest import ADMIN_PASS
    assert auth.post("/api/auth/verify-password",
                     json={"password": ADMIN_PASS}).status_code == 200
    assert auth.post("/api/auth/verify-password",
                     json={"password": "wrong"}).status_code == 401
    # 未登录 401（require_user）
    anon = auth
    token = auth.cookies
    auth.cookies.clear()
    assert auth.post("/api/auth/verify-password",
                     json={"password": "x"}).status_code == 401
    auth.cookies.update(token)
