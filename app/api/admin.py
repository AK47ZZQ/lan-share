"""v3 管理 API：成员管理、邀请制建号、目录授权、审计动态流。

权限：除 /api/invites/redeem（凭邀请码匿名）外全部 require_admin。
"""
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.config import SHARE_ROOT
from app.db import _db, _db_lock, is_unique_violation
from app.filetools import human_size
from app.security import (audit, client_ip, hash_password, require_admin, require_user,
                          reset_netloc_cache)
from app.spaces import (RESERVED_USERNAMES, _normcase_eq, invalidate_grants_cache,
                        visible)
from app.api.notify import get_webhook_url, push_webhook, webhook_send
from app.store.settings import (ACTIVITY_CLEARED_AT, NOTIFY_WEBHOOK_URL,
                                PUBLIC_BASE_URL, PUBLIC_BASE_URL_ALT,
                                get_setting, set_setting)
from app.main import app


def _clean_username(name: str) -> str:
    from app.filetools import _RESERVED_NAME
    n = (name or "").strip()
    if not n or len(n) > 64 or re.search(r"[\s\u0000-\u001f\u007f]", n):
        raise HTTPException(status_code=400, detail="用户名不能含空白/控制字符，且不超过 64 字符")
    if n.lower() in RESERVED_USERNAMES:
        raise HTTPException(status_code=400, detail="该用户名是保留名")
    if re.search(r"[/\\:]", n):
        raise HTTPException(status_code=400, detail="用户名不能含路径分隔符或冒号")
    if _RESERVED_NAME.match(n):
        raise HTTPException(status_code=400, detail="该用户名是 Windows 设备保留名")
    return n


# ─── 成员管理 ─────────────────────────────────────────────


