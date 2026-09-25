"""管理 API · 运维配置域（B2 自 admin.py 按域拆出）：公网地址、webhook 通知配置与测试、家庭动态流（审计水位）、成员数据导出。URL 与行为不变；admin.py 仍是聚合门面。"""
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.config import SHARE_ROOT
from app.db import _db
from app.filetools import to_rel
from app.security import (audit, client_ip, require_admin, require_user,
                          reset_netloc_cache)
from app.spaces import (visible)
from app.api.appver import reset_entries_cache
from app.api.notify import (assert_public_webhook_url, get_webhook_url, webhook_send)
from app.settings import (ACTIVITY_CLEARED_AT, NOTIFY_WEBHOOK_URL,
                          PUBLIC_BASE_URL, PUBLIC_BASE_URL_ALT,
                          get_setting, set_setting)
from app.store import devices as devices_store
from app.store import shares as shares_store
from app.store import users as users_store


# 路由挂到本模块 router；由 admin.py 门面聚合（URL 不变）
router = APIRouter()


@router.get("/api/admin/public-url")
def get_public_url(_admin=Depends(require_admin)):
    return {"public_base_url": get_setting(PUBLIC_BASE_URL),
            "public_base_url_alt": get_setting(PUBLIC_BASE_URL_ALT)}


@router.get("/api/public-url")
def get_public_url_user(_user=Depends(require_user)):
    """公网访问地址：全员可读（分享/直传多地址复制用，非敏感）。"""
    return {"public_base_url": get_setting(PUBLIC_BASE_URL),
            "public_base_url_alt": get_setting(PUBLIC_BASE_URL_ALT)}


@router.post("/api/admin/public-url")
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
    reset_entries_cache()   # entries 端点 60s 缓存同样即时失效
    audit("public_url_set", f"{url} | alt={url_alt}", ip=client_ip(request))
    return {"ok": True, "public_base_url": url, "public_base_url_alt": url_alt}


# ─── 事件推送（通知 bot）──────────────────────────────────


@router.get("/api/admin/webhook")
def admin_get_webhook(_admin=Depends(require_admin)):
    return {"url": get_webhook_url()}


@router.post("/api/admin/webhook")
def admin_set_webhook(body: dict, request: Request, _admin=Depends(require_admin)):
    url = (body.get("url") or "").strip()
    if url:
        if not url.startswith(("https://", "http://")):
            raise HTTPException(status_code=400, detail="地址需以 http(s):// 开头")
        if "{title}" not in url and "{body}" not in url:
            raise HTTPException(status_code=400,
                                detail="URL 需包含 {title} 或 {body} 占位符（URL 编码后替换）")
        # SSRF 防护（06-P2）：保存时即拒内网/保留地址，早失败早反馈
        try:
            assert_public_webhook_url(url.replace("{title}", "t").replace("{body}", "b"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    set_setting(NOTIFY_WEBHOOK_URL, url)
    audit("webhook_set" if url else "webhook_cleared", url[:120], ip=client_ip(request))
    return {"ok": True, "url": url}


@router.post("/api/admin/webhook/test")
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




@router.post("/api/activity/clear")
def activity_clear(_admin=Depends(require_admin)):
    """清空动态（软清空：记水位，审计历史仍完整保留）。"""
    import time as _t
    now = str(_t.time())
    set_setting(ACTIVITY_CLEARED_AT, now)
    return {"ok": True}


@router.get("/api/activity")
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


@router.post("/api/admin/export")
def admin_export(body: ExportBody, request: Request, _admin=Depends(require_admin)):
    """导出成员数据：私人空间 + 直传(incoming)子树全量 zip，附 manifest.json
    （账号信息/分享/设备/通知统计）。

    边界声明（写进 manifest）：共享空间里的文件无逐文件归属元数据，不在本
    导出范围；照片时间线引用的文件若落在上述两棵树内会自然包含。走 /api/zip
    同一套短时效票据，下载一次即删。"""
    row = users_store.get_by_username(body.username)
    if not row:
        raise HTTPException(status_code=404, detail="成员不存在")

    import json
    import zipfile
    from datetime import datetime

    from app.services import archive

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
                z.write(f, to_rel(f))
                file_count += 1
                file_bytes += f.stat().st_size
        shares = shares_store.list_brief_by_owner(row["id"])
        devices = devices_store.list_brief_by_user(row["id"])
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

    token = archive.issue_ticket(zpath)
    audit("export", f"{username} files={file_count} bytes={file_bytes}",
          ip=client_ip(request))
    return {"ok": True, "url": f"/api/zip/{token}",
            "name": f"lan-share-export-{username}-{datetime.now().strftime('%Y%m%d')}.zip",
            "files": file_count, "bytes": file_bytes}


# ─── 运行指标（B5：只读快照）─────────────────────────────


@router.get("/api/metrics")
def admin_metrics(_admin=Depends(require_admin)):
    """进程内指标快照：请求状态码/延迟分布 + 磁盘水位 + 备份编排状态 +
    后台循环健康（loops：每轮成败计数，停摆中的循环 consecutive 持续增长）。"""
    from app.services import loop_health as _loops
    from app.services import metrics as _metrics
    return {
        "requests": _metrics.snapshot(),
        "disk_used_pct": _metrics.disk_used_pct(),
        "backup": _backup_orchestrator_status(),
        "loops": _loops.snapshot(),
    }


def _backup_orchestrator_status() -> dict | None:
    from app.services import backup as _backup_svc
    return _backup_svc.last_status()
