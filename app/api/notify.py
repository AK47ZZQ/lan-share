"""站内通知：收件箱 + 通知偏好 + 管理端广播管理（改进建议 9.6-P2-14）。

- 收件箱：每人只读自己的；支持单条已读/删除（此前只有全量已读）。
- 通知偏好：按类型组（share/admin/system）开关站内接收，存 settings 表
  （key=notify_prefs_user_<uid>，JSON）；发送方统一走 insert_notification()
  检查偏好后再落库，webhook 站外推送不受偏好影响（管理端全局配置）。
- 广播管理：POST /api/admin/notify 落一条 admin_broadcasts 记录（targets
  存全集 JSON），此后可撤回（删未读站内行）、提醒未读（再推站外）、
  草稿/定时发送（startup 周期任务扫描到期 scheduled）。
"""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.db import _db_lock, transaction
from app.security import audit, client_ip, require_admin, require_user
from app.api import events as events_bus
from app.store import notifications as notif_store
from app.store import users as users_store
from app.settings import (NOTIFY_PREFS_PREFIX, get_setting, set_setting)


def _pref_key(uid: int) -> str:
    return f"{NOTIFY_PREFS_PREFIX}{uid}"


def _groups_of(t: str) -> str:
    """通知类型 → 偏好组：share=分享互动、admin=管理广播、其余归 system。"""
    if t in ("share", "admin"):
        return t
    return "system"


def get_prefs(uid: int) -> dict:
    raw = get_setting(_pref_key(uid))
    if not raw:
        return {"share": True, "admin": True, "system": True}
    try:
        prefs = json.loads(raw)
    except (ValueError, TypeError):
        return {"share": True, "admin": True, "system": True}
    return {**{"share": True, "admin": True, "system": True}, **prefs}


def insert_notification(user_id: int, type: str, title: str, body: str = "",
                        link: str | None = None,
                        broadcast_id: str | None = None) -> bool:
    """站内通知统一出口：尊重接收方偏好后落库。返回是否真的写入。"""
    if not get_prefs(user_id).get(_groups_of(type), True):
        return False
    notif_store.insert(user_id, type, title, body, link, broadcast_id)
    events_bus.publish(user_id, {"kind": "notification"})   # SSE 实时信号（B3）
    return True


# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装（07 审查批次 2 消循环）
router = APIRouter()


@router.get("/api/notifications")
def list_notifications(user=Depends(require_user)):
    uid = user["user_id"]
    unread = notif_store.count_unread(uid)
    rows = notif_store.list_for_user(uid)
    return {"unread": unread, "items": [{
        "id": r["id"], "type": r["type"], "title": r["title"], "body": r["body"],
        "link": r["link"], "created_at": r["created_at"],
        "read": r["read_at"] is not None,
    } for r in rows]}


@router.post("/api/notifications/read")
def mark_notifications_read(user=Depends(require_user)):
    notif_store.mark_all_read(user["user_id"])
    return {"ok": True}


@router.post("/api/notifications/{nid}/read")
def mark_one_read(nid: int, user=Depends(require_user)):
    changed = notif_store.mark_one_read(nid, user["user_id"])
    return {"ok": True, "changed": changed}


@router.delete("/api/notifications/{nid}")
def delete_notification(nid: int, user=Depends(require_user)):
    notif_store.delete_for_user(nid, user["user_id"])
    return {"ok": True}


# ─── 通知偏好（按类型组）──────────────────────────────────

class PrefsBody(BaseModel):
    share: bool | None = None
    admin: bool | None = None
    system: bool | None = None


@router.get("/api/notifications/prefs")
def read_prefs(user=Depends(require_user)):
    return get_prefs(user["user_id"])


@router.put("/api/notifications/prefs")
def write_prefs(body: PrefsBody, user=Depends(require_user)):
    prefs = get_prefs(user["user_id"])
    for k in ("share", "admin", "system"):
        v = getattr(body, k)
        if v is not None:
            prefs[k] = bool(v)
    set_setting(_pref_key(user["user_id"]), json.dumps(prefs))
    return {"ok": True, **prefs}


# ─── 管理端广播管理 ──────────────────────────────────────

class NotifyBody(BaseModel):
    title: str
    body: str = ""
    user_ids: list[int] | None = None   # None/空 = 全体在职用户


def _resolve_targets(user_ids: list[int] | None) -> list[dict]:
    """定向成员 or 全体在职用户（沿用原 /api/admin/notify 语义）。"""
    rows = users_store.list_active_brief()
    if user_ids:
        want = set(user_ids)
        return [r for r in rows if r["id"] in want]
    return list(rows)


def _deliver_broadcast(bid: str, title: str, text: str,
                       targets: list[dict], webhook: bool = True) -> int:
    """把广播逐人落站内（尊重偏好）+ 可选站外推送。返回实际站内送达数。"""
    sent = 0
    # 交付原子性（终审 P2-1）：整批一个事务——中途崩溃不会留下"部分人收到"
    # 的无账可查半交付（store 写方法事务内自动推迟 commit）
    with _db_lock, transaction():
        for t in targets:
            # insert_notification 内含同一套 admin 组偏好过滤 + SSE 信号，直接复用
            if insert_notification(t["id"], "admin", title, text, broadcast_id=bid):
                sent += 1
    if webhook:
        push_webhook(title, text)
    return sent


