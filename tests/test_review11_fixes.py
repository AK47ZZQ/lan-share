"""11 号独立复审（2026-09-10）修复批的针对性回归测试。

每项问题一条可执行断言——把"已修复"从叙述变成证据。
"""
import os
import urllib.request

import pytest

from test_extras import _admin, _bare, _member_session, _mk_member

from app.services.webhook import (_ValidatingRedirectHandler,
                                  assert_public_webhook_url)


def _rand() -> str:
    return os.urandom(3).hex()


# ─── P2-4 · Webhook SSRF：重定向逐跳复校验 ─────────────────

def test_webhook_redirect_to_loopback_is_rejected():
    """公网域名 302 到 127.0.0.1 —— 修复前 urlopen 会静默跟随（SSRF 绕过）。"""
    h = _ValidatingRedirectHandler()
    req = urllib.request.Request("https://hooks.example.com/x")
    with pytest.raises(ValueError):
        h.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1:9/x")


def test_webhook_redirect_to_private_range_is_rejected():
    h = _ValidatingRedirectHandler()
    req = urllib.request.Request("https://hooks.example.com/x")
    with pytest.raises(ValueError):
        h.redirect_request(req, None, 302, "Found", {}, "http://192.168.1.100/hook")


def test_webhook_literal_internal_targets_rejected():
    """初始化校验：字面内网/回环/链路本地一律拒绝。"""
    for bad in ("http://127.0.0.1:9/x", "http://192.168.1.100/x",
                "http://[::1]/x", "http://169.254.1.1/x", "ftp://example.com/x"):
        with pytest.raises(ValueError):
            assert_public_webhook_url(bad)


def test_webhook_public_host_allowed():
    """公网域名放行（不因加固而误伤正常渠道）。"""
    assert_public_webhook_url("https://ntfy.sh/mytopic")


def test_webhook_open_seam_uses_validating_opener():
    """守卫：出网出口必须挂着逐跳校验 handler。

    防止将来有人"顺手"把 `_safe_opener` 改回裸 `urllib.request.urlopen`——
    那样重定向校验会被静默绕过，且没有任何测试会红。
    """
    import app.api.notify as notify
    assert any(isinstance(h, notify._ValidatingRedirectHandler)
               for h in notify._safe_opener.handlers), \
        "_safe_opener 未挂 _ValidatingRedirectHandler，重定向 SSRF 校验失效"


# ─── P2-5 · WebDAV 真锁（原实现返回 token 却不记状态 = 假锁）──

def test_dav_options_advertises_class_two(auth):
    """LOCK 现在是真锁，因此 OPTIONS 声明 DAV: 1, 2 才不算撒谎。"""
    r = auth.request("OPTIONS", "/dav/")
    assert r.status_code == 200
    classes = {s.strip() for s in r.headers["dav"].split(",")}
    assert classes == {"1", "2"}, f"DAV 能力声明异常: {r.headers['dav']}"


def test_dav_lock_blocks_other_user_then_owner_releases(client):
    """完整锁生命周期：他人写入被 423 拦下 → 非锁主 UNLOCK 被拒 → 锁主释放后放行。

    （2026-09-19 对抗审查 F1 收紧：LOCK 要求写权（RFC 4918 §9.10），故锁主
    member 必须持目标目录写授权——原版用只读 member 拿写锁挡 admin 写入，
    恰是把"只读者可写 DoS 全体"的越权面当预期固化了。）"""
    d = f"davlock-{_rand()}"
    admin = _admin(client)
    assert admin.post(f"/api/mkdir/{d}").status_code == 200
    assert admin.post(f"/api/upload/{d}", files={"file": ("f.txt", b"v1")}).status_code == 200

    uname = f"davlocku-{_rand()}"
    _mk_member(client, uname)
    from app.db import _db
    uid = _db().execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()["id"]
    assert admin.post("/api/admin/grants", json={
        "user_id": uid, "path": d, "can_read": True, "can_write": True}).status_code == 200
    member = _member_session(client, uname)

    dav = f"/dav/{d}/f.txt"
    r = member.request("LOCK", dav)
    assert r.status_code == 200, r.text
    token = r.headers["lock-token"].strip("<>")
    assert token.startswith("opaquelocktoken:")

    # ① 他人（admin）未出示 token 的写入 → 423 Locked（修复前会静默成功并互踩）
    r = admin.request("PUT", dav, content=b"v2-admin")
    assert r.status_code == 423, f"锁未生效，实际 {r.status_code}"

    # ② 非锁主不能 UNLOCK
    r = admin.request("UNLOCK", dav, headers={"Lock-Token": f"<{token}>"})
    assert r.status_code == 403

    # ③ 锁主释放后写入放行
    assert member.request("UNLOCK", dav,
                          headers={"Lock-Token": f"<{token}>"}).status_code == 204
    assert admin.request("PUT", dav, content=b"v3").status_code in (201, 204)


