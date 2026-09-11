"""元数据库双引擎：SQLite（默认，开发/测试）或 PostgreSQL（DATABASE_URL，生产）。

- SQL 统一用 '?' 占位符书写；PG 适配层在执行前转为 '%s'（SQL 字符串字面量内的
  '?' 不替换——本项目 SQL 无该写法）。SQL 字面量里的 '%'（LIKE 模式等）会
  统一翻倍成 '%%'——psycopg 把裸 '%' 当占位符前缀，不翻倍直接 ProgrammingError。
- 行访问统一按 key（r["col"]）：SQLite 用 Row，PG 用 dict_row。
- 写并发：SQLite 靠 _db_lock 全局串行 + WAL；PG 用 autocommit（多语句原子序列
  已改为引擎中立的补偿写法，见 admin.py invite_redeem）。
- schema 与迁移在 `app/store/schema.py`（B1 存储层拆分迁出），本模块末尾
  re-export `init_db`/`pg_setval_sequences` 保既有 import 路径不变。
- 各表数据的语义读写归 `app/store/` 下各表主模块（users/notifications/…）。
"""
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
import atexit

from app.config import DB_PATH

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = DATABASE_URL.startswith("postgres")

_db_local = threading.local()
_txn_depth = threading.local()   # transaction() 嵌套深度（决定由谁 commit）
# 元数据库写锁。**RLock 而非 Lock**（11 号复审批次 2，A1）：本锁在写路径上被
# 广泛嵌套持有（调用方 `with _db_lock:` 临界区里再调用仓储方法/助手函数），
# 不可重入会自死锁——项目已被咬过一次（R1-P0-1：锁内调用 audit() 内部再取
# 本锁）。同一线程重入是无害的（所有 DB 访问都在单线程内完成，无跨线程移交），
# 故改为可重入锁，把"小心别嵌套"的隐式纪律变成结构性安全。
_db_lock = threading.RLock()


# ── PostgreSQL 适配层 ─────────────────────────────────────

def _pg_sql(sql: str) -> str:
    """SQLite 风格 '?' 占位符 → psycopg 的 '%s'，并把字面 '%' 翻倍。

    契约（tests/test_pgsql_adapter.py 钉死）：
    - 字符串字面量内的 '?' **不**替换（单引号开关状态机，'' 转义按 PG 语义处理）；
    - 所有 '%' 一律翻倍——psycopg 的 %-格式化作用于整条 SQL，字面量内也要翻；
    - 调用方**不得**预先转义 '%'（'%%' 会被再翻倍成 '%%%%'）。

    不支持的语法直接**响亮失败**而非静默错改（11 号复审 P1-1）：美元引号
    `$$...$$` 里的 '?' 会被错误替换而不报错。此前该风险只写在注释里，抓不到。
    """
    if "$$" in sql:
        raise ValueError("_pg_sql 不支持美元引号（$$...$$）：请改用参数占位符")
    out = []
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
        if ch == "?" and not in_str:
            out.append("%s")
        elif ch == "%":
            # psycopg 把裸 % 当占位符前缀（only '%s','%b','%t' are allowed），
            # LIKE 模式里的 'private/%' 会让整条 SQL 炸成 ProgrammingError——
            # 字面 % 一律翻倍（R1-P2-10 照片下推 SQL 生产 500 的根因）
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    def __iter__(self):
        return iter(self._cur.fetchall())


class _PgConn:
    """psycopg 连接包装：autocommit（commit/rollback 为兼容 no-op）。"""

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        self._conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(_pg_sql(sql), tuple(params))
        return _PgCursor(cur)

    def executemany(self, sql, seq):
        with self._conn.cursor() as cur:
            cur.executemany(_pg_sql(sql), [tuple(p) for p in seq])

    def commit(self):
        pass  # autocommit

    def rollback(self):
        pass

    @contextmanager
    def transaction(self):
        """真事务块（11 号复审 P2-6），**支持嵌套**。

        本项目默认 autocommit=True，多语句操作靠"补偿写法"（先做、失败再撤），
        中途进程崩溃会留下孤儿数据。关键多语句序列改用本上下文后可获得原子性：
        块内抛异常 = 整体回滚。

        嵌套语义：psycopg 的 `conn.transaction()` 在已处于事务内时会自动改用
        **savepoint**——内层回滚不影响外层。因此 autocommit 的切换**只在最外层**
        做（按连接当前状态判断，不依赖计数器）：内层若再切 autocommit，psycopg
        会抛 "can't change 'autocommit' now"（PG 全量实测踩到）。
        """
        prev = self._conn.autocommit
        if prev:
            self._conn.autocommit = False
        try:
            with self._conn.transaction():
                yield self
        finally:
            if prev:
                self._conn.autocommit = prev

    def executescript(self, script: str):
        for stmt in script.split(";"):
            if stmt.strip():
                self.execute(stmt)

    def close(self):
        self._conn.close()


