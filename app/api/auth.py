"""认证 API：初始化/登录(含 TOTP 两步)/登出/改密/改用户名/账号状态/两步验证管理/设备 Token。"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from typing import Optional

import pyotp
from fastapi import Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.config import (COOKIE_NAME, DEVICE_TOKEN_MAX, DEVICE_TOKEN_TTL, SESSION_TTL,
                        SHARE_ROOT)
from app.db import _db, _db_lock
from app.security import (audit, allow_rate, clear_login_fails, client_ip, create_session,
                          get_bearer_user, get_session_user, hash_password, is_https,
                          is_public_entry, login_locked_remaining, record_login_fail,
                          require_user, verify_password, _sha)
from app.spaces import RESERVED_USERNAMES, _normcase_eq
from app.main import app

class AuthBody(BaseModel):
    username: str
    password: str


class TotpLoginBody(BaseModel):
    username: str
    password: str
    code: str


class TotpCodeBody(BaseModel):
    code: str


class TotpDisableBody(BaseModel):
    old_password: str


class SharePrepareBody(BaseModel):
    password: Optional[str] = None


def user_count() -> int:
    return _db().execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def set_session_cookie(response: Response, request: Request, user_id: int):
    token = create_session(request, user_id)
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_TTL, path="/",
        httponly=True, samesite="lax", secure=is_https(request))


@app.get("/api/auth/state")
async def auth_state(request: Request):
    user = get_session_user(request)
    return {"needs_setup": user_count() == 0,
            "logged_in": bool(user),
            "username": user["username"] if user else None}


@app.post("/api/setup")
async def setup(body: AuthBody, request: Request, response: Response):
    # 检查 + 插入同锁（否则空库并发 setup 可建出双管理员）
    with _db_lock:
        if user_count() > 0:
            raise HTTPException(status_code=403, detail="管理员已存在")
        if len(body.password) < 8:
            raise HTTPException(status_code=400, detail="密码至少 8 位")
        if not body.username.strip():
            raise HTTPException(status_code=400, detail="用户名不能为空")
        _db().execute("INSERT INTO users(username, password_hash, created_at, role) VALUES(?,?,?,'admin')",
                      (body.username.strip(), hash_password(body.password), time.time()))
        _db().commit()
    uid = _db().execute("SELECT id FROM users WHERE username=?",
                        (body.username.strip(),)).fetchone()["id"]
    audit("setup", body.username.strip(), ip=client_ip(request))
    set_session_cookie(response, request, uid)
    return {"ok": True}


def _login_gate(username: str, request: Request):
    """登录前置：防爆破锁定（按入口分账）+ 账号在用检查（不含密码校验，scrypt 校验留在线程池）。"""
    pub = is_public_entry(request)
    remaining = login_locked_remaining(username, pub)
    if remaining:
        audit("login_locked", username, f"{'pub ' if pub else ''}{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    row = _db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row and not row["is_active"]:
        audit("login_disabled", username, ip=client_ip(request))
        raise HTTPException(status_code=403, detail="账号已停用")
    return row


async def _verify_pw(pw: str, stored: str) -> bool:
    return await run_in_threadpool(verify_password, pw, stored)


@app.post("/api/login")
async def login(body: AuthBody, request: Request, response: Response):
    pub = is_public_entry(request)
    row = _login_gate(body.username, request)
    ok = bool(row) and await _verify_pw(body.password, row["password_hash"])
    if not ok:
        record_login_fail(body.username, pub)
        audit("login_fail", body.username, "pub" if pub else "", client_ip(request))
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 已启用两步验证：密码正确也不发会话，要求第二因子
    if row["totp_enabled"]:
        audit("login_totp_needed", body.username, ip=client_ip(request))
        return {"totp_required": True, "username": row["username"]}
    clear_login_fails(body.username, pub)
    audit("login", body.username, ip=client_ip(request))
    set_session_cookie(response, request, row["id"])
    return {"ok": True, "username": row["username"]}


def _totp_match_step(secret: str, code: str) -> int | None:
    """±1 窗口内匹配动态码，返回命中的时间步；不中返回 None。
    不用 pyotp.verify 只取布尔——它不记步，同一 30s 窗口内的码可被重放；
    返回步数后与 users.totp_last_step 比对即可拦截（review 2026-09-02 P3）。"""
    totp = pyotp.TOTP(secret)
    now_step = int(time.time() // 30)
    for off in (-1, 0, 1):
        if hmac.compare_digest(totp.at((now_step + off) * 30), code):
            return now_step + off
    return None


@app.post("/api/login/totp")
async def login_totp(body: TotpLoginBody, request: Request, response: Response):
    """登录第二步：密码 + 动态验证码（复用按账号防爆破；验证码错误同样计入锁定）。
    认证器不可用时可用开通时下发的一次性恢复码顶替（用一枚销一枚）。"""
    pub = is_public_entry(request)
    row = _login_gate(body.username, request)
    ok = bool(row) and await _verify_pw(body.password, row["password_hash"])
    if not ok:
        record_login_fail(body.username, pub)
        audit("login_fail", body.username, "pub" if pub else "", client_ip(request))
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not row["totp_enabled"] or not row["totp_secret"]:
        raise HTTPException(status_code=400, detail="该账号未启用两步验证")

    def _fail():
        record_login_fail(body.username, pub)
        audit("totp_fail", body.username, ip=client_ip(request))

    raw = body.code.strip().replace(" ", "")
    rc = raw.replace("-", "").lower()
    used_rc = False
    if raw.isdigit() and len(raw) == 6:
        matched = _totp_match_step(row["totp_secret"], raw)
        # 命中步 <= 已用步 = 同码重放/过期步回放，一律拒绝
        if matched is None or matched <= (row["totp_last_step"] or 0):
            _fail()
            raise HTTPException(status_code=401, detail="动态验证码错误")
        with _db_lock:
            _db().execute("UPDATE users SET totp_last_step=? WHERE id=?",
                          (matched, row["id"]))
            _db().commit()
    elif _RC_RE.match(rc):
        # 恢复码路径：SHA256 命中库存即消费（库内只存哈希，用一枚少一枚）
        hashes = json.loads(row["totp_rc"]) if row["totp_rc"] else []
        h = _rc_hash(rc)
        if h not in hashes:
            _fail()
            raise HTTPException(status_code=401, detail="恢复码无效或已用掉")
        hashes.remove(h)
        with _db_lock:
            _db().execute("UPDATE users SET totp_rc=? WHERE id=?",
                          (json.dumps(hashes), row["id"]))
            _db().commit()
        used_rc = True
    else:
        _fail()
        raise HTTPException(status_code=401, detail="动态验证码错误")

    clear_login_fails(body.username, pub)
    audit("login", body.username, "totp_rc" if used_rc else "totp", client_ip(request))
    set_session_cookie(response, request, row["id"])
    return {"ok": True, "username": row["username"]}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    tok = request.cookies.get(COOKIE_NAME)
    if tok:
        with _db_lock:
            _db().execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(tok),))
            _db().commit()
    audit("logout", ip=client_ip(request))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/authcheck")
async def authcheck(request: Request):
    """Caddy forward_auth 子请求入口：会话有效 200，否则 401。"""
    if get_session_user(request):
        return PlainTextResponse("ok")
    raise HTTPException(status_code=401, detail="Not authenticated")


@app.get("/api/auth/me")
async def me(request: Request):
    # Cookie 会话与 App Bearer 设备 Token 并存（App 登录后用它确认身份）
    user = get_session_user(request) or get_bearer_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"username": user["username"], "role": user["role"],
            "quota_bytes": user["quota_bytes"],
            "totp_enabled": bool(user["totp_enabled"]),
            "totp_rc_left": len(json.loads(user["totp_rc"])) if user["totp_rc"] else 0}


# ─── 两步验证（TOTP，可选启用）────────────────────────────

# 恢复码：认证器不可用（手机丢失/卸载）时顶替动态码的一次性第二因子。
# 库内只存 SHA256（JSON 数组），明文仅在生成响应中出现一次，用一枚少一枚。
_RC_COUNT = 10
_RC_RE = re.compile(r"^[0-9a-f]{8}$")


def _rc_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _gen_recovery_codes() -> tuple[list[str], str]:
    """生成 _RC_COUNT 枚举 8 位 hex 恢复码，返回 (明文列表, 库存 JSON)。"""
    codes = [secrets.token_hex(4) for _ in range(_RC_COUNT)]
    return codes, json.dumps([_rc_hash(c) for c in codes])


class TotpRcBody(BaseModel):
    old_password: str


@app.post("/api/auth/totp/setup")
def totp_setup(user=Depends(require_user)):
    """生成 TOTP 密钥（未验证前不生效），返回密钥与 otpauth:// URI（前端转二维码）。"""
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    if row["totp_enabled"]:
        raise HTTPException(status_code=400, detail="两步验证已启用")
    secret = pyotp.random_base32()
    with _db_lock:
        _db().execute("UPDATE users SET totp_secret=?, totp_enabled=0, totp_last_step=NULL WHERE id=?",
                      (secret, user["user_id"]))
        _db().commit()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=row["username"], issuer_name="LAN Share")
    return {"secret": secret, "otpauth": uri}


