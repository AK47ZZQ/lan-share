"""效率舱 · 待办与家庭清单测试（M1，v1.1）。

覆盖最终计划 v5 的关键语义：
- 可见性：personal 仅创建者（**admin 也不看**）、family member/admin 可见、
  guest 全不可用（403）。
- 家庭协作：指派通知、完成通知（含 10 分钟窗口合并节流）。
- 软删除墓碑 + sweep 到期提醒（幂等：重跑不重复通知）+ 墓碑物理清理。
- 校验：priority 白名单、被指派对象必须是在用 member/admin、清空 due_at。

用户工厂沿用 test_multiuser 的邀请码建号路径；测试数据自建自清。
"""
import secrets
import time

import pytest
from fastapi.testclient import TestClient

from conftest import MEMBER_PASS2
from app.main import app


@pytest.fixture()
def make_user(auth, client):
    """邀请码建号工厂（同 test_multiuser）：make_user("member") -> 已登录客户端。"""
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
    from app.db import _db
    for username in created:
        row = _db().execute("SELECT id FROM users WHERE username=?",
                            (username,)).fetchone()
        if row:
            _db().execute("DELETE FROM todo_lists WHERE user_id=?", (row["id"],))
            _db().execute("DELETE FROM todos WHERE assignee_id=?", (row["id"],))
            _db().execute("DELETE FROM users WHERE id=?", (row["id"],))
            _db().commit()


