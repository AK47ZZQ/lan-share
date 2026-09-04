"""分享 API：外链创建/取件（密码走 POST body 换一次性票据）/QR/取件记录/撤销。"""
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from typing import Optional

import qrcode
import qrcode.image.svg  # noqa: F401 子模块必须显式导入，SvgPathImage 才存在
from fastapi import (Depends, Form, HTTPException, Request)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.config import LOGIN_LOCK_SECONDS, LOGIN_MAX_FAILS, SHARE_ROOT, TICKET_TTL
from app.db import _db, _db_lock
from app.security import (audit, client_ip, get_bearer_user, get_session_user,
                          preview_user, require_user)
from app.filetools import human_size
from app.spaces import resolve
from app.api.auth import SharePrepareBody
from app.api.notify import push_webhook
from app.main import app

@app.post("/api/share")
async def create_share(request: Request, path: str = Form(...), expires_hours: int = Form(default=72),
                       password: Optional[str] = Form(default=None),
                       max_downloads: Optional[int] = Form(default=None),
                       target: Optional[str] = Form(default=None),
                       _user=Depends(require_user)):
    if expires_hours < 1:
        raise HTTPException(status_code=400, detail="expires_hours must be >= 1")
    if max_downloads is not None and max_downloads < 1:
        raise HTTPException(status_code=400, detail="max_downloads must be >= 1")
    # 只能分享自己可读的路径（v3：分享权限跟随创建者）。
    # 局部名 target_path：与上方 Form 参数 target（定向成员）严格区分
    target_path = resolve(_user, path, "read")
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if target_path.is_dir():
        raise HTTPException(status_code=400, detail="暂不支持分享文件夹，请打包后分享")

    # 定向分享：目标成员必须存在且在用
    target_id = None
    target_username = None
    form = await request.form()
    raw_target = form.get("target")
    if raw_target and str(raw_target).strip():
        trow = _db().execute(
            "SELECT id, username FROM users WHERE LOWER(username)=LOWER(?) AND is_active=1",
            (str(raw_target).strip(),)).fetchone()
        if not trow:
            raise HTTPException(status_code=404, detail="目标成员不存在或已停用")
        target_id = trow["id"]
        target_username = trow["username"]

    token = secrets.token_urlsafe(16)
    pwd_hash = None
    if password:
        salt = secrets.token_bytes(16)
        pwd_hash = salt.hex() + "$" + hashlib.sha256(salt + password.encode()).hexdigest()
    with _db_lock:
        _db().execute(
            "INSERT INTO shares(token, path, name, size, created_at, expires_at, password_hash, max_downloads, owner_id, password_plain, target_user_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (token, str(target_path.relative_to(SHARE_ROOT)).replace("\\", "/"), target_path.name,
             target_path.stat().st_size if target_path.is_file() else None,
             time.time(), time.time() + expires_hours * 3600, pwd_hash, max_downloads,
             _user["user_id"] if "user_id" in _user.keys() else None,
             password or None, target_id))
        if target_id:
            _db().execute(
                "INSERT INTO notifications(user_id, type, title, body, link, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (target_id, "share", f"{_user['username']} 给你分享了文件",
                 target_path.name, f"/s/{token}", time.time()))
        _db().commit()
    audit("share_create", path, f"{expires_hours}h", client_ip(request))
    if target_id:
        audit("share_target", path, f"→ {target_username}", client_ip(request))
        push_webhook("📬 收到定向分享",
                     f"{_user['username']} 分享给 {target_username}：{target_path.name}")
    return {"ok": True, "token": token, "expires_in": f"{expires_hours}h",
            "target": target_username}


def _target_guard(s: sqlite3.Row, request: Request) -> bool:
    """定向分享访问判定：返回请求者是否是目标成员（或 admin）。
    非定向分享恒返回 False（不拦截）；定向分享对无关者表现为不存在。
    Cookie 会话与 App Bearer 均可（App 也能取定向分享，review 2026-09-02 P3）。"""
    if s["target_user_id"] is None:
        return False
    u = get_session_user(request) or get_bearer_user(request)
    if u and (u["user_id"] == s["target_user_id"] or u["role"] == "admin"):
        return True
    # 404 而非 403：不向无关者泄露分享的存在；detail 供落地页给出登录引导
    raise HTTPException(status_code=404, detail="定向分享：请登录对应成员账号")


def _get_active_share(token: str) -> sqlite3.Row:
    s = _db().execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()
    if not s:
        raise HTTPException(status_code=404, detail="Share not found")
    if time.time() > s["expires_at"]:
        raise HTTPException(status_code=410, detail="Share expired")
    return s


_share_fails: dict[str, list[float]] = {}
_share_fails_lock = threading.Lock()


def _share_locked_remaining(token: str) -> int:
    now = time.time()
    with _share_fails_lock:
        q = _share_fails.get(token, [])
        while q and q[0] <= now - LOGIN_LOCK_SECONDS:
            q.pop(0)
        if len(q) >= LOGIN_MAX_FAILS:
            return int(q[0] + LOGIN_LOCK_SECONDS - now) + 1
        return 0


