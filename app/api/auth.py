"""认证 API：初始化/登录/登出/改密/改用户名/账号状态。"""
import os
import re
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import COOKIE_NAME, SESSION_TTL, SHARE_ROOT
from app.db import _db, _db_lock
from app.security import (audit, client_ip, create_session, get_session_user,
                          hash_password, is_https, login_locked_remaining,
                          record_login_fail, require_user, verify_password,
                          _login_fails, _rate_lock, _sha)
from app.spaces import RESERVED_USERNAMES, _normcase_eq
from app.main import app

class AuthBody(BaseModel):
    username: str
    password: str


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
    if user_count() > 0:
        raise HTTPException(status_code=403, detail="管理员已存在")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="用户名不能为空")
    with _db_lock:
        _db().execute("INSERT INTO users(username, password_hash, created_at, role) VALUES(?,?,?,'admin')",
                      (body.username.strip(), hash_password(body.password), time.time()))
        _db().commit()
    uid = _db().execute("SELECT id FROM users WHERE username=?",
                        (body.username.strip(),)).fetchone()["id"]
    audit("setup", body.username.strip(), ip=client_ip(request))
    set_session_cookie(response, request, uid)
    return {"ok": True}


@app.post("/api/login")
async def login(body: AuthBody, request: Request, response: Response):
    remaining = login_locked_remaining(body.username)
    if remaining:
        audit("login_locked", body.username, f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    row = _db().execute("SELECT * FROM users WHERE username=?",
                        (body.username,)).fetchone()
    if row and not row["is_active"]:
        audit("login_disabled", body.username, ip=client_ip(request))
        raise HTTPException(status_code=403, detail="账号已停用")
    ok = bool(row) and await run_in_threadpool(verify_password, body.password, row["password_hash"])
    if not ok:
        record_login_fail(body.username)
        audit("login_fail", body.username, ip=client_ip(request))
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    with _rate_lock:
        _login_fails.pop(body.username.strip().lower(), None)
    audit("login", body.username, ip=client_ip(request))
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
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"username": user["username"], "role": user["role"],
            "quota_bytes": user["quota_bytes"]}


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/password")
async def change_password(request: Request, response: Response, body: PasswordBody,
                          user=Depends(require_user)):
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    # 密码确认失败同样计入防爆破锁定（否则会话被劫持后可无限试密码）
    remaining = login_locked_remaining(row["username"])
    if remaining:
        audit("login_locked", row["username"], f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    if not await run_in_threadpool(verify_password, body.old_password, row["password_hash"]):
        record_login_fail(row["username"])
        audit("password_change_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="原密码错误")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    new_hash = await run_in_threadpool(hash_password, body.new_password)
    with _db_lock:
        _db().execute("UPDATE users SET password_hash=? WHERE id=?",
                      (new_hash, row["id"]))
        # 改密后吊销全部旧会话，重新签发当前这一个
        _db().execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
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
    # 密码确认失败同样计入防爆破锁定（否则会话被劫持后可无限试密码）
    remaining = login_locked_remaining(row["username"])
    if remaining:
        audit("login_locked", row["username"], f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    if not await run_in_threadpool(verify_password, body.password, row["password_hash"]):
        record_login_fail(row["username"])
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
    if _db().execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (new_name,)).fetchone():
        raise HTTPException(status_code=409, detail="该用户名已被占用")
    with _db_lock:
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
    # 会话按 user_id 关联，改名不影响登录态；旧用户名的防爆破计数自然失效
    audit("username_change", row["username"], f"→ {new_name}", client_ip(request))
    return {"ok": True, "username": new_name}


# ─── 文件 API（需登录）─────────────────────────────────────
