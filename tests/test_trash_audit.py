""".trash 无主残留对账（services/trash_audit）：
无主识别 / 宽限期 / 索引内不误报 / settings 落盘与健康日报可见。
只读对账：任何用例都不得真删 .trash 文件。
"""
import json
import os
import time
from pathlib import Path

from app.config import SHARE_ROOT
from app.db import _db
from app.services import trash_audit
from app.settings import get_setting
from app.store import trash as trash_store


def _mk_trash_file(name: str, age_days: float) -> Path:
    d = SHARE_ROOT / ".trash"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_bytes(b"x" * 11)
    old = time.time() - age_days * 86400
    os.utime(f, (old, old))
    return f


def _mk_indexed_file(name: str) -> Path:
    """建一个**有索引行**的 .trash 文件（对账必须不把它当无主）。"""
    f = _mk_trash_file(name, age_days=30)
    _db().execute(
        "INSERT INTO trash(orig_path, trash_path, name, size, deleted_at) "
        "VALUES(?,?,?,?,?)",
        ("2026/09/legit.txt", f".trash/{name}", "legit.txt", 11, time.time()))
    _db().commit()
    return f


def test_orphan_detected_and_reported():
    f = _mk_trash_file("999999_orphan-old.txt", age_days=30)
    try:
        r = trash_audit.audit_trash_orphans()
        names = [o["name"] for o in r["items"]]
        assert f.name in names
        assert r["count"] >= 1 and r["total_bytes"] >= 11
        # settings 落盘且可反序列化
        stored = json.loads(get_setting(trash_audit.AUDIT_KEY))
        assert stored["count"] == r["count"]
    finally:
        f.unlink(missing_ok=True)


def test_recent_orphan_within_grace_ignored():
    f = _mk_trash_file("888888_orphan-new.txt", age_days=0.1)
    try:
        r = trash_audit.audit_trash_orphans()
        assert f.name not in [o["name"] for o in r["items"]]
    finally:
        f.unlink(missing_ok=True)


def test_indexed_file_not_flagged():
    f = _mk_indexed_file("777777_legit.txt")
    try:
        assert trash_store.referenced_trash_names() >= {f.name}
        r = trash_audit.audit_trash_orphans()
        assert f.name not in [o["name"] for o in r["items"]]
    finally:
        f.unlink(missing_ok=True)
        _db().execute("DELETE FROM trash WHERE trash_path=?", (f".trash/{f.name}",))
        _db().commit()


def test_healthreport_surfaces_audit():
    _mk_trash_file("666666_orphan-hr.txt", age_days=30)
    try:
        trash_audit.audit_trash_orphans()
        from app.api.healthreport import gather_health
        d = gather_health()
        assert d["trash_orphans"] is not None
        assert d["trash_orphans"]["count"] >= 1
    finally:
        (SHARE_ROOT / ".trash" / "666666_orphan-hr.txt").unlink(missing_ok=True)


def test_sweep_registered():
    from app.services.cleanup import _sweeps
    assert any(n == "trash_orphan_audit" for n, _ in _sweeps)
