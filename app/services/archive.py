"""zip 打包票据（07 审查批次 3A：从 files.py 收口为服务；v3.57 状态落 PG）。

票据表此前是 files.py 的模块私有字典，admin 导出与 system 清理各自 import
内部符号——存储布局改动会连锁。现在这里是唯一事实源：
- issue_ticket()   签发短时效下载票据
- pop_ticket()     取件（一次性，过期即毁）
- sweep_expired()  过期 zip 文件回收（services.cleanup 注册调用）

状态本身存 `store/ttl_state`（scope=zip_ticket / zip_slot），**不再在进程内**：
- 几十分钟的大 zip 下到一半遇到服务重启，链接不再凭空失效（旧行为：403 重来）；
- 打包异常漏 `release_slot()` 时，在途位随 TTL 自愈（旧行为：该用户被锁到重启）。
对外函数签名与返回形态保持不变（`pop_ticket` 仍回 {"path": Path}）。
"""
import secrets
import shutil
import threading
import time
from pathlib import Path

from app.config import SHARE_ROOT, ZIP_TTL
from app.services import cleanup
from app.store import ttl_state
from fastapi import HTTPException

_SLOT_TTL = ZIP_TTL * 2   # 在途打包位最长占用（崩溃/漏 release 的自愈上界）
_TICKET_CAP = 200   # 票据上限（9.9 复审 P2）：开工前拒绝，不留孤儿 zip 文件


class SlotBusy(Exception):
    """同一用户已有打包任务在途。"""


class SlotCap(Exception):
    """票据表已达上限。"""


def acquire_slot(username: str) -> None:
    """占打包并发位并做票据上限预检；调用方用完必须 release_slot()。

    占位是原子的（put_if_absent：INSERT ON CONFLICT DO NOTHING + 过期行清理同
    事务内），两 worker 同时为同一账号打包也只有一个能拿到。
    """
    if ttl_state.count(ttl_state.SCOPE_ZIP_TICKET) >= _TICKET_CAP:
        raise SlotCap()
    if not ttl_state.put_if_absent(ttl_state.SCOPE_ZIP_SLOT, username, "1", _SLOT_TTL):
        raise SlotBusy()


def release_slot(username: str) -> None:
    ttl_state.delete(ttl_state.SCOPE_ZIP_SLOT, username)


def issue_ticket(zpath: Path) -> str:
    token = secrets.token_urlsafe(16)
    ttl_state.put(ttl_state.SCOPE_ZIP_TICKET, token, str(zpath), ZIP_TTL)
    return token


def pop_ticket(token: str) -> dict | None:
    """取走票据（一次性）。过期/未命中返回 None。"""
    val = ttl_state.take(ttl_state.SCOPE_ZIP_TICKET, token)
    if val is None:
        return None
    return {"path": Path(val)}


# ── 打包字节预留制（v3.68，分叉批 P2-5 移植）──────────────────
# /api/zip 与 admin_export 的"检查 free-5GB → 开写"是 check-then-act：
# 两路并发各自看到同样的 free，合计可越过余量把盘写满（数据盘与 PG 同盘）。
# 预留台账把"预计写入量"记在进程内，后到者按 free - 在途预留 判定；
# 进程重启台账清零 = 自然自愈（最坏窗口内可能超写，仍有 5GB 底线兜着）。
_reserved_lock = threading.Lock()
_reserved_bytes = 0
_ZIP_FLOOR = 5 * 1024 ** 3   # 与 files.py 原 5GB 底线一致


def reserve_bytes(n: int) -> None:
    """为一次打包预留 n 字节；余量不足（含他人在途预留）抛 507。"""
    global _reserved_bytes
    with _reserved_lock:
        free = shutil.disk_usage(str(SHARE_ROOT)).free
        if free - _reserved_bytes - n < _ZIP_FLOOR:
            raise HTTPException(
                status_code=507,
                detail="磁盘剩余空间不足，无法打包（含在途打包预留，需为系统保留 5GB 余量）")
        _reserved_bytes += n


def release_bytes(n: int) -> None:
    global _reserved_bytes
    with _reserved_lock:
        _reserved_bytes = max(0, _reserved_bytes - n)


def sweep_expired() -> None:
    """过期回收：.zips 目录里超 2×TTL 的文件。

    （票据行本身的过期删除已随 `cleanup._cleanup_db` 的 ttl_state.sweep_expired
    统一收口，此处不再各扫各的——v3.57 前这里还要清一份进程内字典。）
    """
    zdir = SHARE_ROOT / ".zips"
    if not zdir.exists():
        return
    cutoff = time.time() - ZIP_TTL * 2
    for f in zdir.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            continue   # 竞态（票据下载并发删除）跳过（R1-P2-3）


cleanup.register_sweep("zip_tickets", sweep_expired)
