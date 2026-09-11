"""管理台（Web 控制台）端点：总览聚合 + 内容治理 + 会话/设备治理。

产品定位更新（2026-09-10）：App 主攻内容入口，Web 转为站长管理工作台。
本模块为控制台供数；除会话/设备吊销外全部只读。全部 require_admin。

大文件 TOP 与疑似重复共用同一次目录遍历（跳过隐藏目录）：
blobs 表只存秒传样本（每 hash 一行、无全量文件 hash），故"重复"按
同尺寸聚类给出疑似组（免全盘哈希，标注"疑似"由站长自行核对）。
"""
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.config import SHARE_ROOT
from app.api.appver import DIST_DIR
from app.db import _db, _db_lock
from app.security import COOKIE_NAME, _sha, audit, client_ip, require_admin
from app.services import traffic
from app.store import users as users_store

router = APIRouter()


def _username_of(uid: int) -> str:
    """users 表 SQL 归 store（AST 守卫）：用户名映射走 store。"""
    return users_store.get_username(uid) or f"#{uid}"


def _walk_files(root: Path):
    """遍历共享空间常规文件（跳过隐藏名：.trash、.thumbs、.healthz-probe 等），
    产出 (rel_path, size, mtime)。sync 处理器自动进线程池，不冻结事件循环
    （与 DAV/PIL 同款纪律）。"""
    stack = [str(root)]
    root_s = str(root)
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat()
                            rel = e.path[len(root_s):].replace("\\", "/").lstrip("/")
                            yield rel, st.st_size, st.st_mtime
                    except OSError:
                        continue
        except OSError:
            continue


def _app_latest() -> dict | None:
    """App 分发版本（version.json），未配置时给 null 不挡总览。"""
    try:
        data = json.loads((SHARE_ROOT / DIST_DIR / "version.json").read_text(encoding="utf-8"))
        return {"version": str(data.get("version")), "build": int(data.get("build"))}
    except Exception:
        return None


@router.get("/api/admin/overview")
def admin_overview(_admin=Depends(require_admin)):
    """控制台总览：一张聚合快照（全部 COUNT/SUM，单次往返拼装）。"""
    now = time.time()
    with _db_lock:
        # 单值聚合一律 AS v 按列名取——PG 行（RealDictRow）不支持整数下标，
        # SQLite（测试引擎）支持，裸 [0] 会成"测试过、生产炸"的双引擎盲区
        def one(sql, params=()):
            return _db().execute(sql, params).fetchone()["v"]
        # users 表 SQL 归 store（AST 守卫）：概览统计由 list_for_admin 聚合
        _urows = users_store.list_for_admin()
        users_total = len(_urows)
        users_active = sum(1 for r in _urows if r["is_active"])
        users_admin = sum(1 for r in _urows if r["role"] == "admin")
        users_guest = sum(1 for r in _urows if r["role"] == "guest")
        photos_count = one("SELECT COUNT(*) AS v FROM photos")
        photos_size = one("SELECT COALESCE(SUM(size), 0) AS v FROM photos")
        shares_total = one("SELECT COUNT(*) AS v FROM shares")
        shares_active = one("SELECT COUNT(*) AS v FROM shares WHERE expires_at > ?", (now,))
        trash_count = one("SELECT COUNT(*) AS v FROM trash")
        trash_size = one("SELECT COALESCE(SUM(size), 0) AS v FROM trash")
        blobs_count = one("SELECT COUNT(*) AS v FROM blobs")
        blobs_size = one("SELECT COALESCE(SUM(size), 0) AS v FROM blobs")
        sessions_active = one("SELECT COUNT(*) AS v FROM sessions WHERE expires_at > ?", (now,))
        devices_active = one("SELECT COUNT(*) AS v FROM device_tokens WHERE expires_at > ?", (now,))
        downloads_total = one("SELECT COALESCE(SUM(downloads), 0) AS v FROM shares")

    import shutil
    du = shutil.disk_usage(str(SHARE_ROOT))
    return {
        "users": {"total": users_total, "active": users_active,
                  "admins": users_admin, "guests": users_guest},
        "photos": {"count": photos_count, "size": photos_size},
        "shares": {"total": shares_total, "active": shares_active,
                   "downloads": downloads_total},
        "trash": {"count": trash_count, "size": trash_size},
        "blobs": {"count": blobs_count, "size": blobs_size},
        "sessions": {"active": sessions_active},
        "devices": {"active": devices_active},
        "disk": {"used_pct": round(du.used / du.total * 100, 1),
                 "free_gb": round(du.free / 2**30, 1),
                 "total_gb": round(du.total / 2**30, 1),
                 "free_bytes": du.free, "total_bytes": du.total, "used_bytes": du.used},
        "app_latest": _app_latest(),
        "traffic": traffic.summarize(),
        "server_time": now,
    }


@router.get("/api/admin/large-files")
def admin_large_files(_admin=Depends(require_admin), limit: int = Query(50, ge=1, le=200)):
    """大文件 TOP N（跳过隐藏目录）。"""
    files = list(_walk_files(SHARE_ROOT))
    files.sort(key=lambda t: t[1], reverse=True)
    return {
        "items": [{"path": p, "size": s, "mtime": m} for p, s, m in files[:limit]],
        "total_files": len(files),
        "total_size": sum(s for _, s, _ in files),
    }


