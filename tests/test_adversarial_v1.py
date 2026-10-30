"""对抗式安全回归（v1.0.0 发版前红队轮，2026-09-13）。

以攻击者视角对**安全红线**做端到端钉死：路径解析穿越（各类编码 / Windows
语义）、跨用户越权（IDOR）、`X-As-User` 提权伪造、限速桶隔离。

价值：把红队探针固化成回归网——v1.0.0 之后任何改动打破任一防线立即变红。
取向：只钉"必须被拒绝 / 必须被隔离"的不变量，不钉实现细节。

红线依据：见 docs/SECURITY.md 与 README 安全章节。
"""
import os
import secrets

import pytest
from fastapi.testclient import TestClient

from conftest import MEMBER_PASS2
from app.config import SHARE_ROOT
from app.db import _db
from app.main import app

SECRET_BODY = "TOP-SECRET-ADV-2026"


@pytest.fixture()
def make_user(auth, client):
    """邀请码建号工厂（与 test_review_fixes 同款）。"""
    created = []

    def _make(role, name=None):
        username = name or f"a{secrets.token_hex(3)}"
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


def _plant(username: str, name: str, content: str = SECRET_BODY):
    d = os.path.join(SHARE_ROOT, "private", username)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(content)


# ─── ① 路径穿越 / 编码绕过（打 /preview 与 /api/files 两个入口）────────────

# 每条都指向"读受害者的 private/secret.txt"，用的是不同编码/语义变体
TRAVERSAL_PAYLOADS = [
    "../private/{v}/secret-adv.txt",              # 裸 ..
    "..%2fprivate/{v}/secret-adv.txt",            # 单层 URL 编码
    "%2e%2e/private/{v}/secret-adv.txt",          # 点被编码
    "%252e%252e/private/{v}/secret-adv.txt",      # 双重编码
    "..\\private\\{v}\\secret-adv.txt",           # 反斜杠
    "....//private/{v}/secret-adv.txt",           # 多重点
    "..%5cprivate%5c{v}%5csecret-adv.txt",        # 反斜杠编码
    "\uff0e\uff0e/private/{v}/secret-adv.txt",    # 全角点
    "..\u2044private/{v}/secret-adv.txt",         # U+2044 fraction slash
    "private/{v}/../{v}/secret-adv.txt",          # 合法段内夹 ..
    "/etc/passwd",                                # 绝对路径
    "C:/Windows/win.ini",                         # 盘符
    "\\\\server\\share\\x",                        # UNC
]


@pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
def test_preview_traversal_all_denied(auth, make_user, payload):
    """红队①：任意编码的穿越串都不得读到他人 private 内容。

    注意：不匹配任何路由的路径会落到 SPA 深链回退（index.html, 200 text/html）
    ——这是前端兜底，**不是数据泄露**。故不变量是"响应体不含机密"，而非"非 200"。
    """
    _plant(auth.username, "secret-adv.txt")
    member = make_user("member")
    p = payload.format(v=auth.username)
    r = member.get(f"/preview/{p}")
    assert SECRET_BODY not in r.text, f"穿越读到机密: {payload!r}"
    assert "secret-adv.txt" not in r.text, f"穿越泄露文件名: {payload!r}"


@pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
def test_files_list_traversal_all_denied(auth, make_user, payload):
    """红队①（列表入口）：目录列表同样不得穿越/不得列出他人 private。"""
    _plant(auth.username, "secret-adv.txt")
    member = make_user("member")
    p = payload.format(v=auth.username)
    r = member.get(f"/api/files/{p}")
    assert SECRET_BODY not in r.text, f"列表穿越泄露内容: {payload!r}"
    if r.status_code == 200:
        assert "secret-adv.txt" not in r.text, f"列表穿越列出他人文件: {payload!r}"


def test_thumb_traversal_denied(auth, make_user):
    """红队①（缩略图入口）：/thumb 也不得成为穿越的侧门。

    `/thumb/../private/<他人>/x.png` 不匹配 /thumb 路由 → 落 SPA 回退返回
    HTML（200 但无内容），因此断言"不得返回图片/不得含机密"，不看状态码。
    """
    _plant(auth.username, "secret-adv.txt")
    member = make_user("member")
    for p in (f"/thumb/../private/{auth.username}/secret-adv.txt",
              f"/thumb/private/{auth.username}/secret-adv.txt"):
        r = member.get(p)
        assert SECRET_BODY not in r.text
        if r.status_code == 200:
            ct = r.headers.get("content-type", "")
            assert "image" not in ct, f"缩略图侧门返回了图片: {p} ({ct})"


