"""v3 管理 API：成员管理、邀请制建号、目录授权、审计动态流。

权限：除 /api/invites/redeem（凭邀请码匿名）外全部 require_admin。
"""
import re
import secrets
import time

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import SHARE_ROOT
from app.db import _db, _db_lock
from app.filetools import human_size
from app.security import audit, client_ip, hash_password, require_admin, require_user
from app.spaces import RESERVED_USERNAMES
from app.main import app


def _clean_username(name: str) -> str:
    n = (name or "").strip()
    if not n or len(n) > 64 or re.search(r"[\s\u0000-\u001f\u007f]", n):
        raise HTTPException(status_code=400, detail="用户名不能含空白/控制字符，且不超过 64 字符")
    if n.lower() in RESERVED_USERNAMES:
        raise HTTPException(status_code=400, detail="该用户名是保留名")
    if re.search(r"[/\\:]", n):
        raise HTTPException(status_code=400, detail="用户名不能含路径分隔符或冒号")
    return n


# ─── 成员管理 ─────────────────────────────────────────────


@app.get("/api/admin/users")
def admin_list_users(_admin=Depends(require_admin)):
    rows = _db().execute(
        "SELECT id, username, role, is_active, quota_bytes, created_at "
        "FROM users ORDER BY id").fetchall()
    return [{
        "id": r["id"], "username": r["username"], "role": r["role"],
        "is_active": bool(r["is_active"]), "quota_bytes": r["quota_bytes"],
        "quota_human": human_size(r["quota_bytes"]) if r["quota_bytes"] else "-",
        "created_at": time.strftime("%Y-%m-%d", time.localtime(r["created_at"])),
    } for r in rows]


class UserPatchBody(BaseModel):
    is_active: bool | None = None
    quota_bytes: int | None = None
    role: str | None = None


def _last_admin_guard(conn, uid: int):
    """禁止把最后一个管理员降级/停用。"""
    admins = conn.execute(
        "SELECT id FROM users WHERE role='admin' AND is_active=1").fetchall()
    if len(admins) == 1 and admins[0]["id"] == uid:
        raise HTTPException(status_code=400, detail="不能停用/降级最后一个管理员")


@app.patch("/api/admin/users/{uid}")
def admin_patch_user(uid: int, body: UserPatchBody, request: Request,
                     _admin=Depends(require_admin)):
    row = _db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    with _db_lock:
        if body.role is not None:
            if body.role not in ("admin", "member", "guest"):
                raise HTTPException(status_code=400, detail="非法角色")
            if body.role != "admin":
                _last_admin_guard(_db(), uid)
            _db().execute("UPDATE users SET role=? WHERE id=?", (body.role, uid))
        if body.is_active is not None:
            if not body.is_active:
                _last_admin_guard(_db(), uid)
                _db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            _db().execute("UPDATE users SET is_active=? WHERE id=?",
                          (1 if body.is_active else 0, uid))
        if body.quota_bytes is not None:
            _db().execute("UPDATE users SET quota_bytes=? WHERE id=?",
                          (body.quota_bytes or None, uid))
        _db().commit()
    audit("user_update", row["username"], f"active={body.is_active} role={body.role} "
          f"quota={body.quota_bytes}", client_ip(request))
    return {"ok": True}


class AdminCredBody(BaseModel):
    value: str


@app.post("/api/admin/users/{uid}/username")
def admin_set_username(uid: int, body: AdminCredBody, request: Request,
                       _admin=Depends(require_admin)):
    """管理员直接改成员用户名（同步私人目录改名；成员会话保持）。"""
    row = _db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    new_name = _clean_username(body.value)
    if new_name == row["username"]:
        raise HTTPException(status_code=400, detail="新用户名与当前相同")
    if _db().execute("SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)",
                     (new_name,)).fetchone():
        raise HTTPException(status_code=409, detail="该用户名已被占用")
    with _db_lock:
        _db().execute("UPDATE users SET username=? WHERE id=?", (new_name, uid))
        _db().commit()
    old_dir = SHARE_ROOT / "private" / row["username"]
    new_dir = SHARE_ROOT / "private" / new_name
    import os
    if old_dir.exists() and not os.path.normcase(str(old_dir)) == os.path.normcase(str(new_dir)):
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            old_dir.rename(new_dir)
        except OSError:
            audit("admin_dir_rename_fail", f"{row['username']} → {new_name}", ip=client_ip(request))
    audit("admin_rename_user", f"{row['username']} → {new_name}", ip=client_ip(request))
    return {"ok": True, "username": new_name}


