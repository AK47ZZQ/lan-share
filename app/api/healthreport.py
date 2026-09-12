"""每日健康日报：磁盘水位 / 昨晚备份产物 / 证书到期 / 公网隧道探活 / DB 统计，
汇总成一条推送打 Webhook（notify_webhook_url，未配置则只在管理端可见）。

设计要点：
- 调度是"每分钟看一次表"而非长睡眠：改配置即时生效，重启不重发（按日期去重，
  服务器整天没到点开着的场景也能在到点后的第一分钟补发）。
- gather() 只读本地状态不发网络请求；隧道探活是唯一出网动作，拆成独立参数，
  单测不走。备份产物只看 BACKUP_DEST 里的文件时间戳——备份是 Task Scheduler
  独立跑的，服务端不参与也就不该去驱动它，日报只负责"看得见"。
"""
import os
import re
import shutil
import socket
import ssl
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.config import PROJECT_ROOT, SHARE_ROOT
from app.security import audit, client_ip, require_admin
from app.store import devices as devices_store
from app.store import photos as photos_store
from app.store import shares as shares_store
from app.store import users as users_store
from app.settings import (HEALTH_REPORT_ENABLED, HEALTH_REPORT_TIME,
                                NOTIFY_WEBHOOK_URL, get_setting, set_setting)

# 备份产物目录：backup.bat（Task Scheduler 03:30 独立跑）的落点，服务端只读状态
BACKUP_DEST = Path(os.environ.get("BACKUP_DEST", "C:/lan-share-backup"))

# 免费隧道突然挂掉是"全家公网失联"的前兆，日报兜底探活（host:port，env 可换）
PUBLIC_PROBE = "your-frp-domain.com:xxxxx"

_DEFAULT_TIME = "09:00"
_last_sent_date = None   # 内存态：进程级按日去重（重启后补发当天的，无重复风险）
_daily_task = None       # 日报任务引用：未保存引用的 create_task 可能被 GC 回收（R2-P3-8）


def get_report_config() -> dict:
    return {"enabled": get_setting(HEALTH_REPORT_ENABLED, "1") == "1",
            "time": get_setting(HEALTH_REPORT_TIME, _DEFAULT_TIME),
            "backup_dest": str(BACKUP_DEST),
            "public_probe": PUBLIC_PROBE}


