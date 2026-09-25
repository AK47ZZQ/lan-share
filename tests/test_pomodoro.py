"""M2 番茄钟服务端切片：三态结算 / 同用户单 running / 今日归属 / 个人可见性。

设计依据 docs/效率舱-最终计划-20260917.md §3.3/§3.5。
"""
import secrets
import time

import pytest
from fastapi.testclient import TestClient

from conftest import MEMBER_PASS2
from app.main import app
from app.store import pomodoros as pomodoros_store


@pytest.fixture()
def make_user(auth, client):
    """邀请码建号工厂（test_review_fixes 同款）。"""
    def _make(role, name=None):
        username = name or f"p{secrets.token_hex(3)}"
        r = auth.post("/api/admin/invites", json={"role": role, "hours": 1})
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        r = client.post("/api/invites/redeem", json={
            "code": code, "username": username, "password": MEMBER_PASS2})
        assert r.status_code == 200, r.text
        c = TestClient(app, base_url="https://testserver")
        r = c.post("/api/login", json={"username": username, "password": MEMBER_PASS2})
        assert r.status_code == 200, r.text
        return c
    return _make


def _start(c, **body):
    return c.post("/api/pomodoro/start", json=body)


def test_start_and_running(auth):
    r = _start(auth, focus_min=25)
    assert r.status_code == 200 and r.json()["focus_min"] == 25
    run = auth.get("/api/pomodoro/running").json()["running"]
    assert run and run["status"] == "running" and run["focus_min"] == 25


def test_start_twice_settles_old_as_abandoned(auth):
    r1 = _start(auth, focus_min=30)
    assert r1.status_code == 200
    time.sleep(0.01)
    r2 = _start(auth, focus_min=25)
    assert r2.status_code == 200
    assert auth.get("/api/pomodoro/running").json()["running"]["id"] == r2.json()["id"]
    s = auth.get("/api/pomodoro/summary").json()
    assert s["today"]["count"] >= 1


def test_finish_focus_counts_elapsed_minutes(auth):
    r = _start(auth, focus_min=25)
    sid = r.json()["id"]
    started = r.json()["started_at"]
    r = auth.post(f"/api/pomodoro/{sid}/finish",
                  json={"status": "focus", "ended_at": started + 25 * 60})
    assert r.status_code == 200 and r.json()["minutes"] == 25
    s = auth.get("/api/pomodoro/summary").json()
    assert s["today"]["minutes"] >= 25
    assert auth.get("/api/pomodoro/running").json()["running"] is None


def test_finish_abandoned_credits_elapsed(auth):
    r = _start(auth, focus_min=25)
    sid = r.json()["id"]
    started = r.json()["started_at"]
    r = auth.post(f"/api/pomodoro/{sid}/finish",
                  json={"status": "abandoned", "ended_at": started + 7 * 60})
    assert r.status_code == 200 and r.json()["minutes"] == 7


def test_finish_rejects_bad_status_and_backwards_clock(auth):
    """status 由 FinishBody 的 Literal 在模型层拒绝（422，二轮审查 P2-1）；
    ended_at 倒挂仍由业务层 400。"""
    sid = _start(auth).json()["id"]
    assert auth.post(f"/api/pomodoro/{sid}/finish",
                     json={"status": "nonsense"}).status_code == 422
    assert auth.post(f"/api/pomodoro/{sid}/finish",
                     json={"status": "focus", "ended_at": 1}).status_code == 400


def test_finish_zero_duration_abandoned_is_zero_minutes(make_user):
    """二轮审查 P3-9：零时长放弃计 0 分钟（E2E"1个·0分钟"口径的单元钉）。"""
    u = make_user("member")
    _start(u)                       # 会话 1
    _start(u)                       # 收束会话 1（abandoned，亚秒间隔）；会话 2 running
    r = u.get("/api/pomodoro/summary").json()
    assert r["today"]["count"] == 1        # 只计已收束会话，running 不计
    assert r["today"]["minutes"] == 0      # 亚秒间隔的 abandoned 不产生分钟


def test_start_cannot_attach_others_personal_todo(make_user):
    """二轮审查 P1-1：成员 B 不能把成员 A 的 personal 待办挂进自己的会话
    （404 不泄露存在）。"""
    a = make_user("member")
    b = make_user("member")
    # A 建 personal 清单 + 待办
    lid = a.post("/api/todos/lists", json={"name": "p-list", "kind": "personal"}).json()["id"]
    tid = a.post("/api/todos", json={"list_id": lid, "title": "a-private"}).json()["id"]
    r = _start(b, todo_id=tid)
    assert r.status_code == 404
    # A 自己挂自己的可以
    assert _start(a, todo_id=tid).status_code == 200


def test_finish_twice_conflicts(auth):
    sid = _start(auth).json()["id"]
    assert auth.post(f"/api/pomodoro/{sid}/finish",
                     json={"status": "focus"}).status_code == 200
    assert auth.post(f"/api/pomodoro/{sid}/finish",
                     json={"status": "focus"}).status_code == 409


def test_start_unknown_todo_404(auth):
    assert _start(auth, todo_id=99999999).status_code == 404


def test_sweep_auto_closes_expired_running(auth):
    """App 被杀/断网：sweep 把超时 running 按计划时长 auto_closed（§3.3）。"""
    from app.db import _db
    sid = _start(auth, focus_min=25).json()["id"]
    row = pomodoros_store.get(sid)
    old = row["started_at"] - 3600
    # 回拨 started_at 构造"开屏后 App 被杀"的超时形态
    _db().execute("UPDATE pomodoro_sessions SET started_at=? WHERE id=?", (old, sid))
    _db().commit()
    from app.api import pomodoros as api
    api.sweep_pomodoro_settle()
    row = pomodoros_store.get(sid)
    assert row["status"] == "auto_closed"
    assert abs(row["ended_at"] - (old + 25 * 60)) < 1


def test_session_personal_isolation(make_user):
    """个人数据：成员 A 的会话对成员 B 不可寻址（404 不泄露存在）。"""
    a = make_user("member")
    b = make_user("member")
    sid = _start(a).json()["id"]
    assert b.post(f"/api/pomodoro/{sid}/finish",
                  json={"status": "focus"}).status_code == 404
    assert b.get("/api/pomodoro/running").json()["running"] is None
    assert b.get("/api/pomodoro/summary").json()["today"]["count"] == 0


def test_guest_forbidden(make_user):
    g = make_user("guest")
    assert _start(g).status_code == 403
    assert g.get("/api/pomodoro/summary").status_code == 403


def test_summary_days_bounds(auth):
    assert auth.get("/api/pomodoro/summary", params={"days": 0}).status_code == 400
    assert auth.get("/api/pomodoro/summary", params={"days": 31}).status_code == 400
    assert auth.get("/api/pomodoro/summary", params={"days": 7}).status_code == 200
