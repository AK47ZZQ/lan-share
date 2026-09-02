"""认证与安全：审计、scrypt 密码、会话、防爆破、限速、client_ip、公网入口判定。"""
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from contextvars import ContextVar
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from app.config import (AUDIT_KEEP, COOKIE_NAME, DEVICE_TOKEN_TTL, LOGIN_LOCK_SECONDS,
                        LOGIN_MAX_FAILS, SESSION_RENEW_AFTER, SESSION_TTL)
from app.db import _db, _db_lock

# 当前请求的登录用户名：中间件注入，audit() 自动带上（公开入口为空串）
_audit_user: ContextVar[str] = ContextVar("audit_user", default="")


def set_audit_user(username: str):
    _audit_user.set(username or "")


def audit(action: str, target: str = "", detail: str = "", ip: str = ""):
    with _db_lock:
        _db().execute(
            "INSERT INTO audit_log(ts, ip, action, target, detail, username) VALUES(?,?,?,?,?,?)",
            (time.time(), ip, action, target, detail, _audit_user.get()))
        _db().execute(
            "DELETE FROM audit_log WHERE id <= (SELECT MAX(id) FROM audit_log) - ?", (AUDIT_KEEP,))
        _db().commit()


# ─── 密码与会话 ───────────────────────────────────────────

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
_SCRYPT_MAXMEM = 128 * 1024 * 1024  # OpenSSL 默认 32MB 上限不够，需显式放开


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                       dklen=32, maxmem=_SCRYPT_MAXMEM)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, h_hex = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex),
                              n=int(n), r=int(r), p=int(p), dklen=32,
                              maxmem=_SCRYPT_MAXMEM)
        return hmac.compare_digest(calc.hex(), h_hex)
    except Exception:
        return False


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto == "https"


def get_session_user(request: Request) -> Optional[sqlite3.Row]:
    """校验 Cookie 会话；有效则滑动续期。返回 users 行（含 role/is_active/quota_bytes）或 None。"""
    tok = request.cookies.get(COOKIE_NAME)
    if not tok:
        return None
    th = _sha(tok)
    row = _db().execute(
        "SELECT s.expires_at, s.user_id, u.username, u.role, u.is_active, u.quota_bytes, "
        "u.totp_secret, u.totp_enabled, u.totp_rc "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (th,)).fetchone()
    if not row:
        return None
    now = time.time()
    if now > row["expires_at"] or not row["is_active"]:
        with _db_lock:
            _db().execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            _db().commit()
        return None
    if row["expires_at"] < now + SESSION_TTL - SESSION_RENEW_AFTER:
        with _db_lock:
            _db().execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                          (now + SESSION_TTL, th))
            _db().commit()
    return row


def get_bearer_user(request: Request) -> Optional[sqlite3.Row]:
    """App 设备 Token 认证（Authorization: Bearer，APP-DESIGN §3），与 Cookie 会话并存。
    库存 SHA256 哈希；90 天滑动过期（距上次续期超 24h 才写库）；last_seen 节流 1h；
    停用账号即失效（JOIN users 过滤）。浏览器不会自动携带该头，无 CSRF 面。"""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    th = _sha(auth[7:].strip())
    row = _db().execute(
        "SELECT t.id AS device_id, t.user_id, t.expires_at, t.last_seen, "
        "u.username, u.role, u.is_active, u.quota_bytes, u.totp_enabled, u.totp_rc "
        "FROM device_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
        (th,)).fetchone()
    if not row or not row["is_active"]:
        return None
    now = time.time()
    if now > row["expires_at"]:
        return None
    with _db_lock:
        if row["expires_at"] < now + DEVICE_TOKEN_TTL - SESSION_RENEW_AFTER:
            _db().execute("UPDATE device_tokens SET expires_at=? WHERE id=?",
                          (now + DEVICE_TOKEN_TTL, row["device_id"]))
        if not row["last_seen"] or now - row["last_seen"] > 3600:
            _db().execute("UPDATE device_tokens SET last_seen=? WHERE id=?",
                          (now, row["device_id"]))
        _db().commit()
    return row


def create_session(request: Request, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _db_lock:
        _db().execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, ip, ua) "
            "VALUES(?,?,?,?,?,?)",
            (_sha(token), user_id, now, now + SESSION_TTL,
             client_ip(request), request.headers.get("user-agent", "")[:200]))
        _db().commit()
    return token


