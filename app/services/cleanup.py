"""定时清理调度器（07 审查批次 3B：注册式改造）。

历史：system.py 的"清理五件套"直接 import files/_zips、share/_tickets 等
别家私有符号——每加一个带 TTL 状态的模块都要回来改这里。现在各域在自己
的模块里通过 register_sweep() 注册清理函数（uploads 会话/archive zip/
share 票据），本模块只负责：
- 通用清理：tmp 过期、孤儿上传临时文件、缩略图缓存、回收站过期、DB 行级过期
- 周期调度：CLEANUP_INTERVAL 一轮，线程池执行（全树 rglob 是同步 IO）
"""
import asyncio
import logging
import re
import shutil
import time
from typing import Callable

from app.config import CLEANUP_INTERVAL, SHARE_ROOT, TMP_MAX_AGE_DAYS, TRASH_DAYS
from app.db import _db, _db_lock
from app.store import devices as devices_store
from app.store import notifications as notifications_store
from app.store import shares as shares_store
from app.store import trash as trash_store

# (名称, 清理函数) 注册表：各域模块在自身被导入时注册
_sweeps: list[tuple[str, Callable[[], None]]] = []


def register_sweep(name: str, fn: Callable[[], None]) -> None:
    _sweeps.append((name, fn))


def run_registered() -> None:
    for _name, fn in _sweeps:
        fn()


async def startup() -> None:
    """先跑一轮再起周期循环（由 app.main lifespan 统一调度）。"""

    def _run_all():
        _cleanup_tmp()
        _cleanup_trash()
        run_registered()
        _cleanup_db()

    async def cleanup_loop():
        while True:
            # 清理任务含全树 rglob（同步 IO），必须放线程池——直接 await 会周期性
            # 冻住整个事件循环（review 2026-09-02 P2-4）
            # 单轮异常必须吞掉记日志：清理内部对竞态（文件在 iterdir 与 stat/unlink
            # 之间被并发删掉）不设防，一次 OSError 逸出就会杀死 while 循环——
            # 此后到重启为止所有定时清理（回收站过期/上传会话/zip/DB）全部停摆
            try:
                await asyncio.to_thread(_run_all)
            except Exception:
                logging.exception("cleanup round failed")
            await asyncio.sleep(CLEANUP_INTERVAL)

    try:
        await asyncio.to_thread(_run_all)
    except Exception:
        logging.exception("first cleanup round failed")
    asyncio.create_task(cleanup_loop())


def _cleanup_tmp():
    tmp = SHARE_ROOT / "tmp"
    if not tmp.exists():
        return
    cutoff = time.time() - TMP_MAX_AGE_DAYS * 86400
    for f in tmp.rglob("*"):
        try:
            if not f.is_file() or f.stat().st_mtime >= cutoff:
                continue
            f.unlink(missing_ok=True)
            if f.parent != tmp and not any(f.parent.iterdir()):
                f.parent.rmdir()
        except OSError:
            continue   # 竞态（并发删/占用）跳过，下轮再收
    # 孤儿上传临时文件（崩溃残留）：超过 1 天的 .uploading/.dav-uploading/
    # .dedup/.tmp-* 一律回收。上传临时名已唯一化（.uploading-<pid>-<ns>），
    # endswith 匹配不到——必须同时按中缀匹配（R2-P1 回归修复）
    orphan_cutoff = time.time() - 86400
    # 锚定正则而非任意中缀：用户可能合法拥有 "a.uploading-1-2" 这类文件名
    # （红队 P1），只有精确仿造临时名格式（.uploading-<pid>-<ns>，ns 为
    # 15 位以上时间戳量级）才回收
    orphan_re = re.compile(r"\.(?:uploading|dav-uploading)-\d+-\d{15,}$")
    for f in SHARE_ROOT.rglob("*"):
        # 全树扫描对竞态不设防会杀掉整轮清理（R1-P2-3，与函数头部注释同源）
        try:
            if f.is_file() and (f.name.endswith((".uploading", ".dav-uploading", ".dedup"))
                                or orphan_re.search(f.name)
                                or f.name.startswith(".tmp-")) \
                    and f.stat().st_mtime < orphan_cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            continue
    # 缩略图缓存：30 天未再生成（即 30 天没被访问命中缺失）的档位回收，防慢性磁盘泄漏
    thumbs = SHARE_ROOT / ".thumbs"
    if thumbs.exists():
        stale = time.time() - 30 * 86400
        for f in thumbs.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < stale:
                    f.unlink(missing_ok=True)
            except OSError:
                continue


def _cleanup_trash():
    trash_root = SHARE_ROOT / ".trash"
    if not trash_root.exists():
        return
    cutoff = time.time() - TRASH_DAYS * 86400
    for f in trash_root.iterdir():
        try:
            if f.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue   # 与用户"清空回收站"并发：条目已消失，下轮自然收敛
        if f.is_dir():
            shutil.rmtree(f, ignore_errors=True)
        else:
            f.unlink(missing_ok=True)
    trash_store.delete_before(cutoff)


def _cleanup_db():
    now = time.time()
    with _db_lock:
        _db().execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        _db().commit()
    devices_store.delete_expired(now)
    shares_store.purge_expired(now)
    notifications_store.delete_read_before(now - 30 * 86400)
