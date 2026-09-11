"""管理 API · 成员域（B2 自 admin.py 按域拆出）：成员列表/资料与凭据重置/停用/删除/成员名录。URL 与行为不变；admin.py 仍是聚合门面。"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import SHARE_ROOT
from app.db import _db, _db_lock, transaction
from app.filetools import human_size
from app.roles import ADMIN, ALL_ROLES
from app.security import (audit, client_ip, hash_password, require_admin, require_user)
from app.spaces import (invalidate_grants_cache)
from app.store import devices as devices_store
from app.store import users as users_store
from app.services.accounts import (clean_username as _clean_username,
                                   rename_user_everywhere)

# 路由挂到本模块 router；由 admin.py 门面聚合（URL 不变）
router = APIRouter()


@router.get("/api/admin/users")
def admin_list_users(_admin=Depends(require_admin)):
    # 06-P2：不把 webdav_password_hash（scrypt 哈希）拉进应用层，SQL 层直接转布尔。
    # 原实现 SELECT 完整哈希仅为 bool() 判断，等于把口令材料多暴露一层。
    rows = users_store.list_for_admin()
    return [{
        "id": r["id"], "username": r["username"],
        "nickname": r["nickname"] if "nickname" in r.keys() else "",
        "avatar_path": r["avatar_path"] if "avatar_path" in r.keys() else "",
        "role": r["role"],
        "is_active": bool(r["is_active"]), "quota_bytes": r["quota_bytes"],
        "quota_human": human_size(r["quota_bytes"]) if r["quota_bytes"] else "-",
        "created_at": time.strftime("%Y-%m-%d", time.localtime(r["created_at"])),
        "webdav_set": bool(r["webdav_set"]),
        "totp_enabled": bool(r["totp_enabled"]),
    } for r in rows]


class UserPatchBody(BaseModel):
    is_active: bool | None = None
    quota_bytes: int | None = None
    role: str | None = None


def _last_admin_guard(uid: int):
    """禁止把最后一个管理员降级/停用。"""
    admins = users_store.list_active_admin_ids()
    if len(admins) == 1 and admins[0] == uid:
        raise HTTPException(status_code=400, detail="不能停用/降级最后一个管理员")


@router.patch("/api/admin/users/{uid}")
def admin_patch_user(uid: int, body: UserPatchBody, request: Request,
                     _admin=Depends(require_admin)):
    row = users_store.get(uid)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 最后管理员守卫与负配额校验都提到写事务之前（R1-P0-1 / R2-P1 同类）：
    # 锁内"先 UPDATE 后 raise"会把连接留在持有 RESERVED 锁的未决事务里
    if (body.role is not None and body.role != ADMIN) or body.is_active is False:
        _last_admin_guard(uid)
    if body.quota_bytes is not None and body.quota_bytes < 0:
        raise HTTPException(status_code=400, detail="配额不能为负数")
    # 三处改动同一事务（PG 真回滚；SQLite 由 transaction() 统一收束）。
    # 中途抛错（如非法角色）不留"改了一半"的账号。
    with _db_lock, transaction():
        if body.role is not None:
            if body.role not in ALL_ROLES:
                raise HTTPException(status_code=400, detail="非法角色")
            # 锁内复查守卫（R2-P2）：两个并发 PATCH 降级最后两名 admin 可同时
            # 通过锁外检查，双双提交后 0 admin
            if body.role != ADMIN:
                _last_admin_guard(uid)
            users_store.set_role(uid, body.role)
        if body.is_active is not None:
            if not body.is_active:
                _last_admin_guard(uid)
                # 停用即吊销全部登录态：Cookie 会话 + App 设备 Token
                #（否则重新启用后旧设备 Token 全部复活）
                _db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                devices_store.delete_for_user(uid)
            users_store.set_active(uid, body.is_active)
        if body.quota_bytes is not None:
            users_store.set_quota(uid, body.quota_bytes or None)
    invalidate_grants_cache(uid)   # 角色/状态变更影响可见性，失效 grants 缓存
    audit("user_update", row["username"], f"active={body.is_active} role={body.role} "
          f"quota={body.quota_bytes}", client_ip(request))
    return {"ok": True}


class AdminCredBody(BaseModel):
    value: str


@router.post("/api/admin/users/{uid}/username")
def admin_set_username(uid: int, body: AdminCredBody, request: Request,
                       _admin=Depends(require_admin)):
    """管理员直接改成员用户名（同步私人目录改名；成员会话保持）。"""
    row = users_store.get(uid)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    new_name = _clean_username(body.value)
    if new_name == row["username"]:
        raise HTTPException(status_code=400, detail="新用户名与当前相同")
    rename_user_everywhere(uid, row["username"], new_name, ADMIN,
                           client_ip(request))
    audit("admin_rename_user", f"{row['username']} → {new_name}", ip=client_ip(request))
    return {"ok": True, "username": new_name}


@router.post("/api/admin/users/{uid}/password")
def admin_set_password(uid: int, body: AdminCredBody, request: Request,
                       _admin=Depends(require_admin)):
    """管理员直接重置成员密码（成员全部会话吊销，需用新密码重新登录）。"""
    row = users_store.get(uid)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if len(body.value) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    # scrypt 求值（约 100-300ms）放全局锁外，避免占住 _db_lock 让并发会话续期排队
    new_hash = hash_password(body.value)
    # 重置密码 = 凭据疑似泄露的恢复动作：清 TOTP/恢复码 + 吊销全部 Cookie 会话
    # 与 App 设备 Token（Bearer 90 天滑动，不吊销则接管恢复不彻底）。
    # 三者必须同一事务：只改了密码却没吊销会话 = 接管不彻底（P2-6）。
    with _db_lock, transaction():
        users_store.reset_password_full(uid, new_hash)
        _db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        devices_store.delete_for_user(uid)
    audit("admin_reset_password", row["username"], ip=client_ip(request))
    return {"ok": True}


@router.post("/api/admin/users/{uid}/totp-disable")
def admin_totp_disable(uid: int, request: Request, _admin=Depends(require_admin)):
    """仅解除成员的两步验证（含恢复码）：不动密码、不吊销会话。
    成员认证器不可用、又无法自助关闭（关闭需原密码）时的管理端精准通道。"""
    row = users_store.get(uid)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not row["totp_enabled"] and not row["totp_rc"]:
        raise HTTPException(status_code=400, detail="该账号未启用两步验证")
    with _db_lock:
        users_store.clear_totp(uid)
        _db().commit()
    audit("admin_totp_disable", row["username"], ip=client_ip(request))
    return {"ok": True}


@router.post("/api/admin/users/{uid}/webdav-password")
def admin_set_webdav_password(uid: int, body: AdminCredBody, request: Request,
                              _admin=Depends(require_admin)):
    """管理员重置/清除成员的 WebDAV 独立密码。value 空 = 清除（回退登录密码挂载）。
    只动 WebDAV 凭据，不吊销网页会话（与重置登录密码不同）。"""
    row = users_store.get(uid)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.value:
        if len(body.value) < 8:
            raise HTTPException(status_code=400, detail="密码至少 8 位")
        new_hash = hash_password(body.value)
    else:
        new_hash = None
    with _db_lock:
        users_store.set_webdav_password_hash(uid, new_hash)
        _db().commit()
    audit("admin_webdav_password" if new_hash else "admin_webdav_password_clear",
          row["username"], ip=client_ip(request))
    return {"ok": True, "set": bool(new_hash)}


@router.get("/api/members")
def list_members(_user=Depends(require_user)):
    """在用成员名单（定向分享选择收件人用；用户名经动态流已互相可见，非敏感）。"""
    rows = users_store.list_active_except(_user["user_id"])
    return [{"id": r["id"], "username": r["username"], "role": r["role"]} for r in rows]


@router.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: int, request: Request, _admin=Depends(require_admin)):
    """删号（E7）：grants/会话 CASCADE；私人目录改名 .orphaned/<uid>-<名>/ 防复用占坑。"""
    if uid == _admin["user_id"]:
        raise HTTPException(status_code=400, detail="不能删除自己")
    row = users_store.get(uid)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if row["role"] == ADMIN:
        _last_admin_guard(uid)
    with _db_lock, transaction():
        # sessions 显式清理（红队 P3-1）：SQLite 该表未声明 CASCADE，残留行
        # 只能等过期清理（JOIN users 使其本就不可用，但占库）
        _db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        users_store.delete(uid)
    invalidate_grants_cache(uid)
    # 头像清理（v0.17）：头像按 uid 固定文件名，删号即成孤儿文件
    try:
        (SHARE_ROOT / ".avatars" / f"{uid}.jpg").unlink(missing_ok=True)
    except OSError:
        pass
    private_dir = SHARE_ROOT / "private" / row["username"]
    if private_dir.exists():
        orphan_root = SHARE_ROOT / ".orphaned"
        orphan_root.mkdir(exist_ok=True)
        try:
            private_dir.rename(orphan_root / f"{uid}-{row['username']}")
        except OSError:
            audit("orphan_rename_fail", row["username"], ip=client_ip(request))
    audit("user_deleted", row["username"], ip=client_ip(request))
    return {"ok": True}
