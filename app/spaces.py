"""v3 多用户空间模型（docs/DESIGN-v3/PERMISSIONS.md 的实现）。

虚拟路径（VPath）：
    private:<path>  → 物理 SHARE_ROOT/private/<username>/<path>（仅本人）
    shared:<path>   → 物理 SHARE_ROOT/<path>（v2 存量目录即 shared 空间，不搬迁）
    裸路径          → 等价 shared:<path>（兼容 v2 客户端与旧 UI）

特殊段（shared 空间内）：
    private/...     → 重定向到请求者自己的 private 空间（旧 UI 入口，永不跨用户）
    incoming/...    → 手机直传落地：写时落到 incoming/<username>/ 下；读时
                      admin 全见，member 仅本人子树（v2 存量根文件仅 admin）

权限语义（PERMISSIONS.md §3 定稿）：
    admin   —— shared 读写全权；private 仅本人
    member  —— shared 默认只读，写需 grant（can_write 前缀命中）；private 仅本人
    guest   —— shared 全部凭 grant（can_read / can_write），无默认权限；private 仅本人
    grant 不递归取消语义：按路径段前缀命中（"shared/photos" 命中其全部子树）
"""

import os
import re
import time

from fastapi import HTTPException

from app.config import SHARE_ROOT
from app.db import _db, _db_lock
from app.filetools import safe_path

# 邀请建号拒绝的用户名（避免与空间保留段/系统目录冲突）
RESERVED_USERNAMES = {"shared", "private", "incoming", "tmp",
                      "admin", "administrator", "root", "system"}


def _normcase(s: str) -> str:
    return os.path.normcase(s)


def _normcase_eq(a: str, b: str) -> bool:
    return _normcase(a) == _normcase(b)


def _clean_parts(rest: str) -> list[str]:
    """分隔符归一 + 段级清洗（E13 反斜杠、E1 穿越、点前缀隐藏目录）。"""
    parts = [p for p in rest.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".") for p in parts):
        raise HTTPException(status_code=403, detail="Forbidden")
    return parts


def _grant_hit(user_id: int, vkey: str, need: str) -> bool:
    """授权前缀命中（按路径段，E10：不得用裸 startswith）。grant 过期即失效。"""
    key = _normcase(vkey)
    rows = _db().execute(
        "SELECT path, can_read, can_write, expires_at FROM grants WHERE user_id=?",
        (user_id,)).fetchall()
    now = time.time()
    for r in rows:
        if r["expires_at"] and r["expires_at"] < now:
            continue
        gp = _normcase(r["path"])
        if gp == key or key.startswith(gp + "/"):
            if need == "read":
                if r["can_read"]:
                    return True
            elif need == "write" and r["can_write"]:
                return True
    return False


def resolve(user, path: str, need: str = "read"):
    """解析虚拟路径 → 物理路径；无权限一律 403（不区分 404 防探测）。

    user: sqlite3.Row（users 行，含 role；调用方来自 require_user/require_admin）。
    """
    username = user["username"]
    role = user["role"] if "role" in user.keys() else "member"
    raw = (path or "").replace("\\", "/")

    m = re.match(r"^(shared|private):(.*)$", raw)
    space, rest = (m.group(1), m.group(2)) if m else ("shared", raw)
    parts = _clean_parts(rest)

    if space == "private":
        # private:<path>：path 相对本人私人根，任何写法都触达不到他人空间
        # （他人 private 无任何 vpath 寻址方式，admin 也不例外——E8 管理动作暂缺）
        own = SHARE_ROOT / "private" / username
        own.mkdir(parents=True, exist_ok=True)  # 私人空间永远可进入
        rel_parts = ["private", username] + parts
        return safe_path("/".join(rel_parts))

    # ── shared 空间 ──
    if parts and parts[0] == "private":
        # 旧 UI 入口：shared:private/... 永远映射到请求者本人 private（防跨用户）
        own = SHARE_ROOT / "private" / username
        own.mkdir(parents=True, exist_ok=True)  # 私人空间永远可进入
        rel_parts = ["private", username] + parts[1:]
        return safe_path("/".join(rel_parts))

    if parts and parts[0] == "incoming":
        sub = parts[1:]
        if need == "write":
            # 直传落各自子目录（权限内建，不查 grant）
            return safe_path("/".join(["incoming", username] + sub))
        # 读：admin 全见；member 仅本人子树；guest 凭 grant
        if role == "admin":
            return safe_path("/".join(parts))
        if role == "member":
            if not sub or not _normcase_eq(sub[0], username):
                # 列 incoming 根 → 只能给本人子树视图
                return safe_path("/".join(["incoming", username]))
            return safe_path("/".join(parts))
        # guest
        vkey = "shared/" + "/".join(parts)
        if _grant_hit(user["user_id"], vkey, need):
            return safe_path("/".join(parts))
        raise HTTPException(status_code=403, detail="Forbidden")

    vkey = "shared/" + "/".join(parts)
    if need == "write":
        if role != "admin" and not _grant_hit(user["user_id"], vkey, "write"):
            raise HTTPException(status_code=403, detail="Forbidden")
    else:  # read
        if role == "guest" and not _grant_hit(user["user_id"], vkey, "read"):
            raise HTTPException(status_code=403, detail="Forbidden")
        # member/admin：shared 默认可读
    return safe_path("/".join(parts)) if parts else SHARE_ROOT


def enforce_quota(user, dest, incoming_bytes: int):
    """配额（M3 定稿）：仅 private 空间计逻辑字节；shared 不限额。dest 为物理路径。"""
    try:
        quota = user["quota_bytes"]
    except (IndexError, KeyError):
        return
    if not quota:
        return
    try:
        rel = dest.relative_to(SHARE_ROOT)
    except ValueError:
        return
    if not rel.parts or rel.parts[0] != "private":
        return
    used = 0
    own = SHARE_ROOT / "private" / user["username"]
    if own.exists():
        for f in own.rglob("*"):
            if f.is_file():
                try:
                    used += f.stat().st_size
                except OSError:
                    continue
    if used + incoming_bytes > quota:
        raise HTTPException(status_code=409, detail="配额不足（私人空间已满）")


def sync_grants_on_move(old_rel: str, new_rel: str):
    """目录改名/移动后同步 grants.path（E6，事务内）。仅作用于 shared 空间的普通目录。"""
    if not old_rel or not new_rel:
        return
    old_parts = old_rel.split("/")
    new_parts = new_rel.split("/")
    if old_parts[0] in ("private", "incoming") or new_parts[0] in ("private", "incoming"):
        return
    old_vkey = "shared/" + old_rel
    new_vkey = "shared/" + new_rel
    with _db_lock:
        _db().execute("UPDATE grants SET path=? WHERE path=? OR path LIKE ? || '/%'",
                      (new_vkey, old_vkey, old_vkey))
        _db().commit()


def visible(user, rel_parts) -> bool:
    """给定相对 SHARE_ROOT 的路径段元组，判断是否对 user 可见（搜索/最近/照片/回收站过滤）。"""
    parts = [p for p in rel_parts]
    if any(p.startswith(".") for p in parts):
        return False
    role = user["role"] if "role" in user.keys() else "member"
    username = user["username"]
    if parts[0] == "private":
        return len(parts) > 1 and _normcase_eq(parts[1], username)
    if parts[0] == "incoming":
        if role == "admin":
            return True
        return len(parts) > 1 and _normcase_eq(parts[1], username)
    if role == "guest":
        vkey = "shared/" + "/".join(parts)
        return _grant_hit(user["user_id"], vkey, "read")
    return True
