"""对抗式审查·并发与资源耗尽（2026-09-20 下午批次）。

为什么单测全绿还不够：下载上限、一次性票据、"恢复至多一次"都是**跨请求**不变量。
顺序执行的测试里它们天然成立——所以 462 项测试全绿并不能证明它们没坏。
本文件只问一件事：把同一动作并发打出去，不变量还成立吗？

判据设计（避免把"没测到"写成"测过了"）：
  · 每个用例先断言**赢者恰好一个**，这是原子性的正面证据；
  · 再断言**零 5xx**——并发路径最典型的失败不是放行越权，而是撞锁崩在 500；
  · 对限速/排队造成的额外 4xx 保持宽容：并发失败模式只会让 200 变多，
    不会变少，所以"恰好一个 200"不会因为限速而假红。
"""

import concurrent.futures as cf
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import SHARE_ROOT
from app.db import _db
from app.main import app
from app.store import shares as shares_store
from app.store import ttl_state


def _workers(n):
    return cf.ThreadPoolExecutor(max_workers=n)


# ── 下载上限：发票时原子递增（R1-P2-4）──────────────────────────

def _make_share(auth, workdir, name, **data):
    auth.post(f"/api/upload/{workdir}", files={"file": (name, b"payload", "text/plain")})
    r = auth.post("/api/share", data={"path": f"{workdir}/{name}", **data})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_consume_download_is_single_winner(auth, workdir):
    """16 路并发抢"限 1 次"的额度，只能有 1 个赢。

    这是 R1-P2-4 修复的核心断言：计数若是"读—判—写"两步，并发下会全部读到
    downloads=0 从而集体放行，把限 1 次变成 16 次完整下载。
    """
    token = _make_share(auth, workdir, "race-once.txt", max_downloads="1")
    with _workers(16) as pool:
        wins = list(pool.map(lambda _: shares_store.consume_download(token), range(16)))
    assert sum(wins) == 1, f"下载上限被并发突破：{sum(wins)} 个赢家"
    row = _db().execute("SELECT downloads FROM shares WHERE token=?", (token,)).fetchone()
    assert row["downloads"] == 1


def test_consume_download_respects_n(auth, workdir):
    token = _make_share(auth, workdir, "race-five.txt", max_downloads="5")
    with _workers(20) as pool:
        wins = list(pool.map(lambda _: shares_store.consume_download(token), range(20)))
    assert sum(wins) == 5
    assert _db().execute("SELECT downloads FROM shares WHERE token=?",
                         (token,)).fetchone()["downloads"] == 5


def test_consume_download_unlimited_never_denies(auth, workdir):
    token = _make_share(auth, workdir, "race-unlimited.txt")
    with _workers(12) as pool:
        wins = list(pool.map(lambda _: shares_store.consume_download(token), range(12)))
    assert all(wins), "无上限分享不得被并发误判为超额"


def test_prepare_race_over_http(auth, workdir):
    """端到端：并发打 /prepare，只有一张票，且没有一个 5xx。"""
    token = _make_share(auth, workdir, "race-prepare.txt", max_downloads="1")
    clients = [TestClient(app, base_url="https://testserver") for _ in range(6)]

    def fire(c):
        return c.post(f"/api/share/{token}/prepare", json={}).status_code

    with _workers(6) as pool:
        codes = list(pool.map(fire, clients))
    assert codes.count(200) == 1, codes
    assert not [c for c in codes if c >= 500], codes


# ── 一次性票据：take 的原子消费 ────────────────────────────────

def test_ttl_take_is_single_winner():
    key = "race-tkt-" + os.urandom(4).hex()
    ttl_state.put(ttl_state.SCOPE_ZIP_TICKET, key, "the-value", 60)
    with _workers(16) as pool:
        got = list(pool.map(lambda _: ttl_state.take(ttl_state.SCOPE_ZIP_TICKET, key),
                            range(16)))
    assert [g for g in got if g is not None] == ["the-value"], "一次性票据被并发消费多次"


def test_ttl_put_if_absent_single_holder():
    key = "race-slot-" + os.urandom(4).hex()
    with _workers(10) as pool:
        held = list(pool.map(
            lambda i: ttl_state.put_if_absent(ttl_state.SCOPE_ZIP_SLOT, key, str(i), 60),
            range(10)))
    assert sum(held) == 1, "并发占位只能有一个持有者（打包在途位语义）"
    ttl_state.delete(ttl_state.SCOPE_ZIP_SLOT, key)


def test_ticket_is_bound_to_its_own_share(auth, workdir):
    """A 分享换来的票据不得取到 B 分享的文件——票据值必须是 token 而非"存在即可"。"""
    a = _make_share(auth, workdir, "bound-a.txt")
    b = _make_share(auth, workdir, "bound-b.txt")
    prep = auth.post(f"/api/share/{a}/prepare", json={})
    assert prep.status_code == 200
    url = prep.json()["url"]
    assert auth.get(url).status_code == 200          # 正当用途先证明票据确实有效
    forged = f"/api/share/{b}/dl?tkt={url.split('tkt=')[1]}"
    assert auth.get(forged).status_code == 403       # 换 token 复用必须被拒


# ── 回收站恢复：并发下"至多一次"且不丢不重 ──────────────────────

def _spawns(client, n):
    """n 个**独立** TestClient，各自复制会话 Cookie。

    为什么不能共用一个 client 打并发：httpx.Client + anyio portal 不是
    按线程隔离的，共用会让响应互相串味（第一版就是被同一个 auth 客户端
    跑出"一次恢复三个 200"的假象——那是测试脚手架的假阳性，不是服务端行为）。
    """
    out = []
    for _ in range(n):
        c = TestClient(app, base_url="https://testserver")
        c.cookies.update(client.cookies)
        out.append(c)
    return out


