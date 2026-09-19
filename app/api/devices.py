"""设备 Token 与配对码 API（07 小尾巴：从 auth.py 按域拆出）。

Cookie 会话换长效设备 Token（App 专用，APP-DESIGN §3 / 里程碑 M-A0）；
配对码登录：已登录 Web 端生成一次性 6 位码，手机免密换设备 Token
（60 秒级时效 + 一码一用 + 按 IP 限速防爆破；配对码自 v3.57 起落
`store/ttl_state` 表，跨重启与跨进程有效）。
"""
import re
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import DEVICE_TOKEN_MAX, DEVICE_TOKEN_TTL
from app.db import _db_lock
from app.security import (_sha, allow_rate, audit, client_ip, is_public_entry,
                          require_user)
from app.store import devices as devices_store
from app.store import ttl_state
from app.store import users as users_store

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


class DeviceTokenBody(BaseModel):
    name: str


@router.post("/api/auth/token")
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
    if devices_store.count_for_user(user_id) >= DEVICE_TOKEN_MAX:
        raise HTTPException(status_code=400,
                            detail=f"设备数已达上限（{DEVICE_TOKEN_MAX}），请先吊销不用的设备")
    token = secrets.token_urlsafe(32)
    now = time.time()
    # 轮换判定 + UPDATE/INSERT 必须整体在 _db_lock 内（SELECT-then-write 竞态面）
    with _db_lock:
        existing = devices_store.find_id_by_name(user_id, name)
        if existing is not None:
            devices_store.rotate(existing, _sha(token), now, DEVICE_TOKEN_TTL)
            device_id = existing
        else:
            try:
                device_id = devices_store.insert(user_id, name, _sha(token),
                                                 now, DEVICE_TOKEN_TTL)
            except Exception:
                # token_hash UNIQUE 极端碰撞：换一枚重试一次
                # （裸 Exception：PG 生产库抛的是 psycopg 的唯一约束异常，
                #   只捕 sqlite3.IntegrityError 在 PG 下会 500）
                token = secrets.token_urlsafe(32)
                device_id = devices_store.insert(user_id, name, _sha(token),
                                                 now, DEVICE_TOKEN_TTL)
        devices_store.commit_if_standalone()
    return token, device_id


@router.get("/api/auth/devices")
def list_devices(request: Request, user=Depends(require_user)):
    """本账号的设备列表（他人不可见）。current 标记本次请求所用的 Bearer 设备。"""
    auth = request.headers.get("authorization", "")
    cur_hash = _sha(auth[7:].strip()) if auth.startswith("Bearer ") else None
    rows = devices_store.list_for_user(user["user_id"])
    return {"devices": [{
        "id": r["id"], "name": r["name"],
        "created_at": r["created_at"], "last_seen": r["last_seen"],
        "current": bool(cur_hash and r["token_hash"] == cur_hash),
    } for r in rows]}


@router.delete("/api/auth/devices/{device_id}")
def revoke_device(device_id: int, request: Request, user=Depends(require_user)):
    """吊销本人设备（被吊销端下次请求即 401）。"""
    row = devices_store.get_for_user(device_id, user["user_id"])
    if not row:
        raise HTTPException(status_code=404, detail="设备不存在")
    devices_store.delete(device_id)
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
# v3.57 外置到 ttl_state 表（scope=pair_code）：配对是"另一台设备正在输入这枚
# 码"的用户在途动作，进程内字典会让服务端重启把正在进行的配对直接打断，且多
# worker 下生成的码在另一进程里claim不到。原子消费由 ttl_state.take() 保证。


def reset_pair_codes() -> None:
    """清空配对码（测试专用接口，07 审查批次 2 测试解钉）。

    外置后它不再是"进程态清理"而是真删库行——测试要的语义没变（上一条不干扰
    下一条），实现从 dict.clear() 换成**按 scope 精确清空**。2026-09-19 对抗
    审查 F4：此前误用 clear_all() 把整张 ttl_state（zip 票据/分享票据/审计位/
    打包位）一并清掉——函数名撒谎，且测试隔离会掩盖跨域票据的共存场景。
    """
    ttl_state.clear_scope(ttl_state.SCOPE_PAIR_CODE)


@router.post("/api/pair/code")
async def pair_code_create(request: Request, user=Depends(require_user)):
    """已登录方（Web 会话或 Bearer）生成一次性配对码；同一账号只保留最新一枚。"""
    if not allow_rate(f"paircode:{user['user_id']}", 10, 60):
        raise HTTPException(status_code=429, detail="生成过于频繁，请稍后再试")
    code = f"{secrets.randbelow(1_000_000):06d}"
    # 顶掉本账号旧码再写新码。两条独立写入（不包事务，理由见
    # store/ttl_state.put_if_absent docstring）：并发下最坏是短时间内多留一枚
    # 旧码，而 claim 要求码值精确匹配 + 一码一用，不构成越权面。
    ttl_state.delete_scope_value(ttl_state.SCOPE_PAIR_CODE, str(user["user_id"]))
    ttl_state.put(ttl_state.SCOPE_PAIR_CODE, code,
                  str(user["user_id"]), _PAIR_TTL)
    audit("pair_code_create", user["username"], ip=client_ip(request))
    return {"code": code, "expires_in": int(_PAIR_TTL)}


@router.post("/api/pair/claim")
async def pair_claim(request: Request, body: PairClaimBody):
    """手机提交配对码换设备 Token（免账号密码）。凭据规则与 /api/auth/token
    完全一致（同名轮换、上限、TTL），签出的 Token 也能随时在 Web 端吊销。"""
    code = body.code.strip().replace(" ", "")
    ip = client_ip(request)
    if not (code.isdigit() and len(code) == 6):
        raise HTTPException(status_code=400, detail="配对码格式错误")
    # 公网侧全体共享一个 IP 桶（出口恒为隧道），阈值放大防单人占满卡死全体配对；
    # 爆破面由 6 位码 + 一码一用 + 3min TTL 兜底（R1-P3-6）
    pub = is_public_entry(request)
    if not allow_rate(f"pairclaim:{ip}", 30 if pub else 5, 60):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    now = time.time()
    # 原子消费（一码一用）：take() 用 DELETE...RETURNING 实现，并发/多进程下
    # 只有一个请求能拿到值——比原先"进程锁内 pop"的保证更强，且过期码不可能
    # 被误消费（条件里带 expires_at>now），也就不再需要 claim 路径的懒清理。
    hit = ttl_state.take(ttl_state.SCOPE_PAIR_CODE, code, now)
    if hit is None:
        audit("pair_claim_fail", ip=ip)
        raise HTTPException(status_code=401, detail="配对码无效或已过期")
    user_id = int(hit)
    row = users_store.get(user_id)
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
