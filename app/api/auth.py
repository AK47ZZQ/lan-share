"""认证 API·会话域（07 小尾巴按域拆分：本模块留会话/登录/登出/身份复核。

兄弟域：两步验证→totp、资料（昵称/头像）→profile、
账号安全（改密/改名/DAV 密码）→account、设备 Token/配对码→devices）。
"""
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import COOKIE_NAME, SESSION_TTL
from app.db import _db, _db_lock
from app.roles import ADMIN
from app.security import (audit, allow_rate, clear_login_fails, client_ip,
                          create_session, get_bearer_user, get_session_user,
                          hash_password, is_https, is_public_entry,
                          login_locked_remaining, record_login_fail,
                          require_user, _sha)
from app.services.accounts import clean_username
from app.services.passwords import verify_pw as _verify_pw
from app.services.passwords import verify_pw_with_dummy as _verify_pw_with_dummy
from app.store import users as users_store


class AuthBody(BaseModel):
    username: str
    password: str


def user_count() -> int:
    return users_store.count()


def _drop_session(token_hash: str) -> None:
    """吊销单个 Cookie 会话（同步；经 run_in_threadpool 调用，见 P2-2）。"""
    with _db_lock:
        _db().execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        _db().commit()


def set_session_cookie(response: Response, request: Request, user_id: int):
    token = create_session(request, user_id)
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_TTL, path="/",
        httponly=True, samesite="lax", secure=is_https(request))


# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装（07 审查批次 2 消循环）
router = APIRouter()


@router.get("/api/auth/state")
async def auth_state(request: Request):
    user = await run_in_threadpool(get_session_user, request)
    return {"needs_setup": user_count() == 0,
            "logged_in": bool(user),
            "username": user["username"] if user else None}


@router.post("/api/setup")
async def setup(body: AuthBody, request: Request, response: Response):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    # 首个管理员用户名同样过完整校验（含路径分隔符/Windows 保留名，R1-P3-5），
    # 否则可建出 private 目录落在设备命名空间的残废账号。
    # setup 是引导入口：放行 admin/administrator/root/system 等管理语义保留名
    # （测试夹具与存量惯例），但路径段冲突名（shared/private/incoming/tmp）、
    # 分隔符与设备名仍然拒绝
    username = body.username.strip()
    if username.lower() not in {"admin", "administrator", "root", "system"}:
        username = clean_username(username)
    else:
        if len(username) > 64 or re.search(r"[\s\u0000-\u001f\u007f]", username):
            raise HTTPException(status_code=400, detail="用户名不能含空白/控制字符，且不超过 64 字符")
    if user_count() > 0:
        # 廉价预检（锁内还有复查兜底双管理员）：setup 关闭后未认证请求
        # 不必白烧 100-300ms 的 scrypt（R2-P3）
        raise HTTPException(status_code=403, detail="管理员已存在")
    # scrypt 求值（约 100-300ms）放全局锁外 + 线程池，避免占住 _db_lock 并阻塞事件循环
    new_hash = await run_in_threadpool(hash_password, body.password)
    # 检查 + 插入同锁（否则空库并发 setup 可建出双管理员）。
    # 同步块整体进线程池：事件循环内不得做 PG 往返（11 号复审 P2-2）
    def _create_first_admin() -> int:
        with _db_lock:
            if user_count() > 0:
                raise HTTPException(status_code=403, detail="管理员已存在")
            users_store.create(username, new_hash, ADMIN)
        return users_store.id_by_username(username)

    uid_ = await run_in_threadpool(_create_first_admin)
    audit("setup", username, ip=client_ip(request))
    set_session_cookie(response, request, uid_)
    return {"ok": True}


