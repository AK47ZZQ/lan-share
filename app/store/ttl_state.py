"""`ttl_state` 表的唯一主人：跨重启存活、可跨进程共享的短时效票据（v3.57）。

## 为什么是 PG 而不是 Redis

v3.56 复查把"一切状态在进程内"列为风险：限速桶、登录锁、配对码、zip/分享票据、
SSE 订阅、授权缓存全部随进程走，重启即全丢，且事实上焊死单 worker。
但逐项看下来，**只有"代表用户正在做的一件具体事"的票据**值得外置：

| 状态 | 重启丢失的用户可见后果 | 处置 |
|---|---|---|
| 配对码 | 另一台设备正在扫码/输码 → 直接失败，得重新登录生成 | **落库** |
| zip 打包票据 + 在途位 | 几十分钟的大 zip 下到一半 404，重打包 | **落库** |
| 分享取件票据 + 审计位 | 已验证提取码拿到的下载链接失效；Range 分段审计重复计数 | **落库** |
| 限速桶 / 登录失败计数 | 少一分钟记账（重启罕见，无用户可见损失） | 留在进程内 |
| grants / netloc / apk sha / 目录扫描 | **它们是缓存**，重建即可，落库只会拖慢热路径 | 留在进程内 |
| SSE 订阅 | 连接本身即进程态，无法外置；客户端有 60s 轮询兜底 | 留在进程内 |

为这三类 KV 引入 Redis = 给生产机加一个 NSSM 服务 + 一条依赖 + 一份凭据 +
一个故障域，收益为零——PostgreSQL 已经在跑，且这些量级（几百行、TTL 分钟级）
对它毫无压力。**外置的目标是"抗重启/可共享"，不是"换存储"。**

## 语义边界

- `take()` 是**原子消费**：`DELETE ... RETURNING`，并发下只有一个调用者拿到值
  （比原先的"进程锁内 pop"更强：多进程/多 worker 同样成立，且修掉了配对码
   在 claim 与过期判定之间的竞态）。
- `get()` **不消费**，过期即删并返回 None（分享票据要在 TTL 内被多个 Range
  分段请求复用）。
- 写入一律 upsert：同 key 覆盖并续期。
- 本表只有 TTL 语义，不做持久业务记录；过期行由 cleanup sweep 收口。
"""
import time

from app import db as _dbmod
from app.db import _db, _db_lock

# scope 命名空间（同一张表按 scope 隔离，避免为三个同构需求开三张表）
SCOPE_PAIR_CODE = "pair_code"       # key=6 位码，value=user_id
SCOPE_ZIP_TICKET = "zip_ticket"     # key=票据 token，value=zip 相对路径
SCOPE_ZIP_SLOT = "zip_slot"         # key=username，value="1"（在途打包占位）
SCOPE_SHARE_TICKET = "share_ticket"  # key=票据，value=分享 token
SCOPE_SHARE_AUDIT = "share_audit"   # key=票据，一次性审计位


def put(scope: str, key: str, value: str, ttl_seconds: float,
        now: float | None = None) -> None:
    """写入/覆盖一条短时效状态并续期。"""
    ts = time.time() if now is None else now
    _write("INSERT INTO ttl_state(scope, tkey, tval, expires_at) VALUES(?,?,?,?) "
           "ON CONFLICT(scope, tkey) DO UPDATE SET tval=excluded.tval, "
           "expires_at=excluded.expires_at",
           (scope, key, value, ts + ttl_seconds))


def get(scope: str, key: str, now: float | None = None) -> str | None:
    """读取（不消费）。过期行就地删除并返回 None——不留"读到过期值"的窗口。"""
    ts = time.time() if now is None else now
    with _db_lock:
        row = _db().execute(
            "SELECT tval, expires_at FROM ttl_state WHERE scope=? AND tkey=?",
            (scope, key)).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= ts:
            _db().execute("DELETE FROM ttl_state WHERE scope=? AND tkey=?",
                          (scope, key))
            if not _dbmod.in_transaction():
                _db().commit()
            return None
        return row["tval"]


