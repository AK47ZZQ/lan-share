"""分享 API：外链创建/取件（密码走 POST body 换一次性票据）/QR/取件记录/撤销。"""
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import qrcode
import qrcode.image.svg  # noqa: F401 子模块必须显式导入，SvgPathImage 才存在
from fastapi import (APIRouter, Depends, Form, HTTPException, Request)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.config import LOGIN_LOCK_SECONDS, LOGIN_MAX_FAILS, TICKET_TTL
from app.db import _db
from app.roles import ADMIN
from app.api.deps import ensure_share_owner, get_share_or_404, preview_viewer
from app.security import (audit, client_ip, get_bearer_user, get_session_user,
                          hash_password, require_user, verify_password)
from app.filetools import human_size, safe_path, to_rel
from app.settings import PUBLIC_BASE_URL, get_setting
from app.api.qr import qr_svg_response
from app.spaces import resolve
from app.store import shares as shares_store
from app.store import ttl_state
from app.store import users as users_store
from app.api.notify import insert_notification, push_webhook

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装（07 审查批次 2 消循环）
router = APIRouter()


class SharePrepareBody(BaseModel):
    # 创建分享前校验密码（原在 auth.py，随 07 小尾巴拆分归位分享域）；
    # 注释不用 docstring——pydantic 会把它带进 OpenAPI description，契约就漂了
    password: Optional[str] = None


@router.post("/api/share")
async def create_share(request: Request, path: str = Form(...), expires_hours: int = Form(default=72),
                       password: Optional[str] = Form(default=None),
                       max_downloads: Optional[int] = Form(default=None),
                       target: Optional[str] = Form(default=None),
                       _user=Depends(require_user)):
    if expires_hours < 1 or expires_hours > 24 * 365:
        # 上限防毒数据：expires_at 过大时 Windows datetime.fromtimestamp 抛
        # OSError，GET /api/shares 对所有人持续 500 且 UI 无法撤销（红队 P2）
        raise HTTPException(status_code=400, detail="expires_hours must be 1-8760")
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
        trow = users_store.get_active_by_username_ci(str(raw_target).strip())
        if not trow:
            raise HTTPException(status_code=404, detail="目标成员不存在或已停用")
        target_id = trow["id"]
        target_username = trow["username"]

    token = secrets.token_urlsafe(16)
    # 提取码与登录口令同源 scrypt（sha256 单轮在库表泄漏时可离线穷举 4–6 位
    # 数字码，15-路线图 P3）；存量旧行由校验侧在首次校验成功时惰性升级
    pwd_hash = hash_password(password) if password else None
    await run_in_threadpool(_insert_share, _ShareRow(
        token=token, rel=to_rel(target_path), name=target_path.name,
        size=target_path.stat().st_size if target_path.is_file() else None,
        created_at=time.time(), expires_at=time.time() + expires_hours * 3600,
        pwd_hash=pwd_hash, max_downloads=max_downloads,
        owner_id=_user["user_id"] if "user_id" in _user.keys() else None,
        target_id=target_id))
    # 站内通知统一出口（9.6-P2-14）：偏好关闭的接收方不再落库
    if target_id:
        insert_notification(target_id, "share",
                            f"{_user['username']} 给你分享了文件",
                            target_path.name, f"/s/{token}")
    audit("share_create", to_rel(target_path),
          f"{expires_hours}h", client_ip(request))
    if target_id:
        audit("share_target", to_rel(target_path),
              f"→ {target_username}", client_ip(request))
        push_webhook("📬 收到定向分享",
                     f"{_user['username']} 分享给 {target_username}：{target_path.name}")
    return {"ok": True, "token": token, "expires_in": f"{expires_hours}h",
            "target": target_username,
            # 提取码不再落库（R1-P2-5）：仅创建响应返回一次，供创建者当场记录
            "password": password or None}


@dataclass
class _ShareRow:
    """create_share 的落库参数（同步写助手 _insert_share 的入参）。"""
    token: str
    rel: str
    name: str
    size: int | None
    created_at: float
    expires_at: float
    pwd_hash: str | None
    max_downloads: int | None
    owner_id: int | None
    target_id: int | None