def require_user(request: Request) -> sqlite3.Row:
    user = get_session_user(request) or get_bearer_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> sqlite3.Row:
    """v3：管理端点依赖（成员/邀请/授权管理、审计、删号）。"""
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def preview_user(request: Request, user: sqlite3.Row) -> "sqlite3.Row | dict":
    """admin 成员视角预览：只读列表接口（文件/相册/分享/元数据）带 X-As-User 头时，
    以目标成员身份做可见性解析；非 admin 一律原样返回（成员传该头无效）。
    仅影响"能看到什么"——写操作仍按真实会话身份走（前端横幅有提示）。
    命中预览记审计（防被劫持会话静默侦察用户名）。
    返回 dict 并补 user_id=id（users 表主键是 id，会话行叫 user_id）；dict 与
    Row 的键访问/`.keys()` 语义一致，resolve/visible 的取值写法两者兼容。"""
    as_name = (request.headers.get("x-as-user") or "").strip()
    if not as_name or user["role"] != "admin":
        return user
    row = _db().execute(
        "SELECT * FROM users WHERE username=? AND is_active=1", (as_name,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="预览目标成员不存在或已停用")
    audit("preview_as", as_name, ip=client_ip(request))
    d = dict(row)
    d["user_id"] = d["id"]
    return d


# ─── 限速 / 防爆破 / CSRF ─────────────────────────────────

_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
_login_fails: dict[str, list[float]] = {}

# ─── 公网入口判定 ─────────────────────────────────────────
# FRP 隧道使公网流量 client_ip 全为 127.0.0.1，无法按 IP 区分内外，只能靠 Host
# （Caddy 对 8765/8767 都透传原始 Host：局域网=192.168.x:8765，公网=frp 域名）。
_netloc_cache: list = []   # [netloc, ts]


def public_netloc() -> str:
    """管理面板配置的公网地址 netloc（如 your-frp-domain.com:xxxxx），60s 缓存；未配置返回空。"""
    now = time.time()
    if _netloc_cache and now - _netloc_cache[1] < 60:
        return _netloc_cache[0]
    netloc = ""
    try:
        row = _db().execute("SELECT value FROM settings WHERE key='public_base_url'").fetchone()
        if row and row["value"]:
            netloc = (urlsplit(row["value"]).netloc or "").lower()
    except Exception:
        netloc = ""
    _netloc_cache[:] = [netloc, now]
    return netloc


def reset_netloc_cache():
    """管理面板改公网地址后立即生效（否则等 60s 缓存过期）。"""
    _netloc_cache[:] = ["", 0]


def is_public_entry(request: Request) -> bool:
    """请求是否来自公网入口。防爆破/限速/healthz 管控的判定基础：
    公网侧的失败只锁公网侧，LAN 家人登录永不因公网攻击被锁（DoS 隔离）。"""
    host = (request.headers.get("host") or "").lower()
    return bool(host) and host == public_netloc()


def allow_rate(key: str, limit: int, window: int = 60) -> bool:
    now = time.time()
    with _rate_lock:
        q = _rate.setdefault(key, [])
        while q and q[0] <= now - window:
            q.pop(0)
        if len(q) >= limit:
            return False
        q.append(now)
        # 顺带清理空桶，防内存膨胀
        if len(_rate) > 4096:
            for k in [k for k, v in _rate.items() if not v or v[-1] <= now - window]:
                del _rate[k]
        return True


def _lock_key(username: str, public: bool) -> str:
    """锁定/失败计数键：公网入口与局域网入口分账——
    公网攻击者只能锁住公网侧自己的路，LAN 家人登录不受波及。"""
    u = username.strip().lower()   # 小写化：防大小写变体绕过锁定
    return f"{u}@pub" if public else u


def login_locked_remaining(username: str, public: bool = False) -> int:
    """账号连续失败 LOGIN_MAX_FAILS 次后的剩余锁定秒数，0 = 未锁。"""
    now = time.time()
    with _rate_lock:
        q = _login_fails.get(_lock_key(username, public), [])
        while q and q[0] <= now - LOGIN_LOCK_SECONDS:
            q.pop(0)
        if len(q) >= LOGIN_MAX_FAILS:
            return int(q[0] + LOGIN_LOCK_SECONDS - now) + 1
        return 0


def record_login_fail(username: str, public: bool = False):
    with _rate_lock:
        _login_fails.setdefault(_lock_key(username, public), []).append(time.time())


def clear_login_fails(username: str, public: bool = False):
    """登录成功后清当前入口的失败计数（不清另一入口——公网攻击者的锁定不因 LAN 登录解除）。"""
    with _rate_lock:
        _login_fails.pop(_lock_key(username, public), None)


def client_ip(request: Request) -> str:
    """取 XFF 的最后一个值——那才是我们自己的 Caddy 追加的、不可被客户端伪造的；
    配合 Caddyfile 的 request_header -X-Forwarded-For（剥离外部传入的 XFF）双保险。"""
    xff = request.headers.get("x-forwarded-for", "")
    values = [v.strip() for v in xff.split(",") if v.strip()]
    if values:
        return values[-1]
    return request.client.host if request.client else "?"