@router.post("/api/admin/notify")
def admin_broadcast_notify(body: NotifyBody, _admin=Depends(require_admin)):
    title = body.title.strip()[:80]
    text = (body.body or "").strip()[:500]
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    targets = _resolve_targets(body.user_ids)
    if body.user_ids and not targets:
        # 显式定向却一个都匹配不上：报错比静默发 0 人友好（管理员能发现 ID 打错）
        raise HTTPException(status_code=404, detail="目标用户不存在或已停用")
    bid = f"bc-{time.time_ns()}"
    notif_store.create_broadcast(bid, title, text,
                                 json.dumps([t["id"] for t in targets]),
                                 _admin["username"], "sent", time.time(),
                                 sent_at=time.time())
    sent = _deliver_broadcast(bid, title, text, targets)
    return {"ok": True, "sent": sent, "webhook": bool(get_webhook_url()),
            "broadcast_id": bid}


@router.get("/api/admin/notifications/sent")
def admin_list_broadcasts(_admin=Depends(require_admin)):
    """发送记录 + 草稿/定时箱：含每条的未读/已读统计与撤回状态。"""
    rows = notif_store.list_broadcasts()
    items = []
    for r in rows:
        try:
            tids = json.loads(r["targets"] or "[]")
        except (ValueError, TypeError):
            tids = []
        stat = {"unread": 0, "read": 0}
        if tids:
            stat = notif_store.stats_by_broadcast(r["id"], tids)
        items.append({
            "id": r["id"], "title": r["title"], "body": r["body"],
            "target_count": len(tids),
            "status": r["status"], "scheduled_at": r["scheduled_at"],
            "sent_at": r["sent_at"], "withdrawn_at": r["withdrawn_at"],
            "created_at": r["created_at"], "created_by": r["created_by"],
            **stat,
        })
    return {"items": items}


@router.post("/api/admin/notifications/broadcast/{bid}/withdraw")
def admin_withdraw_broadcast(bid: str, _admin=Depends(require_admin)):
    """撤回：删该广播全部未读站内行（已读的保留为历史），记录标记撤回。"""
    with _db_lock, transaction():
        removed = notif_store.delete_unread_by_broadcast(bid)
        notif_store.mark_broadcast_withdrawn(bid, time.time())
    audit("broadcast_withdraw", bid, f"removed={removed}")
    return {"ok": True, "removed": removed}


@router.post("/api/admin/notifications/broadcast/{bid}/remind")
def admin_remind_broadcast(bid: str, _admin=Depends(require_admin)):
    """提醒未读：对仍处未读的成员再推一次站外 webhook（站内不重复落）。"""
    row = notif_store.get_broadcast(bid)
    if not row:
        raise HTTPException(status_code=404, detail="广播不存在")
    try:
        tids = json.loads(row["targets"] or "[]")
    except (ValueError, TypeError):
        tids = []
    unread = notif_store.list_unread_users(bid, tids) if tids else []
    if unread:
        push_webhook(row["title"], f"提醒：{len(unread)} 位成员尚未读 —— {(row['body'] or '')[:120]}")
    audit("broadcast_remind", bid, f"unread={len(unread)}")
    return {"ok": True, "unread": len(unread)}


class DraftBody(BaseModel):
    title: str
    body: str = ""
    user_ids: list[int] | None = None
    scheduled_at: float | None = None   # 非空 = 定时发送（unix 秒）


@router.post("/api/admin/notifications/draft")
def admin_save_draft(body: DraftBody, _admin=Depends(require_admin)):
    title = body.title.strip()[:80]
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    bid = f"bd-{time.time_ns()}"
    status = "scheduled" if body.scheduled_at else "draft"
    notif_store.create_broadcast(bid, title, (body.body or "").strip()[:500],
                                 json.dumps(body.user_ids or []), _admin["username"],
                                 status, time.time(), scheduled_at=body.scheduled_at)
    return {"ok": True, "id": bid}


@router.post("/api/admin/notifications/draft/{bid}/send")
def admin_send_draft(bid: str, _admin=Depends(require_admin)):
    row = notif_store.get_live_broadcast(bid)
    if not row:
        raise HTTPException(status_code=404, detail="草稿不存在")
    if row["status"] == "sent":
        raise HTTPException(status_code=409, detail="该广播已发送")
    targets = _resolve_targets(json.loads(row["targets"] or "[]") or None)
    notif_store.mark_broadcast_sent(bid, time.time(),
                                    json.dumps([t["id"] for t in targets]))
    sent = _deliver_broadcast(bid, row["title"], row["body"] or "", targets)
    audit("broadcast_send", bid, f"sent={sent}")
    return {"ok": True, "sent": sent}


