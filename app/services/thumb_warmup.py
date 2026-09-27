"""缩略图后台预热（20260914213301-31cd「照片加载很慢」根修的服务端一半）。

根因：/thumb 全按需生成——批量上传后首次浏览照片墙，视口内每个格子都是
冷缩略图，视频 ffmpeg 抽帧最坏 25s/个，客户端 8 路并发请求全部堵在服务端
生成队列里，表现为整屏转圈。

预热循环按**入库时间新→旧**扫描 photos 表，低速率补齐 .thumbs/ 缺失的
缩略图，让浏览命中时直接回热图。节流设计：
- 单张串行（asyncio.to_thread），不与上传/浏览抢多核；
- 每轮最多 _BATCH 张、其中视频最多 _VIDEO_CAP 个（ffmpeg 最重）；
- 每轮只看最新 _SCAN_ROWS 行（用户最先浏览的就是刚上传的），无缺失静默待机；
- 张间小睡，把 CPU 让给上传流水线。
损坏媒体/缺解码器会稳定 415/404/500：单张失败跳过即可，客户端负缓存
（AuthCacheManager 10 分钟 TTL）兜底，不会反复重试同一张。
"""
import asyncio
import hashlib
import logging
import time
from pathlib import Path

from app.api.previews import generate_thumb
from app.config import SHARE_ROOT
from app.filetools import IMG_EXTS, VID_EXTS
from app.services import loop_health
from app.store import photos as photos_store

_TICK_SECONDS = 30     # 轮间隔
_BATCH = 8             # 每轮最多补几张
_VIDEO_CAP = 2         # 每轮视频上限（ffmpeg 最重）
_SCAN_ROWS = 600       # 每轮只看最新 N 行
_PER_ITEM_SLEEP = 0.3  # 张间小睡（秒），预热让路于上传/浏览


def pick_missing(rows, thumbs_dir: Path, batch: int = _BATCH,
                 video_cap: int = _VIDEO_CAP) -> list[tuple[str, Path]]:
    """从 [(path, mtime_ns, size)]（新→旧）挑缺缩略图的，返回 (rel, 缓存路径)。
    纯函数（目录注入），供单测。键公式与 /thumb 端点严格一致，错一位就白烧 CPU。"""
    out: list[tuple[str, Path]] = []
    vids = 0
    for rel, mtime_ns, size in rows:
        ext = Path(rel).suffix.lower()
        if ext in VID_EXTS:
            if vids >= video_cap:
                continue
        elif ext not in IMG_EXTS:
            continue   # photos 表只存媒体，防御性跳过
        key = hashlib.md5(f"{rel}:{mtime_ns}:{size}".encode()).hexdigest()
        cached = thumbs_dir / f"{key}.jpg"
        if cached.exists():
            continue
        out.append((str(rel), cached))
        if ext in VID_EXTS:
            vids += 1
        if len(out) >= batch:
            break
    return out


def _tick() -> int:
    """一轮预热，返回本轮生成张数（运维观测用）。"""
    raw = photos_store.list_newest_rows(_SCAN_ROWS)
    if not raw:
        return 0
    # 行归一化：SQLite sqlite3.Row 可直接 unpack，PG RealDictRow 是 dict——
    # 直接 for a, b, c in rows 解包出的是列名（PG 双引擎教训），按类型收敛
    rows = [
        (r["path"], r["mtime_ns"], r["size"]) if isinstance(r, dict)
        else tuple(r)
        for r in raw
    ]
    thumbs = SHARE_ROOT / ".thumbs"
    thumbs.mkdir(exist_ok=True)
    made = 0
    for rel, cached in pick_missing(rows, thumbs):
        f = SHARE_ROOT / rel
        if not f.exists():
            continue
        try:
            generate_thumb(f, cached, f.suffix.lower())
            made += 1
        except Exception:
            # 损坏媒体/缺解码器：跳过，下轮不再挑中（键含 mtime+size，
            # 文件没变就还是缺——靠客户端负缓存兜底，不在此重试）
            logging.debug("thumb warmup skip %s", rel, exc_info=True)
        time.sleep(_PER_ITEM_SLEEP)
    return made


async def startup() -> None:
    """独立周期循环（main lifespan 挂载，与 cleanup/notify 同型）。"""

    async def loop():
        while True:
            # 单轮异常吞掉记日志：逸出会杀死 while 循环，预热静默停摆。
            # 成败同时登记进 loop_health——本循环曾因 SQL 用了 SQLite 独有的
            # rowid 而在生产 PG 上每轮抛错、静默停摆数日（v3.56 教训），
            # "吞异常保活"必须配"看得见"，否则保活=失踪。
            async with loop_health.watched("thumb warmup"):
                await asyncio.to_thread(_tick)
            await asyncio.sleep(_TICK_SECONDS)

    # 持引用防 GC（v3.67.5，R2-P3-8 同款）：main.py 注释声称"自持"与事实不符，
    # create_task 未保存引用的任务可能被 GC 回收——预热循环静默停摆
    global _warmup_task
    _warmup_task = asyncio.create_task(loop())


_warmup_task: asyncio.Task | None = None
