"""缩略图后台预热（20260914213301-31cd）测试。

预热键公式必须与 /thumb 端点逐字节一致——错一位就是整轮 CPU 白烧且永远
补不齐（键 = md5(f"{rel}:{mtime_ns}:{size}")，与 previews.thumb_file 同源）；
视频按轮限额（ffmpeg 最重）；非媒体扩展名跳过；batch 截断。

**本文件曾有盲区**（v3.56 事故）：只测纯函数 `pick_missing`，从不执行
`_tick()`，于是 `_tick` 依赖的 `list_newest_rows` 里那句 SQLite 独有的
`rowid` 排序键在门禁全绿下上线，生产 PG 每 30s 抛 UndefinedColumn、预热
静默停摆数日。故下述 `test_tick_*` 必须**真跑 `_tick()`**：跑在 conftest
选中的引擎上（设 LANSHARE_TEST_PG_URL 即真 PG），这条路径再无人值守不可达。
"""

import hashlib
import os
import shutil

import pytest

from app.config import SHARE_ROOT
from app.db import USE_PG, _db, _db_lock
from app.services import thumb_warmup
from app.services.thumb_warmup import pick_missing
from app.store import photos as photos_store
from app.store.schema import _PHOTOS_ADDED_BACKFILL


def test_pick_missing_generates_endpoint_key(tmp_path):
    rows = [("照片/a.jpg", 111, 222)]
    got = pick_missing(rows, tmp_path)
    assert len(got) == 1
    rel, cached = got[0]
    assert rel == "照片/a.jpg"
    key = hashlib.md5("照片/a.jpg:111:222".encode()).hexdigest()
    assert cached == tmp_path / f"{key}.jpg"
    # 与端点同键：文件生成后下一轮不再挑中（不重复烧 CPU）
    cached.write_bytes(b"x")
    assert pick_missing(rows, tmp_path) == []


def test_pick_missing_video_cap_and_non_media(tmp_path):
    rows = [
        ("照片/a.jpg", 1, 1),
        ("视频/v1.mp4", 2, 2),
        ("视频/v2.mp4", 3, 3),
        ("视频/v3.mp4", 4, 4),   # 超出 video_cap
        ("照片/b.txt", 5, 5),    # 非媒体：跳过
        ("照片/c", 6, 6),        # 无扩展名：跳过
    ]
    got = pick_missing(rows, tmp_path, batch=10, video_cap=2)
    assert {r for r, _ in got} == {"照片/a.jpg", "视频/v1.mp4", "视频/v2.mp4"}


def test_pick_missing_batch_limit(tmp_path):
    rows = [(f"照片/x{i}.jpg", i, i) for i in range(20)]
    assert len(pick_missing(rows, tmp_path, batch=8)) == 8


# ── _tick 真跑（v3.56 双引擎盲区补钉）─────────────────────────
# 下面三个用例的全部价值在于"执行"：它们跑在 conftest 选中的引擎上，
# 设 LANSHARE_TEST_PG_URL 即在真 PG 上执行同一句 SQL。SQLite-only 语法、
# 列不存在、NULL 排序漂移这类问题只有"真跑"能抓到。


def _seed(rel: str, added_at: float, mtime_ns: int = 1) -> str:
    """经真实写入口落一行 photos，并建同名空文件（_tick 会先判存在性）。"""
    f = SHARE_ROOT / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\xff\xd8fakejpg")
    photos_store.apply_scan([(rel, "2020-01-01", mtime_ns, 10, 0.0, added_at)], [])
    return rel


@pytest.fixture()
def warmup_set():
    """唯一前缀的 3 张测试照片（added_at 取 1e12+ 保证全库最新，不受他表残留干扰）。"""
    prefix = f"warm-{os.urandom(4).hex()}"
    rels = [_seed(f"{prefix}/a.jpg", 1e12 + 100),
            _seed(f"{prefix}/b.jpg", 1e12 + 300),
            _seed(f"{prefix}/c.jpg", 1e12 + 200)]
    yield prefix, rels
    photos_store.apply_scan([], rels)          # 唯一前缀定位，绝不用"最后一行"
    shutil.rmtree(SHARE_ROOT / prefix, ignore_errors=True)