@app.get("/api/admin/users")
def admin_list_users(_admin=Depends(require_admin)):
    rows = _db().execute(
        "SELECT id, username, role, is_active, quota_bytes, created_at, webdav_password_hash, "
        "totp_enabled, nickname, avatar_path FROM users ORDER BY id").fetchall()
    return [{
        "id": r["id"], "username": r["username"],
        "nickname": r["nickname"] if "nickname" in r.keys() else "",
        "avatar_path": r["avatar_path"] if "avatar_path" in r.keys() else "",
        "role": r["role"],
        "is_active": bool(r["is_active"]), "quota_bytes": r["quota_bytes"],
        "quota_human": human_size(r["quota_bytes"]) if r["quota_bytes"] else "-",
        "created_at": time.strftime("%Y-%m-%d", time.localtime(r["created_at"])),
        "webdav_set": bool(r["webdav_password_hash"]),
        "totp_enabled": bool(r["totp_enabled"]),
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
    # 最后管理员守卫与负配额校验都提到写事务之前（R1-P0-1 / R2-P1 同类）：
    # 锁内"先 UPDATE 后 raise"会把连接留在持有 RESERVED 锁的未决事务里
    if (body.role is not None and body.role != "admin") or body.is_active is False:
        _last_admin_guard(_db(), uid)
    if body.quota_bytes is not None and body.quota_bytes < 0:
        raise HTTPException(status_code=400, detail="配额不能为负数")
    with _db_lock:
        if body.role is not None:
            if body.role not in ("admin", "member", "guest"):
                raise HTTPException(status_code=400, detail="非法角色")
            # 锁内复查守卫（R2-P2）：两个并发 PATCH 降级最后两名 admin 可同时
            # 通过锁外检查，双双提交后 0 admin
            if body.role != "admin":
                _last_admin_guard(_db(), uid)
            _db().execute("UPDATE users SET role=? WHERE id=?", (body.role, uid))
        if body.is_active is not None:
            if not body.is_active:
                _last_admin_guard(_db(), uid)
                # 停用即吊销全部登录态：Cookie 会话 + App 设备 Token
                #（否则重新启用后旧设备 Token 全部复活）
                _db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                _db().execute("DELETE FROM device_tokens WHERE user_id=?", (uid,))
            _db().execute("UPDATE users SET is_active=? WHERE id=?",
                          (1 if body.is_active else 0, uid))
        if body.quota_bytes is not None:
            _db().execute("UPDATE users SET quota_bytes=? WHERE id=?",
                          (body.quota_bytes or None, uid))
        _db().commit()
    invalidate_grants_cache(uid)   # 角色/状态变更影响可见性，失效 grants 缓存
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
    with _db_lock:
        # 占用检查与 UPDATE 同锁（检查在锁外时并发两次改名可双双通过、第二个撞唯一约束 500）
        if _db().execute("SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)",
                         (new_name,)).fetchone():
            raise HTTPException(status_code=409, detail="该用户名已被占用")
        _db().execute("UPDATE users SET username=? WHERE id=?", (new_name, uid))
        _db().commit()
    from app.api.system import rename_album_owner
    rename_album_owner(row["username"], new_name)   # 精选专辑归属跟随（R2-P2-3）
    old_dir = SHARE_ROOT / "private" / row["username"]
    new_dir = SHARE_ROOT / "private" / new_name
    import os
    if old_dir.exists() and not os.path.normcase(str(old_dir)) == os.path.normcase(str(new_dir)):
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            old_dir.rename(new_dir)
        except OSError:
            # 目录改名失败时回滚用户名：否则 users.username 与私人目录永久错位，
            # 用户私人数据"消失"（P2 边界）。DB 已提交但目录占用可重试整个改名。
            # 专辑归属同步翻回（R3-P1：rename_album_owner 已在尝试前执行）
            with _db_lock:
                _db().execute("UPDATE users SET username=? WHERE id=?", (row["username"], uid))
                _db().commit()
            from app.api.system import rename_album_owner
            rename_album_owner(new_name, row["username"])
            audit("admin_dir_rename_fail", f"{row['username']} → {new_name}", ip=client_ip(request))
            raise HTTPException(status_code=500,
                                detail="私人目录改名失败（可能正被占用），已回滚用户名，请稍后重试")
    # 直传子目录同步改名（否则旧 incoming/<旧名>/ 文件对本人不可见）
    old_inc = SHARE_ROOT / "incoming" / row["username"]
    new_inc = SHARE_ROOT / "incoming" / new_name
    if old_inc.exists() and not os.path.normcase(str(old_inc)) == os.path.normcase(str(new_inc)):
        try:
            old_inc.rename(new_inc)
        except OSError:
            audit("admin_incoming_rename_fail", f"{row['username']} → {new_name}",
                  ip=client_ip(request))
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
    # scrypt 求值（约 100-300ms）放全局锁外，避免占住 _db_lock 让并发会话续期排队
    new_hash = hash_password(body.value)
    with _db_lock:
        # 重置密码 = 凭据疑似泄露的恢复动作：清 TOTP/恢复码 + 吊销全部 Cookie 会话
        # 与 App 设备 Token（Bearer 90 天滑动，不吊销则接管恢复不彻底）
        _db().execute("UPDATE users SET password_hash=?, totp_secret=NULL, totp_enabled=0, totp_rc=NULL, totp_last_step=NULL WHERE id=?",
                      (new_hash, uid))
        _db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        _db().execute("DELETE FROM device_tokens WHERE user_id=?", (uid,))
        _db().commit()
    audit("admin_reset_password", row["username"], ip=client_ip(request))
    return {"ok": True}


@app.post("/api/admin/users/{uid}/totp-disable")
def admin_totp_disable(uid: int, request: Request, _admin=Depends(require_admin)):
    """仅解除成员的两步验证（含恢复码）：不动密码、不吊销会话。
    成员认证器不可用、又无法自助关闭（关闭需原密码）时的管理端精准通道。"""
    row = _db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not row["totp_enabled"] and not row["totp_rc"]:
        raise HTTPException(status_code=400, detail="该账号未启用两步验证")
    with _db_lock:
        _db().execute("UPDATE users SET totp_secret=NULL, totp_enabled=0, totp_rc=NULL, "
                      "totp_last_step=NULL WHERE id=?", (uid,))
        _db().commit()
    audit("admin_totp_disable", row["username"], ip=client_ip(request))
    return {"ok": True}


@app.post("/api/admin/users/{uid}/webdav-password")
def admin_set_webdav_password(uid: int, body: AdminCredBody, request: Request,
                              _admin=Depends(require_admin)):
    """管理员重置/清除成员的 WebDAV 独立密码。value 空 = 清除（回退登录密码挂载）。
    只动 WebDAV 凭据，不吊销网页会话（与重置登录密码不同）。"""
    row = _db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.value:
        if len(body.value) < 8:
            raise HTTPException(status_code=400, detail="密码至少 8 位")
        new_hash = hash_password(body.value)
    else:
        new_hash = None
    with _db_lock:
        _db().execute("UPDATE users SET webdav_password_hash=? WHERE id=?", (new_hash, uid))
        _db().commit()
    audit("admin_webdav_password" if new_hash else "admin_webdav_password_clear",
          row["username"], ip=client_ip(request))
    return {"ok": True, "set": bool(new_hash)}


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
        # sessions 显式清理（红队 P3-1）：SQLite 该表未声明 CASCADE，残留行
        # 只能等过期清理（JOIN users 使其本就不可用，但占库）
        _db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        _db().execute("DELETE FROM users WHERE id=?", (uid,))
        _db().commit()
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
            cur = _db().execute(
                "INSERT INTO users(username, password_hash, created_at, role) VALUES(?,?,?,?) "
                "RETURNING id",
                (username, new_hash, now, row["role"]))
            uid = cur.fetchone()["id"]
        except Exception as e:
            # 并发 redeem 撞同名：唯一约束兜底（占位查询与插入之间的窗口）。
            # 其它 DB 故障必须上抛——吞成 409 会误导用户且掩盖故障（R2-P2）
            if is_unique_violation(e):
                raise HTTPException(status_code=409, detail="该用户名已被占用")
            raise
        # 原子占位：仅当邀请未被使用且未过期时认领（并发 redeem 安全）
        claim = _db().execute(
            "UPDATE invites SET used_by=?, used_at=? WHERE code=? AND used_by IS NULL "
            "AND expires_at > ?", (uid, now, body.code, now))
        if not claim.rowcount:
            # 补偿回退（引擎中立：不等价于事务 rollback，但同样不留下孤儿账号）
            _db().execute("DELETE FROM users WHERE id=?", (uid,))
            _db().commit()
            # audit() 内部也要取 _db_lock（不可重入）：锁内调用会自死锁（R1-P0-1），
            # 移到锁外执行
            redeem_failed = True
        else:
            _db().commit()
    if redeem_failed:
        audit("invite_redeem_fail", body.code[:6], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="邀请码无效或已过期")
    audit("invite_redeemed", username, row["role"], client_ip(request))
    push_webhook("👋 新成员加入", f"{username}（{row['role']}）已激活账号")
    return {"ok": True, "username": username, "role": row["role"]}


# ─── 公网访问地址（分享链接双地址用）───────────────────────


@app.get("/api/admin/public-url")
def get_public_url(_admin=Depends(require_admin)):
    return {"public_base_url": get_setting(PUBLIC_BASE_URL),
            "public_base_url_alt": get_setting(PUBLIC_BASE_URL_ALT)}


@app.get("/api/public-url")
def get_public_url_user(_user=Depends(require_user)):
    """公网访问地址：全员可读（分享/直传多地址复制用，非敏感）。"""
    return {"public_base_url": get_setting(PUBLIC_BASE_URL),
            "public_base_url_alt": get_setting(PUBLIC_BASE_URL_ALT)}


@app.post("/api/admin/public-url")
def set_public_url(body: dict, request: Request, _admin=Depends(require_admin)):
    url = (body.get("url") or "").strip().rstrip("/")
    url_alt = (body.get("url_alt") or "").strip().rstrip("/")
    for u in (url, url_alt):
        if u and not u.startswith(("https://", "http://")):
            raise HTTPException(status_code=400, detail="地址需以 http(s):// 开头")
    if url and url_alt and urlsplit(url).netloc.lower() == urlsplit(url_alt).netloc.lower():
        raise HTTPException(status_code=400, detail="备用地址不能与主地址相同")
    set_setting(PUBLIC_BASE_URL, url)
    set_setting(PUBLIC_BASE_URL_ALT, url_alt)
    reset_netloc_cache()   # 入口判定即时生效
    audit("public_url_set", f"{url} | alt={url_alt}", ip=client_ip(request))
    return {"ok": True, "public_base_url": url, "public_base_url_alt": url_alt}


# ─── 事件推送（通知 bot）──────────────────────────────────


@app.get("/api/admin/webhook")
def admin_get_webhook(_admin=Depends(require_admin)):
    return {"url": get_webhook_url()}


@app.post("/api/admin/webhook")
def admin_set_webhook(body: dict, request: Request, _admin=Depends(require_admin)):
    url = (body.get("url") or "").strip()
    if url:
        if not url.startswith(("https://", "http://")):
            raise HTTPException(status_code=400, detail="地址需以 http(s):// 开头")
        if "{title}" not in url and "{body}" not in url:
            raise HTTPException(status_code=400,
                                detail="URL 需包含 {title} 或 {body} 占位符（URL 编码后替换）")
    set_setting(NOTIFY_WEBHOOK_URL, url)
    audit("webhook_set" if url else "webhook_cleared", url[:120], ip=client_ip(request))
    return {"ok": True, "url": url}


@app.post("/api/admin/webhook/test")
async def admin_test_webhook(request: Request, _admin=Depends(require_admin)):
    """同步发一条测试消息，失败原样带回错误（配置排错用）。走线程池执行网络请求，
    不长期占用事件循环。"""
    url = get_webhook_url()
    if not url:
        raise HTTPException(status_code=400, detail="尚未配置推送地址")
    who = _admin["username"]
    try:
        await run_in_threadpool(
            webhook_send, url, "LAN Share 测试推送", f"来自 {who} 的配置测试，收到即说明链路 OK")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"推送失败：{str(e)[:200]}")
    audit("webhook_test", ip=client_ip(request))
    return {"ok": True}


# 手动广播 POST /api/admin/notify 已迁往 notify.py（改进建议 9.6-P2-14）：
# 广播落 admin_broadcasts 记录，支持发送记录/撤回/提醒未读/草稿定时；
# 站内写入统一走 notify.insert_notification（尊重接收方通知偏好）。


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


@app.delete("/api/admin/grants/{gid}")
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


@app.post("/api/activity/clear")
def activity_clear(_admin=Depends(require_admin)):
    """清空动态（软清空：记水位，审计历史仍完整保留）。"""
    import time as _t
    now = str(_t.time())
    set_setting(ACTIVITY_CLEARED_AT, now)
    return {"ok": True}


@app.get("/api/activity")
def activity(limit: int = 50, user=Depends(require_user)):
    """动态流：审计日志过滤为家庭可见事件。private 路径仅本人可见（E7/E8 折衷）。
    同人同类动作在 10 分钟窗口内聚合为一条（count 表示合并条数）。"""
    cutoff = float(get_setting(ACTIVITY_CLEARED_AT) or 0)
    rows = _db().execute(
        "SELECT ts, username, action, target, detail FROM audit_log "
        "WHERE ts > ? ORDER BY id DESC LIMIT 500", (cutoff,)).fetchall()
    show = {"upload", "upload_instant", "mkdir", "share", "share_target", "share_download",
            "invite_redeemed", "trash", "restore", "move", "rename"}
    items: list[dict] = []
    max_items = max(1, min(limit, 100))
    for r in rows:
        if r["action"] not in show or len(items) >= max_items:
            continue
        target = r["target"] or ""
        # 统一用 visible() 判权（红队 P2）：此前只过滤 private/，他人 incoming/
        # 直传文件名与移动轨迹对全员可见
        if not visible(user, [p for p in target.split("/") if p]):
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


# ─── 数据导出逃生门（B3）：把某成员的数据足迹打包带走 ───────

class ExportBody(BaseModel):
    username: str


@app.post("/api/admin/export")
def admin_export(body: ExportBody, request: Request, _admin=Depends(require_admin)):
    """导出成员数据：私人空间 + 直传(incoming)子树全量 zip，附 manifest.json
    （账号信息/分享/设备/通知统计）。

    边界声明（写进 manifest）：共享空间里的文件无逐文件归属元数据，不在本
    导出范围；照片时间线引用的文件若落在上述两棵树内会自然包含。走 /api/zip
    同一套短时效票据，下载一次即删。"""
    row = _db().execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="成员不存在")

    import json
    import zipfile
    from datetime import datetime

    from app.api.files import ZIP_TTL, _zips, _zips_lock

    username = row["username"]
    roots = [("private", SHARE_ROOT / "private" / username),
             ("incoming", SHARE_ROOT / "incoming" / username)]
    if not any(r.exists() for _, r in roots):
        raise HTTPException(status_code=404, detail="该成员没有可导出的数据")

    zdir = SHARE_ROOT / ".zips"
    zdir.mkdir(exist_ok=True)
    zpath = zdir / f"export-{secrets.token_urlsafe(8)}.zip"
    file_count = 0
    file_bytes = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for _label, root in roots:
            if not root.exists():
                continue
            for f in root.rglob("*"):
                if not f.is_file():
                    continue
                # arcname 保留 private/incoming 前缀（相对 SHARE_ROOT），恢复时按空间原样落位
                z.write(f, str(f.relative_to(SHARE_ROOT)).replace("\\", "/"))
                file_count += 1
                file_bytes += f.stat().st_size
        shares = _db().execute(
            "SELECT token, path, name, created_at, expires_at FROM shares "
            "WHERE owner_id=?", (row["id"],)).fetchall()
        devices = _db().execute(
            "SELECT name, created_at, last_seen FROM device_tokens WHERE user_id=?",
            (row["id"],)).fetchall()
        manifest = {
            "kind": "lan-share-member-export",
            "generated_at": datetime.now().isoformat(),
            "member": {"username": username, "role": row["role"],
                       "created_at": row["created_at"]},
            "scope": ["private/<用户名>/", "incoming/<用户名>/"],
            "scope_note": "共享空间文件无逐文件归属元数据，不在此包内",
            "stats": {"files": file_count, "bytes": file_bytes,
                      "shares": len(shares), "devices": len(devices)},
            "shares": [dict(s) for s in shares],
            "devices": [dict(d) for d in devices],
        }
        z.writestr("manifest.json",
                   json.dumps(manifest, ensure_ascii=False, indent=2, default=str))

    token = secrets.token_urlsafe(16)
    with _zips_lock:
        _zips[token] = {"path": zpath, "exp": time.time() + ZIP_TTL}
    audit("export", f"{username} files={file_count} bytes={file_bytes}",
          ip=client_ip(request))
    return {"ok": True, "url": f"/api/zip/{token}",
            "name": f"lan-share-export-{username}-{datetime.now().strftime('%Y%m%d')}.zip",
            "files": file_count, "bytes": file_bytes}
