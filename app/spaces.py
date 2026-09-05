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
import threading
import time
from pathlib import Path

from fastapi import HTTPException

from app.config import SHARE_ROOT
from app.db import _db, _db_lock
from app.filetools import safe_path

# 邀请建号拒绝的用户名（避免与空间保留段/系统目录冲突）
RESERVED_USERNAMES = {"shared", "private", "incoming", "tmp",
                      "admin", "administrator", "root", "system",
                      ".", ".."}  # 纯点号：safe_path 会 403，建出来即废号（v0.15.2）


def _normcase(s: str) -> str:
    # Windows 的 os.path.normcase 会把 "/" 归一成 "\\"——授权前缀命中与
    # 移动同步都靠 `gp + "/"` 拼接段边界，混入反斜杠后 startswith 永不命中，
    # 子目录授权形同虚设（review 2026-09-03 P1：存量测试只覆盖精确路径，
    # 前缀方向常年未暴露）。这里统一回正斜杠做规范化比较键。
    return os.path.normcase(s).replace("\\", "/")


def _normcase_eq(a: str, b: str) -> bool:
    return _normcase(a) == _normcase(b)


def _clean_parts(rest: str) -> list[str]:
    """分隔符归一 + 段级清洗（E13 反斜杠、E1 穿越、点前缀隐藏目录）。"""
    parts = [p for p in rest.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".") for p in parts):
        raise HTTPException(status_code=403, detail="Forbidden")
    return parts


# grants 进程内 TTL 缓存（R1-P2-9）：guest 路径每次 visible()/_grant_hit 都全量
# 拉 grants，照片墙 500 行/页 = 500 次 DB 往返。30s TTL + 写操作失效。
_grants_cache: dict[int, tuple[list, float]] = {}
_grants_cache_lock = threading.Lock()
_GRANTS_TTL = 30.0


def _grants_for(user_id: int) -> list:
    now = time.time()
    with _grants_cache_lock:
        hit = _grants_cache.get(user_id)
        if hit and now - hit[1] < _GRANTS_TTL:
            return hit[0]
    rows = _db().execute(
        "SELECT path, can_read, can_write, expires_at FROM grants WHERE user_id=?",
        (user_id,)).fetchall()
    with _grants_cache_lock:
        _grants_cache[user_id] = (rows, time.time())
    return rows


def invalidate_grants_cache(user_id: int | None = None):
    """授权/用户变更后失效缓存；user_id=None 清全部（如角色变更影响可见性）。"""
    with _grants_cache_lock:
        if user_id is None:
            _grants_cache.clear()
        else:
            _grants_cache.pop(user_id, None)


def _grant_hit(user_id: int, vkey: str, need: str) -> bool:
    """授权前缀命中（按路径段，E10：不得用裸 startswith）。grant 过期即失效。"""
    key = _normcase(vkey)
    rows = _grants_for(user_id)
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



def _own_private_rel(username: str, parts: list[str]) -> list[str]:
    """私人空间路径统一：parts 是 "private" 之后的段。兼容两种形态——
    虚拟形态（首段是子目录名，需补本人用户名）与物理形态（首段已是
    本人用户名，来自目录列表的 rel path，直接使用）。"""
    if parts and _normcase_eq(parts[0], username):
        return ["private"] + parts
    return ["private", username] + parts


