"""`ttl_state`：跨重启的短时效票据（v3.57 状态外置）行为与原子性测试。

这些用例的共同前提：**状态在库里，不在进程里**。因此每条都在 conftest 选中的
引擎上执行（设 LANSHARE_TEST_PG_URL 即真 PG），并且特意用"另一个调用点重新
读取"的方式验证，而不是信任调用方的内存缓存。

原子性用真线程验证（03 清单纪律：并发必须真并发，顺序测试等于零覆盖）。
"""
import os
import threading
import time

import pytest

from app.db import _db
from app.store import ttl_state as ts


def _tag() -> str:
    return "ttl-" + os.urandom(4).hex()


@pytest.fixture(autouse=True)
def _clean_scope():
    yield
    ts.clear_all()


# ─── 基本语义 ─────────────────────────────────────────────


def test_put_get_take_roundtrip():
    k = _tag()
    ts.put("t1", k, "v1", 60)
    assert ts.get("t1", k) == "v1"
    assert ts.get("t1", k) == "v1", "get 不得消费"
    assert ts.take("t1", k) == "v1"
    assert ts.take("t1", k) is None, "take 必须一次性"
    assert ts.get("t1", k) is None


def test_put_upserts_and_renews():
    k = _tag()
    ts.put("t2", k, "a", 60)
    ts.put("t2", k, "b", 60)
    assert ts.get("t2", k) == "b"
    assert ts.count("t2") == 1, "同 key 应覆盖而非追加"


def test_expired_rows_invisible_to_get_and_take():
    k = _tag()
    past = time.time() - 120
    ts.put("t3", k, "v", 1, now=past)          # expires_at = past+1，早已过期
    assert ts.get("t3", k) is None
    assert ts.take("t3", k) is None, "过期条目不可能被消费"
    assert ts.get("t3", k) is None, "get 命中过期行应就地删除"
    assert ts.count("t3") == 0


def test_scopes_are_isolated():
    k = "same-key"
    ts.put("sa", k, "1", 60)
    ts.put("sb", k, "2", 60)
    assert ts.get("sa", k) == "1" and ts.get("sb", k) == "2"
    ts.take("sa", k)
    assert ts.get("sb", k) == "2", "跨 scope 不得互相影响"


def test_delete_scope_value_targets_by_value():
    """配对码"同一账号只保留最新一枚"依赖的按值清除。"""
    uid = _tag()
    ts.put("pc", "111111", uid, 60)
    ts.put("pc", "222222", uid, 60)
    assert ts.count("pc") == 2
    ts.delete_scope_value("pc", uid)
    assert ts.count("pc") == 0


def test_sweep_expired_only_removes_expired():
    old, fresh = _tag(), _tag()
    ts.put("sw", old, "v", 1, now=time.time() - 100)
    ts.put("sw", fresh, "v", 600)
    assert ts.sweep_expired() >= 1
    assert ts.get("sw", old) is None
    assert ts.get("sw", fresh) == "v"


def test_get_all_returns_live_pairs_sorted():
    k = _tag()
    ts.put("ga", k, "v", 60)
    rows = ts.get_all("ga")
    assert (k, "v") in rows
    assert [a for a, _ in rows] == sorted(a for a, _ in rows), "读函数契约：稳定序"


# ─── 原子性：真并发 ───────────────────────────────────────


def test_take_is_atomic_across_threads():
    """N 个线程同时 take 同一 key：恰好一个成功（"一码一用"的实现前提）。

    ⚠️ 这条**不足以**证伪"先 get 再 delete"的两步实现：竞态窗口受 GIL 与
    `_db_lock` 串行化影响，命中是**概率性**的——同一份两步实现，第一次消融
    8 线程全绿、第二次才红。原子性由下面
    `test_take_is_a_single_statement` 用结构断言**确定性地**钉住。
    本用例保留作并发冒烟（它顺带验证"多线程不会同时拿到值"这个结果态）。
    """
    k = _tag()
    ts.put("race", k, "prize", 60)
    won: list[str] = []
    barrier = threading.Barrier(8)

    def grab():
        barrier.wait()
        got = ts.take("race", k)
        if got is not None:
            won.append(got)

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(won) == 1, f"一码一用被破坏：{len(won)} 个线程拿到值"


