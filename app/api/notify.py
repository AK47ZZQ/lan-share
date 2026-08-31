"""站内通知：定向分享等事件的收件箱（每人只读自己的）+ 站外事件推送（通知 bot）。"""
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends

from app.db import _db, _db_lock
from app.security import audit, require_user
from app.main import app


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


# ─── 站外事件推送（通知 bot）：管理端配置 GET 模板 URL ──────────
# 模板占位符 {title}/{body} 会被 URL 编码后替换，GET 请求发出——一套格式通吃
# ntfy（https://ntfy.sh/主题）、Bark（https://api.day.app/key）、Server酱、
# Telegram（bot API sendMessage 的 GET 形式）等常见推送渠道。

_WEBHOOK_KEY = "notify_webhook_url"


def get_webhook_url() -> str:
    row = _db().execute("SELECT value FROM settings WHERE key=?", (_WEBHOOK_KEY,)).fetchone()
    return (row["value"] or "").strip() if row else ""


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
