"""站内通知：收件箱 + 通知偏好 + 管理端广播管理（改进建议 9.6-P2-14）。

- 收件箱：每人只读自己的；支持单条已读/删除（此前只有全量已读）。
- 通知偏好：按类型组（share/admin/system）开关站内接收，存 settings 表
  （key=notify_prefs_user_<uid>，JSON）；发送方统一走 insert_notification()
  检查偏好后再落库，webhook 站外推送不受偏好影响（管理端全局配置）。
- 广播管理：POST /api/admin/notify 落一条 admin_broadcasts 记录（targets
  存全集 JSON），此后可撤回（删未读站内行）、提醒未读（再推站外）、
  草稿/定时发送（startup 周期任务扫描到期 scheduled）。
"""
import ipaddress
import json
import socket
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.db import _db, _db_lock
from app.security import audit, client_ip, require_admin, require_user
from app.store import users as users_store
from app.settings import (NOTIFY_PREFS_PREFIX, NOTIFY_WEBHOOK_URL,
                                get_setting, set_setting)


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


# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装（07 审查批次 2 消循环）
router = APIRouter()


@router.get("/api/notifications")
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


@router.post("/api/notifications/read")
def mark_notifications_read(user=Depends(require_user)):
    with _db_lock:
        _db().execute(
            "UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",
            (time.time(), user["user_id"]))
        _db().commit()
    return {"ok": True}


@router.post("/api/notifications/{nid}/read")
def mark_one_read(nid: int, user=Depends(require_user)):
    with _db_lock:
        cur = _db().execute(
            "UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL",
            (time.time(), nid, user["user_id"]))
        _db().commit()
    return {"ok": True, "changed": cur.rowcount}


@router.delete("/api/notifications/{nid}")
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


@router.get("/api/admin/notifications/sent")
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
                f"SELECT SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS unread, "
                f"SUM(CASE WHEN read_at IS NOT NULL THEN 1 ELSE 0 END) AS read "
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


@router.post("/api/admin/notifications/broadcast/{bid}/withdraw")
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


@router.post("/api/admin/notifications/broadcast/{bid}/remind")
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


@router.post("/api/admin/notifications/draft")
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


@router.post("/api/admin/notifications/draft/{bid}/send")
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


@router.delete("/api/admin/notifications/draft/{bid}")
def admin_delete_draft(bid: str, _admin=Depends(require_admin)):
    with _db_lock:
        _db().execute(
            "DELETE FROM admin_broadcasts WHERE id=? AND status IN ('draft','scheduled')",
            (bid,))
        _db().commit()
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


def _ip_blocked(ip: str) -> bool:
    """私网/回环/链路本地/保留/多播/未指定——SSRF 的目标特征集。"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True   # 解析不出的一律当不可信
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
            or a.is_reserved or a.is_unspecified)


def assert_public_webhook_url(url: str) -> None:
    """SSRF 防护（06 审查 P2）：webhook 只允许指向公网地址。

    - scheme 必须 http/https；
    - 主机是**字面 IP** 时直接判私网/保留段（挡住 http://192.168.x / 127.0.0.1 / [::1]）；
    - 主机是**域名**时尽力解析，能解析则要求全部地址都是公网地址
      （挡住"域名指向内网"的常见绕过）；解析失败不阻断——DNS 暂不可用的环境
      仍要允许保存配置，实际出网成败由 urlopen 决定。

    残留风险（有意接受）：不防 DNS rebinding（解析时校验、连接时复解析之间的
    窗口）。webhook 仅 admin 可配置，作为纵深防线足够。
    **重定向已闭环**（11 号复审 P2-4）：发送走 `_safe_opener`，每一跳复校验。
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("webhook 地址需以 http(s):// 开头")
    host = parts.hostname
    if not host:
        raise ValueError("webhook 地址缺少主机名")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except OSError:
        return   # 解析不了：不阻断（见 docstring）
    for info in infos:
        if _ip_blocked(str(info[4][0])):
            raise ValueError("webhook 目标指向内网/保留地址，已拒绝")


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向逐跳复校验（11 号复审 P2-4）。

    `urlopen` 默认**静默跟随 302**——只在发送前校验初始 URL 等于没校验终点：
    一个合法的公网域名只要 302 到 `http://127.0.0.1/...` 即可绕过 SSRF 防线。
    这里在每一跳真正发起请求前重新跑一遍 `assert_public_webhook_url()`，
    非公网目标直接拒绝（抛 ValueError，由调用方按 webhook 失败处理）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_public_webhook_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# 走自定义 opener：urlopen 的默认 opener 不校验重定向目标