@router.delete("/api/admin/notifications/draft/{bid}")
def admin_delete_draft(bid: str, _admin=Depends(require_admin)):
    notif_store.delete_draft(bid)
    return {"ok": True}


@router.on_event("startup")
async def start_scheduled_broadcasts():
    """定时广播：周期扫描到期的 scheduled 记录并发送（与清理循环同款
    线程池模式——同步 DB IO 不进事件循环）。"""
    async def loop():
        import logging
        while True:
            try:
                await _fire_due_once()
            except Exception:
                logging.exception("scheduled broadcast round failed")
            await _sleep(60)
    async def _sleep(sec: float):
        import asyncio
        await asyncio.sleep(sec)
    import asyncio
    asyncio.create_task(loop())


async def _fire_due_once():
    from fastapi.concurrency import run_in_threadpool

    def _run():
        rows = notif_store.list_due_broadcasts(time.time())
        for r in rows:
            targets = _resolve_targets(json.loads(r["targets"] or "[]") or None)
            notif_store.mark_broadcast_sent(r["id"], time.time())
            _deliver_broadcast(r["id"], r["title"], r["body"] or "", targets)
            audit("broadcast_scheduled_send", r["id"], f"sent={len(targets)}")
        return len(rows)

    await run_in_threadpool(_run)


# ── 站外事件推送（通知 bot）：B2 拆至 app/services/webhook.py ————
# re-export 保兼容：share/healthreport/admin 等消费方与既有测试仍从
# app.api.notify import 这些名字。桩点 _webhook_open 的权威位置已随实现
# 搬到 services.webhook（测试 patch 目标同步）。
from app.services.webhook import (  # noqa: E402,F401
    _ValidatingRedirectHandler, _ip_blocked, _safe_opener, _webhook_open,
    assert_public_webhook_url, get_webhook_url, push_webhook, webhook_send,
)

# ── 公告栏（v3.40）：面向全家的持续布告，区别于一次性推送 ———
# 公告 = 主页公告栏条目（登录可见，站长增删）；通知 = 个人收件箱推送。
# 广播（admin_broadcasts）仍承担"推送一次性通知"，公告承担"长期挂着的告知"。


@router.get("/api/announcements")
def announcements_list(_user: dict = Depends(require_user)):
    rows = notif_store.list_recent_announcements()
    return {"items": [dict(r) for r in rows]}


class AnnouncementBody(BaseModel):
    title: str
    body: str = ""


@router.post("/api/admin/announcements")
def announcement_create(body: AnnouncementBody, request: Request,
                        _admin=Depends(require_admin)):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="标题不能为空")
    notif_store.create_announcement(title[:80], body.body.strip()[:500],
                                    _admin["username"])
    audit("announcement.create", target=title[:40], ip=client_ip(request))
    return {"ok": True}


@router.delete("/api/admin/announcements/{aid}")
def announcement_delete(aid: int, request: Request,
                        _admin=Depends(require_admin)):
    if notif_store.delete_announcement(aid) == 0:
        raise HTTPException(status_code=404, detail="公告不存在")
    audit("announcement.delete", target=str(aid), ip=client_ip(request))
    return {"ok": True}


# ── 站内通知管理（v3.40）：admin 全站视角 + 单条删除 ————————


@router.get("/api/admin/notifications")
def admin_notifications_list(request: Request, _admin=Depends(require_admin),
                             username: str = "", limit: int = 100):
    """全站通知近况（新→旧），可按成员筛选；用户名映射走 users store。"""
    limit = max(1, min(limit, 200))
    uid = users_store.id_by_username(username) if username.strip() else None
    if username.strip() and uid is None:
        return {"items": []}
    rows = notif_store.list_for_admin(uid, limit)
    items = []
    for r in rows:
        d = dict(r)
        uid = d.pop("user_id")
        # 成员被删后其通知可能仍残留（无外键级联的存量库）：回退显示 #uid
        d["username"] = users_store.get_username(uid) or f"#{uid}"
        items.append(d)
    return {"items": items}


@router.delete("/api/admin/notifications/{nid}")
def admin_notification_delete(nid: int, request: Request,
                              _admin=Depends(require_admin)):
    if notif_store.delete(nid) == 0:
        raise HTTPException(status_code=404, detail="通知不存在")
    audit("admin.notification.delete", target=str(nid), ip=client_ip(request))
    return {"ok": True}


@router.delete("/api/admin/notifications/broadcast/{bid}")
def admin_broadcast_purge(bid: str, request: Request,
                          _admin=Depends(require_admin)):
    """彻底删除广播：记录 + 已送达的站内通知一并清除（撤回只删未读，这个删干净）。"""
    with _db_lock, transaction():
        removed = notif_store.delete_by_broadcast(bid)
        n = notif_store.delete_broadcast(bid)
    if n == 0:
        raise HTTPException(status_code=404, detail="广播不存在")
    audit("admin.broadcast.purge", target=bid[:12] + "…",
          detail=f"notifications={removed}", ip=client_ip(request))
    return {"ok": True, "notifications_removed": removed}
