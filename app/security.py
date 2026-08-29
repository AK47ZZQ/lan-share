"""认证与安全：审计、scrypt 密码、会话、防爆破、限速、client_ip。"""
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from typing import Optional

from fastapi import HTTPException, Request

from app.config import (AUDIT_KEEP, COOKIE_NAME, LOGIN_LOCK_SECONDS, LOGIN_MAX_FAILS,
                        SESSION_RENEW_AFTER, SESSION_TTL)
from app.db import _db, _db_lock

def audit(action: str, target: str = "", detail: str = "", ip: str = ""):
    with _db_lock:
        _db().execute(
            "INSERT INTO audit_log(ts, ip, action, target, detail) VALUES(?,?,?,?,?)",
            (time.time(), ip, action, target, detail))
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
        "u.totp_secret, u.totp_enabled "
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
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> sqlite3.Row:
    """v3：管理端点依赖（成员/邀请/授权管理、审计、删号）。"""
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


# ─── 限速 / 防爆破 / CSRF ─────────────────────────────────

_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
_login_fails: dict[str, list[float]] = {}


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


def login_locked_remaining(username: str) -> int:
    """账号连续失败 LOGIN_MAX_FAILS 次后的剩余锁定秒数，0 = 未锁。"""
    now = time.time()
    with _rate_lock:
        # key 与 record_login_fail 一致：小写化，防大小写变体绕过锁定
        q = _login_fails.get(username.strip().lower(), [])
        while q and q[0] <= now - LOGIN_LOCK_SECONDS:
            q.pop(0)
        if len(q) >= LOGIN_MAX_FAILS:
            return int(q[0] + LOGIN_LOCK_SECONDS - now) + 1
        return 0


def record_login_fail(username: str):
    with _rate_lock:
        # key 统一小写：Windows/SQLite 侧用户名大小写变体不能绕过锁定
        _login_fails.setdefault(username.strip().lower(), []).append(time.time())


def client_ip(request: Request) -> str:
    """取 XFF 的最后一个值——那才是我们自己的 Caddy 追加的、不可被客户端伪造的；
    配合 Caddyfile 的 request_header -X-Forwarded-For（剥离外部传入的 XFF）双保险。"""
    xff = request.headers.get("x-forwarded-for", "")
    values = [v.strip() for v in xff.split(",") if v.strip()]
    if values:
        return values[-1]
    return request.client.host if request.client else "?"
