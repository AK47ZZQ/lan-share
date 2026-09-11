"""管理 API · 授权域（B2 自 admin.py 按域拆出）：邀请制建号（含匿名 redeem 原子认领）+ 目录授权 grants。URL 与行为不变；admin.py 仍是聚合门面。"""
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import SHARE_ROOT
from app.db import _db, _db_lock, transaction, is_unique_violation
from app.roles import GUEST, MEMBER
from app.security import (audit, client_ip, hash_password, require_admin)
from app.spaces import (_normcase_eq, invalidate_grants_cache)
from app.api.notify import (push_webhook)
from app.store import users as users_store
from app.services.accounts import (clean_username as _clean_username)


# 路由挂到本模块 router；由 admin.py 门面聚合（URL 不变）
router = APIRouter()


class _InviteClaimLost(Exception):
    """内部信号：邀请认领失败（已被并发使用/刚过期）→ 触发事务整体回滚。

    作为异常抛出而非直接 return，是为了让 `with transaction():` 能回滚掉
    同一事务里刚建出的账号（11 号复审 P2-6）。
    """




# ─── 邀请制建号 ───────────────────────────────────────────


class InviteBody(BaseModel):
    role: str = MEMBER
    hours: int = 72


@router.post("/api/admin/invites")
def admin_create_invite(body: InviteBody, request: Request, _admin=Depends(require_admin)):
    if body.role not in (MEMBER, GUEST):
        raise HTTPException(status_code=400, detail="邀请角色只能是 member/guest")
    if not 1 <= body.hours <= 24 * 30:
        raise HTTPException(status_code=400, detail="有效期 1 小时 ~ 30 天")
    code = secrets.token_urlsafe(12)
    with _db_lock:
        _db().execute("INSERT INTO invites(code, role, created_by, created_at, expires_at) "
                      "VALUES(?,?,?,?,?)",
                      (code, body.role, _admin["user_id"], time.time(),
                       time.time() + body.hours * 3600))
        _db().commit()
    audit("invite_created", body.role, f"{body.hours}h", client_ip(request))
    return {"ok": True, "code": code, "role": body.role,
            "expires_at": time.time() + body.hours * 3600}


@router.get("/api/admin/invites")
def admin_list_invites(_admin=Depends(require_admin)):
    rows = _db().execute(
        "SELECT code, role, created_at, expires_at, used_by, used_at "
        "FROM invites ORDER BY created_at DESC").fetchall()
    now = time.time()
    return [{
        "code": r["code"], "role": r["role"],
        "expired": r["expires_at"] < now, "used": r["used_by"] is not None,
        "expires_at": r["expires_at"],
    } for r in rows]


@router.delete("/api/admin/invites/{code}")
def admin_delete_invite(code: str, request: Request, _admin=Depends(require_admin)):
    with _db_lock:
        cur = _db().execute("DELETE FROM invites WHERE code=?", (code,))
        _db().commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="邀请不存在")
    audit("invite_revoked", code, ip=client_ip(request))
    return {"ok": True}


class RedeemBody(BaseModel):
    code: str
    username: str
    password: str


@router.post("/api/invites/redeem")
def invite_redeem(body: RedeemBody, request: Request):
    """凭邀请码建号（匿名入口，走 /api/* 常规限速；防爆破复用按 code 记失败审计）。"""
    row = _db().execute("SELECT * FROM invites WHERE code=?", (body.code,)).fetchone()
    if not row or row["used_by"] is not None or row["expires_at"] < time.time():
        audit("invite_redeem_fail", body.code[:6], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="邀请码无效或已过期")
    username = _clean_username(body.username)
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    if users_store.exists_username_ci(username):
        raise HTTPException(status_code=409, detail="该用户名已被占用")
    now = time.time()
    # scrypt 求值（约 100-300ms）放全局锁外，避免占住 _db_lock
    new_hash = hash_password(body.password)
    # private 目录先建：账号入库后再 mkdir 失败会留下残废账号（目录落在设备命名空间）
    try:
        (SHARE_ROOT / "private" / username).mkdir(parents=True, exist_ok=True)
    except OSError:
        raise HTTPException(status_code=500, detail="私人目录创建失败，请稍后重试")
    redeem_failed = False
    with _db_lock:
        try:
            # 建号与认领邀请包进同一事务（11 号复审 P2-6）：此前靠"插入后再补偿
            # 删除"，中途进程崩溃会留下**孤儿账号**（占了用户名、邀请却仍未使用）。
            # 真事务下 claim 失败 = 整体回滚，不再需要补偿删除。
            with transaction():
                uid = users_store.create(username, new_hash, row["role"], now)
                # 原子占位：仅当邀请未被使用且未过期时认领（并发 redeem 安全）
                claim = _db().execute(
                    "UPDATE invites SET used_by=?, used_at=? WHERE code=? AND used_by IS NULL "
                    "AND expires_at > ?", (uid, now, body.code, now))
                if not claim.rowcount:
                    raise _InviteClaimLost()
        except _InviteClaimLost:
            # audit() 内部也要取 _db_lock：本锁已改为 RLock（同一线程可重入），
            # 但为避免在未决写事务上再写审计，仍移到锁外执行（R1-P0-1 原意保留）
            redeem_failed = True
        except Exception as e:
            # 并发 redeem 撞同名：唯一约束兜底（占位查询与插入之间的窗口）。
            # 其它 DB 故障必须上抛——吞成 409 会误导用户且掩盖故障（R2-P2）
            if is_unique_violation(e):
                raise HTTPException(status_code=409, detail="该用户名已被占用")
            raise
    if redeem_failed:
        audit("invite_redeem_fail", body.code[:6], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="邀请码无效或已过期")
    audit("invite_redeemed", username, row["role"], client_ip(request))
    push_webhook("👋 新成员加入", f"{username}（{row['role']}）已激活账号")
    return {"ok": True, "username": username, "role": row["role"]}


