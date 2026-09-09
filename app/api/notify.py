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
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.db import _db, _db_lock
from app.security import audit, require_admin, require_user
from app.store.settings import (NOTIFY_PREFS_PREFIX, NOTIFY_WEBHOOK_URL,
                                get_setting, set_setting)
from app.main import app


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
    with _db_lock:
        _db().execute(
            "INSERT INTO notifications(user_id, type, title, body, link, "
            "created_at, broadcast_id) VALUES(?,?,?,?,?,?,?)",
            (user_id, type, title, body, link, time.time(), broadcast_id))
        _db().commit()
    return True


@app.get("/api/notifications")
def list_notifications(user=Depends(require_user)):
    uid = user["user_id"]
    unread = _db().execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND read_at IS NULL",
        (uid,)).fetchone()["c"]
    rows = _db().execute(
        "SELECT id, type, title, body, link, created_at, read_at FROM notifications "
        "WHERE user_id=? ORDER BY id DESC LIMIT 50", (uid,)).fetchall()
    return {"unread": unread, "items": [{
        "id": r["id"], "type": r["type"], "title": r["title"], "body": r["body"],
        "link": r["link"], "created_at": r["created_at"],
        "read": r["read_at"] is not None,
    } for r in rows]}


@app.post("/api/notifications/read")
def mark_notifications_read(user=Depends(require_user)):
    with _db_lock:
        _db().execute(
            "UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",
            (time.time(), user["user_id"]))
        _db().commit()
    return {"ok": True}


@app.post("/api/notifications/{nid}/read")
def mark_one_read(nid: int, user=Depends(require_user)):
    with _db_lock:
        cur = _db().execute(
            "UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL",
            (time.time(), nid, user["user_id"]))
        _db().commit()
    return {"ok": True, "changed": cur.rowcount}


@app.delete("/api/notifications/{nid}")
def delete_notification(nid: int, user=Depends(require_user)):
    with _db_lock:
        _db().execute("DELETE FROM notifications WHERE id=? AND user_id=?",
                      (nid, user["user_id"]))
        _db().commit()
    return {"ok": True}


# ─── 通知偏好（按类型组）──────────────────────────────────

class PrefsBody(BaseModel):
    share: bool | None = None
    admin: bool | None = None
    system: bool | None = None


@app.get("/api/notifications/prefs")
def read_prefs(user=Depends(require_user)):
    return get_prefs(user["user_id"])


@app.put("/api/notifications/prefs")
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
    rows = _db().execute(
        "SELECT id, username, nickname FROM users WHERE is_active=1").fetchall()
    if user_ids:
        want = set(user_ids)
        return [r for r in rows if r["id"] in want]
    return list(rows)


def _deliver_broadcast(bid: str, title: str, text: str,
                       targets: list[dict], webhook: bool = True) -> int:
    """把广播逐人落站内（尊重偏好）+ 可选站外推送。返回实际站内送达数。"""
    now = time.time()
    sent = 0
    with _db_lock:
        for t in targets:
            if not get_prefs(t["id"]).get("admin", True):
                continue
            _db().execute(
                "INSERT INTO notifications(user_id, type, title, body, created_at, "
                "broadcast_id) VALUES(?,?,?,?,?,?)",
                (t["id"], "admin", title, text, now, bid))
            sent += 1
        _db().commit()
    if webhook:
        push_webhook(title, text)
    return sent


@app.post("/api/admin/notify")
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
    with _db_lock:
        _db().execute(
            "INSERT INTO admin_broadcasts(id, title, body, targets, created_by, "
            "status, sent_at, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (bid, title, text, json.dumps([t["id"] for t in targets]),
             _admin["username"], "sent", time.time(), time.time()))
        _db().commit()
    sent = _deliver_broadcast(bid, title, text, targets)
    return {"ok": True, "sent": sent, "webhook": bool(get_webhook_url()),
            "broadcast_id": bid}


@app.get("/api/admin/notifications/sent")
def admin_list_broadcasts(_admin=Depends(require_admin)):
    """发送记录 + 草稿/定时箱：含每条的未读/已读统计与撤回状态。"""
    rows = _db().execute(
        "SELECT * FROM admin_broadcasts ORDER BY created_at DESC LIMIT 100").fetchall()
    items = []
    for r in rows:
        try:
            tids = json.loads(r["targets"] or "[]")
        except (ValueError, TypeError):
            tids = []
        stat = {"unread": 0, "read": 0}
        if tids:
            marks = ",".join("?" * len(tids))
            row = _db().execute(
                f"SELECT SUM(read_at IS NULL) AS unread, SUM(read_at IS NOT NULL) AS read "
                f"FROM notifications WHERE broadcast_id=? AND user_id IN ({marks})",
                [r["id"], *tids]).fetchone()
            stat = {"unread": row["unread"] or 0, "read": row["read"] or 0}
        items.append({
            "id": r["id"], "title": r["title"], "body": r["body"],
            "target_count": len(tids),
            "status": r["status"], "scheduled_at": r["scheduled_at"],
            "sent_at": r["sent_at"], "withdrawn_at": r["withdrawn_at"],
            "created_at": r["created_at"], "created_by": r["created_by"],
            **stat,
        })
    return {"items": items}


@app.post("/api/admin/notifications/broadcast/{bid}/withdraw")
def admin_withdraw_broadcast(bid: str, _admin=Depends(require_admin)):
    """撤回：删该广播全部未读站内行（已读的保留为历史），记录标记撤回。"""
    with _db_lock:
        cur = _db().execute(
            "DELETE FROM notifications WHERE broadcast_id=? AND read_at IS NULL", (bid,))
        _db().execute(
            "UPDATE admin_broadcasts SET withdrawn_at=? WHERE id=? AND withdrawn_at IS NULL",
            (time.time(), bid))
        _db().commit()
    audit("broadcast_withdraw", bid, f"removed={cur.rowcount}")
    return {"ok": True, "removed": cur.rowcount}


