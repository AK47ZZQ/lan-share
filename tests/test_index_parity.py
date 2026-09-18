"""索引落地守卫（v3.56）：**两引擎都跑**（本文件故意不设 skipif）。

`_INDEXES` 是索引单源，两条建库路径（PG 的 `executescript(_INDEXES)`、
SQLite `_migrate_v3` 尾部拼接）共用同一份文本。文本一致 ≠ 落地一致：
PG 的 `executescript` 是按 `;` 拆条逐个执行，SQLite 走原生 executescript——
任一侧因语法/顺序/权限静默少建一个索引，查询会退回全表扫且**没有任何报错**。

所以这里不看文本，直接问库："你身上到底有哪些 idx_*"。
跑法同整轮：不设 LANSHARE_TEST_PG_URL 走 SQLite，设了走真 PG。
"""
import inspect
import re

from app.db import USE_PG, _db
from app.store import photos as photos_store
from app.store.schema import _INDEXES

_CREATE_INDEX = re.compile(
    r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+ON\s+(\w+)", re.I)
_INDEX_BODY = re.compile(
    r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+ON\s+(\w+)\s*\(([^)]*)\)",
    re.I)


def _expected() -> dict[str, str]:
    """{索引名: 所属表}——来自 _INDEXES 单源。"""
    return {n.lower(): t.lower() for n, t in _CREATE_INDEX.findall(_INDEXES)}


def _index_columns() -> dict[str, str]:
    """{索引名: 括号内列序小写归一}。"""
    return {n.lower(): re.sub(r"\s+", " ", cols).strip().lower()
            for n, _t, cols in _INDEX_BODY.findall(_INDEXES)}


def _live_indexes() -> set[str]:
    conn = _db()
    if USE_PG:
        rows = conn.execute(
            "SELECT indexname AS n FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname LIKE 'idx_%'").fetchall()
    else:
        rows = conn.execute(
            "SELECT name AS n FROM sqlite_master "
            "WHERE type='index' AND name LIKE 'idx_%'").fetchall()
    return {r["n"].lower() for r in rows}


def test_index_source_declares_indexes():
    """解析器与命名约定对不上时，本守卫会因"期望集为空"而假绿——先自证有效。"""
    exp = _expected()
    assert len(exp) >= 9, f"_INDEXES 解析出的索引数异常（{len(exp)}），守卫失效"


def test_every_declared_index_actually_exists():
    missing = sorted(set(_expected()) - _live_indexes())
    assert not missing, (
        f"这些索引在库中不存在（USE_PG={USE_PG}）——对应查询会静默退回全表扫: {missing}")


def test_no_undeclared_index_in_live_db():
    extra = sorted(_live_indexes() - set(_expected()))
    assert not extra, (
        f"库里有 _INDEXES 之外的 idx_*（USE_PG={USE_PG}）：单源被绕过，"
        f"下次建库/迁移会丢: {extra}")


def test_photos_added_index_matches_warmup_order_by():
    """索引列序必须与预热查询的 ORDER BY **逐项相等**，否则索引存在但用不上。

    两边都从源码里取（不写第二份字面量）：任一侧改动都会让另一侧变红。
    这条查询每 30s 跑一轮、扫全 photos 表排序，退化成全表扫是性能事故。
    """
    src = inspect.getsource(photos_store.list_newest_rows)
    m = re.search(r"ORDER BY (.+?) LIMIT", src)
    assert m, "list_newest_rows 里没有 `ORDER BY ... LIMIT` 了？同步更新本守卫"
    cols = _index_columns().get("idx_photos_added")
    assert cols == m.group(1).strip().lower(), (
        f"索引列序 `{cols}` 与查询 ORDER BY `{m.group(1).strip()}` 不一致")