def _mk_list(c, name, kind="personal"):
    r = c.post("/api/todos/lists", json={"name": name, "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _mk_todo(c, list_id, title, **kw):
    body = {"list_id": list_id, "title": title, **kw}
    r = c.post("/api/todos", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ── 鉴权与 guest ─────────────────────────────────────────


def test_todos_require_login(client):
    assert client.get("/api/todos").status_code == 401
    assert client.get("/api/todos/lists").status_code == 401


def test_guest_forbidden(make_user):
    guest = make_user("guest")
    assert guest.get("/api/todos").status_code == 403
    assert guest.post("/api/todos/lists",
                      json={"name": "rv-t", "kind": "personal"}).status_code == 403


# ── 可见性 ───────────────────────────────────────────────


def test_personal_private_even_from_admin(auth, make_user):
    """personal 清单对 admin 也不可见（私人待办是隐私）。"""
    member = make_user("member")
    lid = _mk_list(member, f"rv-私人清单-{secrets.token_hex(3)}")
    _mk_todo(member, lid, "rv-私事")
    # admin 的全量列表里看不到 member 的 personal 待办
    titles = [t["title"] for t in auth.get("/api/todos").json()["todos"]]
    assert not any(t.startswith("rv-私事") for t in titles)
    # 直接按 id 取也 404（不泄露存在）
    tid = member.get("/api/todos").json()["todos"][0]["id"]
    assert auth.patch(f"/api/todos/{tid}", json={"status": "done"}).status_code == 404


def test_family_visible_to_members(make_user):
    m1, m2 = make_user("member"), make_user("member")
    lid = _mk_list(m1, f"rv-家庭清单-{secrets.token_hex(3)}", kind="family")
    tid = _mk_todo(m1, lid, "rv-买牛奶")
    rows = m2.get("/api/todos").json()["todos"]
    assert any(t["id"] == tid for t in rows)
    # member2 可勾选完成（家庭共同清单）
    assert m2.patch(f"/api/todos/{tid}", json={"status": "done"}).status_code == 200
    done = [t for t in m1.get("/api/todos").json()["todos"] if t["id"] == tid]
    assert done and done[0]["status"] == "done" and done[0]["done_by"] == _uid(m2)


def _uid(c):
    from app.store import users as users_store
    return users_store.id_by_username(c.username)


# ── 指派与通知 ────────────────────────────────────────────


def test_assign_notifies_and_complete_notifies_creator(make_user):
    m1, m2 = make_user("member"), make_user("member")
    lid = _mk_list(m1, f"rv-家务-{secrets.token_hex(3)}", kind="family")
    m2_id = _uid(m2)
    tid = _mk_todo(m1, lid, "rv-倒垃圾", assignee_id=m2_id)
    # m2 收到指派通知
    titles = [n["title"] for n in m2.get("/api/notifications").json()["items"]]
    assert any("新指派" in t for t in titles)
    # m2 完成 → 创建者 m1 收到完成通知
    m2.patch(f"/api/todos/{tid}", json={"status": "done"})
    items = m1.get("/api/notifications").json()["items"]
    assert any("有新完成" in n["title"] for n in items)


def test_family_complete_notification_merged(make_user):
    """同一清单 10 分钟窗口内多次完成 → 合并为一条（bump 正文），不刷屏。"""
    m1, m2 = make_user("member"), make_user("member")
    lid = _mk_list(m1, f"rv-购物-{secrets.token_hex(3)}", kind="family")
    t1 = _mk_todo(m1, lid, "rv-牛奶")
    t2 = _mk_todo(m1, lid, "rv-鸡蛋")
    m2.patch(f"/api/todos/{t1}", json={"status": "done"})
    m2.patch(f"/api/todos/{t2}", json={"status": "done"})
    items = [n for n in m1.get("/api/notifications").json()["items"]
             if n["link"] == f"todos:list:{lid}"]
    assert len(items) == 1
    assert "鸡蛋" in (items[0]["body"] or "")


def test_self_assign_no_self_notify(make_user):
    m1 = make_user("member")
    lid = _mk_list(m1, f"rv-自用-{secrets.token_hex(3)}", kind="family")
    before = len(m1.get("/api/notifications").json()["items"])
    _mk_todo(m1, lid, "rv-自己的事", assignee_id=_uid(m1))
    after = m1.get("/api/notifications").json()["items"]
    assert len(after) == before


# ── 校验 ─────────────────────────────────────────────────


def test_validation_priority_and_assignee(make_user):
    m1, m2 = make_user("member"), make_user("guest")
    lid = _mk_list(m1, f"rv-校验-{secrets.token_hex(3)}", kind="family")
    assert m1.post("/api/todos", json={
        "list_id": lid, "title": "rv-x", "priority": 3}).status_code == 400
    assert m1.post("/api/todos", json={
        "list_id": lid, "title": "rv-x", "assignee_id": _uid(m2)}).status_code == 400
    assert m1.post("/api/todos", json={
        "list_id": lid, "title": "rv-x", "notes": "x" * 2001}).status_code == 400


def test_patch_clear_due_and_assignee(make_user):
    m1 = make_user("member")
    lid = _mk_list(m1, f"rv-改期-{secrets.token_hex(3)}", kind="personal")
    tid = _mk_todo(m1, lid, "rv-有期限", due_at=time.time() + 3600)
    row = [t for t in m1.get("/api/todos").json()["todos"] if t["id"] == tid][0]
    assert row["due_at"] is not None
    # 不传 due_at 的 PATCH 不动它
    m1.patch(f"/api/todos/{tid}", json={"title": "rv-改名"})
    row = [t for t in m1.get("/api/todos").json()["todos"] if t["id"] == tid][0]
    assert row["due_at"] is not None
    # 显式传 null → 清空
    m1.patch(f"/api/todos/{tid}", json={"due_at": None})
    row = [t for t in m1.get("/api/todos").json()["todos"] if t["id"] == tid][0]
    assert row["due_at"] is None


def test_list_permissions(make_user):
    m1, m2 = make_user("member"), make_user("member")
    lid = _mk_list(m1, f"rv-权限-{secrets.token_hex(3)}", kind="family")
    assert m2.patch(f"/api/todos/lists/{lid}",
                    json={"name": "rv-抢改名"}).status_code == 403
    assert m2.delete(f"/api/todos/lists/{lid}").status_code == 403
    assert m1.delete(f"/api/todos/lists/{lid}").status_code == 200
    rest = m1.get("/api/todos/lists").json()["lists"]
    assert all(item["id"] != lid for item in rest)


def test_duplicate_family_list_name_rejected(make_user):
    m1, m2 = make_user("member"), make_user("member")
    name = f"rv-重名-{secrets.token_hex(3)}"
    _mk_list(m1, name, kind="family")
    assert m2.post("/api/todos/lists",
                   json={"name": name, "kind": "family"}).status_code == 409


# ── 软删除 + sweep ────────────────────────────────────────


def test_soft_delete_hides_and_sweep_purges(make_user):
    from app.api import todos as todos_api
    m1 = make_user("member")
    lid = _mk_list(m1, f"rv-墓碑-{secrets.token_hex(3)}", kind="personal")
    tid = _mk_todo(m1, lid, "rv-将删")
    assert m1.delete(f"/api/todos/{tid}").status_code == 200
    assert not any(t["id"] == tid
                   for t in m1.get("/api/todos").json()["todos"])
    # 墓碑超过保留期 → sweep 物理清除
    from app.db import _db
    _db().execute("UPDATE todos SET deleted_at=? WHERE id=?",
                  (time.time() - 31 * 86400, tid))
    _db().commit()
    todos_api.sweep_todo_due()
    row = _db().execute("SELECT 1 FROM todos WHERE id=?", (tid,)).fetchone()
    assert row is None


def test_sweep_due_reminder_idempotent(make_user):
    """到期 sweep：提醒落一条 + reminded_at 打点；重跑不重复。"""
    from app.api import todos as todos_api
    m1 = make_user("member")
    lid = _mk_list(m1, f"rv-到期-{secrets.token_hex(3)}", kind="personal")
    tid = _mk_todo(m1, lid, "rv-逾期", due_at=time.time() - 60)
    todos_api.sweep_todo_due()
    items = [n for n in m1.get("/api/notifications").json()["items"]
             if n["link"] == f"todos:todo:{tid}"]
    assert len(items) == 1
    from app.db import _db
    row = _db().execute("SELECT reminded_at FROM todos WHERE id=?",
                        (tid,)).fetchone()
    assert row["reminded_at"] is not None
    # 重跑不重复（幂等）
    todos_api.sweep_todo_due()
    items = [n for n in m1.get("/api/notifications").json()["items"]
             if n["link"] == f"todos:todo:{tid}"]
    assert len(items) == 1
