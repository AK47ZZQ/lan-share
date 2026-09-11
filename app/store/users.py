"""users 表的**唯一主人**（11 号独立复审批次 2，A1）。

## 为什么

此前 `users` 表被 **11 个模块**直接读写、**80 条 SQL** 散落在 api/services/security/db：
表结构知识在 11 处各自编码，"加一列"要改 11 个地方，且每次都得人肉确认改全了没有。
这是典型的**隐式耦合**——它不出现在 import 图里，所以历次重构（消循环、拆上帝模块）
都没碰到它。08 号审查给的判据是：**"这条规则在仓库里存在几份？"** 1 份 = 低耦。

## 边界

- 本模块是该表**唯一**的 SQL 出口：业务模块只调用语义方法，不认识列名。
- **schema 定义在 `app/store/schema.py`**（建库与增量迁移是它的职责）——加一列 = 改
  `schema.py`（DDL）+ 本模块（语义方法），业务模块零改动。
- 读方法返回数据库**原始行**（PG 是 dict、SQLite 是 Row），调用方仍按 `r["col"]`
  取值——因此迁移是**行为等价**的，不改变任何现有取值写法。
- 写方法自持 `_db_lock` + commit，与项目既有写约定一致。需要跨表复合事务的调用方
  （如邀请码 redeem）仍自行持锁，并在锁内调用本模块的写方法。

## 命名约定

`get_*` 单行；`list_*` 集合；`set_*` 单列更新；`create/delete` 增删。
方法名说"做什么"，不说"查哪个表"。
"""
import time

from app import db as _dbmod
from app.db import _db, _db_lock

# ─── 读 ───────────────────────────────────────────────────

def get(uid: int):
    """按主键取全行（不存在返回 None）。"""
    return _db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def get_by_username(username: str):
    """按用户名取全行（精确匹配，大小写按库的排序规则）。"""
    return _db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def get_active_by_username(username: str):
    """按用户名取**在用**用户全行（preview_user / 登录前置等语义）。"""
    return _db().execute(
        "SELECT * FROM users WHERE username=? AND is_active=1", (username,)).fetchone()


def get_active_by_username_ci(username: str):
    """大小写不敏感地按用户名取在用用户（分享定向收件人解析）。"""
    return _db().execute(
        "SELECT id, username FROM users WHERE LOWER(username)=LOWER(?) AND is_active=1",
        (username,)).fetchone()


def get_username(uid: int):
    row = _db().execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    return row["username"] if row else None


def get_avatar_path_by_username(username: str):
    return _db().execute(
        "SELECT id, avatar_path FROM users WHERE username=?", (username,)).fetchone()


def get_profile(uid: int):
    """个人资料投影：nickname / avatar_path。"""
    return _db().execute(
        "SELECT nickname, avatar_path FROM users WHERE id=?", (uid,)).fetchone()


def get_password_hash(uid: int):
    row = _db().execute(
        "SELECT password_hash FROM users WHERE id=?", (uid,)).fetchone()
    return row["password_hash"] if row else None


def get_webdav_password_hash(uid: int):
    row = _db().execute(
        "SELECT webdav_password_hash FROM users WHERE id=?", (uid,)).fetchone()
    return row["webdav_password_hash"] if row else None


def get_totp_state(uid: int):
    """两步验证状态投影（secret/enabled/恢复码/已用步）。"""
    return _db().execute(
        "SELECT totp_secret, totp_enabled, totp_rc, totp_last_step FROM users WHERE id=?",
        (uid,)).fetchone()


def get_allowed_field(uid: int, col: str):
    """按**白名单列**取单字段（替代调用方拼 SQL 的动态列）。

    白名单由调用方语义限定：目前仅用于"会话行不带这些列、需要按需回查"的场景。
    """
    allowed = _ALLOWED_FIELDS
    if col not in allowed:
        raise ValueError(f"column not allowed: {col}")
    row = _db().execute(f"SELECT {col} FROM users WHERE id=?", (uid,)).fetchone()
    return row[col] if row else None


_ALLOWED_FIELDS = frozenset({"password_hash", "nickname", "avatar_path"})


