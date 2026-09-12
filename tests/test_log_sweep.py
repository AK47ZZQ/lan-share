"""NSSM 日志归档裁剪（app/services/log_sweep.py）单测。

纯函数哲学：不 mock、不碰真实 logs/，全部在 tmp_path 沙箱内验证。
"""
import os
import time

from app.services.log_sweep import ARCHIVE_RE, prune_archives


def _touch(p, mtime):
    p.write_bytes(b"x")
    os.utime(p, (mtime, mtime))


def test_archive_pattern_matches_nssm_naming():
    assert ARCHIVE_RE.match("api-stdout-20260912T055847.984.log")
    assert ARCHIVE_RE.match("caddy-stderr-20260908T171622.580.log")
    # 活文件与无关文件一律不匹配——裁剪永不误伤
    assert not ARCHIVE_RE.match("api-stdout.log")
    assert not ARCHIVE_RE.match("caddy-stderr.log")
    assert not ARCHIVE_RE.match("server.log")
    assert not ARCHIVE_RE.match("api-stdout-20260912T055847.log")  # 缺毫秒段


def test_prune_deletes_only_expired_archives(tmp_path):
    old = time.time() - 20 * 86400
    recent = time.time()
    _touch(tmp_path / "api-stdout-20260801T000000.000.log", old)
    _touch(tmp_path / "caddy-stderr-20260901T000000.000.log", old)
    _touch(tmp_path / "api-stdout-20260912T055847.984.log", recent)
    _touch(tmp_path / "api-stdout.log", old)   # 活文件即使过期也不删
    _touch(tmp_path / "server.log", old)       # 非归档命名不碰

    removed = prune_archives(tmp_path, max_age_days=14)

    assert removed == 2
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {
        "api-stdout-20260912T055847.984.log",
        "api-stdout.log",
        "server.log",
    }


def test_prune_custom_age_boundary(tmp_path):
    edge = time.time() - 7 * 86400 + 60   # 比 7 天线新一点点
    _touch(tmp_path / "api-stderr-20260905T000000.000.log", edge)
    assert prune_archives(tmp_path, max_age_days=7) == 0
    assert (tmp_path / "api-stderr-20260905T000000.000.log").exists()


def test_prune_missing_dir_returns_zero(tmp_path):
    assert prune_archives(tmp_path / "nope") == 0
