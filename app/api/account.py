"""账号安全 API（07 小尾巴：从 auth.py 按域拆出）。

改密码（连坐吊销旧会话/设备 Token）、改用户名（rename_user_everywhere
全端跟随）、WebDAV 独立密码（与登录密码隔离的挂载凭据）。
密码确认失败一律计入按入口防爆破锁定（与登录同策略，防跨入口锁人）。
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.db import _db, _db_lock, transaction
from app.security import (audit, client_ip, hash_password, is_public_entry,
                          login_locked_remaining, record_login_fail,
                          require_user, verify_password)
from app.services.accounts import clean_username, rename_user_everywhere
from app.store import users as users_store
from app.api.auth import set_session_cookie

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


@router.post("/api/auth/password")
async def change_password(request: Request, response: Response, body: PasswordBody,
                          user=Depends(require_user)):
    row = users_store.get(user["user_id"])
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
    # 改密 + 吊销旧会话/设备必须同一事务（P2-6）：只改了密码却没吊销
    # 旧 Token，等于"改密不生效"（旧凭据仍可用）。
    # 同步块进线程池（P2-2）：事件循环内不做 PG 往返
    def _persist_password() -> None:
        with _db_lock, transaction():
            users_store.set_password_hash(row["id"], new_hash)
            # 改密后吊销全部旧会话与设备 Token（与 admin 重置同语义）
            _db().execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            _db().execute("DELETE FROM device_tokens WHERE user_id=?", (row["id"],))

    await run_in_threadpool(_persist_password)
    audit("password_change", row["username"], ip=client_ip(request))
    set_session_cookie(response, request, row["id"])
    return {"ok": True}


class UsernameBody(BaseModel):
    new_username: str
    password: str


@router.post("/api/auth/username")
async def change_username(request: Request, body: UsernameBody,
                          user=Depends(require_user)):
    row = users_store.get(user["user_id"])
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
    new_name = clean_username(body.new_username.strip())
    if new_name == row["username"]:
        raise HTTPException(status_code=400, detail="新用户名与当前相同")
    rename_user_everywhere(row["id"], row["username"], new_name, "user",
                           client_ip(request))
    # 会话按 user_id 关联，改名不影响登录态；旧用户名的防爆破计数自然失效
    audit("username_change", row["username"], f"→ {new_name}", client_ip(request))
    return {"ok": True, "username": new_name}


# ─── WebDAV 独立密码（与登录密码隔离的挂载凭据）────────────

class WebDavPasswordBody(BaseModel):
    current_password: str
    webdav_password: str = ""   # 空串 = 清除（回退为登录密码挂载）


@router.get("/api/auth/webdav-password")
def webdav_password_status(user=Depends(require_user)):
    """只暴露是否已设置，绝不回传哈希。"""
    return {"set": bool(users_store.get_webdav_password_hash(user["user_id"]))}


@router.post("/api/auth/webdav-password")
async def set_webdav_password(request: Request, body: WebDavPasswordBody,
                              user=Depends(require_user)):
    row = users_store.get(user["user_id"])
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
        users_store.set_webdav_password_hash(row["id"], new_hash)
    audit("webdav_password_set" if new_hash else "webdav_password_clear",
          row["username"], ip=client_ip(request))
    return {"ok": True, "set": bool(new_hash)}
