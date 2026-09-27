"""v3.67.2 回收站加固与审计保留回归钉（移植自分叉批 P2-4 / P2-6 / P3-7）。

- containment：trash 行的 trash_path 逃逸必须 403，而非任意路径 unlink/rmtree；
- .trash 全操作锁：入站/恢复/清理路径执行期间必须持锁（防清空 rmtree 与
  恢复 shutil.move 交错"恢复"出静默截断的目录——Windows rename 退化 copytree）；
- 审计安全类独立保留：非安全类洪泛不得把安全类取证记录挤出环外。
"""
import threading
import time

import pytest

import app.api.trash as trash_api
import app.filetools as filetools_mod
from app import security
from app.config import SHARE_ROOT
from app.db import _db, _db_lock
from app.filetools import TRASH_OPS_LOCK, move_to_trash
from app.services import cleanup as cleanup_svc
from app.store import trash as trash_store
from fastapi import HTTPException


def _rl_owned(lock: threading.RLock) -> bool:
    # RLock 没有 .locked()；同线程判定用 CPython 的 _is_owned（本项目钉子
    # 全部在同线程断言"当前持锁"）
    return lock._is_owned()


# ── containment（P3-7）────────────────────────────────────

def test_trash_path_containment_rejects_escape():
    for evil in ("../outside/evil", ".trash/../../etc/passwd",
                 "..\\..\\Windows\\evil", "shared/../../../evil"):
        with pytest.raises(HTTPException) as ei:
            trash_api._trash_fs_path(evil)
        assert ei.value.status_code == 403, evil
    ok = trash_api._trash_fs_path(".trash/1790400000000000000_x.txt")
    assert str(ok).startswith(str(SHARE_ROOT / ".trash"))


# ── .trash 全操作锁（P2-6）─────────────────────────────────

def test_trash_ops_lock_held_on_entry_and_cleanup(monkeypatch):
    held = {}
    orig_locked = filetools_mod._move_to_trash_locked

    def spy_locked(target, orig):
        held["entry"] = _rl_owned(TRASH_OPS_LOCK)
        orig_locked(target, orig)

    monkeypatch.setattr(filetools_mod, "_move_to_trash_locked", spy_locked)
    orig_list = cleanup_svc.trash_store.list_all

    src = SHARE_ROOT / f"hardening-{time.time_ns()}.txt"
    src.write_bytes(b"x")
    try:
        move_to_trash(src, src.name)
        assert held.get("entry") is True, "move_to_trash 未持 TRASH_OPS_LOCK"

        def spy_list():
            held["cleanup"] = _rl_owned(TRASH_OPS_LOCK)
            return orig_list()

        monkeypatch.setattr(cleanup_svc.trash_store, "list_all", spy_list)
        cleanup_svc._cleanup_trash()
        assert held.get("cleanup") is True, "_cleanup_trash 未持 TRASH_OPS_LOCK"
    finally:
        for r in _db().execute(
                "SELECT id FROM trash WHERE name LIKE 'hardening-%'").fetchall():
            trash_store.drop(r["id"])
        src.unlink(missing_ok=True)


def test_restore_move_holds_trash_ops_lock(monkeypatch, tmp_path):
    held = {}
    orig_move = trash_api.shutil.move

    def spy_move(src, dst):
        held["v"] = _rl_owned(TRASH_OPS_LOCK)
        return orig_move(src, dst)

    monkeypatch.setattr(trash_api.shutil, "move", spy_move)
    src = tmp_path / "a.txt"
    src.write_bytes(b"x")
    dest = tmp_path / "b.txt"
    trash_api._move_out_locked(src, dest)
    assert held.get("v") is True, "_move_out_locked 未持 TRASH_OPS_LOCK"
    assert dest.exists() and not src.exists()


# ── 审计安全类独立保留（P2-4）───────────────────────────────

def test_audit_security_rows_survive_nonsec_flood(monkeypatch):
    """非安全类洪泛（≫AUDIT_KEEP）不得把安全类取证记录挤出环外。
    探针必须用**真实**安全类 action（login_fail 在 _AUDIT_SECURITY_ACTIONS
    里，自定义名会被归入普通类——首轮踩过）；行定位/清理按唯一 target。
    消融锚：退回单条 DELETE（无 action 过滤）→ 断言即红（sec 被清光）。"""
    tag = f"zzhard-{time.time_ns()}"
    monkeypatch.setattr(security, "AUDIT_KEEP", 5)
    monkeypatch.setattr(security, "AUDIT_SECURITY_KEEP", 200)
    try:
        with _db_lock:
            for i in range(10):
                _db().execute(
                    "INSERT INTO audit_log(ts, ip, action, target) VALUES(?,?,?,?)",
                    (time.time(), "t", "login_fail", f"{tag}-s{i}"))
            for i in range(100):
                _db().execute(
                    "INSERT INTO audit_log(ts, ip, action, target) VALUES(?,?,?,?)",
                    (time.time(), "t", "zzn_probe", f"{tag}-n{i}"))
            _db().commit()
        security.audit("zzn_probe", f"{tag}-trigger")
        rows = _db().execute(
            "SELECT action, target FROM audit_log WHERE target LIKE ?",
            (f"{tag}%",)).fetchall()
        sec = sum(1 for r in rows if r["action"] == "login_fail")
        non = sum(1 for r in rows if r["action"] == "zzn_probe")
        assert sec == 10, f"安全类取证被普通流量冲掉（剩 {sec}/10）"
        assert non <= 6, "非安全类未按 AUDIT_KEEP 收敛"
    finally:
        with _db_lock:
            _db().execute("DELETE FROM audit_log WHERE target LIKE ?", (f"{tag}%",))
            _db().commit()