@app.post("/api/auth/totp/enable")
def totp_enable(body: TotpCodeBody, user=Depends(require_user)):
    """用动态验证码确认开通（证明认证器已正确录入）。开通时生成一次性恢复码，
    明文仅本响应返回一次，供认证器不可用时顶替第二因子。"""
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    if row["totp_enabled"]:
        raise HTTPException(status_code=400, detail="两步验证已启用")
    if not row["totp_secret"]:
        raise HTTPException(status_code=400, detail="请先获取密钥")
    code = body.code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6) \
            or not pyotp.TOTP(row["totp_secret"]).verify(code, valid_window=1):
        raise HTTPException(status_code=400, detail="验证码错误，请重试")
    codes, rc_json = _gen_recovery_codes()
    with _db_lock:
        _db().execute("UPDATE users SET totp_enabled=1, totp_rc=? WHERE id=?",
                      (rc_json, user["user_id"]))
        _db().commit()
    audit("totp_enabled", row["username"], ip="")
    return {"ok": True, "recovery_codes": codes}


@app.post("/api/auth/totp/rc")
async def totp_rc_regen(request: Request, body: TotpRcBody, user=Depends(require_user)):
    """重新生成恢复码（需原密码）：旧码全部作废。用于码没用过却弄丢了的情况。"""
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    if not row["totp_enabled"]:
        raise HTTPException(status_code=400, detail="两步验证未启用")
    if not await _verify_pw(body.old_password, row["password_hash"]):
        audit("totp_rc_regen_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="原密码错误")
    codes, rc_json = _gen_recovery_codes()
    with _db_lock:
        _db().execute("UPDATE users SET totp_rc=? WHERE id=?", (rc_json, user["user_id"]))
        _db().commit()
    audit("totp_rc_regen", row["username"], ip=client_ip(request))
    return {"ok": True, "recovery_codes": codes}


@app.post("/api/auth/totp/disable")
async def totp_disable(request: Request, body: TotpDisableBody, user=Depends(require_user)):
    """关闭两步验证：需原密码确认（会话被劫持时不能随手关掉）。"""
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    if not await _verify_pw(body.old_password, row["password_hash"]):
        audit("totp_disable_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="原密码错误")
    with _db_lock:
        _db().execute("UPDATE users SET totp_secret=NULL, totp_enabled=0, totp_rc=NULL, "
                      "totp_last_step=NULL WHERE id=?",
                      (user["user_id"],))
        _db().commit()
    audit("totp_disabled", row["username"], ip=client_ip(request))
    return {"ok": True}


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/password")
async def change_password(request: Request, response: Response, body: PasswordBody,
                          user=Depends(require_user)):
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    # 密码确认失败同样计入防爆破锁定（否则会话被劫持后可无限试密码）；
    # 公网侧失败只记公网侧（与登录/_dav_auth 同策略，防跨入口锁人）
    pub = is_public_entry(request)
    remaining = login_locked_remaining(row["username"], pub)
    if remaining:
        audit("login_locked", row["username"], f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    if not await run_in_threadpool(verify_password, body.old_password, row["password_hash"]):
        record_login_fail(row["username"], pub)
        audit("password_change_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="原密码错误")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    new_hash = await run_in_threadpool(hash_password, body.new_password)
    with _db_lock:
        _db().execute("UPDATE users SET password_hash=? WHERE id=?",
                      (new_hash, row["id"]))
        # 改密后吊销全部旧会话与设备 Token（与 admin 重置同语义），重新签发当前 Cookie
        _db().execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
        _db().execute("DELETE FROM device_tokens WHERE user_id=?", (row["id"],))
        _db().commit()
    audit("password_change", row["username"], ip=client_ip(request))
    set_session_cookie(response, request, row["id"])
    return {"ok": True}


class UsernameBody(BaseModel):
    new_username: str
    password: str


@app.post("/api/auth/username")
async def change_username(request: Request, body: UsernameBody,
                          user=Depends(require_user)):
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    # 密码确认失败同样计入防爆破锁定（否则会话被劫持后可无限试密码）；
    # 公网侧失败只记公网侧（与登录/_dav_auth 同策略，防跨入口锁人）
    pub = is_public_entry(request)
    remaining = login_locked_remaining(row["username"], pub)
    if remaining:
        audit("login_locked", row["username"], f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    if not await run_in_threadpool(verify_password, body.password, row["password_hash"]):
        record_login_fail(row["username"], pub)
        audit("username_change_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="密码错误")
    new_name = body.new_username.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if len(new_name) > 64 or re.search(r"[\s\u0000-\u001f\u007f]", new_name):
        raise HTTPException(status_code=400,
                            detail="用户名不能含空白/控制字符，且不超过 64 字符")
    if new_name == row["username"]:
        raise HTTPException(status_code=400, detail="新用户名与当前相同")
    if new_name.lower() in RESERVED_USERNAMES:
        raise HTTPException(status_code=400, detail="该用户名是保留名")
    # 重名检查与 UPDATE 同锁（否则并发改名可双双通过检查）
    with _db_lock:
        if _db().execute("SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)",
                         (new_name,)).fetchone():
            raise HTTPException(status_code=409, detail="该用户名已被占用")
        _db().execute("UPDATE users SET username=? WHERE id=?", (new_name, row["id"]))
        _db().commit()
    # 私人空间目录跟随用户名迁移（Windows 大小写不敏感：仅大小写变化时 rename 会变 no-op）
    old_dir = SHARE_ROOT / "private" / row["username"]
    new_dir = SHARE_ROOT / "private" / new_name
    if old_dir.exists() and not _normcase_eq(old_dir, new_dir):
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            old_dir.rename(new_dir)
        except OSError:
            audit("username_dir_rename_fail", f"{row['username']} → {new_name}",
                  ip=client_ip(request))
    # 直传子目录同步改名（否则旧 incoming/<旧名>/ 文件对本人不可见）
    old_inc = SHARE_ROOT / "incoming" / row["username"]
    new_inc = SHARE_ROOT / "incoming" / new_name
    if old_inc.exists() and not _normcase_eq(old_inc, new_inc):
        try:
            old_inc.rename(new_inc)
        except OSError:
            audit("incoming_dir_rename_fail", f"{row['username']} → {new_name}",
                  ip=client_ip(request))
    # 会话按 user_id 关联，改名不影响登录态；旧用户名的防爆破计数自然失效
    audit("username_change", row["username"], f"→ {new_name}", client_ip(request))
    return {"ok": True, "username": new_name}


# ─── WebDAV 独立密码（与登录密码隔离的挂载凭据）────────────

class WebDavPasswordBody(BaseModel):
    current_password: str
    webdav_password: str = ""   # 空串 = 清除（回退为登录密码挂载）


@app.get("/api/auth/webdav-password")
def webdav_password_status(user=Depends(require_user)):
    """只暴露是否已设置，绝不回传哈希。"""
    row = _db().execute("SELECT webdav_password_hash FROM users WHERE id=?",
                        (user["user_id"],)).fetchone()
    return {"set": bool(row and row["webdav_password_hash"])}


@app.post("/api/auth/webdav-password")
async def set_webdav_password(request: Request, body: WebDavPasswordBody,
                              user=Depends(require_user)):
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    # 与改密同款防线：密码确认失败计入防爆破锁定（否则会话被劫持后可无限试）。
    # 公网侧失败只记公网侧，避免公网攻击者锁掉本人 LAN 登录（与 _dav_auth 同策略）
    pub = is_public_entry(request)
    remaining = login_locked_remaining(row["username"], pub)
    if remaining:
        audit("login_locked", row["username"], f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    if not await run_in_threadpool(verify_password, body.current_password, row["password_hash"]):
        record_login_fail(row["username"], pub)
        audit("webdav_password_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="当前密码错误")
    if body.webdav_password:
        if len(body.webdav_password) < 8:
            raise HTTPException(status_code=400, detail="WebDAV 密码至少 8 位")
        new_hash = await run_in_threadpool(hash_password, body.webdav_password)
    else:
        new_hash = None
    with _db_lock:
        _db().execute("UPDATE users SET webdav_password_hash=? WHERE id=?",
                      (new_hash, row["id"]))
        _db().commit()
    audit("webdav_password_set" if new_hash else "webdav_password_clear",
          row["username"], ip=client_ip(request))
    return {"ok": True, "set": bool(new_hash)}


# ─── 设备 Token（App 专用，APP-DESIGN §3 / 里程碑 M-A0）──────

class DeviceTokenBody(BaseModel):
    name: str


@app.post("/api/auth/token")
async def create_device_token(request: Request, body: DeviceTokenBody,
                              user=Depends(require_user)):
    """登录会话（Cookie）换长效设备 Token。明文仅本次响应返回一次，库内只存
    SHA256；90 天滑动过期；吊销由 DELETE /api/auth/devices/{id} 完成。"""
    if not allow_rate(f"devtok:{user['user_id']}", 20, 3600):
        raise HTTPException(status_code=429, detail="签发过于频繁，请稍后再试")
    name = body.name.strip()
    # 允许内部空格（"小米 14"），禁控制字符（换行/制表等）与首尾空白
    if not name or len(name) > 64 or re.search(r"[\u0000-\u001f\u007f]", name):
        raise HTTPException(status_code=400,
                            detail="设备名不能为空，且不能含控制字符，不超过 64 字符")
    token, device_id = _issue_device_token(user["user_id"], name)
    audit("device_token_create", name, ip=client_ip(request))
    return JSONResponse({"token": token, "device_id": device_id, "name": name,
                         "created_at": time.time()},
                        headers={"Cache-Control": "no-store"})


def _issue_device_token(user_id: int, name: str) -> tuple[str, int]:
    """签发设备 Token 内核：同名轮换（重装/重复登录不产生僵尸设备）+ 上限
    校验 + 入库。/api/auth/token 与 /api/pair/claim 共用；明文仅在调用方
    响应中出现一次，库内只存 SHA256。"""
    n = _db().execute("SELECT COUNT(*) AS c FROM device_tokens WHERE user_id=?",
                      (user_id,)).fetchone()["c"]
    if n >= DEVICE_TOKEN_MAX:
        raise HTTPException(status_code=400,
                            detail=f"设备数已达上限（{DEVICE_TOKEN_MAX}），请先吊销不用的设备")
    token = secrets.token_urlsafe(32)
    now = time.time()
    # 轮换判定 + UPDATE/INSERT 必须整体在 _db_lock 内（SELECT-then-write 竞态面）
    with _db_lock:
        existing = _db().execute(
            "SELECT id FROM device_tokens WHERE user_id=? AND name=?",
            (user_id, name)).fetchone()
        if existing:
            _db().execute(
                "UPDATE device_tokens SET token_hash=?, created_at=?, last_seen=?, expires_at=? WHERE id=?",
                (_sha(token), now, now, now + DEVICE_TOKEN_TTL, existing["id"]))
            device_id = existing["id"]
        else:
            try:
                cur = _db().execute(
                    "INSERT INTO device_tokens(user_id, name, token_hash, created_at, last_seen, expires_at) "
                    "VALUES(?,?,?,?,?,?) RETURNING id",
                    (user_id, name, _sha(token), now, now, now + DEVICE_TOKEN_TTL))
                device_id = cur.fetchone()["id"]
            except sqlite3.IntegrityError:
                # token_hash UNIQUE 极端碰撞：换一枚重试一次
                token = secrets.token_urlsafe(32)
                cur = _db().execute(
                    "INSERT INTO device_tokens(user_id, name, token_hash, created_at, last_seen, expires_at) "
                    "VALUES(?,?,?,?,?,?) RETURNING id",
                    (user_id, name, _sha(token), now, now, now + DEVICE_TOKEN_TTL))
                device_id = cur.fetchone()["id"]
        _db().commit()
    return token, device_id


@app.get("/api/auth/devices")
def list_devices(request: Request, user=Depends(require_user)):
    """本账号的设备列表（他人不可见）。current 标记本次请求所用的 Bearer 设备。"""
    auth = request.headers.get("authorization", "")
    cur_hash = _sha(auth[7:].strip()) if auth.startswith("Bearer ") else None
    rows = _db().execute(
        "SELECT id, name, created_at, last_seen, token_hash FROM device_tokens "
        "WHERE user_id=? ORDER BY id", (user["user_id"],)).fetchall()
    return {"devices": [{
        "id": r["id"], "name": r["name"],
        "created_at": r["created_at"], "last_seen": r["last_seen"],
        "current": bool(cur_hash and r["token_hash"] == cur_hash),
    } for r in rows]}


@app.delete("/api/auth/devices/{device_id}")
def revoke_device(device_id: int, request: Request, user=Depends(require_user)):
    """吊销本人设备（被吊销端下次请求即 401）。"""
    row = _db().execute("SELECT * FROM device_tokens WHERE id=? AND user_id=?",
                        (device_id, user["user_id"])).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="设备不存在")
    with _db_lock:
        _db().execute("DELETE FROM device_tokens WHERE id=?", (device_id,))
        _db().commit()
    audit("device_revoke", row["name"], ip=client_ip(request))
    return {"ok": True}


# ─── 配对码登录（手机 App 免密配对）────────────────────────
# 场景：家里长辈的手机不输长密码——已登录的 Web 端生成一次性 6 位码，
# 手机在登录页选"配对码登录"输码即换设备 Token。安全三重保险：
# 60 秒时效 + 一码一用（取走即删）+ 按 IP 限速防爆破。

class PairClaimBody(BaseModel):
    code: str
    name: str = "我的手机"


_PAIR_TTL = 180.0  # 3 分钟：留足"拿起另一台设备→解锁→找到入口→输码"的时间；6 位码 + 一码一用 + claim 限速下不放大爆破面
# 进程内存态即可：本项目 NSSM 单 worker 部署；重启即全失效（重生成即可）
_pair_codes: dict[str, tuple[int, float]] = {}
_pair_lock = threading.Lock()


@app.post("/api/pair/code")
async def pair_code_create(request: Request, user=Depends(require_user)):
    """已登录方（Web 会话或 Bearer）生成一次性配对码；同一账号只保留最新一枚。"""
    if not allow_rate(f"paircode:{user['user_id']}", 10, 60):
        raise HTTPException(status_code=429, detail="生成过于频繁，请稍后再试")
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = time.time()
    with _pair_lock:
        for k in [k for k, v in _pair_codes.items()
                  if v[1] < now or v[0] == user["user_id"]]:
            _pair_codes.pop(k)
        _pair_codes[code] = (user["user_id"], now + _PAIR_TTL)
    audit("pair_code_create", user["username"], ip=client_ip(request))
    return {"code": code, "expires_in": int(_PAIR_TTL)}


@app.post("/api/pair/claim")
async def pair_claim(request: Request, body: PairClaimBody):
    """手机提交配对码换设备 Token（免账号密码）。凭据规则与 /api/auth/token
    完全一致（同名轮换、上限、TTL），签出的 Token 也能随时在 Web 端吊销。"""
    code = body.code.strip().replace(" ", "")
    ip = client_ip(request)
    if not (code.isdigit() and len(code) == 6):
        raise HTTPException(status_code=400, detail="配对码格式错误")
    if not allow_rate(f"pairclaim:{ip}", 5, 60):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    now = time.time()
    with _pair_lock:
        # 懒清理：claim 路径也顺带清过期项——只靠生成端清理时，
        # 生成后无人认领的过期码会一直残留（同账号顶掉才消失）
        for k in [k for k, v in _pair_codes.items() if v[1] < now]:
            _pair_codes.pop(k)
        hit = _pair_codes.pop(code, None)
    if not hit or hit[1] < now:
        audit("pair_claim_fail", ip=ip)
        raise HTTPException(status_code=401, detail="配对码无效或已过期")
    user_id, _ = hit
    row = _db().execute("SELECT id, username FROM users WHERE id=?",
                        (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="配对码无效或已过期")
    name = body.name.strip()
    if not name or len(name) > 64 or re.search(r"[\u0000-\u001f\u007f]", name):
        raise HTTPException(status_code=400,
                            detail="设备名不能为空，且不能含控制字符，不超过 64 字符")
    token, device_id = _issue_device_token(user_id, name)
    audit("pair_claim", row["username"], name, ip=ip)
    return JSONResponse({"token": token, "device_id": device_id, "name": name,
                         "created_at": now},
                        headers={"Cache-Control": "no-store"})


# ─── 文件 API（需登录）─────────────────────────────────────
