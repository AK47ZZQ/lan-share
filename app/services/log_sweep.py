"""NSSM 日志归档裁剪（值守挂账：api-stdout.log 无轮转的收尾半块）。

轮转本身由 NSSM 承担（AppRotateFiles/AppRotateOnline/AppRotateBytes，
2026-09-12 已对齐活服务配置并受控重启生效），但 NSSM 只负责改名——轮转出的
api-*-<时间戳>.log 归档永不清理，10MB/个无限累积。本 sweep 只认时间戳归档
命名，活文件（api-stdout.log / caddy-stderr.log 等）一律不碰。
"""
import re
import time
from pathlib import Path

from app.config import LOG_ARCHIVE_MAX_AGE_DAYS, PROJECT_ROOT
from app.services import cleanup

# NSSM 轮转归档命名：api-stdout-20260912T055847.984.log（AppStdoutCopyTimestamp 式）
ARCHIVE_RE = re.compile(r"^(?:api|caddy)-(?:stdout|stderr)-\d{8}T\d{6}\.\d{3}\.log$")


def prune_archives(log_dir: Path | None = None, max_age_days: int | None = None) -> int:
    """删除超过 max_age_days 的日志归档，返回删除数。竞态 OSError 逐文件跳过。"""
    log_dir = log_dir if log_dir is not None else PROJECT_ROOT / "logs"
    days = LOG_ARCHIVE_MAX_AGE_DAYS if max_age_days is None else max_age_days
    cutoff = time.time() - days * 86400
    removed = 0
    if not log_dir.is_dir():
        return removed
    for f in log_dir.iterdir():
        try:
            if not ARCHIVE_RE.match(f.name) or not f.is_file():
                continue
            if f.stat().st_mtime >= cutoff:
                continue
            f.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


def _sweep_log_archives() -> None:
    prune_archives()


cleanup.register_sweep("log_archives", _sweep_log_archives)