@router.get("/api/admin/duplicates")
def admin_duplicates(_admin=Depends(require_admin),
                     min_size: int = Query(1024 * 1024, ge=0),
                     limit: int = Query(50, ge=1, le=200)):
    """疑似重复：同尺寸聚类（size 相同即归组，不做全盘哈希）。
    wasted = 组尺寸 × (组内数 − 1)，按浪费字节降序。"""
    by_size: dict[int, list[str]] = {}
    for p, s, _m in _walk_files(SHARE_ROOT):
        if s >= min_size:
            by_size.setdefault(s, []).append(p)
    groups = [(s, ps) for s, ps in by_size.items() if len(ps) > 1]
    groups.sort(key=lambda g: g[0] * (len(g[1]) - 1), reverse=True)
    return {
        "groups": [{"size": s, "paths": ps, "wasted": s * (len(ps) - 1)}
                   for s, ps in groups[:limit]],
        "group_count": len(groups),
    }


@router.get("/api/admin/audit")
def admin_audit(_admin=Depends(require_admin), limit: int = Query(100, ge=1, le=500),
                action: str = Query("", max_length=64)):
    """审计日志近况（audit_log 自带 AUDIT_KEEP 水位）。

    action 非空时按前缀筛选（如 login → login/login_fail/login_locked；
    share → share_download 等），响应带 actions（现存 DISTINCT 动作全集，
    表受 AUDIT_KEEP 水位约束不无限涨）供前端下拉。"""
    acts = sorted({r["action"] for r in
                   _db().execute("SELECT DISTINCT action FROM audit_log").fetchall()})
    where, args = "", []
    if action:
        where, args = " WHERE action LIKE ?", [action.replace("%", "") + "%"]
    rows = _db().execute(
        "SELECT ts, ip, action, target, detail, username FROM audit_log" + where +
        " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return {"items": [dict(r) for r in rows], "actions": acts}


@router.get("/api/admin/sessions")
def admin_sessions(_admin=Depends(require_admin)):
    """活跃 Web 会话（token_hash 仅作吊销句柄，不回传原文令牌——库里本就只有哈希）。"""
    rows = _db().execute(
        "SELECT token_hash, user_id, created_at, expires_at, ip, ua FROM sessions "
        "WHERE expires_at > ? ORDER BY created_at DESC", (time.time(),)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["username"] = _username_of(d.pop("user_id"))
        items.append(d)
    return {"items": items}


@router.delete("/api/admin/sessions/{token_hash}")
def admin_revoke_session(token_hash: str, request: Request,
                         _admin=Depends(require_admin)):
    with _db_lock:
        cur = _db().execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        _db().commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    audit("admin.session.revoke", target=token_hash[:12] + "…", ip=client_ip(request))
    return {"ok": True}


@router.get("/api/admin/devices")
def admin_devices(_admin=Depends(require_admin)):
    """活跃 App 设备（device_tokens）。"""
    rows = _db().execute(
        "SELECT id, user_id, name, created_at, last_seen, expires_at FROM device_tokens "
        "WHERE expires_at > ? ORDER BY created_at DESC", (time.time(),)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["username"] = _username_of(d.pop("user_id"))
        items.append(d)
    return {"items": items}


@router.delete("/api/admin/devices/{device_id}")
def admin_revoke_device(device_id: int, request: Request,
                        _admin=Depends(require_admin)):
    with _db_lock:
        cur = _db().execute("DELETE FROM device_tokens WHERE id = ?", (device_id,))
        _db().commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="设备不存在或已过期")
    audit("admin.device.revoke", target=str(device_id), ip=client_ip(request))
    return {"ok": True}


# ── 会话治理增强（v3.40）：按账号一键下线 / 全站会话清空 ─———


class KickBody(BaseModel):
    scope: str = "all"  # all | web | app


@router.post("/api/admin/users/{uid}/kick")
def admin_kick_user(uid: int, request: Request, body: KickBody | None = None,
                    _admin=Depends(require_admin)):
    """按账号一键下线：scope=all 全部 / web 仅 Web 会话 / app 仅 App 设备。
    对当前登录账号拒绝 all/web（防止站长把自己踢下线）；app 范围允许（可踢自己的旧手机）。"""
    scope = (body.scope if body and body.scope in ("all", "web", "app") else "all")
    # 会话 principal 行的键是 user_id（sessions.user_id），Bearer 行也一致
    if uid == _admin["user_id"] and scope in ("all", "web"):
        raise HTTPException(status_code=400, detail="不能对当前登录账号吊销 Web 会话（会把自己踢下线）")
    username = users_store.get_username(uid)
    if username is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    with _db_lock:
        cs = (_db().execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
              if scope in ("all", "web") else None)
        cd = (_db().execute("DELETE FROM device_tokens WHERE user_id = ?", (uid,))
              if scope in ("all", "app") else None)
        _db().commit()
    counts = {"sessions": cs.rowcount if cs is not None else 0,
              "devices": cd.rowcount if cd is not None else 0}
    audit("admin.kick", target=username,
          detail=f"scope={scope} web={counts['sessions']} app={counts['devices']}",
          ip=client_ip(request))
    return {"ok": True, **counts}


@router.delete("/api/admin/sessions")
def admin_sessions_purge(request: Request, _admin=Depends(require_admin)):
    """全站会话清空（应急"全部下线"）：保留当前请求自己的会话。"""
    keep = _sha(request.cookies.get(COOKIE_NAME) or "")
    with _db_lock:
        cur = _db().execute("DELETE FROM sessions WHERE token_hash != ?", (keep,))
        _db().commit()
    audit("admin.sessions.purge", detail=f"revoked={cur.rowcount}",
          ip=client_ip(request))
    return {"ok": True, "revoked": cur.rowcount}