@app.post("/api/admin/users/{uid}/password")
def admin_set_password(uid: int, body: AdminCredBody, request: Request,
                       _admin=Depends(require_admin)):
    """管理员直接重置成员密码（成员全部会话吊销，需用新密码重新登录）。"""
    row = _db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if len(body.value) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    with _db_lock:
        # 重置密码同时清除两步验证（成员手机丢失时的管理端恢复通道）
        _db().execute("UPDATE users SET password_hash=?, totp_secret=NULL, totp_enabled=0 WHERE id=?",
                      (hash_password(body.value), uid))
        _db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        _db().commit()
    audit("admin_reset_password", row["username"], ip=client_ip(request))
    return {"ok": True}


@app.get("/api/members")
def list_members(_user=Depends(require_user)):
    """在用成员名单（定向分享选择收件人用；用户名经动态流已互相可见，非敏感）。"""
    rows = _db().execute(
        "SELECT id, username, role FROM users WHERE is_active=1 AND id != ? ORDER BY username",
        (_user["user_id"],)).fetchall()
    return [{"id": r["id"], "username": r["username"], "role": r["role"]} for r in rows]


@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: int, request: Request, _admin=Depends(require_admin)):
    """删号（E7）：grants/会话 CASCADE；私人目录改名 .orphaned/<uid>-<名>/ 防复用占坑。"""
    if uid == _admin["user_id"]:
        raise HTTPException(status_code=400, detail="不能删除自己")
    row = _db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if row["role"] == "admin":
        _last_admin_guard(_db(), uid)
    with _db_lock:
        _db().execute("DELETE FROM users WHERE id=?", (uid,))
        _db().commit()
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


# ─── 邀请制建号 ───────────────────────────────────────────


class InviteBody(BaseModel):
    role: str = "member"
    hours: int = 72


@app.post("/api/admin/invites")
def admin_create_invite(body: InviteBody, request: Request, _admin=Depends(require_admin)):
    if body.role not in ("member", "guest"):
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


@app.get("/api/admin/invites")
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


@app.delete("/api/admin/invites/{code}")
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


@app.post("/api/invites/redeem")
def invite_redeem(body: RedeemBody, request: Request):
    """凭邀请码建号（匿名入口，走 /api/* 常规限速；防爆破复用按 code 记失败审计）。"""
    row = _db().execute("SELECT * FROM invites WHERE code=?", (body.code,)).fetchone()
    if not row or row["used_by"] is not None or row["expires_at"] < time.time():
        audit("invite_redeem_fail", body.code[:6], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="邀请码无效或已过期")
    username = _clean_username(body.username)
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    if _db().execute("SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)",
                     (username,)).fetchone():
        raise HTTPException(status_code=409, detail="该用户名已被占用")
    now = time.time()
    with _db_lock:
        cur = _db().execute(
            "INSERT INTO users(username, password_hash, created_at, role) VALUES(?,?,?,?) "
            "RETURNING id",
            (username, hash_password(body.password), now, row["role"]))
        uid = cur.fetchone()["id"]
        # 原子占位：仅当邀请未被使用且未过期时认领（并发 redeem 安全）
        claim = _db().execute(
            "UPDATE invites SET used_by=?, used_at=? WHERE code=? AND used_by IS NULL "
            "AND expires_at > ?", (uid, now, body.code, now))
        if not claim.rowcount:
            # 补偿回退（引擎中立：不等价于事务 rollback，但同样不留下孤儿账号）
            _db().execute("DELETE FROM users WHERE id=?", (uid,))
            _db().commit()
            audit("invite_redeem_fail", body.code[:6], ip=client_ip(request))
            raise HTTPException(status_code=403, detail="邀请码无效或已过期")
        _db().commit()
    (SHARE_ROOT / "private" / username).mkdir(parents=True, exist_ok=True)
    audit("invite_redeemed", username, row["role"], client_ip(request))
    return {"ok": True, "username": username, "role": row["role"]}


# ─── 公网访问地址（分享链接双地址用）───────────────────────


def _get_setting(key: str) -> str:
    row = _db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else ""


@app.get("/api/admin/public-url")
def get_public_url(_admin=Depends(require_admin)):
    return {"public_base_url": _get_setting("public_base_url")}


