"""效率舱 · 待办与家庭清单（M1，v1.1）。

设计依据 `docs/效率舱-最终计划-20260917.md`（v5）：

- 可见性：personal 清单仅创建者可见（**admin 也不看**——私人待办是隐私）；
  family 清单 member+admin 全员可见可编辑，**guest 不可见**（效率页对访客
  整体隐藏，见 App 端 home_shell）。可见性判定在本层收口，store 只管存取。
- 并发：最后写入胜；单条待办删除走软删除墓碑（防"A 删 B 勾 → 复活"），
  墓碑 30 天后由清理循环物理清除。
- 提醒：`sweep_todo_due` 挂 cleanup 循环（**1 小时粒度**，`CLEANUP_INTERVAL`），
  只做兜底站内通知；**不承诺精确到分**（最终计划 §3.6）。
- 通知节流：家庭清单完成/指派通知按 `link=todos:list:{id}` 10 分钟窗口合并
  （防"买菜 20 项 = 20 条通知"）。
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api import events as events_bus
from app.api import notify as notify_api
from app.security import audit, client_ip, require_user
from app.services import cleanup                     # 清理循环调度（底部注册 sweep）
from app.store import notifications as notifications_store
from app.store import todos as todos_store
from app.store import users as users_store

router = APIRouter()

_TODO_TOMBSTONE_DAYS = 30      # 墓碑保留期（物理清除）
_NOTIFY_MERGE_WINDOW = 600.0   # 家庭清单通知合并窗口（秒）
_MAX_TITLE = 200
_MAX_NOTES = 2000
_MAX_LIST_NAME = 60
_PRIORITIES = (0, 1, 2)


class ListBody(BaseModel):
    name: str = Field(min_length=1, max_length=_MAX_LIST_NAME)
    kind: str = "personal"


class ListRenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=_MAX_LIST_NAME)


class TodoCreateBody(BaseModel):
    list_id: int
    title: str = Field(min_length=1, max_length=_MAX_TITLE)
    notes: str = ""
    due_at: float | None = None
    priority: int = 0
    assignee_id: int | None = None


class TodoPatchBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=_MAX_TITLE)
    notes: str | None = Field(default=None, max_length=_MAX_NOTES)
    due_at: float | None = None
    priority: int | None = None
    assignee_id: int | None = None
    status: str | None = None


# ─── 可见性收口（最终计划 v5 §3.5：家庭数据 member+admin 可见）──────────


def _require_member(user) -> None:
    """guest 不可用待办（效率页对访客隐藏）。"""
    if user["role"] == "guest":
        raise HTTPException(status_code=403, detail="访客不支持待办功能")


def _visible_list_or_404(lid: int, user):
    """personal=仅创建者（admin 也不看）；family=member/admin。404 不泄露存在。"""
    lst = todos_store.get_list(lid)
    if not lst:
        raise HTTPException(status_code=404, detail="清单不存在")
    if lst["kind"] == "family":
        if user["role"] == "guest":
            raise HTTPException(status_code=404, detail="清单不存在")
    elif lst["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="清单不存在")
    return lst


def _visible_todo_or_404(tid: int, user):
    todo = todos_store.get_todo(tid)
    if not todo or todo["deleted_at"] is not None:
        raise HTTPException(status_code=404, detail="待办不存在")
    if todo["list_kind"] == "family":
        if user["role"] == "guest":
            raise HTTPException(status_code=404, detail="待办不存在")
    elif todo["list_owner_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="待办不存在")
    return todo


def _validate_assignee(assignee_id: int | None) -> int | None:
    """指派对象必须是在用的 member/admin（guest 无待办能力，不收指派）。"""
    if assignee_id is None:
        return None
    u = users_store.get(assignee_id)
    if not u or not u["is_active"] or u["role"] == "guest":
        raise HTTPException(status_code=400, detail="被指派成员不存在或不可用")
    return assignee_id


def _members_map() -> list[dict]:
    """在用成员简表（App 端把 assignee_id/done_by 映射成头像/昵称）。"""
    return [{"id": r["id"], "username": r["username"], "nickname": r["nickname"]}
            for r in users_store.list_active_brief()]


def _notify_merged(uid: int, title: str, body: str, link: str) -> None:
    """站内通知（统一出口，尊重接收方偏好）+ SSE 信号；
    同 link 未读且在合并窗口内则只更新正文（节流）。"""
    recent = notifications_store.recent_unread_by_link(
        uid, link, _NOTIFY_MERGE_WINDOW)
    if recent:
        notifications_store.bump_body(recent["id"], body)
        events_bus.publish(uid, {"kind": "notification"})
    else:
        notify_api.insert_notification(uid, "todo", title, body=body, link=link)


# ─── 清单 ─────────────────────────────────────────────────


@router.get("/api/todos/lists")
def list_lists(request: Request, _user=Depends(require_user)):
    _require_member(_user)
    rows = todos_store.lists_visible(_user["user_id"])
    todos = todos_store.todos_visible(_user["user_id"])
    open_counts: dict[int, int] = {}
    for t in todos:
        if t["status"] == "todo":
            open_counts[t["list_id"]] = open_counts.get(t["list_id"], 0) + 1
    return {
        "lists": [
            {
                "id": r["id"], "name": r["name"], "kind": r["kind"],
                "owner_id": r["user_id"],
                "mine": r["user_id"] == _user["user_id"],
                "open_count": open_counts.get(r["id"], 0),
            } for r in rows
        ],
        "members": _members_map(),
    }


@router.post("/api/todos/lists")
def create_list(request: Request, body: ListBody, _user=Depends(require_user)):
    _require_member(_user)
    kind = body.kind if body.kind in ("personal", "family") else "personal"
    if kind == "personal":
        if todos_store.count_lists_by_name(_user["user_id"], body.name):
            raise HTTPException(status_code=409, detail="同名清单已存在")
    else:
        # family 清单全家共用命名空间：重名即拒绝（避免"两个购物清单"）
        if any(r["kind"] == "family" and r["name"] == body.name
               for r in todos_store.lists_visible(_user["user_id"])):
            raise HTTPException(status_code=409, detail="同名家庭清单已存在")
    lid = todos_store.create_list(_user["user_id"], body.name, kind)
    audit("todo_list_create", body.name, f"kind={kind}", client_ip(request))
    return {"id": lid, "name": body.name, "kind": kind}


@router.patch("/api/todos/lists/{lid}")
def rename_list(lid: int, request: Request, body: ListRenameBody,
                _user=Depends(require_user)):
    _require_member(_user)
    lst = _visible_list_or_404(lid, _user)
    if lst["user_id"] != _user["user_id"] and _user["role"] != "admin":
        raise HTTPException(status_code=403, detail="只有清单创建者可以重命名")
    todos_store.rename_list(lid, body.name)
    return {"ok": True}


@router.delete("/api/todos/lists/{lid}")
def delete_list(lid: int, request: Request, _user=Depends(require_user)):
    _require_member(_user)
    lst = _visible_list_or_404(lid, _user)
    if lst["user_id"] != _user["user_id"] and _user["role"] != "admin":
        raise HTTPException(status_code=403, detail="只有清单创建者可以删除")
    todos_store.delete_list(lid)
    audit("todo_list_delete", lst["name"], client_ip(request))
    return {"ok": True}


# ─── 待办 ─────────────────────────────────────────────────


@router.get("/api/todos")
def list_todos(request: Request, _user=Depends(require_user)):
    _require_member(_user)
    rows = todos_store.todos_visible(_user["user_id"])
    return {
        "todos": [
            {
                "id": r["id"], "list_id": r["list_id"],
                "list_name": r["list_name"], "list_kind": r["list_kind"],
                "list_owner_id": r["list_owner_id"],
                "title": r["title"], "notes": r["notes"] or "",
                "due_at": r["due_at"], "priority": r["priority"],
                "assignee_id": r["assignee_id"],
                "status": r["status"], "done_at": r["done_at"],
                "done_by": r["done_by"], "created_at": r["created_at"],
            } for r in rows
        ],
        "members": _members_map(),
        "server_time": time.time(),
    }


@router.post("/api/todos")
def create_todo(request: Request, body: TodoCreateBody,
                _user=Depends(require_user)):
    _require_member(_user)
    _visible_list_or_404(body.list_id, _user)
    assignee = _validate_assignee(body.assignee_id)
    notes = body.notes or ""
    if len(notes) > _MAX_NOTES:
        raise HTTPException(status_code=400, detail="备注过长")
    if body.priority not in _PRIORITIES:
        raise HTTPException(status_code=400, detail="priority 仅支持 0/1/2")
    tid = todos_store.create_todo(body.list_id, body.title.strip(), notes,
                                  body.due_at, body.priority, assignee)
    # 指派给他人 → 站内通知被指派人（自己指派自己不通知）
    if assignee and assignee != _user["user_id"]:
        _notify_merged(assignee, f"新指派：{body.title.strip()}",
                       f"{_user['username']} 指派给你一条家庭待办",
                       f"todos:list:{body.list_id}")
    return {"id": tid}


@router.patch("/api/todos/{tid}")
def patch_todo(tid: int, request: Request, body: TodoPatchBody,
               _user=Depends(require_user)):
    _require_member(_user)
    todo = _visible_todo_or_404(tid, _user)
    provided = body.model_fields_set          # 区分"未传"与"显式传 null"（清空语义）
    fields: dict = {}
    if body.title is not None:
        fields["title"] = body.title.strip()
    if body.notes is not None:
        fields["notes"] = body.notes
    if "due_at" in provided:
        fields["due_at"] = body.due_at
        fields["reminded_at"] = None          # 改期/清空 → 重新计时提醒
    if body.priority is not None:
        if body.priority not in _PRIORITIES:
            raise HTTPException(status_code=400, detail="priority 仅支持 0/1/2")
        fields["priority"] = body.priority
    if "assignee_id" in provided:
        fields["assignee_id"] = _validate_assignee(body.assignee_id)
    if body.status is not None:
        if body.status not in ("todo", "done"):
            raise HTTPException(status_code=400, detail="status 仅支持 todo/done")
        fields["status"] = body.status
        if body.status == "done":
            fields["done_at"] = time.time()
            fields["done_by"] = _user["user_id"]
        else:
            fields["done_at"] = None
            fields["done_by"] = None
    if fields:
        todos_store.update_todo(tid, fields)
    # 家庭清单完成 → 通知清单创建者（合并窗口节流）
    if fields.get("status") == "done" and todo["list_kind"] == "family" \
            and todo["list_owner_id"] != _user["user_id"]:
        _notify_merged(todo["list_owner_id"],
                       f"家庭清单「{todo['list_name']}」有新完成",
                       f"{_user['username']} 完成了：{fields.get('title') or todo['title']}",
                       f"todos:list:{todo['list_id']}")
    # 改指派给他人 → 通知
    if "assignee_id" in fields and fields["assignee_id"] \
            and fields["assignee_id"] != _user["user_id"] \
            and fields["assignee_id"] != todo["assignee_id"]:
        _notify_merged(fields["assignee_id"],
                       f"新指派：{fields.get('title') or todo['title']}",
                       f"{_user['username']} 指派给你一条家庭待办",
                       f"todos:list:{todo['list_id']}")
    return {"ok": True}


@router.delete("/api/todos/{tid}")
def delete_todo(tid: int, request: Request, _user=Depends(require_user)):
    _require_member(_user)
    _visible_todo_or_404(tid, _user)
    todos_store.soft_delete_todo(tid)
    audit("todo_delete", str(tid), client_ip(request))
    return {"ok": True}


# ─── sweep：到期提醒兜底 + 墓碑物理清理（1 小时粒度，见模块 docstring）────


def sweep_todo_due() -> None:
    now = time.time()
    for r in todos_store.due_unreminded(now):
        uid = r["assignee_id"] or r["list_owner_id"]
        if uid:
            notify_api.insert_notification(
                uid, "todo", f"待办到期：{r['title']}",
                body="有一条待办已到截止时间", link=f"todos:todo:{r['id']}")
        todos_store.mark_reminded(r["id"], now)
    stale = todos_store.deleted_before(now - _TODO_TOMBSTONE_DAYS * 86400)
    if stale:
        todos_store.purge_deleted(stale)


cleanup.register_sweep("todo_due", sweep_todo_due)