def test_take_is_a_single_statement(monkeypatch):
    """`take()` 必须是**一条**带 `RETURNING` 的 DELETE——消融验证过的断言。

    把实现退化成 `get()` + `delete()` 两步，本用例变红（并发线程用例却不会，
    见上一条的说明）。原子消费是配对码/下载票据的安全前提，值得用结构断言
    钉死：一条语句 = 不可能有两个赢家。
    """
    real_db = ts._db
    seen: list[str] = []

    class Rec:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, params=()):
            if "ttl_state" in sql:
                seen.append(" ".join(sql.split()).upper())
            return self._c.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(ts, "_db", lambda: Rec(real_db()))
    k = _tag()
    ts.put("atom", k, "v", 60)
    seen.clear()
    assert ts.take("atom", k) == "v"
    dml = [s for s in seen if s.startswith(("DELETE", "INSERT", "UPDATE"))]
    assert len(dml) == 1, f"take 发出了 {len(dml)} 条写语句（应为 1 条原子消费）: {dml}"
    stmt = dml[0]
    assert "RETURNING" in stmt and "EXPIRES_AT>" in stmt.replace(" ", ""), \
        f"消费语句缺少 RETURNING 或过期条件：{stmt}"


def test_put_if_absent_is_exclusive_and_self_healing():
    k = _tag()
    assert ts.put_if_absent("slot", k, "1", 60) is True, "先占者应拿到"
    assert ts.put_if_absent("slot", k, "1", 60) is False, "在途时后来者必须失败"
    assert ts.delete("slot", k) == 1
    assert ts.put_if_absent("slot", k, "1", 60) is True, "释放后可再占"


def test_expired_slot_can_be_taken_over():
    """崩溃/漏 release 的自愈路径：过期占位不阻塞后来者。

    这条是外置相对进程内 set 的**净增益**——旧实现里漏一次 release_slot
    该用户就被锁到服务重启为止。
    """
    k = _tag()
    ts.put("slot2", k, "1", 1, now=time.time() - 50)   # 已过期残留
    assert ts.get("slot2", k) is None
    assert ts.put_if_absent("slot2", k, "1", 60) is True, \
        "过期残留行把占位锁死了（TTL 自愈失效）"


def test_concurrent_put_if_absent_one_winner():
    k = _tag()
    got: list[bool] = []
    barrier = threading.Barrier(6)

    def grab():
        barrier.wait()
        got.append(ts.put_if_absent("slot3", k, "1", 60))

    threads = [threading.Thread(target=grab) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sum(got) == 1, f"占位竞态失守：{got}"


# ─── 持久性：状态真的在库里 ───────────────────────────────


def test_state_lives_in_db_not_module_globals():
    """外置的判据：绕过 store 模块、用裸 SQL 也能读到同一条状态。

    旧实现（进程内 dict）在这里必然读不到——那正是"重启即全丢"的形态。
    """
    k = _tag()
    ts.put("persist", k, "value-in-db", 600)
    row = _db().execute(
        "SELECT tval FROM ttl_state WHERE scope=? AND tkey=?",
        ("persist", k)).fetchone()
    assert row is not None and row["tval"] == "value-in-db"


def test_pair_scope_and_zip_scope_use_distinct_scopes():
    """scope 常量别写串：写错 scope 的两条链路会互不可见且**不会报错**。"""
    assert ts.SCOPE_PAIR_CODE == "pair_code"
    assert ts.SCOPE_ZIP_TICKET == "zip_ticket"
    assert ts.SCOPE_ZIP_SLOT == "zip_slot"
    assert ts.SCOPE_SHARE_TICKET == "share_ticket"
    assert ts.SCOPE_SHARE_AUDIT == "share_audit"
    assert len({ts.SCOPE_PAIR_CODE, ts.SCOPE_ZIP_TICKET, ts.SCOPE_ZIP_SLOT,
                ts.SCOPE_SHARE_TICKET, ts.SCOPE_SHARE_AUDIT}) == 5
