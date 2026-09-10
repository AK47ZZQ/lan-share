"""账号改名通用流程 + 用户名校验（07 审查批次 3C：双实现合流）。

历史上 auth.change_username 与 admin.admin_set_username 各写一遍
"改 users.username → 专辑归属跟随 → 私人目录迁移（失败回滚）→ incoming
迁移"，且 normcase 用法已有差异（os.path.normcase 直比 vs spaces 辅助）——
P1 级重复。现在唯一实现住这里；两个入口只保留各自的凭据校验与审计语义。
"""
import os
import re

from fastapi import HTTPException

from app.config import SHARE_ROOT
from app.db import _db_lock
from app.security import audit
from app.store import users as users_store


def clean_username(name: str) -> str:
    """新用户名合法性校验（原 admin._clean_username，auth 侧内联校验与之对齐）：
    空白/控制符/长度、保留名、路径分隔符与冒号、Windows 设备保留名。"""
    from app.filetools import RESERVED_NAME

    from app.spaces import RESERVED_USERNAMES
    n = (name or "").strip()
    if not n or len(n) > 64 or re.search(r"[\s\u0000-\u001f\u007f]", n):
        raise HTTPException(status_code=400, detail="用户名不能含空白/控制字符，且不超过 64 字符")
    if n.lower() in RESERVED_USERNAMES:
        raise HTTPException(status_code=400, detail="该用户名是保留名")
    if re.search(r"[/\\:]", n):
        raise HTTPException(status_code=400, detail="用户名不能含路径分隔符或冒号")
    if RESERVED_NAME.match(n):
        raise HTTPException(status_code=400, detail="该用户名是 Windows 设备保留名")
    return n


# 审计动作按入口分语义（动态流白名单不含这些失败动作，仅落审计历史）
_AUDIT_ACTIONS = {
    "user": ("username_dir_rename_fail", "incoming_dir_rename_fail"),
    "admin": ("admin_dir_rename_fail", "admin_incoming_rename_fail"),
}


def rename_user_everywhere(uid: int, old_name: str, new_name: str,
                           scope: str, ip: str) -> None:
    """改名的通用机械流程（调用方先完成新名校验与"与当前相同"检查）：

    1. 占用检查与 UPDATE 同锁（锁外检查时并发两次改名可双双通过）；
    2. 精选专辑归属跟随；
    3. 私人目录迁移——Windows 大小写不敏感，仅大小写变化时 rename 为 no-op；
       失败则回滚用户名与专辑归属（否则 username 与目录永久错位、本人数据
       "消失"，R1-P2-4 / R3-P1）；
    4. incoming 直传子目录同步改名（best-effort，失败仅审计）。
    """
    dir_fail_action, inc_fail_action = _AUDIT_ACTIONS[scope]
    with _db_lock:
        if users_store.exists_username_ci(new_name):
            raise HTTPException(status_code=409, detail="该用户名已被占用")
        users_store.set_username(uid, new_name)

    from app.api.albums import rename_album_owner
    rename_album_owner(old_name, new_name)   # 精选专辑归属跟随（R2-P2-3）

    old_dir = SHARE_ROOT / "private" / old_name
    new_dir = SHARE_ROOT / "private" / new_name
    if old_dir.exists() and not os.path.normcase(str(old_dir)) == os.path.normcase(str(new_dir)):
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            old_dir.rename(new_dir)
        except OSError:
            # 目录改名失败时回滚用户名：否则 username 与私人目录永久错位、
            # 本人数据"消失"（R1-P2-4）。专辑归属也要改回去（R3-P1：已在
            # rename 尝试前翻成新名，不回滚则创建者失去编辑权）
            with _db_lock:
                users_store.set_username(uid, old_name)
            rename_album_owner(new_name, old_name)
            audit(dir_fail_action, f"{old_name} → {new_name}", ip=ip)
            raise HTTPException(status_code=500,
                                detail="私人目录改名失败（可能正被占用），已回滚用户名，请稍后重试")

    # 直传子目录同步改名（否则旧 incoming/<旧名>/ 文件对本人不可见）
    old_inc = SHARE_ROOT / "incoming" / old_name
    new_inc = SHARE_ROOT / "incoming" / new_name
    if old_inc.exists() and not os.path.normcase(str(old_inc)) == os.path.normcase(str(new_inc)):
        try:
            old_inc.rename(new_inc)
        except OSError:
            audit(inc_fail_action, f"{old_name} → {new_name}", ip=ip)