@app.get("/api/public-url")
def get_public_url_user(_user=Depends(require_user)):
    """公网访问地址：全员可读（分享/直传双地址复制用，非敏感）。"""
    return {"public_base_url": _get_setting("public_base_url")}


@app.post("/api/admin/public-url")
def set_public_url(body: dict, request: Request, _admin=Depends(require_admin)):
    url = (body.get("url") or "").strip().rstrip("/")
    if url and not url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="地址需以 http(s):// 开头")
    with _db_lock:
        _db().execute("INSERT INTO settings(key, value) VALUES('public_base_url', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (url,))
        _db().commit()
    audit("public_url_set", url, ip=client_ip(request))
    return {"ok": True, "public_base_url": url}


# ─── 目录授权 ─────────────────────────────────────────────


class GrantBody(BaseModel):
    user_id: int
    path: str
    can_read: bool = True
    can_write: bool = False
    expires_hours: int | None = None


@app.get("/api/admin/grants")
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


@app.post("/api/admin/grants")
def admin_create_grant(body: GrantBody, request: Request, _admin=Depends(require_admin)):
    """授权路径仅限 shared 空间（shared:<dir> 或裸相对路径），规范为 'shared/<dir>'。"""
    target = _db().execute("SELECT username FROM users WHERE id=?",
                           (body.user_id,)).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    p = body.path.strip().replace("\\", "/").strip("/")
    if p.startswith("shared:"):
        p = p[len("shared:"):]
    if p.startswith("private"):
        raise HTTPException(status_code=400, detail="私人空间不可授权")
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if not parts or any(s == ".." or s.startswith(".") for s in parts):
        raise HTTPException(status_code=400, detail="非法授权路径")
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
    audit("grant_change", target["username"], f"{vkey} r={body.can_read} w={body.can_write}",
          client_ip(request))
    return {"ok": True, "path": vkey}


@app.delete("/api/admin/grants/{gid}")
def admin_delete_grant(gid: int, request: Request, _admin=Depends(require_admin)):
    with _db_lock:
        cur = _db().execute("DELETE FROM grants WHERE id=?", (gid,))
        _db().commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="授权不存在")
    audit("grant_revoke", str(gid), ip=client_ip(request))
    return {"ok": True}


# ─── 家庭动态流（M4-lite：审计衍生）───────────────────────


@app.post("/api/activity/clear")
def activity_clear(_admin=Depends(require_admin)):
    """清空动态（软清空：记水位，审计历史仍完整保留）。"""
    import time as _t
    now = str(_t.time())
    with _db_lock:
        _db().execute("INSERT INTO settings(key, value) VALUES('activity_cleared_at', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))
        _db().commit()
    return {"ok": True}


@app.get("/api/activity")
def activity(limit: int = 50, user=Depends(require_user)):
    """动态流：审计日志过滤为家庭可见事件。private 路径仅本人可见（E7/E8 折衷）。
    同人同类动作在 10 分钟窗口内聚合为一条（count 表示合并条数）。"""
    cleared = _db().execute("SELECT value FROM settings WHERE key='activity_cleared_at'").fetchone()
    cutoff = float(cleared["value"]) if cleared else 0
    rows = _db().execute(
        "SELECT ts, username, action, target, detail FROM audit_log "
        "WHERE ts > ? ORDER BY id DESC LIMIT 500", (cutoff,)).fetchall()
    show = {"upload", "upload_instant", "mkdir", "share", "share_target", "share_download",
            "invite_redeemed", "trash", "restore", "move", "rename"}
    items = []
    max_items = max(1, min(limit, 100))
    username = user["username"]
    for r in rows:
        if r["action"] not in show or len(items) >= max_items:
            continue
        target = r["target"] or ""
        if target.startswith("private/") and not target.lower().startswith(
                "private/" + username.lower() + "/"):
            continue
        # 聚合：与当前组首（items[-1]）同人同类且落在 10 分钟窗口内
        if items:
            head = items[-1]
            if (head["username"] == r["username"] and head["action"] == r["action"]
                    and head["ts"] - r["ts"] <= 600):
                head["count"] += 1
                continue
        items.append({
            "ts": r["ts"], "username": r["username"], "action": r["action"],
            "target": target, "detail": r["detail"], "count": 1,
        })
    return items