def _record_share_fail(token: str):
    with _share_fails_lock:
        _share_fails.setdefault(token, []).append(time.time())


def _check_share_password(request: Request, s: sqlite3.Row, password: Optional[str]):
    if not s["password_hash"]:
        return
    token = s["token"]
    remaining = _share_locked_remaining(token)
    if remaining:
        audit("share_locked", s["name"], f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"提取码错误次数过多，请 {remaining // 60 + 1} 分钟后重试")
    if not password:
        raise HTTPException(status_code=401, detail="Password required")
    salt_hex, h_hex = s["password_hash"].split("$", 1)
    calc = hashlib.sha256(bytes.fromhex(salt_hex) + password.encode()).hexdigest()
    if not hmac.compare_digest(calc, h_hex):
        _record_share_fail(token)
        audit("share_badpwd", s["name"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="Wrong password")
    with _share_fails_lock:
        _share_fails.pop(token, None)


@app.get("/api/share/meta/{token}")
async def share_meta(token: str, request: Request):
    s = _get_active_share(token)
    is_target = _target_guard(s, request)
    return {
        "name": s["name"],
        "size": s["size"],
        "size_human": human_size(s["size"]) if s["size"] is not None else "-",
        "expires_in": f"{max(0, int((s['expires_at'] - time.time()) / 3600))}h",
        "needs_password": s["password_hash"] is not None and not is_target,
        "max_downloads": s["max_downloads"],
        "downloads": s["downloads"],
        "targeted": s["target_user_id"] is not None,
    }


# 取件票据：POST 验证密码后换一次性短时效 URL，密码不出现在任何 URL/日志
_tickets: dict[str, dict] = {}
_tickets_lock = threading.Lock()


@app.post("/api/share/{token}/prepare")
async def share_prepare(request: Request, token: str, body: SharePrepareBody):
    s = _get_active_share(token)
    is_target = _target_guard(s, request)
    # 定向分享的收件成员（及 admin）登录即取，无需提取码
    if not is_target:
        _check_share_password(request, s, body.password)
    if s["max_downloads"] and s["downloads"] >= s["max_downloads"]:
        raise HTTPException(status_code=410, detail="下载次数已达上限")
    tkt = secrets.token_urlsafe(24)
    with _tickets_lock:
        # 顺带清理过期票据
        now = time.time()
        for k in [k for k, v in _tickets.items() if v["exp"] < now]:
            del _tickets[k]
        _tickets[tkt] = {"token": token, "exp": now + TICKET_TTL, "counted": False}
    return {"ok": True, "url": f"/api/share/{token}/dl?tkt={tkt}",
            "name": s["name"], "expires": TICKET_TTL}


def _share_download_done(token: str, path: str, ip: str, tkt: str = ""):
    """下载响应发送完成后计数与审计（客户端中途取消不计）。

    按票据去重：手机下载管理器（小米等）会对大文件并发多个 Range 分段请求，
    每段完成都会走到这里——同一 tkt 只计 1 次，否则一次下载被记 N 次、
    误触 max_downloads 上限。"""
    with _tickets_lock:
        info = _tickets.get(tkt)
        if info is not None:
            if info.get("counted"):
                return
            info["counted"] = True
    with _db_lock:
        _db().execute("UPDATE shares SET downloads = downloads + 1 WHERE token=?", (token,))
        _db().commit()
    audit("share_download", path, ip=ip)


@app.get("/api/share/{token}/dl")
async def share_download(request: Request, token: str, tkt: str):
    # 票据在 TTL 内可复用（大文件断线后浏览器重试/Range 续传不至于 403），过期由清理任务回收
    with _tickets_lock:
        info = _tickets.get(tkt)
    if not info or info["token"] != token or info["exp"] < time.time():
        raise HTTPException(status_code=403, detail="下载链接已失效，请刷新页面重试")
    s = _get_active_share(token)
    if s["max_downloads"] and s["downloads"] >= s["max_downloads"]:
        raise HTTPException(status_code=410, detail="下载次数已达上限")
    file_path = SHARE_ROOT / s["path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File deleted")
    return FileResponse(file_path, media_type="application/octet-stream",
                        filename=s["name"],
                        background=BackgroundTask(
                            _share_download_done, token, s["path"], client_ip(request), tkt))


@app.get("/api/share/{token}/qr")
async def share_qr(token: str, request: Request, _user=Depends(require_user)):
    if not _db().execute("SELECT 1 FROM shares WHERE token=?", (token,)).fetchone():
        raise HTTPException(status_code=404, detail="Share not found")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    url = f"{proto}://{host}/s/{token}"
    from app.api.system import qr_svg_response
    return qr_svg_response(url)


@app.get("/api/shares")
async def list_shares(request: Request, _user=Depends(require_user)):
    now = time.time()
    with _db_lock:
        _db().execute("DELETE FROM shares WHERE expires_at < ?", (now,))
        _db().commit()
    viewer = preview_user(request, _user)   # admin 成员视角预览
    is_admin = viewer["role"] == "admin"
    rows = _db().execute("SELECT * FROM shares ORDER BY created_at DESC").fetchall()
    uid_map = {r["id"]: r["username"]
               for r in _db().execute("SELECT id, username FROM users").fetchall()}
    active = {}
    for s in rows:
        # v2 遗留分享（owner_id 为 NULL）仅 admin 可见/管理，成员不可见
        if not is_admin and s["owner_id"] is None:
            continue
        if not is_admin and s["owner_id"] != viewer["user_id"]:
            continue
        owner = uid_map.get(s["owner_id"]) if s["owner_id"] else None
        target_user = uid_map.get(s["target_user_id"]) if s["target_user_id"] else None
        active[s["token"]] = {
            "owner": owner or ("早期分享" if is_admin else "我"),
            "target": target_user,
            "path": s["path"],
            "name": s["name"],
            "size": s["size"],
            "size_human": human_size(s["size"]) if s["size"] else "-",
            "expires_at": datetime.fromtimestamp(s["expires_at"]).isoformat(),
            "expires_in": f"{max(0, int((s['expires_at'] - now) / 3600))}h",
            "has_password": s["password_hash"] is not None,
            "password": s["password_plain"],
            "downloads": s["downloads"],
            "max_downloads": s["max_downloads"],
            "created_at": datetime.fromtimestamp(s["created_at"]).isoformat(),
        }
    return active


@app.get("/api/shares/{token}/history")
async def share_history(token: str, _user=Depends(require_user)):
    """某分享的取件记录（下载成功 + 密码错误尝试）。仅创建者与 admin。"""
    s = _db().execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()
    if not s:
        raise HTTPException(status_code=404, detail="Share not found")
    # 无主（v2 遗留）分享归 admin 管；owner_id None 不再等同"人人可管"
    if _user["role"] != "admin" and (s["owner_id"] is None or s["owner_id"] != _user["user_id"]):
        raise HTTPException(status_code=403, detail="Forbidden")
    rows = _db().execute(
        "SELECT ts, ip, action FROM audit_log "
        "WHERE (action='share_download' AND target=?) OR (action='share_badpwd' AND target=?) "
        "ORDER BY id DESC LIMIT 50", (s["path"], s["name"])).fetchall()
    return [{"ts": datetime.fromtimestamp(r["ts"]).isoformat(),
             "ip": r["ip"], "action": r["action"]} for r in rows]


@app.delete("/api/shares/{token}")
async def revoke_share(request: Request, token: str, _user=Depends(require_user)):
    s = _db().execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()
    if not s:
        raise HTTPException(status_code=404, detail="Share not found")
    if _user["role"] != "admin" and (s["owner_id"] is None or s["owner_id"] != _user["user_id"]):
        raise HTTPException(status_code=403, detail="Forbidden")
    with _db_lock:
        cur = _db().execute("DELETE FROM shares WHERE token=?", (token,))
        _db().commit()
    if cur.rowcount:
        audit("share_revoke", token, ip=client_ip(request))
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Share not found")


class ShareEditBody(BaseModel):
    """分享编辑（App v0.6.0）：有效期从现在起重新计时；下载上限可改/取消。"""
    expires_hours: Optional[int] = None
    max_downloads: Optional[int] = None


@app.patch("/api/shares/{token}")
async def edit_share(request: Request, token: str, body: ShareEditBody,
                     _user=Depends(require_user)):
    s = _db().execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()
    if not s:
        raise HTTPException(status_code=404, detail="Share not found")
    if _user["role"] != "admin" and (s["owner_id"] is None or s["owner_id"] != _user["user_id"]):
        raise HTTPException(status_code=403, detail="Forbidden")
    if body.expires_hours is None and body.max_downloads is None:
        raise HTTPException(status_code=400, detail="没有要修改的字段")
    if body.expires_hours is not None and body.expires_hours < 1:
        raise HTTPException(status_code=400, detail="expires_hours must be >= 1")
    if body.max_downloads is not None and body.max_downloads < 1:
        raise HTTPException(status_code=400, detail="max_downloads must be >= 1")
    with _db_lock:
        if body.expires_hours is not None:
            _db().execute("UPDATE shares SET expires_at=? WHERE token=?",
                          (time.time() + body.expires_hours * 3600, token))
        if body.max_downloads is not None:
            _db().execute("UPDATE shares SET max_downloads=? WHERE token=?",
                          (body.max_downloads, token))
        _db().commit()
    audit("share_edit", token,
          f"exp={body.expires_hours}h max={body.max_downloads}", client_ip(request))
    s = _db().execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()
    return {"ok": True,
            "expires_in": f"{max(0, int((s['expires_at'] - time.time()) / 3600))}h",
            "max_downloads": s["max_downloads"]}
