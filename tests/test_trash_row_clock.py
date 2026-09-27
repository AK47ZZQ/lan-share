"""回收站保留期时钟回归钉（2026-09-27 自主审查 P0）。

P0：_cleanup_trash 曾按**文件 mtime** 判保留期，而 os.replace/shutil.move
保留原始 mtime——删除一个 mtime 老于 30 天的文件，下一轮小时级清理就把它
物理销毁，行侧 deleted_at 才刚起算："30 天可恢复"在生产上是假的。
修复：行侧 deleted_at 权威判期；mtime 只给无行孤儿（对账救援的候选，
register_rescued 的行侧 deleted_at 也对齐 mtime，寿命语义不变）。
消融锚：把 _cleanup_trash 里 rows.get(...) 的行侧时钟拿掉（退回纯 mtime
判据），test_cleanup_uses_row_clock_not_file_mtime 的第二段即红。
"""
import os
import time

from app.config import SHARE_ROOT
from app.db import _db, _db_lock
from app.filetools import move_to_trash
from app.services import cleanup as cleanup_svc
from app.store import trash as trash_store

_PREFIX = "rowclock-"


def _db_rows():
    return _db().execute("SELECT * FROM trash").fetchall()


def _purge_my_trash() -> None:
    # 只清本文件创建的行/文件（session 级共享测试 DB，勿动他测数据）；
    # .trash 残留句柄（WinError 5）必须容错，别让收尾反过来打红测试
    for r in _db_rows():
        if not r["name"].startswith(_PREFIX):
            continue
        try:
            (SHARE_ROOT / r["trash_path"]).unlink(missing_ok=True)
        except OSError:
            pass
        trash_store.drop(r["id"])


def test_move_to_trash_preserves_original_mtime_and_fresh_row():
    """删除老 mtime 文件：原始 mtime 必须原样保留（恢复后文件列表排序/
    WebDAV Last-Modified/照片时间线回退都依赖它），行侧 deleted_at 才是
    保留期时钟（≈ 现在）。"""
    src = SHARE_ROOT / f"{_PREFIX}old-{time.time_ns()}.jpg"
    src.write_bytes(b"old")
    old = time.time() - 400 * 86400
    os.utime(src, (old, old))
    try:
        move_to_trash(src, src.name)
        row = [r for r in _db_rows() if r["name"] == src.name][0]
        tp = SHARE_ROOT / row["trash_path"]
        assert abs(tp.stat().st_mtime - old) < 5, "入站改写了原始 mtime"
        assert time.time() - row["deleted_at"] < 60, "行侧 deleted_at 不新鲜"
    finally:
        _purge_my_trash()
        src.unlink(missing_ok=True)


def test_cleanup_uses_row_clock_not_file_mtime():
    """判据闭环：
    - 行 deleted_at 刚起算 + 文件 mtime 400 天 → 必须存活（P0 事故本体）；
    - 行 deleted_at 31 天前 + 文件 mtime 新鲜 → 文件与行都该消失；
    - 无行孤儿 mtime 31 天前（救援窗口外）→ 按 mtime 收敛。"""
    stale_row = time.time() - 31 * 86400
    old_mtime = time.time() - 400 * 86400
    # 刚删除的老文件
    fresh = SHARE_ROOT / f"{_PREFIX}fresh-{time.time_ns()}.jpg"
    fresh.write_bytes(b"fresh")
    os.utime(fresh, (old_mtime,) * 2)
    move_to_trash(fresh, fresh.name)
    # 行侧已躺 31 天（文件 mtime 新鲜）
    aged = SHARE_ROOT / f"{_PREFIX}aged-{time.time_ns()}.jpg"
    aged.write_bytes(b"aged")
    move_to_trash(aged, aged.name)
    aged_row = [r for r in _db_rows() if r["name"] == aged.name][0]
    with _db_lock:
        _db().execute("UPDATE trash SET deleted_at=? WHERE id=?",
                      (stale_row, aged_row["id"]))
        _db().commit()
    # 无行孤儿
    orphan = SHARE_ROOT / ".trash" / f"{_PREFIX}orphan-{time.time_ns()}.jpg"
    orphan.write_bytes(b"orphan")
    os.utime(orphan, (stale_row,) * 2)
    try:
        cleanup_svc._cleanup_trash()
        fresh_row = [r for r in _db_rows() if r["name"] == fresh.name][0]
        fresh_tp = SHARE_ROOT / fresh_row["trash_path"]
        assert fresh_tp.exists(), "刚删除的老 mtime 文件被清理循环误销（P0 回归）"
        assert not aged.exists(), "行侧已过期的条目文件未被清理"
        assert aged_row["id"] not in {r["id"] for r in _db_rows()}, "过期行未摘除"
        assert not orphan.exists(), "无行孤儿未被按 mtime 收敛"
    finally:
        _purge_my_trash()
        orphan.unlink(missing_ok=True)