def _parse_hhmm(s: str) -> str:
    if not re.fullmatch(r"\d{1,2}:\d{1,2}", s or ""):
        raise HTTPException(status_code=400, detail="时间格式应为 HH:MM")
    h, m = int(s.split(":")[0]), int(s.split(":")[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise HTTPException(status_code=400, detail="时间超出 0:00-23:59")
    return f"{h:02d}:{m:02d}"


def _due_now(hhmm: str) -> bool:
    h, m = int(hhmm.split(":")[0]), int(hhmm.split(":")[1])
    now = time.localtime()
    return (now.tm_hour, now.tm_min) >= (h, m)


def _cert_days_left(path: Path) -> int | None:
    try:
        info = ssl._ssl._test_decode_cert(str(path))   # type: ignore[attr-defined]  # 标准库唯一免第三方解析 PEM 的口子
        not_after = ssl.cert_time_to_seconds(info["notAfter"])
        return int((not_after - time.time()) // 86400)
    except Exception:
        return None


def _backup_status(dest: Path) -> dict:
    """备份产物新鲜度：db/ 最新 dump 的月龄 + files/ 目录最近落盘时间。"""
    out: dict[str, bool | str | None | float] = {
        "present": False, "dump_age_hours": None, "dump_name": None}
    try:
        dumps = sorted(dest.glob("db/lanshare-*.dump"), key=lambda p: p.stat().st_mtime)
        if dumps:
            newest = dumps[-1]
            out["present"] = True
            out["dump_name"] = newest.name
            out["dump_age_hours"] = round((time.time() - newest.stat().st_mtime) / 3600, 1)
    except OSError:
        pass
    return out


def _orchestrator_status() -> dict | None:
    """进程内每日备份编排（B5 兜底防线）最近一轮状态。"""
    from app.services import backup as _backup_svc
    return _backup_svc.last_status()


def _probe_public(hostport: str) -> bool:
    try:
        host, port = hostport.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=4):
            return True
    except OSError:
        return False


def gather_health(probe_public: bool = False) -> dict:
    du = shutil.disk_usage(str(SHARE_ROOT))
    used_pct = round(du.used / du.total * 100, 1)
    counts: dict = {}
    try:
        _counters = (("users", users_store.count),
                     ("photos", photos_store.count),
                     ("shares", shares_store.count),
                     ("devices", devices_store.count))
        for name, fn in _counters:
            try:
                counts[name] = fn()
            except Exception:
                counts[name] = None
    except Exception:
        pass
    return {
        "ts": time.time(),
        "db_ok": True,
        "disk_used_pct": used_pct,
        "disk_free_gb": round(du.free / 2**30, 1),
        "backup": {**_backup_status(BACKUP_DEST),
                   "orchestrator": _orchestrator_status()},
        "cert_days_left": _cert_days_left(PROJECT_ROOT / "certs" / "cert.pem"),
        "public_ok": _probe_public(PUBLIC_PROBE) if probe_public else None,
        "counts": counts,
    }


def format_report(d: dict) -> tuple[str, bool]:
    """返回 (正文, 是否有告警)。标题固定由调用方给。"""
    lines = []
    warn = False
    lines.append(f"💾 数据盘 {d['disk_used_pct']}% 已用"
                 f"（剩 {d['disk_free_gb']}GB）"
                 + (" ⚠️ 水位告警" if d["disk_used_pct"] >= 85 else ""))
    warn = warn or d["disk_used_pct"] >= 85

    b = d["backup"]
    if not b["present"]:
        lines.append("🗄️ 备份：未发现备份产物 ⚠️")
        warn = True
    else:
        stale = (b["dump_age_hours"] or 0) > 26
        lines.append(f"🗄️ 备份：{b['dump_name']}（{b['dump_age_hours']}h 前）"
                     + (" ⚠️ 超过 26h 未更新" if stale else ""))
        warn = warn or stale

    cert = d["cert_days_left"]
    if cert is None:
        lines.append("🔐 证书：读取失败（无 cert.pem？）⚠️")
        warn = True
    else:
        low = cert < 14
        lines.append(f"🔐 证书：剩余 {cert} 天" + (" ⚠️" if low else ""))
        warn = warn or low

    if d["public_ok"] is not None:
        lines.append("🌐 公网隧道：通" if d["public_ok"] else "🌐 公网隧道：探活失败 ⚠️")
        warn = warn or not d["public_ok"]

    c = d.get("counts") or {}
    lines.append(f"👥 成员 {c.get('users', '—')} · 照片 {c.get('photos', '—')} · "
                 f"在架分享 {c.get('shares', '—')} · 设备 {c.get('devices', '—')}")
    return "\n".join(lines), warn


# ─── 管理端配置与手动触发 ──────────────────────────────────

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装（07 审查批次 2 消循环）
router = APIRouter()


@router.get("/api/admin/healthreport")
def admin_get_healthreport(_admin=Depends(require_admin)):
    cfg = get_report_config()
    cfg["webhook_set"] = bool(get_setting(NOTIFY_WEBHOOK_URL))
    return cfg


@router.post("/api/admin/healthreport")
def admin_set_healthreport(body: dict, request: Request, _admin=Depends(require_admin)):
    enabled = "1" if body.get("enabled") else "0"
    hhmm = _parse_hhmm(str(body.get("time") or _DEFAULT_TIME))
    set_setting(HEALTH_REPORT_ENABLED, enabled)
    set_setting(HEALTH_REPORT_TIME, hhmm)
    audit("healthreport_set", f"enabled={enabled} time={hhmm}", ip=client_ip(request))
    return {"ok": True, "enabled": enabled == "1", "time": hhmm}




@router.post("/api/admin/healthreport/test")
async def admin_test_healthreport(request: Request, _admin=Depends(require_admin)):
    """立即采集并推送一次，返回报告全文（Webhook 没配也能先看内容）。"""
    from app.api.notify import get_webhook_url, webhook_send
    d = await run_in_threadpool(gather_health, True)
    body, warn = format_report(d)
    url = get_webhook_url()
    sent = None
    if url:
        try:
            await run_in_threadpool(webhook_send, url, "🩺 LAN Share 健康日报（手动）", body)
            sent = True
        except Exception as e:
            sent = False
            audit("healthreport_test_fail", str(e)[:120], ip=client_ip(request))
    audit("healthreport_test", f"sent={sent} warn={warn}", ip=client_ip(request))
    return {"ok": True, "sent": sent, "warn": warn, "report": body,
            "webhook_set": bool(url)}


# ─── 每日调度（startup 起线程，模式同 system.cleanup_loop）────────

async def startup():
    """由 app.main 的 lifespan 统一调度（router.on_event 已弃用）。"""
    import asyncio
    import logging

    from app.api.notify import push_webhook

    async def loop():
        global _last_sent_date
        while True:
            try:
                await run_in_threadpool(_tick)
            except Exception:
                logging.exception("health report tick failed")
            await asyncio.sleep(60)

    def _tick():
        global _last_sent_date
        cfg = get_report_config()
        today = time.strftime("%Y-%m-%d")
        if not cfg["enabled"] or _last_sent_date == today or not _due_now(cfg["time"]):
            return
        d = gather_health(probe_public=True)
        body, _warn = format_report(d)
        push_webhook("🩺 LAN Share 每日健康日报", body)
        _last_sent_date = today

    # 持有引用防 GC（R2-P3-8）：asyncio.create_task 未保存引用的任务可能
    # 被事件循环回收，日报静默停发
    global _daily_task
    _daily_task = asyncio.create_task(loop())
