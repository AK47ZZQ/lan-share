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
from app.filetools import TRASH_OPS_LOCK
from app.services import loop_health
from app.store import devices as devices_store
from app.store import notifications as notifications_store
from app.store import shares as shares_store
from app.store import trash as trash_store
from app.store import ttl_state

# (名称, 清理函数) 注册表：各域模块在自身被导入时注册
_sweeps: list[tuple[str, Callable[[], None]]] = []


def register_sweep(name: str, fn: Callable[[], None]) -> None:
    # 按名去重（v3.67.5，分叉批 P2-8 同款）：模块被二次加载/import 环绕行时
    # 无条件 append 会让同一 sweep 每轮跑两遍——metrics/backup 的幂等声明
    # 与实现就此分叉
    for i, (n, _) in enumerate(_sweeps):
        if n == name:
            _sweeps[i] = (name, fn)
            return
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
    # 持引用防 GC（v3.67.5，R2-P3-8 同款，与 healthreport 一致）：create_task
    # 未保存引用的任务可能被 GC 回收——清理循环静默停摆，所有 sweep 连带失效
    global _cleanup_task
    _cleanup_task = asyncio.create_task(cleanup_loop())


_cleanup_task: asyncio.Task | None = None


def _cleanup_tmp():
    # 2026-11-04 修：tmp 子段不存在时绝不能短路后面的孤儿回收段 ——
    # data/tmp 是历史遗迹（现版上传走同目录 .uploading-* 中间态，全仓
    # 没有任何写手），全新部署上它永远不存在，孤儿清理整段失明。
    tmp = SHARE_ROOT / "tmp"
    if tmp.is_dir():
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
    # 孤儿上传临时文件（崩溃残留）：超过 1 天的一律回收。
    # 上传临时名已唯一化（.uploading-<pid>-<ns>），锚定正则只收精确仿造
    # 临时名格式（ns 为 15 位以上时间戳量级）的（R2-P1 回归修复）。
    # 2026-11-04 收紧：去掉裸 .uploading/.dav-uploading 后缀档 ——
    # 当前代码已无写手（全部唯一化），留着只会误删用户合法文件
    # （my.uploading / video.dav-uploading）；裸 .dedup 仍是活跃中间态
    # （uploads.py 去重），保留。
    orphan_cutoff = time.time() - 86400
    orphan_re = re.compile(r"\.(?:uploading|dav-uploading)-\d+-\d{15,}$")
    for f in SHARE_ROOT.rglob("*"):
        # 全树扫描对竞态不设防会杀掉整轮清理（R1-P2-3，与函数头部注释同源）
        try:
            # .trash 是回收站：保留期由 _cleanup_trash 按 deleted_at 管理，
            # 孤儿规则绝不能越权提前物理销毁里面的条目（2026-11-04 review 轮补）
            if f.relative_to(SHARE_ROOT).parts[0] == ".trash":
                continue
            if f.is_file() and (f.name.endswith(".dedup")
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
    # 与恢复/清空共用全操作锁（filetools.TRASH_OPS_LOCK，v3.67.2 P2-6）：
    # rmtree 整树与成员 restore 的 shutil.move 交错，会把恢复中的目录搬出半截
    with TRASH_OPS_LOCK:
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
        # 审计日志保留期。
        #
        # audit_log 是唯一没有任何保留策略的表 —— 每次登录、上传、删除都写一行，
        # 24×7 跑几年就是百万级。它的用途是「最近谁动过什么」，翻几个月以前
        # 的记录在实际使用里没有意义，但表会一直涨、索引一直胖、VACUUM 越来越吃力。
        #
        # 两年是刻意选的偏长值：一年前的记录基本用不上，但留足时间给
        # 「很久以后才发现的问题」。要更短可以改 settings 里的 audit_keep_days。
        keep_days = 730
        try:
            row = _db().execute(
                "SELECT value FROM settings WHERE key='audit_keep_days'").fetchone()
            if row and row[0]:
                keep_days = max(30, int(row[0]))
        except Exception:
            pass
        _db().execute("DELETE FROM audit_log WHERE ts < ?", (now - keep_days * 86400,))
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
