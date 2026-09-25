""".trash 无主残留对账（services/trash_audit）：
无主识别 / 宽限期 / 索引内不误报 / settings 落盘与健康日报可见。
只读对账：任何用例都不得真删 .trash 文件。
"""
import json

import pytest
import os
import time
from pathlib import Path

from app.config import SHARE_ROOT
from app.db import _db
from app.services import trash_audit
from app.settings import get_setting, set_setting
from app.store import trash as trash_store


@pytest.fixture(autouse=True)
def _clean_audit_settings_key():
    """对账测试会写 settings 键（健康日报读它）——用毕即清，
    防止泄漏进后续文件的 format/gather 用例（09-25 实录：2 个误红）。"""
    yield
    set_setting(trash_audit.AUDIT_KEY, "")


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


def test_rescue_registers_midage_orphan():
    """v3.67 救援：7~29 天的无主文件自动登记回回收站（可还原），报告计数归零。"""
    # 文件名用真实 time_ns 形态（19 位数字前缀）
    f = _mk_trash_file("1790400000000000000_orphan-mid.txt", age_days=15)
    try:
        r = trash_audit.audit_trash_orphans()
        assert r["rescued"] == 1 and r["count"] == 0
        assert f.name in trash_store.referenced_trash_names()
        row = _db().execute("SELECT * FROM trash WHERE trash_path=?",
                            (f".trash/{f.name}",)).fetchone()
        assert row is not None
        # v3.67 P0 修复后：orig_path 为 admin 专属 private vpath（还原落
        # 还原者私人空间的对账救援目录，内容归属未知不落共享区）
        assert row["orig_path"].startswith("private:对账救援-")
        assert row["orig_path"].endswith("/orphan-mid.txt")
        assert row["name"] == "orphan-mid.txt"
        assert row["name"] == "orphan-mid.txt"       # 去掉 time_ns 前缀
        # deleted_at 与文件 mtime 对齐（行与文件同寿命）
        assert abs(row["deleted_at"] - f.stat().st_mtime) < 1
    finally:
        f.unlink(missing_ok=True)
        _db().execute("DELETE FROM trash WHERE trash_path=?",
                      (f".trash/{f.name}",))
        _db().commit()


def test_29day_orphan_not_rescued_report_only():
    """≥29 天：与小时级清理有竞态，只报告不登记（避免有行无文件的坏条目）。"""
    f = _mk_trash_file("444444_orphan-old.txt", age_days=29.5)
    try:
        r = trash_audit.audit_trash_orphans()
        assert r["rescued"] == 0 and r["count"] == 1
        assert f.name not in trash_store.referenced_trash_names()
    finally:
        f.unlink(missing_ok=True)


def test_orig_name_derivation():
    from app.services.trash_audit import _orig_name
    # 落盘名 = {time_ns()}_{原名}；原名可含下划线，只切第一个
    assert _orig_name("1790400000000000000_report-final.docx") == "report-final.docx"
    assert _orig_name("1790400000000000000_my_file_v2.txt") == "my_file_v2.txt"
    assert _orig_name("no-underscore.txt") == "no-underscore.txt"


def test_cleanup_stage_order_pins_trash_before_registered():
    """对抗审查 P1：救援的 29 天上限依赖"先按 mtime 清理、后对账登记"的次序，
    钉住 _STAGES 顺序，防止未来重排静默破坏不变量。"""
    from app.services import cleanup
    names = [n for n, _ in cleanup._STAGES]
    assert names.index("trash") < names.index("registered")