@app.post("/api/admin/notifications/broadcast/{bid}/remind")
def admin_remind_broadcast(bid: str, _admin=Depends(require_admin)):
    """提醒未读：对仍处未读的成员再推一次站外 webhook（站内不重复落）。"""
    row = _db().execute("SELECT * FROM admin_broadcasts WHERE id=?", (bid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="广播不存在")
    try:
        tids = json.loads(row["targets"] or "[]")
    except (ValueError, TypeError):
        tids = []
    marks = ",".join("?" * len(tids)) if tids else "NULL"
    unread = _db().execute(
        f"SELECT user_id FROM notifications WHERE broadcast_id=? AND read_at IS NULL "
        f"AND user_id IN ({marks})", [bid, *tids]).fetchall() if tids else []
    if unread:
        push_webhook(row["title"], f"提醒：{len(unread)} 位成员尚未读 —— {(row['body'] or '')[:120]}")
    audit("broadcast_remind", bid, f"unread={len(unread)}")
    return {"ok": True, "unread": len(unread)}


class DraftBody(BaseModel):
    title: str
    body: str = ""
    user_ids: list[int] | None = None
    scheduled_at: float | None = None   # 非空 = 定时发送（unix 秒）


@app.post("/api/admin/notifications/draft")
def admin_save_draft(body: DraftBody, _admin=Depends(require_admin)):
    title = body.title.strip()[:80]
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    bid = f"bd-{time.time_ns()}"
    status = "scheduled" if body.scheduled_at else "draft"
    with _db_lock:
        _db().execute(
            "INSERT INTO admin_broadcasts(id, title, body, targets, created_by, "
            "status, scheduled_at, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (bid, title, (body.body or "").strip()[:500],
             json.dumps(body.user_ids or []), _admin["username"],
             status, body.scheduled_at, time.time()))
        _db().commit()
    return {"ok": True, "id": bid}


@app.post("/api/admin/notifications/draft/{bid}/send")
def admin_send_draft(bid: str, _admin=Depends(require_admin)):
    row = _db().execute(
        "SELECT * FROM admin_broadcasts WHERE id=? AND withdrawn_at IS NULL",
        (bid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="草稿不存在")
    if row["status"] == "sent":
        raise HTTPException(status_code=409, detail="该广播已发送")
    targets = _resolve_targets(json.loads(row["targets"] or "[]") or None)
    with _db_lock:
        _db().execute(
            "UPDATE admin_broadcasts SET status='sent', sent_at=?, targets=? WHERE id=?",
            (time.time(), json.dumps([t["id"] for t in targets]), bid))
        _db().commit()
    sent = _deliver_broadcast(bid, row["title"], row["body"] or "", targets)
    audit("broadcast_send", bid, f"sent={sent}")
    return {"ok": True, "sent": sent}


@app.delete("/api/admin/notifications/draft/{bid}")
def admin_delete_draft(bid: str, _admin=Depends(require_admin)):
    with _db_lock:
        _db().execute(
            "DELETE FROM admin_broadcasts WHERE id=? AND status IN ('draft','scheduled')",
            (bid,))
        _db().commit()
    return {"ok": True}


@app.on_event("startup")
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
        rows = _db().execute(
            "SELECT id, title, body, targets FROM admin_broadcasts "
            "WHERE status='scheduled' AND scheduled_at IS NOT NULL AND scheduled_at<=?",
            (time.time(),)).fetchall()
        for r in rows:
            targets = _resolve_targets(json.loads(r["targets"] or "[]") or None)
            with _db_lock:
                _db().execute(
                    "UPDATE admin_broadcasts SET status='sent', sent_at=? WHERE id=?",
                    (time.time(), r["id"]))
                _db().commit()
            _deliver_broadcast(r["id"], r["title"], r["body"] or "", targets)
            audit("broadcast_scheduled_send", r["id"], f"sent={len(targets)}")
        return len(rows)

    await run_in_threadpool(_run)


# ─── 站外事件推送（通知 bot）：管理端配置 GET 模板 URL ──────────
# 模板占位符 {title}/{body} 会被 URL 编码后替换，GET 请求发出——一套格式通吃
# ntfy（https://ntfy.sh/主题）、Bark（https://api.day.app/key）、Server酱、
# Telegram（bot API sendMessage 的 GET 形式）等常见推送渠道。

def get_webhook_url() -> str:
    return get_setting(NOTIFY_WEBHOOK_URL).strip()


def webhook_send(url: str, title: str, body: str = "") -> None:
    """同步发送（管理端测试按钮用，异常向上抛）。"""
    final = (url.replace("{title}", urllib.parse.quote_plus(title))
                .replace("{body}", urllib.parse.quote_plus(body)))
    req = urllib.request.Request(final, headers={"User-Agent": "LANShare-webhook/1.0"})
    with urllib.request.urlopen(req, timeout=6) as resp:
        resp.read(64)   # 读完一小段确保送达，随即关闭


_webhook_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webhook-push")


def push_webhook(title: str, body: str = "") -> None:
    """业务事件出口：有界线程池（2 worker）发送，失败只记审计、绝不阻塞/影响请求方；
    突发事件在池的默认无界队列中排队（Python 标准库无有界队列，极端洪峰靠
    限速/低频业务语义兜底）。"""
    url = get_webhook_url()
    if not url:
        return

    def _run():
        try:
            webhook_send(url, title, body)
        except Exception as e:
            audit("webhook_fail", title[:60], str(e)[:120])

    _webhook_pool.submit(_run)