def sample_readable(user, sample) -> bool:
    """秒传越权防线（Dedup Oracle 封堵）：blobs 库是全局的，"服务端存有该
    内容"≠"当前用户可读"。物理样例路径对当前用户可读才允许秒传硬链接。

    不能用 resolve() 判——shared 空间里 "private/..." 段会被重定向到请求者
    本人空间且不做存在性检查，形同虚设。这里按物理路径直判，语义与目录读
    一致：private 仅本人（admin 也无跨用户寻址，PERMISSIONS.md E8）；incoming
    admin 全见、member 仅本人子树、guest 凭 grant；其余 shared 语义。
    """
    username = user["username"]
    role = user["role"] if "role" in user.keys() else "member"
    from pathlib import Path as _Path
    try:
        rel = _Path(sample).resolve().relative_to(_Path(SHARE_ROOT).resolve())
    except (ValueError, OSError):
        return False
    parts = [p for p in rel.parts if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        return False
    if _normcase_eq(parts[0], "private"):
        return len(parts) >= 2 and _normcase_eq(parts[1], username)
    if _normcase_eq(parts[0], "incoming"):
        if role == "admin":
            return True
        if role == "member":
            return len(parts) >= 2 and _normcase_eq(parts[1], username)
        return _grant_hit(user["user_id"], "shared/" + "/".join(parts), "read")
    if role == "guest":
        return _grant_hit(user["user_id"], "shared/" + "/".join(parts), "read")
    return True  # member/admin：shared 默认可读


def resolve(user, path: str, need: str = "read") -> Path:
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
    # 特殊段判定必须 normcase（Windows 大小写不敏感）："Private/" 在 NTFS 上就是
    # private/，精确比较会让它落进 shared 默认可读分支 → 跨用户越权（review 2026-09-02 P0-2）
    if parts and _normcase_eq(parts[0], "private"):
        # 旧 UI 入口：shared:private/... 永远映射到请求者本人 private（防跨用户）。
        # 兼容虚拟/物理两种路径形态（目录列表返回物理形态 private/<用户名>/...）
        own = SHARE_ROOT / "private" / username
        own.mkdir(parents=True, exist_ok=True)  # 私人空间永远可进入
        return safe_path("/".join(_own_private_rel(username, parts[1:])))

    if parts and _normcase_eq(parts[0], "incoming"):
        sub = parts[1:]
        if need == "write":
            # 直传落各自子目录（权限内建，不查 grant）
            if role == "admin" and sub:
                # admin 对既有路径的精确写操作（删除/移动/覆盖他人文件）
                return safe_path("/".join(parts))
            if sub and _normcase_eq(sub[0], username):
                # 路径已带本人子目录（列表展示形态），原样返回
                return safe_path("/".join(parts))
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
    if not rel.parts or not _normcase_eq(rel.parts[0], "private"):
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
    """目录改名/移动后同步 grants.path（E6，事务内）。仅作用于 shared 空间的普通目录。
    前缀匹配走 normcase + Python 侧精确段比较——LIKE 通配符会被路径里的 %/_ 破坏。"""
    if not old_rel or not new_rel:
        return
    old_parts = old_rel.split("/")
    new_parts = new_rel.split("/")

    def _reserved(parts):
        return bool(parts) and (_normcase_eq(parts[0], "private")
                                or _normcase_eq(parts[0], "incoming"))

    if _reserved(old_parts) or _reserved(new_parts):
        return
    old_vkey = "shared/" + old_rel
    new_vkey = "shared/" + new_rel
    ok_old = _normcase(old_vkey)
    with _db_lock:
        for r in _db().execute("SELECT id, path FROM grants").fetchall():
            gp = _normcase(r["path"])
            if gp == ok_old:
                _db().execute("UPDATE grants SET path=? WHERE id=?", (new_vkey, r["id"]))
            elif gp.startswith(ok_old + "/"):
                _db().execute("UPDATE grants SET path=? WHERE id=?",
                              (new_vkey + r["path"][len(old_vkey):], r["id"]))
        _db().commit()
    # 路径改写后失效 grants 缓存（R2-P3-8）：否则 guest 对改名后目录最长
    # 30s 内 403（fail-closed 不越权，但功能受损）
    invalidate_grants_cache(None)


def visible(user, rel_parts) -> bool:
    """给定相对 SHARE_ROOT 的路径段元组，判断是否对 user 可见（搜索/最近/照片/回收站过滤）。"""
    parts = [p for p in rel_parts]
    if any(p.startswith(".") for p in parts):
        return False
    if not parts:
        return False   # 空段无归属语义，宁可不可见（防 IndexError）
    role = user["role"] if "role" in user.keys() else "member"
    username = user["username"]
    if _normcase_eq(parts[0], "private"):
        return len(parts) > 1 and _normcase_eq(parts[1], username)
    if _normcase_eq(parts[0], "incoming"):
        if role == "admin":
            return True
        return len(parts) > 1 and _normcase_eq(parts[1], username)
    if role == "guest":
        vkey = "shared/" + "/".join(parts)
        return _grant_hit(user["user_id"], vkey, "read")
    return True
