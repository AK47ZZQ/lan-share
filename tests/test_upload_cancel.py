"""分片会话取消端点钉（v3.69 · 不自信点④"弃单占用 3 天 TTL"收口）。

DELETE /api/upload/sessions/{sid}：owner-only，摘会话行（个人/全局在途
计数回落）+ 删 .chunks 分片目录；无会话 404 幂等。
消融锚：删掉端点 → 全部红（404→405/404 不匹配）；摘掉 rmtree → 分片残留断言红。
"""
import secrets

import pytest

from app.api.uploads import _chunk_dir


def _init(auth, name="cancel.bin", size=2048) -> str:
    r = auth.post("/api/upload/init",
                  json={"dest_dir": "private:", "filename": name, "size": size},
                  headers={"Host": "lan.local:8765"})
    assert r.status_code == 200, r.text
    return r.json()["session"]


def test_cancel_own_session_drops_row_and_chunks(auth):
    sid = _init(auth)
    assert _chunk_dir(sid).exists(), "init 即建分片目录"

    r = auth.delete(f"/api/upload/sessions/{sid}", headers={"Host": "lan.local:8765"})
    assert r.status_code == 200, r.text
    assert not _chunk_dir(sid).exists(), "分片目录未删"
    r = auth.get(f"/api/upload/state/{sid}", headers={"Host": "lan.local:8765"})
    assert r.status_code == 404, "会话行未摘"

    # 幂等：重复取消同样 404（不 500）
    r = auth.delete(f"/api/upload/sessions/{sid}", headers={"Host": "lan.local:8765"})
    assert r.status_code == 404


def test_cancel_frees_global_cap(auth, monkeypatch):
    """取消后全局在途量立即回落（分叉边界：此前只能等 3 天 TTL）。
    cap 取"会话级测试库的基线在途量 + 10000"——全量套件里 28 处其它
    upload/init 用例留下未清理会话，绝对值 cap 会顺序依赖（首轮全量红实录）。"""
    import app.api.uploads as uploads_mod
    baseline = uploads_mod._session_usage_all()["total"]
    monkeypatch.setattr(uploads_mod, "CHUNKS_TOTAL_CAP", baseline + 10_000)
    sid = _init(auth, name="cap.bin", size=9000)
    r = auth.post("/api/upload/init",
                  json={"dest_dir": "private:", "filename": "cap2.bin", "size": 9000},
                  headers={"Host": "lan.local:8765"})
    assert r.status_code == 507, "两笔 9000 应撞 10000 上限（第二笔）"
    auth.delete(f"/api/upload/sessions/{sid}", headers={"Host": "lan.local:8765"})
    r = auth.post("/api/upload/init",
                  json={"dest_dir": "private:", "filename": "cap2.bin", "size": 9000},
                  headers={"Host": "lan.local:8765"})
    assert r.status_code == 200, "取消后在途量回落，新会话应放行"
    import app.api.uploads as uploads_mod
    uploads_mod._drop_session(r.json()["session"])   # 自清，别污染 session 级测试库


@pytest.fixture()
def member_name():
    return "cancel-" + secrets.token_hex(3)


@pytest.fixture()
def member(auth, member_name):
    """建测试成员并登录（与 test_social 同款的一次性夹具）。"""
    r = auth.post("/api/admin/invites", json={"role": "member", "hours": 1})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    from fastapi.testclient import TestClient
    from app.main import app as _app
    c = TestClient(_app, base_url="https://testserver")
    r = c.post("/api/invites/redeem", json={"code": code, "username": member_name,
                                            "password": "cancel-pass-123"})
    assert r.status_code == 200, r.text
    c.post("/api/login", json={"username": member_name, "password": "cancel-pass-123"})
    return c


def test_cancel_other_users_session_403(auth, member):
    """他人（含 admin）不能取消我的会话：403——与 _session_owner_guard 在
    state/chunk 端点的既有语义一致（不改共享 guard 的口径）。"""
    sid = _init(auth, name="mine.bin")
    r = member.delete(f"/api/upload/sessions/{sid}", headers={"Host": "lan.local:8765"})
    assert r.status_code == 403
    # 会话仍在
    r = auth.get(f"/api/upload/state/{sid}", headers={"Host": "lan.local:8765"})
    assert r.status_code == 200
    auth.delete(f"/api/upload/sessions/{sid}", headers={"Host": "lan.local:8765"})


def test_cancel_nonexistent_404(auth):
    r = auth.delete("/api/upload/sessions/nope-nope", headers={"Host": "lan.local:8765"})
    assert r.status_code == 404
