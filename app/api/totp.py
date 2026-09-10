"""两步验证（TOTP）API（07 小尾巴：从 auth.py 按域拆出）。

登录第二步（/api/login/totp，认证器不可用时可用一次性恢复码顶替）与
开通/恢复码重生成/关闭管理（/api/auth/totp/*）。恢复码库内只存 SHA256，
明文仅在生成响应中出现一次，用一枚少一枚。
"""
import hashlib
import hmac
import json
import re
import secrets
import time

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.db import _db_lock
from app.security import (audit, clear_login_fails, client_ip, is_public_entry,
                          record_login_fail, require_user)
from app.services.passwords import verify_pw as _verify_pw
from app.services.passwords import verify_pw_with_dummy as _verify_pw_with_dummy
from app.store import users as users_store
from app.api.auth import login_gate, set_session_cookie

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


class TotpLoginBody(BaseModel):
    username: str
    password: str
    code: str


class TotpCodeBody(BaseModel):
    code: str


class TotpDisableBody(BaseModel):
    old_password: str


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


@router.post("/api/login/totp")
async def login_totp(body: TotpLoginBody, request: Request, response: Response):
    """登录第二步：密码 + 动态验证码（复用按账号防爆破；验证码错误同样计入锁定）。
    认证器不可用时可用开通时下发的一次性恢复码顶替（用一枚销一枚）。"""
    pub = is_public_entry(request)
    row = await run_in_threadpool(login_gate, body.username, request)
    ok = await _verify_pw_with_dummy(body.password, row["password_hash"] if row else None)
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
        users_store.set_totp_last_step(row["id"], matched)
    elif _RC_RE.match(rc):
        # 恢复码路径：SHA256 命中库存即消费（库内只存哈希，用一枚少一枚）
        hashes = json.loads(row["totp_rc"]) if row["totp_rc"] else []
        h = _rc_hash(rc)
        if h not in hashes:
            _fail()
            raise HTTPException(status_code=401, detail="恢复码无效或已用掉")
        hashes.remove(h)
        users_store.set_totp_recovery_codes(row["id"], json.dumps(hashes))
        used_rc = True
    else:
        _fail()
        raise HTTPException(status_code=401, detail="动态验证码错误")

    clear_login_fails(body.username, pub)
    audit("login", body.username, "totp_rc" if used_rc else "totp", client_ip(request))
    set_session_cookie(response, request, row["id"])
    return {"ok": True, "username": row["username"]}


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


@router.post("/api/auth/totp/setup")
def totp_setup(user=Depends(require_user)):
    """生成 TOTP 密钥（未验证前不生效），返回密钥与 otpauth:// URI（前端转二维码）。"""
    row = users_store.get(user["user_id"])
    if row["totp_enabled"]:
        raise HTTPException(status_code=400, detail="两步验证已启用")
    secret = pyotp.random_base32()
    with _db_lock:
        users_store.set_totp_secret(user["user_id"], secret)
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=row["username"], issuer_name="LAN Share")
    return {"secret": secret, "otpauth": uri}


@router.post("/api/auth/totp/enable")
def totp_enable(body: TotpCodeBody, user=Depends(require_user)):
    """用动态验证码确认开通（证明认证器已正确录入）。开通时生成一次性恢复码，
    明文仅本响应返回一次，供认证器不可用时顶替第二因子。"""
    row = users_store.get(user["user_id"])
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
        users_store.enable_totp(user["user_id"], rc_json)
    audit("totp_enabled", row["username"], ip="")
    return {"ok": True, "recovery_codes": codes}


@router.post("/api/auth/totp/rc")
async def totp_rc_regen(request: Request, body: TotpRcBody, user=Depends(require_user)):
    """重新生成恢复码（需原密码）：旧码全部作废。用于码没用过却弄丢了的情况。"""
    row = users_store.get(user["user_id"])
    if not row["totp_enabled"]:
        raise HTTPException(status_code=400, detail="两步验证未启用")
    if not await _verify_pw(body.old_password, row["password_hash"]):
        audit("totp_rc_regen_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="原密码错误")
    codes, rc_json = _gen_recovery_codes()
    with _db_lock:
        users_store.set_totp_recovery_codes(user["user_id"], rc_json)
    audit("totp_rc_regen", row["username"], ip=client_ip(request))
    return {"ok": True, "recovery_codes": codes}


@router.post("/api/auth/totp/disable")
async def totp_disable(request: Request, body: TotpDisableBody, user=Depends(require_user)):
    """关闭两步验证：需原密码确认（会话被劫持时不能随手关掉）。"""
    row = users_store.get(user["user_id"])
    if not await _verify_pw(body.old_password, row["password_hash"]):
        audit("totp_disable_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="原密码错误")
    with _db_lock:
        users_store.clear_totp(user["user_id"])
    audit("totp_disabled", row["username"], ip=client_ip(request))
    return {"ok": True}