def test_dav_lock_is_per_user_not_shared(client):
    """同一用户的新会话不算冲突（锁按 username 归属，不按会话）。"""
    import app.api.webdav as wd
    assert callable(wd.reset_dav_locks)
    wd.reset_dav_locks()
    assert wd._conflicting_lock({"username": "u1"}, wd.SHARE_ROOT / "x") is None


# ─── A1 · users 表单一主人（防回潮守卫）────────────────────

def test_users_table_sql_is_owned_by_store_only():
    """回归守卫：`users` 表的 SQL 只允许出现在两个文件里。

    - `app/store/users.py` —— 该表唯一的**数据访问**出口（11 号复审批次 2，A1）；
    - `app/store/schema.py` —— **schema 定义与增量迁移**（建表/列变更/一次性引导），
      它是 SQLite/PG 双引擎的 DDL 归属地（B1 存储层拆分自 db.py 迁入）。

    此前该表被 11 个模块直接读写、80 条 SQL 散落各处：加一列要改 11 个地方。
    这条守卫让"某个业务模块图省事又写了一句裸 SQL"在 CI 立刻变红。

    实现用 AST 只检查**真实字符串常量**（并排除 docstring）——注释与文档里提到
    "JOIN users" 是说明性文字，不算违规。
    """
    import ast
    import re as _re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    pattern = _re.compile(r"\b(FROM|INTO|UPDATE|DELETE\s+FROM|JOIN)\s+users\b", _re.I)
    allowed = {"store/users.py", "store/schema.py"}

    def sql_strings(tree) -> list[str]:
        """模块内所有非 docstring 的字符串常量。"""
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None) or []
                if body and isinstance(body[0], ast.Expr) and \
                        isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        return [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docstrings]

    offenders = []
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(root).as_posix()
        if rel in allowed:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        if any(pattern.search(s) for s in sql_strings(tree)):
            offenders.append(rel)
    assert not offenders, (
        f"这些模块直接写了 users 表的 SQL，请改走 app.store.users 语义方法: {offenders}")


# ─── P2-6 · 真事务：中途抛错必须整体回滚 ───────────────────

def test_transaction_rolls_back_on_error(client):
    """注入式用例（09 报告 B4 的验收口径）：事务中途抛错后数据无残留。

    这正是邀请码 redeem 的孤儿账号场景——修复前靠"插入后再补偿删除"，
    中途进程崩溃就留下一个占了用户名、邀请却仍未使用的账号。
    """
    from app.db import _db_lock, transaction
    from app.store import users as users_store

    n0 = users_store.count()
    uname = f"txroll-{_rand()}"
    with pytest.raises(RuntimeError):
        with _db_lock, transaction():
            users_store.create(uname, "x", "member")
            raise RuntimeError("boom")

    assert users_store.count() == n0, "事务未回滚，残留了账号"
    assert users_store.id_by_username(uname) is None


def test_transaction_depth_flag_is_scoped():
    """`in_transaction()` 决定由谁 commit：块内为真、出块复位。"""
    from app.db import in_transaction, transaction

    assert in_transaction() is False
    with transaction():
        assert in_transaction() is True
        with transaction():          # 嵌套
            assert in_transaction() is True
        assert in_transaction() is True
    assert in_transaction() is False


def test_store_write_commits_when_not_in_transaction(client):
    """事务外调用仓储写方法必须自行落库（否则改密/改配额会静默丢失）。"""
    from app.store import users as users_store

    uid = users_store.id_by_username("admin")
    assert uid is not None
    users_store.set_nickname(uid, "tx-outside")
    assert users_store.get_profile(uid)["nickname"] == "tx-outside"
    users_store.set_nickname(uid, "")


# ─── P2-2 · async 路由内不得直接做同步 DB 往返 ─────────────

def test_no_sync_db_calls_inside_async_handlers():
    """回归守卫：`async def` 函数自身作用域内禁止 `_db()`。

    uvicorn 是**单进程**（server.py，reload=False、无 workers），事件循环被
    同步 PG 往返阻塞 = 全体用户一起等。项目规范本就要求同步 DB 走
    `run_in_threadpool`（中间件与多数路由都这么做），但历史上 trash/uploads/
    share/auth 里有 27 处漏网。

    用 AST 做**作用域感知**检查：async 函数内部定义的同步辅助函数（它们本就是
    供 `run_in_threadpool` 调用的）不算违规，避免误报。
    """
    import ast
    from pathlib import Path

    SKIP = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
            ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

    def has_db_call(node) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SKIP):
                continue
            if isinstance(child, ast.Call):
                nm = getattr(child.func, "id", None) or getattr(child.func, "attr", None)
                if nm == "_db":
                    return True
            if has_db_call(child):
                return True
        return False

    offenders = []
    root = Path(__file__).resolve().parent.parent / "app"
    for py in sorted(root.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and has_db_call(node):
                offenders.append(f"{py.relative_to(root).as_posix()}:{node.name}")
    assert not offenders, (
        "async 函数里直接调 _db() 会阻塞事件循环（单进程 uvicorn 全站一起等）；"
        f"请抽成同步函数并经 run_in_threadpool 调用: {offenders}")