def test_vpath_private_prefix_never_crosses_account(auth, make_user):
    """红队①（虚拟路径）：shared:private/<他人> 必须映射到请求者本人空间。"""
    _plant(auth.username, "secret-adv.txt")
    member = make_user("member")
    for vp in (f"shared:private/{auth.username}/secret-adv.txt",
               f"private:{auth.username}/secret-adv.txt"):
        r = member.get(f"/preview/{vp}")
        assert r.status_code != 200, vp
        assert SECRET_BODY not in r.text


# ─── ①b resolve() 直调单元级穿越钉死 ────────────────────────────────
# ⚠️ 为什么必须有这一节（2026-09-13 消融实验结果）：
# 上面的 HTTP 级穿越用例**无法通过消融验证**——httpx/Starlette 会在路由匹配
# 阶段就把含 `..` 的 URL 归一/拒掉（返回 404 SPA 兜底），敌意串根本到不了
# 应用层；而 %2f 编码形态即便到了，逃逸目标在临时目录布局里也恰好不存在，
# 于是"被防线挡住"与"指向了空处"在 HTTP 观测上不可区分。
# 消融三处防线（_clean_parts 的 .. 拒绝 / safe_path 的 .. 拒绝 / safe_path 的
# 根前缀校验）后 HTTP 用例仍全绿——**这正是"消融不红必须查到原因"的实例**：
# 原因是测试选错了观测层，不是防线多余。
# 因此这里直调 resolve()，绕开 HTTP 层，让防线失效必然暴露为返回值逃逸。

def _mk_user(username, role="member", uid=9001):
    """构造 resolve() 需要的 user 形状（sqlite3.Row 的键访问约定）。"""
    return {"username": username, "role": role, "user_id": uid, "quota_bytes": None}


@pytest.mark.parametrize("payload", [
    "../private/victim/secret.txt",
    "..\\private\\victim\\secret.txt",
    "a/../../private/victim/secret.txt",
    "private/../private/victim/secret.txt",
    "./../private/victim/secret.txt",
    ".././private/victim/secret.txt",
])
def test_resolve_rejects_traversal(payload):
    """resolve() 必须对含 `..` 的输入抛 HTTPException（不经 HTTP 层）。"""
    from app.spaces import resolve
    u = _mk_user("attacker")
    with pytest.raises(Exception) as ei:
        resolve(u, payload, "read")
    assert getattr(ei.value, "status_code", None) == 403, (
        f"resolve 未拒绝穿越 {payload!r}：{ei.value!r}")


@pytest.mark.parametrize("payload", [".trash/x", ".meta.db", ".thumbs/a.jpg",
                                     "private/./.hidden/f"])
def test_resolve_rejects_dot_prefixed(payload):
    """点前缀（隐藏）路径一律拒绝——.trash/.meta.db/.thumbs 不得经 API 触达。"""
    from app.spaces import resolve
    u = _mk_user("attacker")
    with pytest.raises(Exception) as ei:
        resolve(u, payload, "read")
    assert getattr(ei.value, "status_code", None) == 403, payload


def test_resolve_output_always_inside_share_root():
    """不变量：resolve() 对任意合法输入，返回值必须落在 SHARE_ROOT 内。"""
    from app.spaces import resolve
    u = _mk_user("attacker")
    for p in ("", "a/b/c.txt", "private:x/y.txt", "shared:z.txt",
              "incoming/attacker/f.txt", "照片/相册/p.jpg"):
        out = resolve(u, p, "read")
        root = os.path.normcase(str(SHARE_ROOT))
        assert os.path.normcase(str(out)).startswith(root), f"{p} -> {out}"


def test_resolve_private_vpath_never_crosses_account():
    """不变量：shared:private/<他人> 必须映射到请求者本人空间（不经 HTTP）。

    注意语义：`shared:private/victim/x` 会落到**请求者自己的**
    `private/attacker/victim/x`（victim 只是个普通子目录名）——这是正确行为，
    跨用户寻址在任何写法下都不存在。故断言"物理路径的第二段是 attacker"。
    """
    from app.spaces import resolve
    u = _mk_user("attacker")
    out = resolve(u, "shared:private/victim/secret.txt", "read")
    norm = str(out).replace("\\", "/")
    root = str(SHARE_ROOT).replace("\\", "/")
    rel = norm[len(root):].lstrip("/")
    segs = rel.split("/")
    assert segs[:2] == ["private", "attacker"], (
        f"未落在请求者本人 private：{rel}")
    # 反向：绝不存在指向他人 private 根的写法
    assert not rel.startswith("private/victim/"), f"越权映射：{rel}"


# ─── ② 跨用户越权（IDOR）──────────────────────────────────────────────