def count() -> int:
    return _db().execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def exists_username_ci(username: str) -> bool:
    """用户名是否已被占用（大小写不敏感）。"""
    return _db().execute(
        "SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)", (username,)).fetchone() is not None


def id_by_username(username: str):
    row = _db().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    return row["id"] if row else None


def list_for_admin() -> list:
    """管理面板用户列表：**不返回任何口令哈希**，webdav 只回布尔。

    （原实现把 scrypt 哈希拉进应用层再 bool()，等于多暴露一层口令材料。）
    """
    return _db().execute(
        "SELECT id, username, role, is_active, quota_bytes, created_at, "
        "(webdav_password_hash IS NOT NULL) AS webdav_set, "
        "totp_enabled, nickname, avatar_path FROM users ORDER BY id").fetchall()


def list_active_admin_ids() -> list[int]:
    """在用管理员 id（"最后一个管理员"守卫用）。"""
    return [r["id"] for r in _db().execute(
        "SELECT id FROM users WHERE role='admin' AND is_active=1").fetchall()]


def list_active_except(uid: int) -> list:
    """在用成员名单（定向分享收件人选择）。"""
    return _db().execute(
        "SELECT id, username, role FROM users WHERE is_active=1 AND id != ? ORDER BY username",
        (uid,)).fetchall()


def list_active_brief() -> list:
    """在用成员简表（广播通知 fan-out 用）。"""
    return _db().execute(
        "SELECT id, username, nickname FROM users WHERE is_active=1").fetchall()


def list_usernames() -> set[str]:
    """全部用户名集合（照片归属识别用）。"""
    return {r["username"] for r in _db().execute("SELECT username FROM users").fetchall()}


def list_nickname_rows() -> list:
    """有昵称的用户（昵称→用户名展示映射）。"""
    return _db().execute(
        "SELECT username, nickname FROM users WHERE nickname != ''").fetchall()


def id_to_username_map() -> dict:
    """{id: username} 全量映射（分享列表把 owner_id 还原成用户名）。"""
    return {r["id"]: r["username"]
            for r in _db().execute("SELECT id, username FROM users").fetchall()}


def principal_by_session(token_hash: str):
    """Cookie 会话 → 用户身份（含认证所需字段）。sessions JOIN users 的唯一出口。"""
    return _db().execute(
        "SELECT s.expires_at, s.user_id, u.username, u.role, u.is_active, u.quota_bytes, "
        "u.totp_secret, u.totp_enabled, u.totp_rc "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (token_hash,)).fetchone()


def principal_by_device(token_hash: str):
    """App 设备 Token → 用户身份。device_tokens JOIN users 的唯一出口。"""
    return _db().execute(
        "SELECT t.id AS device_id, t.user_id, t.expires_at, t.last_seen, "
        "u.username, u.role, u.is_active, u.quota_bytes, u.totp_enabled, u.totp_rc "
        "FROM device_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
        (token_hash,)).fetchone()


# ─── 写 ───────────────────────────────────────────────────