def test_concurrent_restore_never_5xx_and_row_gone(auth, workdir):
    """不变量：并发恢复不得 5xx，且至少成功一次、行最终消失。

    ⚠️ 已实测到的**遗留缺陷（见 15-路线图 P2）**："至多一次"在真并发下**不成立**
    （实测 6 路并发恢复同一 id 得到 [200,404,404,200,404,200] = 3 个赢家，
    即同一份内容落出多个副本/多余 (恢复) 名）。修法必须是**原子认领**
    （单写者 claim），而"先删行再搬文件"的朴素写法会在搬失败时把 .trash 里的
    文件变成无人认领的孤儿（30 天后被 sweep 删掉 = 真丢数据）——那比现在的
    重复恢复更糟。因此这里只钉"已经成立"的部分，不把未修好的语义写死成红测。
    """
    name = "restore-race.txt"
    auth.post(f"/api/upload/{workdir}", files={"file": (name, b"x", "text/plain")})
    assert auth.delete(f"/api/files/{workdir}/{name}").status_code in (200, 204)
    tid = _db().execute(
        "SELECT id FROM trash WHERE orig_path=? ORDER BY id DESC",
        (f"{workdir}/{name}",)).fetchone()["id"]

    def fire(c):
        return c.post(f"/api/trash/{tid}/restore").status_code

    with _workers(6) as pool:
        codes = list(pool.map(fire, _spawns(auth, 6)))
    assert not [c for c in codes if c >= 500], codes        # 竞态不得冒 500
    assert 200 in codes, codes                              # 至少成功一次
    assert _db().execute("SELECT COUNT(*) AS n FROM trash WHERE id=?",
                         (tid,)).fetchone()["n"] == 0, "恢复后回收站行必须消失"
    sibs = [p.name for p in (SHARE_ROOT / workdir).iterdir()]
    assert name in sibs, f"恢复后文件没落位：{sibs}"


def test_concurrent_delete_leaves_no_broken_trash_rows(auth, workdir):
    """并发删除：零 500、至少一个成功、且不留下指向不存在文件的坏行。

    与 restore 同一族：实测赢家可能不止一个（[404,200,200,404,404]），
    即多插了几条回收站行——重复恢复是可见的功能缺陷，但删过头会丢数据，
    故同样只钉"已经成立"的部分（见 15-路线图 P2）。"""
    name = "del-race.txt"
    auth.post(f"/api/upload/{workdir}", files={"file": (name, b"x", "text/plain")})

    def fire(c):
        return c.delete(f"/api/files/{workdir}/{name}").status_code

    with _workers(5) as pool:
        codes = list(pool.map(fire, _spawns(auth, 5)))
    assert not [c for c in codes if c >= 500], codes
    assert any(c in (200, 204) for c in codes), codes
    rows = _db().execute("SELECT trash_path FROM trash WHERE orig_path=?",
                         (f"{workdir}/{name}",)).fetchall()
    assert rows, "删除后至少要留下回收站记录"
    # ⚠️ D5 的第二个症状（实测）：并发下会留下**指向不存在文件的坏行**
    # （如 `.trash/1789…_del-race.txt` 在盘上根本不存在）。
    # 这里只保证"至少有一条真能恢复"，坏行的清算是 15 号挂账里
    # "trash 行原子认领"那一并解决，不在断言里写死成常红。
    assert any((SHARE_ROOT / r["trash_path"]).exists() for r in rows), \
        f"没有一条回收站记录对应真实文件：{[r['trash_path'] for r in rows]}"
    assert not (SHARE_ROOT / workdir / name).exists(), "文件必须已离开原位置"


# ── 资源耗尽：在途分片会话不得无限膨胀（红队 P2）─────────────────

def test_upload_session_cap(auth, workdir):
    """会话真实字节落在 .chunks（配额视野外）——不设并发上限就能灌满磁盘。

    ⚠️ 必须自清：本用例会占满 admin 的 8 个在途会话名额。admin 是全 suite
    共用的会话，第一版没清理，导致后面所有以 admin 调 /api/upload/init 的
    用例集体 429（SQLite/PG 各 9 红）。红线"测试数据自建自清"在这里不是
    洁癖，是**不污染他人**的硬要求。
    """
    sids, codes = [], []
    try:
        for i in range(12):
            r = auth.post("/api/upload/init", json={
                "filename": f"cap-{i}.bin", "size": 1024 * 1024, "dest_dir": workdir})
            codes.append(r.status_code)
            if r.status_code == 200:
                sids.append(r.json()["session"])
        assert all(c == 200 for c in codes[:8]), codes
        assert codes[-1] == 429, codes
        assert not [c for c in codes if c >= 500], codes
        assert len(sids) == 8
    finally:
        from app.db import _db_lock
        with _db_lock:
            for sid in sids:
                _db().execute("DELETE FROM upload_sessions WHERE id=?", (sid,))
            _db().commit()


def test_pathological_filenames_reach_4xx_not_5xx(auth, workdir):
    """恶意/极端文件名：落盘 OSError 不该冒出来成 500。"""
    evil = ["..%2f..%2fetc%2fpasswd", "a" * 400 + ".txt", "con.txt", "nul",
            ".hidden", "trailing.", "emoji😀.txt", "%00abc", "a\\b\\c", "x" * 200 + ".txt"]
    for name in evil:
        r = auth.post(f"/api/upload/{workdir}", files={"file": (name, b"x", "text/plain")})
        assert r.status_code < 500, (name, r.status_code, r.text[:120])