@contextmanager
def transaction():
    """多语句原子块（引擎中立）。

    - PG：委托 `_PgConn.transaction()`，真回滚；
    - SQLite：调用方须已持 `_db_lock`（项目既有写约定），此处只负责收束
      commit / rollback。

    用法：`with transaction(): ...` —— 与 `with _db_lock:` 配合（嵌套安全，
    `_db_lock` 已是 RLock）。

    嵌套感知：事务内的写入方（含 store 仓储的 `_write`）应查 `in_transaction()`
    决定是否自行 commit——**只有最外层收束**，否则内层 commit 会把事务切断，
    原子性丢失。见 `app/store/users.py::_write`。
    """
    conn = _db()
    _txn_depth.depth = getattr(_txn_depth, "depth", 0) + 1
    try:
        if USE_PG:
            with conn.transaction():
                yield conn
            return
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        _txn_depth.depth -= 1


def in_transaction() -> bool:
    """当前线程是否正处在 `transaction()` 块内（决定写入方是否自行 commit）。"""
    return getattr(_txn_depth, "depth", 0) > 0



# ── 连接与初始化 ──────────────────────────────────────────

def is_unique_violation(e: Exception) -> bool:
    """引擎中立地识别唯一约束冲突（sqlite3.IntegrityError / psycopg UniqueViolation）。
    并发竞态兜底场景只允许吞这一种异常，其它 DB 故障必须上抛。"""
    if isinstance(e, sqlite3.IntegrityError):
        return True
    return type(e).__name__ == "UniqueViolation"


# ── PG 连接登记与有界化（11 号复审批次 3，P2-1）──────────────
# 原 `_db()` 每线程一条连接、**从不主动关闭**：线程池上限 40 时最坏常驻 40 条
# PG 后端连接（每条在 PG 侧占内存），且 PG 默认 max_connections=100，再叠加
# pg_dump / 迁移脚本就可能撞顶。这里做**有界化**（不改任何调用方）：
#   · 全局登记活跃连接，超过上限时回收最久未用的空闲连接；
#   · 惰性清扫：顺带关掉"线程已死但连接还挂着"的孤儿连接；
#   · 进程退出时统一关闭（atexit）。
# 连接的生命周期语义不变（仍是每线程一条、断线自愈）。
_PG_MAX_CONN = int(os.environ.get("SHARE_PG_MAX_CONN", "16"))
_PG_IDLE_TTL = 300.0          # 秒：超过则视为空闲可回收
_pg_registry: list = []       # [(conn, thread_id, last_used_monotonic)]
_pg_registry_mu = threading.Lock()


def _reap_pg_conns(force_close_oldest: bool = False) -> None:
    """关闭空闲超时 / 线程已死的 PG 连接；force 时再关一条最久未用的。"""
    now = time.monotonic()
    with _pg_registry_mu:
        keep = []
        for conn, tid, used in _pg_registry:
            dead_thread = not _thread_alive(tid)
            if (now - used > _PG_IDLE_TTL) or dead_thread:
                try:
                    conn.close()
                except Exception:
                    pass
                if getattr(_db_local, "conn", None) is conn:
                    _db_local.conn = None
                continue
            keep.append((conn, tid, used))
        if force_close_oldest and len(keep) >= _PG_MAX_CONN:
            keep.sort(key=lambda x: x[2])
            conn, _, _ = keep.pop(0)
            try:
                conn.close()
            except Exception:
                pass
            if getattr(_db_local, "conn", None) is conn:
                _db_local.conn = None
        _pg_registry[:] = keep


def _thread_alive(tid: int) -> bool:
    for t in threading.enumerate():
        if t.ident == tid:
            return True
    return False


def close_all_pg_connections() -> None:
    """进程退出 / 测试收尾时统一关闭全部 PG 连接。"""
    with _pg_registry_mu:
        for conn, _, _ in _pg_registry:
            try:
                conn.close()
            except Exception:
                pass
        _pg_registry.clear()


atexit.register(close_all_pg_connections)


def _db():
    """每线程独立连接。PG 断线自愈：连接池里的缓存连接在 PG 重启/网络闪断后
    会变死连接，超过 30s 未用时先廉价 ping 一次，失败则重建，避免全站持续 500。"""
    conn = getattr(_db_local, "conn", None)
    if conn is not None and USE_PG:
        now = time.monotonic()
        if now - getattr(_db_local, "last_ok", 0.0) > 30:
            try:
                conn.execute("SELECT 1")
                _db_local.last_ok = now
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                _db_local.conn = None
    if conn is None:
        if USE_PG:
            _reap_pg_conns()
            conn = _PgConn(DATABASE_URL)
        else:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
        _db_local.conn = conn
        if USE_PG:
            _db_local.last_ok = time.monotonic()
            with _pg_registry_mu:
                _pg_registry.append((conn, threading.get_ident(), time.monotonic()))
    elif USE_PG:
        now = time.monotonic()
        with _pg_registry_mu:
            for i, (c, tid, _) in enumerate(_pg_registry):
                if c is conn:
                    _pg_registry[i] = (c, tid, now)
                    break
    return conn




# ── schema 与迁移（B1 存储层拆分，实现在 app/store/schema.py）──
# re-export 保兼容：既有调用方（app/main.py、scripts/migrate_sqlite_to_pg.py）
# 仍从 app.db import init_db / pg_setval_sequences。新增代码建议直接
# from app.store import schema。
from app.store.schema import (  # noqa: E402,F401
    _PG_SCHEMA, _PG_SEQUENCES, init_db, pg_setval_sequences,
)
