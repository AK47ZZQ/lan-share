"""站内通知：定向分享等事件的收件箱（每人只读自己的）。"""
import time

from fastapi import Depends

from app.db import _db, _db_lock
from app.security import require_user
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