def _insert_share(r: _ShareRow) -> None:
    """落一条分享（同步；经 run_in_threadpool 调用，见 P2-2）。"""
    shares_store.create(r.token, r.rel, r.name, r.size, r.created_at,
                        r.expires_at, r.pwd_hash, r.max_downloads,
                        r.owner_id, r.target_id)


def _target_guard(s: sqlite3.Row, request: Request) -> bool:
    """定向分享访问判定：返回请求者是否是目标成员（或 admin）。
    非定向分享恒返回 False（不拦截）；定向分享对无关者表现为不存在。
    Cookie 会话与 App Bearer 均可（App 也能取定向分享，review 2026-09-02 P3）。"""
    if s["target_user_id"] is None:
        return False
    u = get_session_user(request) or get_bearer_user(request)
    if u and (u["user_id"] == s["target_user_id"] or u["role"] == ADMIN):
        return True
    # 404 而非 403：不向无关者泄露分享的存在；detail 供落地页给出登录引导
    raise HTTPException(status_code=404, detail="定向分享：请登录对应成员账号")


def _get_active_share(token: str) -> sqlite3.Row:
    s = shares_store.get_by_token(token)
    if not s:
        raise HTTPException(status_code=404, detail="Share not found")
    if time.time() > s["expires_at"]:
        raise HTTPException(status_code=410, detail="Share expired")
    return s


_share_fails: dict[str, list[float]] = {}
# ↑ 提取码防爆破计数**故意留在进程内**：它是"一分钟内的尝试次数"，重启丢掉的
# 只是计数本身、无用户可见损失，落库反而给每次失败尝试加一次写事务（v3.57 外置
# 的取舍口径见 store/ttl_state.py 模块 docstring 的表格）。


def reset_share_state() -> None:
    """清空分享票据与防爆破状态（测试专用接口，07 审查批次 2 测试解钉）。"""
    ttl_state.clear_scope(ttl_state.SCOPE_SHARE_TICKET)
    ttl_state.clear_scope(ttl_state.SCOPE_SHARE_AUDIT)
    with _share_fails_lock:
        _share_fails.clear()
_share_fails_lock = threading.Lock()