# ─── 公网访问地址（分享链接双地址用）───────────────────────



class GrantBody(BaseModel):
    user_id: int
    path: str
    can_read: bool = True
    can_write: bool = False
    expires_hours: int | None = None


@router.get("/api/admin/grants")
def admin_list_grants(user_id: int | None = None, _admin=Depends(require_admin)):
    if user_id:
        rows = _db().execute("SELECT * FROM grants WHERE user_id=?", (user_id,)).fetchall()
    else:
        rows = _db().execute("SELECT * FROM grants").fetchall()
    return [{
        "id": r["id"], "user_id": r["user_id"], "path": r["path"],
        "can_read": bool(r["can_read"]), "can_write": bool(r["can_write"]),
        "expires_at": r["expires_at"],
    } for r in rows]


@router.post("/api/admin/grants")
def admin_create_grant(body: GrantBody, request: Request, _admin=Depends(require_admin)):
    """授权路径仅限 shared 空间（shared:<dir> 或裸相对路径），规范为 'shared/<dir>'。"""
    target = users_store.get(body.user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    p = body.path.strip().replace("\\", "/").strip("/")
    if p.startswith("shared:"):
        p = p[len("shared:"):]
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if parts and _normcase_eq(parts[0], "private"):
        raise HTTPException(status_code=400, detail="私人空间不可授权")
    if not parts or any(s == ".." or s.startswith(".") for s in parts):
        raise HTTPException(status_code=400, detail="非法授权路径")
    # v0.20：UI 端只提供一级目录选择（子目录随父级授权自动继承）；
    # 服务端保留深路径兼容——目录移动/改名同步 grants 时会产生嵌套路径，
    # 由 spaces.py 直接写库，不走本端点
    vkey = "shared/" + "/".join(parts)
    if not (SHARE_ROOT / "/".join(parts)).is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")
    expires = time.time() + body.expires_hours * 3600 if body.expires_hours else None
    with _db_lock:
        _db().execute("INSERT INTO grants(user_id, path, can_read, can_write, created_by, "
                      "created_at, expires_at) VALUES(?,?,?,?,?,?,?) "
                      "ON CONFLICT(user_id, path) DO UPDATE SET can_read=excluded.can_read, "
                      "can_write=excluded.can_write, expires_at=excluded.expires_at",
                      (body.user_id, vkey, 1 if body.can_read else 0,
                       1 if body.can_write else 0, _admin["user_id"], time.time(), expires))
        _db().commit()
    invalidate_grants_cache(body.user_id)
    audit("grant_change", target["username"], f"{vkey} r={body.can_read} w={body.can_write}",
          client_ip(request))
    return {"ok": True, "path": vkey}


@router.delete("/api/admin/grants/{gid}")
def admin_delete_grant(gid: int, request: Request, _admin=Depends(require_admin)):
    with _db_lock:
        owner = _db().execute("SELECT user_id FROM grants WHERE id=?", (gid,)).fetchone()
        cur = _db().execute("DELETE FROM grants WHERE id=?", (gid,))
        _db().commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="授权不存在")
    invalidate_grants_cache(owner["user_id"] if owner else None)
    audit("grant_revoke", str(gid), ip=client_ip(request))
    return {"ok": True}


# ─── 家庭动态流（M4-lite：审计衍生）───────────────────────

