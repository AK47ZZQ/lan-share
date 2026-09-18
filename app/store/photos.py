"""photos 表的**唯一主人**（B1 存储层拆分）。

## 边界

- 本模块是该表**唯一**的 SQL 出口：业务模块只调用语义方法，不认识列名。
- schema 归 `app/store/schema.py`（DDL）。
- 补扫/扫描的**业务计算**（ffprobe、PIL、文件系统遍历）留在调用方；本模块只收
  "成批写入"的落库动作——多组 executemany 打包在一次锁+commit 内完成
  （与原实现相同的提交粒度，行为等价）。
- 写方法自持 `_db_lock` + commit；处在 `db.transaction()` 内时推迟收束。
"""
from app import db as _dbmod
from app.db import _db, _db_lock

# ─── 读 ───────────────────────────────────────────────────


def list_probe_missing() -> list:
    """时长/编码/尺寸任一缺失的行（视频补扫候选集）。"""
    return _db().execute("SELECT path, duration, codec, width FROM photos "
                         "WHERE duration IS NULL OR codec IS NULL OR width IS NULL").fetchall()


def list_missing_sizes(limit: int) -> list:
    """缺宽高的已处理图片行（尺寸补扫候选集，duration=0 排除视频）。"""
    return _db().execute(
        "SELECT path FROM photos WHERE width IS NULL AND duration=0 LIMIT ?",
        (limit,)).fetchall()


def load_index() -> list:
    """全表轻量索引（增量扫描的内存比对基线）。"""
    return _db().execute("SELECT path, mtime_ns, size FROM photos").fetchall()


def list_newest_rows(limit: int) -> list:
    """最新入库 N 行 (path, mtime_ns, size)（缩略图预热候选集——用户最先
    浏览的就是刚上传的这些；added_at 单调，新→旧）。

    排序键必须**双引擎等价**，这里踩过两个坑（v3.56）：
    - 原先的 `rowid DESC` 是 SQLite 独有伪列，生产 PG 直接 UndefinedColumn，
      而调用方的 per-round 异常吞掉让它静默停摆数日；
    - `added_at DESC` 在 SQLite 把 NULL 排最后、在 PG 排最前（实测），
      存量 NULL 行会在 PG 上顶满候选集。故 added_at 由 schema 启动期回填
      清零（见 store/schema.py），这里不再用 COALESCE——表达式会让
      idx_photos_added 失效，预热每 30s 一轮，必须走索引。
    tiebreaker 用 path（非 rowid）：预热只要"确定的新→旧"，不需要插入序。"""
    return _db().execute(
        "SELECT path, mtime_ns, size FROM photos "
        "ORDER BY added_at DESC, path LIMIT ?", (limit,)).fetchall()


def list_for_gallery(where_sql: str = "", args: "tuple | list" = (),
                     order_sql: str = "") -> list:
    """画廊行集（下推过滤版）。where/order 两个片段由 API 层按**白名单分支**
    拼装（不接用户原文），列名知识仍收敛在本模块——SQL 模板单点在这里。"""
    sql = "SELECT path, taken_at, duration, codec, width, height FROM photos"
    if where_sql:
        sql += f" WHERE {where_sql}"
    if order_sql:
        sql += f" ORDER BY {order_sql}"
    return _db().execute(sql, args).fetchall()


def list_paths_by_taken() -> list:
    """(path, taken_at) 全量按拍摄日期倒序（facets 分布统计用）。"""
    return _db().execute(
        "SELECT path, taken_at FROM photos ORDER BY taken_at DESC").fetchall()


def count() -> int:
    return _db().execute("SELECT COUNT(*) AS c FROM photos").fetchone()["c"]


def count_and_size() -> tuple[int, int]:
    """(条数, 总字节)（管理端概览统计）。"""
    total = _db().execute("SELECT COUNT(*) AS v FROM photos").fetchone()["v"]
    size = _db().execute("SELECT COALESCE(SUM(size), 0) AS v FROM photos").fetchone()["v"]
    return total, size


# ─── 写 ───────────────────────────────────────────────────


def apply_probe_updates(others: list[str], updates: list[tuple]) -> None:
    """视频补扫落库（一次锁+commit）：
    - others：非视频行 duration 置 0（"已处理"）；
    - updates：(duration, codec, width, height, path) 探到啥刷啥。"""
    if not others and not updates:
        return
    with _db_lock:
        if others:
            _db().executemany("UPDATE photos SET duration=0 WHERE path=?",
                              [(p,) for p in others])
        if updates:
            _db().executemany("UPDATE photos SET duration=?, codec=?, width=?, height=? WHERE path=?",
                              updates)
        if not _dbmod.in_transaction():
            _db().commit()


def apply_probe_taken_at(taken_updates: list[tuple]) -> None:
    """视频拍摄日期单独刷（仅探到 creation_time 的行）。"""
    if not taken_updates:
        return
    with _db_lock:
        _db().executemany("UPDATE photos SET taken_at=? WHERE path=?", taken_updates)
        if not _dbmod.in_transaction():
            _db().commit()


def apply_size_scan(updates: list[tuple], gone: list[tuple]) -> None:
    """尺寸补扫落库（一次锁+commit）：updates=(w,h,path)；gone=(path,) 文件已
    消失的行直接删（索引自清理）。"""
    if not updates and not gone:
        return
    with _db_lock:
        if updates:
            _db().executemany("UPDATE photos SET width=?, height=? WHERE path=?",
                              updates)
        if gone:
            _db().executemany("DELETE FROM photos WHERE path=?", gone)
        if not _dbmod.in_transaction():
            _db().commit()


def apply_scan(updates: list[tuple], gone: list[str]) -> None:
    """全量增量扫描落库（一次锁+commit）：
    - upsert 新/变更行（ON CONFLICT 不碰 added_at——首次入库打点保持）；
    - 顺手清 mtime_ns=0 的占位坏行；
    - gone：文件已消失的条目删行。"""
    with _db_lock:
        if updates:
            _db().executemany(
                "INSERT INTO photos(path, taken_at, mtime_ns, size, duration, added_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET taken_at=excluded.taken_at, "
                "mtime_ns=excluded.mtime_ns, size=excluded.size, duration=excluded.duration", updates)
            _db().execute("DELETE FROM photos WHERE mtime_ns=0")
        if gone:
            _db().executemany("DELETE FROM photos WHERE path=?", [(g,) for g in gone])
        if not _dbmod.in_transaction():
            _db().commit()