def test_list_newest_rows_orders_new_first(warmup_set):
    """入库序新→旧，且 LIMIT 生效（预热只要候选集头部）。"""
    _, rels = warmup_set
    paths = [r["path"] for r in photos_store.list_newest_rows(3)]
    assert paths[:3] == [rels[1], rels[2], rels[0]]      # b(300) c(200) a(100)
    assert len(photos_store.list_newest_rows(2)) == 2


def test_list_newest_rows_returns_dict_safe_rows(warmup_set):
    """行访问必须双引擎安全：SQLite 的 sqlite3.Row 可下标可解包，PG 的
    RealDictRow 只认列名——`for a, b, c in rows` 在 PG 上会解包出**列名**。"""
    rows = photos_store.list_newest_rows(5)
    assert rows
    r = rows[0]
    if isinstance(r, dict):
        assert set(("path", "mtime_ns", "size")) <= set(r)
    else:
        assert len(tuple(r)) == 3


def test_added_at_backfill_is_portable(warmup_set):
    """回填语句在两引擎上都能跑且真清 NULL（PG/SQLite 共用 _PHOTOS_ADDED_BACKFILL）。

    背景：`ORDER BY added_at DESC` 对 NULL 的位置两引擎相反（实测：SQLite 沉底、
    PG 置顶），所以存量 NULL 必须清零，否则 PG 上预热候选集被最老的行顶满。
    """
    prefix, rels = warmup_set
    with _db_lock:                                   # 造一行"存量未打点"
        _db().execute("UPDATE photos SET added_at=NULL WHERE path=?", (rels[1],))
        _db().commit()
    with _db_lock:
        _db().execute(_PHOTOS_ADDED_BACKFILL)
        _db().commit()
    with _db_lock:
        row = _db().execute(
            "SELECT added_at FROM photos WHERE path=?", (rels[1],)).fetchone()
        left = _db().execute(
            "SELECT COUNT(*) AS c FROM photos WHERE added_at IS NULL "
            "AND path LIKE ?", (f"{prefix}/%",)).fetchone()["c"]
    assert abs(row["added_at"] - 1e-9) < 1e-6, \
        f"回填没接上 mtime_ns 代理（USE_PG={USE_PG}，得 {row['added_at']}）"
    assert left == 0, "回填语句没清掉 NULL"


def test_tick_runs_end_to_end(warmup_set, monkeypatch):
    """整轮 _tick 必须**不抛异常**地跑完——这一条就是 v3.56 rowid 事故的探测器。

    _render_thumb_slot 换成打桩（真生成要 PIL/ffmpeg，测试环境不该依赖；
    v3.68 起 warmup 经信号量槽调渲染，桩打在槽上连带双检/上限一起被绕开），
    张间小睡清零；DB 读、行归一化、键计算、文件存在性判断全部走真实代码。
    """
    prefix, rels = warmup_set
    mine = {str(SHARE_ROOT / r) for r in rels}      # 必须比全路径：整轮跑起来
    calls: list[str] = []                          # 别的用例也有 a.jpg 这种同名文件

    def fake_thumb(src, dst, ext):
        calls.append(str(src))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"jpeg")

    monkeypatch.setattr(thumb_warmup, "_render_job", fake_thumb)
    monkeypatch.setattr(thumb_warmup, "_PER_ITEM_SLEEP", 0)
    made = thumb_warmup._tick()                      # 曾在此抛 UndefinedColumn: rowid
    assert made >= 3, f"本轮应补齐 3 张，实际 {made}"
    assert mine <= set(calls), f"预热门未命中本批：{sorted(calls)[:5]}"

    calls.clear()
    thumb_warmup._tick()
    # 键含 mtime+size 且与端点同源：已补齐的这批不再被第二轮挑中
    assert not (mine & set(calls))
