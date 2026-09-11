"""路由层公共依赖与守卫助手（B2 样板收编补全）。

- `preview_viewer`：admin 成员视角预览的 FastAPI 依赖（X-As-User，只读）。
  替代 `_user=Depends(require_user)` + `user = preview_user(request, _user)`
  两步样板——photos×3 / files / albums / share 共 6 处收编。
- `ensure_share_owner` / `get_share_or_404`：share 域 history/revoke/edit
  三份同款"取行 → 404 → 属主 403"守卫收编（无主遗留分享归 admin 管）。
- `ensure_exists`：previews 两处"取到的路径必须存在且为文件"的 404 收编。

不上收的两处（口径记录，勿误合并）：
- uploads.py `_session_owner_guard`：分块会话属主守卫（**无 admin 豁免**，
  会话只能创建者续传）与其 finish 段内联变体语义互异，单一消费方，留原位。
- trash.py `_ensure_can_touch`：回收站逐条可见性判定，单消费方，留原位。
"""
from pathlib import Path

from fastapi import Depends, HTTPException, Request

from app.roles import ADMIN
from app.security import preview_user, require_user


def preview_viewer(request: Request, user=Depends(require_user)):
    """require_user + preview_user 的合并依赖（admin 成员视角预览）。"""
    return preview_user(request, user)


def ensure_share_owner(user, s) -> None:
    """分享管理操作属主守卫：创建者或 admin；无主（v2 遗留，owner_id None）
    分享归 admin 管——不再等同"人人可管"。非属主 403。"""
    if user["role"] != ADMIN and (s["owner_id"] is None or
                                    s["owner_id"] != user["user_id"]):
        raise HTTPException(status_code=403, detail="Forbidden")


def get_share_or_404(token: str):
    """按 token 取分享行；不存在即 404（不向无关者泄露存在性）。"""
    from app.store import shares as shares_store
    s = shares_store.get_by_token(token)
    if not s:
        raise HTTPException(status_code=404, detail="Share not found")
    return s


def ensure_exists(target: Path, *, detail: str = "File not found") -> None:
    """目标路径必须存在且为文件，否则 404（预览/缩略图入口共用）。"""
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=detail)