def take(scope: str, key: str, now: float | None = None) -> str | None:
    """**原子消费**：拿到即删。并发/多进程下只有一个调用者能拿到值。

    未命中与"已过期"都返回 None（条件里带 expires_at>now，过期行不会被人
    误消费，也不需要额外一次 DELETE 回合）。
    """
    ts = time.time() if now is None else now
    with _db_lock:
        row = _db().execute(
            "DELETE FROM ttl_state WHERE scope=? AND tkey=? AND expires_at>?"
            " RETURNING tval", (scope, key, ts)).fetchone()
        if not _dbmod.in_transaction():
            _db().commit()
    return row["tval"] if row else None


def put_if_absent(scope: str, key: str, value: str, ttl_seconds: float,
                  now: float | None = None) -> bool:
    """仅当 key 不存在（或已过期）时写入，返回是否占到——占位/抢锁的原子形态。

    两条**各自独立提交**的语句：先清已过期同 key 行，再 `INSERT ... ON
    CONFLICT DO NOTHING` 看 rowcount。不包 `transaction()`——① 独占性已由
    ON CONFLICT 单条语句保证，事务对正确性无增益；② 项目的 SQLite
    `transaction()` 要求调用方先持 `_db_lock`（db.py:145），而"跨两条语句的
    写事务"会让本已提交很快的票据写入变成"长时间持有未提交写事务"，
    SQLite 下直接制造 `database is locked`（实测踩过，见 03 清单）。
    """
    ts = time.time() if now is None else now
    _write("DELETE FROM ttl_state WHERE scope=? AND tkey=? AND expires_at<=?",
           (scope, key, ts))
    return _write("INSERT INTO ttl_state(scope, tkey, tval, expires_at) "
                  "VALUES(?,?,?,?) ON CONFLICT(scope, tkey) DO NOTHING",
                  (scope, key, value, ts + ttl_seconds)) == 1


def delete(scope: str, key: str) -> int:
    """删一条（占位释放用）。返回删除行数。"""
    return _write("DELETE FROM ttl_state WHERE scope=? AND tkey=?", (scope, key))


def clear_scope(scope: str) -> int:
    """清空一个 scope（测试隔离钩子）。"""
    return _write("DELETE FROM ttl_state WHERE scope=?", (scope,))


def delete_scope_value(scope: str, value: str) -> int:
    """按 value 清同域旧条目（配对码"同一账号只保留最新一枚"语义）。"""
    return _write("DELETE FROM ttl_state WHERE scope=? AND tval=?", (scope, value))


def get_all(scope: str, now: float | None = None) -> list[tuple[str, str]]:
    """某 scope 的全部有效 (key, value)（测试与运维观测用）。"""
    ts = time.time() if now is None else now
    return [(r["tkey"], r["tval"]) for r in _db().execute(
        "SELECT tkey, tval FROM ttl_state WHERE scope=? AND expires_at>? "
        "ORDER BY tkey", (scope, ts)).fetchall()]


def count(scope: str | None = None) -> int:
    if scope is None:
        return _db().execute(
            "SELECT COUNT(*) AS c FROM ttl_state").fetchone()["c"]
    return _db().execute(
        "SELECT COUNT(*) AS c FROM ttl_state WHERE scope=?", (scope,)).fetchone()["c"]


def sweep_expired(now: float | None = None) -> int:
    """过期行回收（cleanup sweep 注册项，每小时随主循环）。"""
    ts = time.time() if now is None else now
    return _write("DELETE FROM ttl_state WHERE expires_at<=?", (ts,))


def clear_all() -> int:
    """测试钩子：整表清空（取代原先各模块的进程内 reset_*）。"""
    return _write("DELETE FROM ttl_state", ())


def _write(sql: str, params: tuple) -> int:
    with _db_lock:
        cur = _db().execute(sql, params)
        if not _dbmod.in_transaction():
            _db().commit()
        return cur.rowcount
