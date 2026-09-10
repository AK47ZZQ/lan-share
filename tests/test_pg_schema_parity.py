"""跨引擎 schema 漂移守卫（11 号复审批次 0，P1-1）。

背景：测试夹具默认只设 `SHARE_ROOT`、不设 `DATABASE_URL` → `USE_PG=False` →
**全部测试跑 SQLite，而生产跑 PostgreSQL**。于是"加了列只改 SQLite 分支、
忘了改 `_PG_SCHEMA`"这类改动在门禁里**完全看不出来**：测试全绿、上线即
`UndefinedColumn` 500。本文件把这条盲区用静态比对堵上。

做法：SQLite 侧读**已迁移**的夹具库真实列（PRAGMA），PG 侧解析 `_PG_SCHEMA`
的 CREATE TABLE 文本，逐表断言 PG ⊇ SQLite。加表/加列漏一边 → 红。

（`_pg_sql` 的方言契约在 `test_pg_sql_dialect.py`；真库端到端由
`LANSHARE_TEST_PG_URL` 驱动的整轮测试覆盖，见 tests/conftest.py 模块 docstring。）
"""
import json
import re

import pytest

from app.db import USE_PG, _PG_SCHEMA

pytestmark = pytest.mark.skipif(
    USE_PG,
    reason="本守卫基于 SQLite 的 sqlite_master/PRAGMA 反射；PG 引擎下由真库端到端测试覆盖",
)

_CREATE_TABLE = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\);", re.S | re.I)
_NOT_A_COLUMN = {"PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT", "CHECK"}


def _pg_tables() -> dict[str, set[str]]:
    """从 `_PG_SCHEMA` 解析 {表名: 列名集合}。"""
    out: dict[str, set[str]] = {}
    for name, body in _CREATE_TABLE.findall(_PG_SCHEMA):
        cols = set()
        for raw in body.split(","):
            parts = raw.strip().split()
            if not parts or parts[0].upper() in _NOT_A_COLUMN:
                continue
            cols.add(parts[0].lower())
        out[name.lower()] = cols
    return out


def _sqlite_tables() -> dict[str, set[str]]:
    """从**已迁移**的测试 SQLite 库读取真实列（夹具已跑过 init_db + _migrate_v3）。"""
    from app.db import _db
    rows = _db().execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    out: dict[str, set[str]] = {}
    for r in rows:
        t = r["name"]
        if t.startswith("sqlite_"):
            continue
        out[t.lower()] = {c["name"].lower() for c in
                          _db().execute(f"PRAGMA table_info({t})").fetchall()}
    return out


def test_every_sqlite_table_exists_in_pg_schema():
    """加表只改 SQLite 分支（漏了 PG）→ 红。"""
    sqlite_t, pg_t = _sqlite_tables(), _pg_tables()
    assert sqlite_t, "SQLite 夹具库为空则本守卫失效"
    missing = sorted(set(sqlite_t) - set(pg_t))
    assert not missing, f"这些表在 PG schema 中缺失（生产会 500）: {missing}"


def test_pg_schema_covers_all_sqlite_columns():
    """加列只改 SQLite 分支（漏了 PG 的 CREATE TABLE 或增量 ALTER）→ 红。

    这是"生产库缺列"这类事故的直接探测器：SQLite 测试全绿、上线即 500。
    """
    sqlite_t, pg_t = _sqlite_tables(), _pg_tables()
    drift = {}
    for t, cols in sqlite_t.items():
        pg_cols = pg_t.get(t)
        if pg_cols is None:
            continue
        gap = sorted(cols - pg_cols)
        if gap:
            drift[t] = gap
    assert not drift, \
        f"PG schema 缺列（生产会 ProgrammingError）: {json.dumps(drift, ensure_ascii=False)}"