_safe_opener = urllib.request.build_opener(_ValidatingRedirectHandler)


def _webhook_open(req, timeout: float = 6):
    """出网发送的唯一出口。

    独立成函数有两个目的：① 是 SSRF 逐跳校验的落点；② 是**测试桩点**——
    此前测试 patch 的是 `urllib.request.urlopen`，那是 stdlib 全局，改动实现方式
    就会静默绕过桩点（本质是"桩在错误的接缝上"）。把接缝挪到本模块自己的名字上，
    测试与实现之间就有了显式契约。
    """
    return _safe_opener.open(req, timeout=timeout)


def webhook_send(url: str, title: str, body: str = "") -> None:
    """同步发送（管理端测试按钮用，异常向上抛）。"""
    final = (url.replace("{title}", urllib.parse.quote_plus(title))
                .replace("{body}", urllib.parse.quote_plus(body)))
    assert_public_webhook_url(final)
    req = urllib.request.Request(final, headers={"User-Agent": "LANShare-webhook/1.0"})
    with _webhook_open(req, timeout=6) as resp:
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


# ── 公告栏（v3.40）：面向全家的持续布告，区别于一次性推送 ———
# 公告 = 主页公告栏条目（登录可见，站长增删）；通知 = 个人收件箱推送。
# 广播（admin_broadcasts）仍承担"推送一次性通知"，公告承担"长期挂着的告知"。


@router.get("/api/announcements")
def announcements_list(_user: dict = Depends(require_user)):
    rows = _db().execute(
        "SELECT id, title, body, created_by, created_at FROM announcements "
        "ORDER BY id DESC LIMIT 10").fetchall()
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
    with _db_lock:
        _db().execute(
            "INSERT INTO announcements(title, body, created_by, created_at) "
            "VALUES(?,?,?,?)",
            (title[:80], body.body.strip()[:500],
             _admin["username"], time.time()))
        _db().commit()
    audit("announcement.create", target=title[:40], ip=client_ip(request))
    return {"ok": True}


@router.delete("/api/admin/announcements/{aid}")
def announcement_delete(aid: int, request: Request,
                        _admin=Depends(require_admin)):
    with _db_lock:
        cur = _db().execute("DELETE FROM announcements WHERE id = ?", (aid,))
        _db().commit()
    if cur.rowcount == 0:
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
    rows = (_db().execute(
        "SELECT id, user_id, type, title, body, link, created_at, read_at, "
        "broadcast_id FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (uid, limit)).fetchall() if uid is not None else _db().execute(
        "SELECT id, user_id, type, title, body, link, created_at, read_at, "
        "broadcast_id FROM notifications ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall())
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
    with _db_lock:
        cur = _db().execute("DELETE FROM notifications WHERE id = ?", (nid,))
        _db().commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="通知不存在")
    audit("admin.notification.delete", target=str(nid), ip=client_ip(request))
    return {"ok": True}


@router.delete("/api/admin/notifications/broadcast/{bid}")
def admin_broadcast_purge(bid: str, request: Request,
                          _admin=Depends(require_admin)):
    """彻底删除广播：记录 + 已送达的站内通知一并清除（撤回只删未读，这个删干净）。"""
    with _db_lock:
        cur = _db().execute("DELETE FROM admin_broadcasts WHERE id = ?", (bid,))
        n = _db().execute(
            "DELETE FROM notifications WHERE broadcast_id = ?", (bid,))
        _db().commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="广播不存在")
    audit("admin.broadcast.purge", target=bid[:12] + "…",
          detail=f"notifications={n.rowcount}", ip=client_ip(request))
    return {"ok": True, "notifications_removed": n.rowcount}