def test_member_cannot_restore_others_trash(auth, make_user):
    """红队②：成员不得恢复他人回收站条目。"""
    member = make_user("member")
    _plant(auth.username, "trash-me.txt")
    # admin 删自己的文件 → 进回收站 → 拿条目 id
    r = auth.delete(f"/api/files/private/{auth.username}/trash-me.txt")
    assert r.status_code == 200, r.text
    rows = _db().execute("SELECT id FROM trash").fetchall()
    assert rows, "应有一条回收站记录"
    tid = rows[-1]["id"]
    r = member.post(f"/api/trash/{tid}/restore")
    assert r.status_code in (403, 404), f"越权恢复被放行: {r.status_code}"


def test_member_cannot_purge_others_trash(auth, make_user):
    """红队②：成员不得彻底删除他人回收站条目。"""
    member = make_user("member")
    _plant(auth.username, "purge-me.txt")
    auth.delete(f"/api/files/private/{auth.username}/purge-me.txt")
    rows = _db().execute("SELECT id FROM trash").fetchall()
    tid = rows[-1]["id"]
    r = member.delete(f"/api/trash/{tid}")
    assert r.status_code in (403, 404), f"越权彻底删除被放行: {r.status_code}"


def test_member_cannot_download_others_share_records(auth, make_user):
    """红队②：他人分享的取件记录不可见（owner 隔离）。"""
    member = make_user("member")
    _plant(auth.username, "shared-file.txt")
    r = auth.post("/api/share",
                  data={"path": f"private/{auth.username}/shared-file.txt",
                        "expires_hours": "1"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    # 成员尝试读该分享的取件记录
    r = member.get(f"/api/shares/{token}/history")
    assert r.status_code in (403, 404), f"越权读取他人分享记录: {r.status_code}"
    # 成员也不得撤销他人分享
    r = member.delete(f"/api/shares/{token}")
    assert r.status_code in (403, 404), f"越权撤销他人分享: {r.status_code}"


# ─── ③ X-As-User 提权伪造 ─────────────────────────────────────────────

def test_member_cannot_use_x_as_user_to_escalate(auth, make_user):
    """红队③：普通成员伪造 X-As-User 头不得产生任何提权效果。"""
    _plant(auth.username, "secret-adv.txt")
    member = make_user("member")
    # 伪造为 admin 身份去读 admin 的 private
    r = member.get(f"/api/files/private/{auth.username}",
                   headers={"X-As-User": auth.username})
    assert r.status_code != 200, "成员用 X-As-User 看到了他人空间"
    assert "secret-adv.txt" not in r.text


def test_x_as_user_requires_admin_and_audits(auth, make_user):
    """红队③：admin 才是合法使用者——正常路径仍然可用（防误杀）。"""
    _plant(auth.username, "secret-adv.txt")
    r = auth.get(f"/api/files/private/{auth.username}",
                 headers={"X-As-User": auth.username})
    assert r.status_code == 200, r.text


def test_x_as_user_to_nonexistent_member_404(auth):
    """红队③：指向不存在/停用成员应 404（不泄露是否存在）。"""
    r = auth.get("/api/files/", headers={"X-As-User": "no-such-user-xyz"})
    assert r.status_code in (404, 403)


# ─── ④ 免登录面信息泄露 ───────────────────────────────────────────────

def test_healthz_hidden_from_public_host(client):
    """红队④：healthz 不得在公网入口暴露（磁盘水位信息）。"""
    r = client.get("/healthz", headers={"Host": "frp-adv.example.com:35391"})
    assert r.status_code == 404


def test_setup_closed_after_first_admin(client, auth):
    """红队④：setup 在已有管理员后必须关闭（防重置接管）。"""
    r = client.post("/api/setup", json={"username": "hijack", "password": "pass-12345678"})
    assert r.status_code == 403


def test_app_entries_needs_no_auth_but_no_secrets(client):
    """红队④：/api/app/entries 免登录可用，但不得泄露路径/用户名等内部信息。"""
    r = client.get("/api/app/entries")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        body = r.text
        leaks = ["private/", "password", "token_hash",
                 str(SHARE_ROOT).replace("\\", "/")]
        for leak in leaks:
            assert leak not in body, f"entries 泄露了 {leak}"


# ─── ⑤ 限速桶隔离（882a 关联）────────────────────────────────────────

def test_upload_bucket_isolated_from_api(auth):
    """红队⑤：上传洪峰不得让交互 API 也 429（882a 红队视角复核）。

    与 test_zz_rate_limit 的用例互为犄角：那条测"上传桶打满后交互仍可用"，
    这条测"交互桶打满后上传仍可用"（反向），确保两个桶真正相互独立。
    """
    from app.security import reset_auth_gates
    from app.config import API_RATE_LIMIT
    reset_auth_gates()
    try:
        # 打满交互桶
        for _ in range(API_RATE_LIMIT + 10):
            auth.get("/api/auth/me")
        # 上传桶应独立（不被交互桶的用量影响）——init 是上传面最小请求
        r = auth.post("/api/upload/init",
                      json={"path": "照片/手机相册/x.jpg", "size": 1})
        assert r.status_code != 429, "上传桶被交互桶连坐（桶未真正隔离）"
    finally:
        reset_auth_gates()


# ─── ⑥ 路由遮蔽（bug·249b：网页 /files 刷新白屏）────────────────────

def test_web_files_page_serves_spa_not_api(auth):
    """红队⑥：网页文件页路由 `/files` 必须返回 SPA，不得被下载端点遮蔽。

    bug·249b 根因：裸 `/files` 曾注册为下载端点（与 Vue 路由 `/files` 冲突），
    刷新时落到下载端点 → resolve("") 得 SHARE_ROOT 目录 → JSON 400
    「目录请在界面打包下载」→ SPA 永不启动 = 用户看到的白屏。
    修复：只保留 `/files/{path:path}`（该形态不匹配无路径的裸 `/files`），
    裸 `/files` 落到 SPA 深链回退。
    """
    r = auth.get("/files", follow_redirects=False)
    assert r.status_code == 200, f"/files 应回 SPA，实得 {r.status_code}: {r.text[:120]}"
    assert "text/html" in r.headers.get("content-type", ""), (
        f"/files 返回了非 HTML（下载端点遮蔽又复发）：{r.headers.get('content-type')}")
    assert "目录请在界面打包下载" not in r.text, "回退到了下载端点的 400 文案"


def test_web_download_route_still_works(auth, workdir):
    """红队⑥（防误杀）：去掉裸路由后，带路径的下载端点必须仍然可用。"""
    import os
    from app.config import SHARE_ROOT
    rel = f"{workdir}/dl-probe.txt"
    with open(os.path.join(SHARE_ROOT, rel), "w", encoding="utf-8") as f:
        f.write("DL-CONTENT-2026")
    r = auth.get(f"/files/{rel}")
    assert r.status_code == 200, r.text
    assert "DL-CONTENT-2026" in r.text


def test_all_web_pages_serve_spa(auth):
    """红队⑥（面覆盖）：所有前端页面路由都必须返回 SPA HTML（防同类遮蔽）。"""
    for p in ("/", "/files", "/photos", "/trash", "/shares", "/activity",
              "/status", "/governance", "/notify-ops", "/share-ops", "/settings"):
        r = auth.get(p, follow_redirects=False)
        assert r.status_code == 200, f"{p} -> {r.status_code}"
        assert "text/html" in r.headers.get("content-type", ""), (
            f"{p} 未返回 SPA：{r.headers.get('content-type')}")


# ─── ⑦ 上传会话授权 fail-closed（v1.0.0 终审发现）────────────────────

def test_upload_session_null_owner_denied_to_non_admin(auth, make_user):
    """红队⑦：无主（user_id 为 NULL）上传会话不得对普通成员放行。

    原守卫 `if row["user_id"] and ...` 在 user_id 为 NULL 时短路放行——
    finish 端点会以**请求者**身份 resolve + 落盘，等于任意登录用户可收尾
    他人遗留会话（写坏在途上传 / 提前 finish 的 DoS）。user_id 是后加列
    （ALTER TABLE 迁移）可空，历史行即 NULL，故必须 fail-closed。
    """
    from app.api.uploads import _session_owner_guard
    from fastapi import HTTPException

    class _Row(dict):
        def __getitem__(self, k):
            return dict.get(self, k)

    orphan = _Row(user_id=None, dest_dir="", filename="x.bin", total_size=1)
    member = {"username": "m1", "role": "member", "user_id": 7}
    with pytest.raises(HTTPException) as ei:
        _session_owner_guard(orphan, member)
    assert ei.value.status_code == 403, "无主会话对成员 fail-open（应 403）"

    # admin 放行（收尾权保留）
    admin = {"username": "admin", "role": "admin", "user_id": 1}
    _session_owner_guard(orphan, admin)   # 不抛异常即通过

    # 本人会话正常放行（防误杀）
    mine = _Row(user_id=7, dest_dir="", filename="x.bin", total_size=1)
    _session_owner_guard(mine, member)

    # 他人会话必须拒绝
    others = _Row(user_id=99, dest_dir="", filename="x.bin", total_size=1)
    with pytest.raises(HTTPException) as ei2:
        _session_owner_guard(others, member)
    assert ei2.value.status_code == 403