def create(username: str, password_hash: str, role: str, created_at: float | None = None) -> int:
    """建号，返回新 uid。唯一约束冲突由调用方按 `is_unique_violation` 处理。

    commit 规则与 `_write` 一致：不在 `db.transaction()` 内就**立即提交**；
    在事务内（如邀请码 redeem 的"建号+认领"原子块）则推迟给最外层收束。

    ⚠️ 这条规则是必须的：SQLite 下悬空未提交的 INSERT 会持有写锁，
    其它连接全部 `database is locked`（本轮实测踩到并修复）。
    """
    cur = _db().execute(
        "INSERT INTO users(username, password_hash, created_at, role) VALUES(?,?,?,?) RETURNING id",
        (username, password_hash, created_at or time.time(), role))
    row = cur.fetchone()
    uid = row["id"] if row else _db().execute(
        "SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    if not _dbmod.in_transaction():
        _db().commit()
    return uid


def delete(uid: int) -> None:
    """删号（grants/sessions 依 schema CASCADE；调用方按需先清 sessions）。"""
    with _db_lock:
        _db().execute("DELETE FROM users WHERE id=?", (uid,))
        _db().commit()


def set_role(uid: int, role: str) -> None:
    _write("UPDATE users SET role=? WHERE id=?", (role, uid))


def set_active(uid: int, active: bool) -> None:
    _write("UPDATE users SET is_active=? WHERE id=?", (1 if active else 0, uid))


def set_quota(uid: int, quota_bytes: int | None) -> None:
    _write("UPDATE users SET quota_bytes=? WHERE id=?", (quota_bytes, uid))


def set_username(uid: int, username: str) -> None:
    _write("UPDATE users SET username=? WHERE id=?", (username, uid))


def set_nickname(uid: int, nickname: str) -> None:
    _write("UPDATE users SET nickname=? WHERE id=?", (nickname, uid))


def set_avatar_path(uid: int, avatar_path: str) -> None:
    _write("UPDATE users SET avatar_path=? WHERE id=?", (avatar_path, uid))


def set_password_hash(uid: int, password_hash: str) -> None:
    """仅改密码（保留 TOTP 等其它状态）。"""
    _write("UPDATE users SET password_hash=? WHERE id=?", (password_hash, uid))


def set_webdav_password_hash(uid: int, password_hash: str | None) -> None:
    """设/清 WebDAV 独立密码（None = 清除）。"""
    _write("UPDATE users SET webdav_password_hash=? WHERE id=?", (password_hash, uid))


def set_totp_secret(uid: int, secret: str) -> None:
    """预置 TOTP 密钥（此时 totp_enabled 仍为 0，需第二步启用确认）。"""
    _write("UPDATE users SET totp_secret=?, totp_enabled=0, totp_last_step=NULL WHERE id=?",
           (secret, uid))


def enable_totp(uid: int, recovery_codes_json: str) -> None:
    """启用两步验证并写入恢复码集合。"""
    _write("UPDATE users SET totp_enabled=1, totp_rc=? WHERE id=?",
           (recovery_codes_json, uid))


def set_totp_recovery_codes(uid: int, recovery_codes_json: str) -> None:
    """重写恢复码集合（消费一枚后回写）。"""
    _write("UPDATE users SET totp_rc=? WHERE id=?", (recovery_codes_json, uid))


def set_totp_last_step(uid: int, step: int) -> None:
    """记录已用时间步（防同一 30s 窗口内的动态码重放）。"""
    _write("UPDATE users SET totp_last_step=? WHERE id=?", (step, uid))


def clear_totp(uid: int) -> None:
    """解除两步验证：密钥/开关/恢复码/已用步一并清空（密码不动）。"""
    _write("UPDATE users SET totp_secret=NULL, totp_enabled=0, totp_rc=NULL, "
           "totp_last_step=NULL WHERE id=?", (uid,))


def reset_password_full(uid: int, password_hash: str) -> None:
    """管理端重置密码：改密码 **并**清空 TOTP 全家族（凭据泄露的恢复动作）。

    与 `set_password_hash` 的区别：后者是用户自助改密，不动 TOTP。
    """
    _write("UPDATE users SET password_hash=?, totp_secret=NULL, totp_enabled=0, "
           "totp_rc=NULL, totp_last_step=NULL WHERE id=?", (password_hash, uid))


def promote_first_user_to_admin() -> None:
    """仅在库内已无 admin 时把最小 id 用户提为 admin（幂等；兼容 v2 单管理员存量）。

    注意：**不能**无条件执行——管理员被降级/删号后重启会"复活"成 admin。
    """
    _write("UPDATE users SET role='admin' WHERE id=(SELECT MIN(id) FROM users) "
           "AND NOT EXISTS(SELECT 1 FROM users WHERE role='admin')", ())


def _write(sql: str, params: tuple) -> None:
    """单语句写的统一出口（持锁 + commit），保证与项目写约定一致。

    处在 `db.transaction()` 块内时**不自行 commit**——由最外层收束，
    否则会切断事务、丢失原子性（11 号复审批次 3，P2-6）。
    """
    with _db_lock:
        _db().execute(sql, params)
        if not _dbmod.in_transaction():
            _db().commit()
