"""zip 打包票据表（07 审查批次 3A：从 files.py 收口为服务）。

票据表此前是 files.py 的模块私有字典，admin 导出与 system 清理各自
import 内部符号——存储布局改动会连锁。现在这里是唯一事实源：
- issue_ticket()   签发短时效下载票据
- pop_ticket()     取件（一次性，过期即毁）
- sweep_expired()  过期票据与孤儿 zip 文件回收（services.cleanup 注册调用）
"""
import secrets
import threading
import time
from pathlib import Path

from app.config import SHARE_ROOT, ZIP_TTL
from app.services import cleanup

_tickets: dict[str, dict] = {}
_lock = threading.Lock()
# 在途打包的用户名（红队 P2：打包与全局线程池/磁盘共用，不限并发则单成员
# 可打瘫全站 + 30min 回收窗内灌满磁盘）
_busy: set[str] = set()
_TICKET_CAP = 200   # 票据上限（9.9 复审 P2）：开工前拒绝，不留孤儿 zip 文件


class SlotBusy(Exception):
    """同一用户已有打包任务在途。"""


class SlotCap(Exception):
    """票据表已达上限。"""


def acquire_slot(username: str) -> None:
    """占打包并发位并做票据上限预检；调用方用完必须 release_slot()。"""
    with _lock:
        if username in _busy:
            raise SlotBusy()
        if len(_tickets) >= _TICKET_CAP:
            raise SlotCap()
        _busy.add(username)


def release_slot(username: str) -> None:
    with _lock:
        _busy.discard(username)


def issue_ticket(zpath: Path) -> str:
    token = secrets.token_urlsafe(16)
    with _lock:
        _tickets[token] = {"path": zpath, "exp": time.time() + ZIP_TTL}
    return token


def pop_ticket(token: str) -> dict | None:
    """取走票据（一次性）。过期返回 None。"""
    with _lock:
        info = _tickets.pop(token, None)
    if not info or info["exp"] < time.time():
        return None
    return info


def sweep_expired() -> None:
    """过期回收：.zips 目录里超 2×TTL 的文件 + 票据表过期项
    （原 system._cleanup_zips，归位到状态所有者）。"""
    zdir = SHARE_ROOT / ".zips"
    if zdir.exists():
        cutoff = time.time() - ZIP_TTL * 2
        for f in zdir.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except OSError:
                continue   # 竞态（票据下载并发删除）跳过（R1-P2-3）
    with _lock:
        now = time.time()
        for k in [k for k, v in _tickets.items() if v["exp"] < now]:
            del _tickets[k]


cleanup.register_sweep("zip_tickets", sweep_expired)
