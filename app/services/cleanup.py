"""定时清理调度器（07 审查批次 3B：注册式改造）。

历史：system.py 的"清理五件套"直接 import files/_zips、share/_tickets 等
别家私有符号——每加一个带 TTL 状态的模块都要回来改这里。现在各域在自己
的模块里通过 register_sweep() 注册清理函数（uploads 会话/archive zip/
share 票据），本模块只负责：
- 通用清理：tmp 过期、孤儿上传临时文件、缩略图缓存、回收站过期、DB 行级过期
- 周期调度：CLEANUP_INTERVAL 一轮，线程池执行（全树 rglob 是同步 IO）
"""
import asyncio
import re
import shutil
import time
from typing import Callable

from app.config import CLEANUP_INTERVAL, SHARE_ROOT, TMP_MAX_AGE_DAYS, TRASH_DAYS
from app.db import _db, _db_lock
from app.services import loop_health
from app.store import devices as devices_store
from app.store import notifications as notifications_store
from app.store import shares as shares_store
from app.store import trash as trash_store
from app.store import ttl_state

# (名称, 清理函数) 注册表：各域模块在自身被导入时注册
_sweeps: list[tuple[str, Callable[[], None]]] = []


def register_sweep(name: str, fn: Callable[[], None]) -> None:
    _sweeps.append((name, fn))


def run_registered() -> None:
    for name, fn in _sweeps:
        # 分项就地吞 + 分项登记：一个坏 sweep 不该毒掉后面的 sweep 和 _cleanup_db
        with loop_health.watched_sync(f"sweep:{name}"):
            fn()



async def startup() -> None:
    """先跑一轮再起周期循环（由 app.main lifespan 统一调度）。"""

    def _run_all():
        # 四段各自隔离（同上）：全树 rglob 的 tmp/trash 清理最容易因竞态抛
        # OSError，段间不隔离则一次竞态 = 本轮 DB 过期行全部不清
        for name, fn in _STAGES:
            with loop_health.watched_sync(f"cleanup:{name}"):
                fn()

    async def cleanup_loop():
        while True:
            # 清理任务含全树 rglob（同步 IO），必须放线程池——直接 await 会周期性
            # 冻住整个事件循环（review 2026-09-02 P2-4）
            # 单轮异常必须吞掉记日志：逸出就会杀死 while 循环——此后到重启为止
            # 所有定时清理全部停摆。watched 同时登记，见 services/loop_health
            async with loop_health.watched("cleanup"):
                await asyncio.to_thread(_run_all)
            await asyncio.sleep(CLEANUP_INTERVAL)

    async with loop_health.watched("cleanup"):
        await asyncio.to_thread(_run_all)
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
    # 保留期时钟 = 行侧 deleted_at（回收站界面展示的也是它）：文件 mtime 是
    # 原始修改时间且 os.replace/shutil.move 原样保留——删一个 400 天前的老
    # 照片，按 mtime 判"已在回收站 30 天"会在下一小时内把它物理销毁，而
    # deleted_at 才刚起算："30 天可恢复"在生产上是假的（2026-09-27 自主审查
    # P0）。行不在的孤儿文件（对账救援的候选）仍按 mtime 判期，救援条目的
    # 行侧 deleted_at 也对齐 mtime（register_rescued），故"先清理后登记"的
    # 段序约束与本判据相容：被救援条目的寿命仍由 mtime 决定。
    rows = {r["trash_path"]: r["deleted_at"] for r in trash_store.list_all()}
    for f in trash_root.iterdir():
        try:
            clock = rows.get(f".trash/{f.name}")
            if clock is None:
                clock = f.stat().st_mtime
            if clock >= cutoff:
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
    # v3.57 外置票据的过期回收（配对码/zip 票据与在途位/分享取件票据）：
    # 与其它"行级过期"同类，放这里而不是另开 sweep——少一个"忘了注册"的机会
    ttl_state.sweep_expired(now)


# 段顺序有语义约束（v3.67 对抗审查 P1）：**trash 段必须在 registered 之前**——
# trash_orphan_audit 的 29 天救援上限依赖"先按 mtime 清理、后对账登记"的次序；
# 顺序颠倒会让登记立即被清理吃掉（家人看到条目秒生秒灭）。测试钉住次序。
_STAGES = (("tmp", _cleanup_tmp), ("trash", _cleanup_trash),
           ("registered", run_registered), ("db", _cleanup_db))
