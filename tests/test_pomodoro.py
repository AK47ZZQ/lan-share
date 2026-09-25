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
    sid = _start(auth).json()["id"]
    assert auth.post(f"/api/pomodoro/{sid}/finish",
                     json={"status": "nonsense"}).status_code == 400
    assert auth.post(f"/api/pomodoro/{sid}/finish",
                     json={"status": "focus", "ended_at": 1}).status_code == 400


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