def login_gate(username: str, request: Request):
    """登录前置：防爆破锁定（按入口分账）+ 账号在用检查（不含密码校验，
    scrypt 校验留在线程池）。登录与 TOTP 第二步（totp 模块）共用。"""
    pub = is_public_entry(request)
    remaining = login_locked_remaining(username, pub)
    if remaining:
        audit("login_locked", username, f"{'pub ' if pub else ''}{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    row = users_store.get_by_username(username)
    if row and not row["is_active"]:
        audit("login_disabled", username, ip=client_ip(request))
        raise HTTPException(status_code=403, detail="账号已停用")
    return row


@router.post("/api/login")
async def login(body: AuthBody, request: Request, response: Response):
    pub = is_public_entry(request)
    row = await run_in_threadpool(login_gate, body.username, request)
    ok = await _verify_pw_with_dummy(body.password, row["password_hash"] if row else None)
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


@router.post("/api/logout")
async def logout(request: Request, response: Response):
    tok = request.cookies.get(COOKIE_NAME)
    if tok:
        await run_in_threadpool(_drop_session, _sha(tok))
    audit("logout", ip=client_ip(request))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/authcheck")
async def authcheck(request: Request):
    """Caddy forward_auth 子请求入口：会话有效 200，否则 401。"""
    if await run_in_threadpool(get_session_user, request):
        return PlainTextResponse("ok")
    raise HTTPException(status_code=401, detail="Not authenticated")


@router.get("/api/auth/me")
async def me(request: Request):
    # Cookie 会话与 App Bearer 设备 Token 并存（App 登录后用它确认身份）。
    # nickname/avatar_path 不在会话行里（security SELECT 列受限），按 uid 回查
    user = await run_in_threadpool(get_session_user, request)
    if not user:
        user = await run_in_threadpool(get_bearer_user, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    def _prof():
        row = users_store.get_profile(uid(user))
        return (row["nickname"] if row and row["nickname"] else "",
                row["avatar_path"] if row and row["avatar_path"] else "")

    nick, avatar = await run_in_threadpool(_prof)
    # totp_rc 意外损坏（非合法 JSON）不能让整个 /api/auth/me 500（9.9 复审 P2）
    try:
        rc_left = len(json.loads(user["totp_rc"])) if user["totp_rc"] else 0
    except (TypeError, ValueError):
        rc_left = 0
    return {"username": user["username"], "role": user["role"],
            "quota_bytes": user["quota_bytes"],
            "nickname": nick,
            "avatar_path": avatar,
            "totp_enabled": bool(user["totp_enabled"]),
            "totp_rc_left": rc_left}


class VerifyPwBody(BaseModel):
    password: str


def uid(user) -> int:
    """会话行/Bearer 行的统一 uid：两行都没有 id 列，只有 user_id
    （security.get_session_user/get_bearer_user 的 SELECT 列决定）。
    资料域（profile）等兄弟模块共用。"""
    return user["user_id"]


# 列名白名单（9.9 复审 P2）：_user_field 签名开放，调用方误传用户输入即成
# 注入口——只放行资料/口令三列，其余一律拒绝
_USER_COLS = frozenset({"password_hash", "nickname", "avatar_path"})


def _user_field(user, col: str):
    """会话行列不全（无 password_hash/nickname 等）：按 uid 回查白名单列。"""
    return users_store.get_allowed_field(uid(user), col)


@router.post("/api/auth/verify-password")
async def verify_password_ep(body: VerifyPwBody, request: Request,
                             user=Depends(require_user)):
    """高危操作前的本人口令复核（清空服务端备份等）：对当前会话/Bearer
    用户校验密码。不发新会话；限速防拿到的会话在线爆破口令。
    （函数名不与 security.verify_password 撞名——模块内 import 会被覆盖）"""
    if not allow_rate(f"verifypw:{uid(user)}", 5, 60):
        audit("verify_password_rate", user["username"], ip=client_ip(request))
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    stored = await run_in_threadpool(
        lambda: _user_field(user, "password_hash"))
    ok = await _verify_pw(body.password, stored)
    audit("verify_password", user["username"], "ok" if ok else "fail",
          client_ip(request))
    if not ok:
        raise HTTPException(status_code=401, detail="密码错误")
    return {"ok": True, "username": user["username"]}