def _share_locked_remaining(token: str) -> int:
    now = time.time()
    with _share_fails_lock:
        q = _share_fails.get(token, [])
        while q and q[0] <= now - LOGIN_LOCK_SECONDS:
            q.pop(0)
        if not q:
            # 空队列即回收键（15-路线图 P3）：不然键随分享数只增不减，
            # 量级无害但没必要留
            _share_fails.pop(token, None)
            return 0
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
    stored = s["password_hash"]
    # 双格式校验：scrypt$（新，security.py 单源）/ 旧 <salt_hex>$<sha256>。
    # share_prepare 为 sync def，本就运行在线程池内，同步 scrypt 不阻塞事件
    # 循环；单次 scrypt ~300ms 意味着并发 prepare 有线程池容量上限（默认池
    # 40 线程 ⇒ ~40 并发取件饱和）——家庭规模 2-20 人下远不可达，属有意取舍
    if stored.startswith("scrypt$"):
        ok = verify_password(password, stored)
    else:
        try:
            salt_hex, h_hex = stored.split("$", 1)
            calc = hashlib.sha256(
                bytes.fromhex(salt_hex) + password.encode()).hexdigest()
            ok = hmac.compare_digest(calc, h_hex)
        except ValueError:
            ok = False   # 畸形哈希 fail-closed 为 403 而非 500
        if ok:
            # 存量行惰性升级：无需迁移脚本，存量分享链接保持可用；触发前提是
            # 已给出正确密码，公开端点不因此新增写入原语。
            # 并发收敛（对抗审查 P1-1）：预读跳过并行请求已完成的升级（免重复
            # 300ms scrypt）；CAS 条件更新（旧哈希作 WHERE 条件）保证单一写者，
            # 极端竞态下最多各算一次、绝不双写
            cur = shares_store.get_by_token(token)["password_hash"] or ""
            if not cur.startswith("scrypt$"):
                shares_store.set_password_hash_if(
                    token, hash_password(password), stored)
    if not ok:
        _record_share_fail(token)
        audit("share_badpwd", s["name"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="Wrong password")
    with _share_fails_lock:
        _share_fails.pop(token, None)


@router.get("/api/share/meta/{token}")
def share_meta(token: str, request: Request):
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


# 取件票据：POST 验证密码后换一次性短时效 URL，密码不出现在任何 URL/日志。
# v3.57 起落 ttl_state 表（scope=share_ticket），TTL 内可被同一大文件的多个
# Range 分段请求复用；重启不再把已验证过提取码的下载链接清空。
_AUDIT_TTL_MULT = 24   # 审计位有效期 = 票据 × 24（下载可远长于票据寿命）


@router.post("/api/share/{token}/prepare")
def share_prepare(request: Request, token: str, body: SharePrepareBody):
    s = _get_active_share(token)
    is_target = _target_guard(s, request)
    # 定向分享的收件成员（及 admin）登录即取，无需提取码
    if not is_target:
        _check_share_password(request, s, body.password)
    tkt = secrets.token_urlsafe(24)
    # 下载计数移到发票时原子递增（R1-P2-4）：此前计数在 dl 完成后才发生，
    # 并发 prepare N 张票（此刻 downloads=0）即可把"限 1 次下载"变 N 次完整下载
    # consume_download 内部：UPDATE 命中即提交（commit 必须在 raise 之前：
    # UPDATE 命中 0 行也已开启事务，锁内 raise 会把连接留在持有 RESERVED 锁
    # 的未决事务里，锁死后续所有写操作）
    if not shares_store.consume_download(token):
        raise HTTPException(status_code=410, detail="下载次数已达上限")
    now = time.time()
    # 票据 + 审计位两条独立写入（不包事务，见 store/ttl_state.put_if_absent
    # 注释：SQLite 下跨两条语句的写事务会制造 database is locked）。
    # 审计位 TTL 故意**长于**票据：大文件下到一半票据过期是常态，旧实现靠
    # "查不到票据就当没审计过"兜住这种情况，这里用更长的有效期显式兜住。
    ttl_state.put(ttl_state.SCOPE_SHARE_TICKET, tkt, token, TICKET_TTL, now)
    ttl_state.put(ttl_state.SCOPE_SHARE_AUDIT, tkt, token,
                  TICKET_TTL * _AUDIT_TTL_MULT, now)
    return {"ok": True, "url": f"/api/share/{token}/dl?tkt={tkt}",
            "name": s["name"], "expires": TICKET_TTL}


def _share_download_done(token: str, path: str, ip: str, tkt: str):
    """下载响应发送完成后审计（客户端中途取消不计）。

    按票据去重：手机下载管理器（小米等）会对大文件并发多个 Range 分段请求，
    每段完成都会走到这里——同一 tkt 只审计 1 次。外置后去重靠 `take()` 的
    原子消费（旧实现是往票据 dict 里塞 counted_audit 标志位，重启即失效）。
    计数已移至 prepare 发票时（R1-P2-4），此处不再递增 downloads。"""
    if tkt and ttl_state.take(ttl_state.SCOPE_SHARE_AUDIT, tkt) is None:
        return
    audit("share_download", path, ip=ip)


@router.get("/api/share/{token}/dl")
async def share_download(request: Request, token: str, tkt: str):
    # 票据在 TTL 内可复用（大文件断线后浏览器重试/Range 续传不至于 403）；
    # get() 不消费且自带过期判定，过期回收由 cleanup._cleanup_db 统一收口
    if ttl_state.get(ttl_state.SCOPE_SHARE_TICKET, tkt) != token:
        raise HTTPException(status_code=403, detail="下载链接已失效，请刷新页面重试")
    # 次数上限已在 prepare 发票时原子把关（R1-P2-4）：此处不再复查——
    # prepare 计数后 downloads 已达上限，复查会把自己的合法票据 410 掉
    s = _get_active_share(token)
    # 读侧过闸（06 审查 P1-2 防御纵深）：写侧创建分享时虽经 relative_to
    # 保证是 SHARE_ROOT 内相对路径，但读侧不设防——万一未来任何路径把含
    # "../" 或点前缀的值写进 shares 表（或库被其他漏洞篡改），此处即成任意
    # 文件读取。safe_path 对穿越/隐藏段直接 403
    file_path = safe_path(s["path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File deleted")
    return FileResponse(file_path, media_type="application/octet-stream",
                        filename=s["name"],
                        background=BackgroundTask(
                            _share_download_done, token, s["path"], client_ip(request), tkt))


@router.get("/api/share/{token}/qr")
def share_qr(token: str, request: Request, _user=Depends(require_user)):
    if not shares_store.exists(token):
        raise HTTPException(status_code=404, detail="Share not found")
    # host 优先取管理员配置的公网地址（R1-P3-5）：LAN 可注入 X-Forwarded-Proto
    # /Host 影响二维码指向；配置缺失时才回退请求头
    pub = get_setting(PUBLIC_BASE_URL)
    if pub:
        url = f"{pub.rstrip('/')}/s/{token}"
    else:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("host") or request.url.netloc
        url = f"{proto}://{host}/s/{token}"
    return qr_svg_response(url)


@router.get("/api/shares")
def list_shares(request: Request, viewer=Depends(preview_viewer)):
    now = time.time()
    shares_store.purge_expired(now)
    is_admin = viewer["role"] == ADMIN
    rows = shares_store.list_all()
    uid_map = users_store.id_to_username_map()
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
            # 提取码不再回传（R1-P2-5：明文落库=一次 dump 全泄露，存量字段也
            # 不再暴露）；新分享的提取码在创建响应中一次性下发
            "downloads": s["downloads"],
            "max_downloads": s["max_downloads"],
            "created_at": datetime.fromtimestamp(s["created_at"]).isoformat(),
        }
    return active


@router.get("/api/shares/{token}/history")
def share_history(token: str, _user=Depends(require_user)):
    """某分享的取件记录（下载成功 + 密码错误尝试）。仅创建者与 admin。"""
    s = get_share_or_404(token)
    # 无主（v2 遗留）分享归 admin 管；owner_id None 不再等同"人人可管"
    ensure_share_owner(_user, s)
    rows = _db().execute(
        "SELECT ts, ip, action FROM audit_log "
        "WHERE (action='share_download' AND target=?) OR (action='share_badpwd' AND target=?) "
        "ORDER BY id DESC LIMIT 50", (s["path"], s["name"])).fetchall()
    return [{"ts": datetime.fromtimestamp(r["ts"]).isoformat(),
             "ip": r["ip"], "action": r["action"]} for r in rows]


@router.delete("/api/shares/{token}")
def revoke_share(request: Request, token: str, _user=Depends(require_user)):
    s = get_share_or_404(token)
    ensure_share_owner(_user, s)
    if shares_store.delete_by_token(token):
        audit("share_revoke", token, ip=client_ip(request))
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Share not found")


class ShareEditBody(BaseModel):
    """分享编辑（App v0.6.0）：有效期从现在起重新计时；下载上限可改/取消。"""
    expires_hours: Optional[int] = None
    max_downloads: Optional[int] = None


@router.patch("/api/shares/{token}")
def edit_share(request: Request, token: str, body: ShareEditBody,
               _user=Depends(require_user)):
    s = get_share_or_404(token)
    ensure_share_owner(_user, s)
    if body.expires_hours is None and body.max_downloads is None:
        raise HTTPException(status_code=400, detail="没有要修改的字段")
    # 与 create_share 同上限（R1-P1-1）：过大值会让 datetime.fromtimestamp 抛
    # OSError，GET /api/shares 持续 500 且 UI 无法撤销
    if body.expires_hours is not None and not 1 <= body.expires_hours <= 24 * 365:
        raise HTTPException(status_code=400, detail="expires_hours must be 1-8760")
    if body.max_downloads is not None and body.max_downloads < 1:
        raise HTTPException(status_code=400, detail="max_downloads must be >= 1")
    if body.expires_hours is not None:
        shares_store.renew(token, time.time() + body.expires_hours * 3600)
    if body.max_downloads is not None:
        shares_store.set_max_downloads(token, body.max_downloads)
    audit("share_edit", token,
          f"exp={body.expires_hours}h max={body.max_downloads}", client_ip(request))
    s = shares_store.get_by_token(token)
    return {"ok": True,
            "expires_in": f"{max(0, int((s['expires_at'] - time.time()) / 3600))}h",
            "max_downloads": s["max_downloads"]}
