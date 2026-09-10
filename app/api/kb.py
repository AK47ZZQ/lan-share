"""知识库端点（M-K0，规格见 data/笔记/知识库与图谱功能设计方案）。

全部端点 require_user + resolve 目录授权，权限与笔记完全同源；
图谱/反链/邻接只读走 read，reindex 走 write（防只读成员刷 CPU）。
path = 图谱根目录（与 /api/notes/list 同语义），file = 笔记文件
（SHARE_ROOT 相对路径，与 notes 列表项的 path 字段同语义）。
同步 def 端点由 FastAPI 自动进线程池——索引器的同步 IO 纪律（9.9 P1-1）。
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlite3 import Row

from app.security import audit, require_user
from app.services import kb
from app.spaces import resolve

router = APIRouter()


def _root_or_404(user: Row, path: str) -> Path:
    root = resolve(user, path, "read")
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")
    return root


def _file_under_root_or_404(user: Row, root: Path, file: str) -> None:
    """file 必须可读且物理位于 root 之内（防拿别处路径蹭图谱缓存键）。"""
    f = resolve(user, file, "read")
    if not f.is_file():
        raise HTTPException(status_code=404, detail="笔记不存在")
    try:
        f.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="笔记不在图谱根目录内")


def _kb_call(user: Row, root: Path, fn, *args):
    """护栏转换：超 2000 节点明确 400（图谱截断比列表截断更误导，不静默）。"""
    try:
        return fn(user, root, *args)
    except kb.KbTooMany:
        raise HTTPException(
            status_code=400,
            detail="笔记数量超过 2000 上限，请缩小 path（选择更深的子目录作为图谱根）")


def _persist_ok(user: Row, path: str) -> bool:
    """只读请求者触发重建时布局不得落盘（红队 V4：服务进程有 FS 写权，
    应用层只读必须显式表达）。写权限判定复用 resolve 的 write 语义。"""
    try:
        resolve(user, path, "write")
        return True
    except HTTPException:
        return False


@router.get("/api/kb/graph")
def kb_graph(path: str = Query(..., min_length=1),
             _user: Row = Depends(require_user)):
    root = _root_or_404(_user, path)
    return _kb_call(_user, root, kb.get_graph, _persist_ok(_user, path))


@router.get("/api/kb/backlinks")
def kb_backlinks(path: str = Query(..., min_length=1),
                 file: str = Query(..., min_length=1),
                 _user: Row = Depends(require_user)):
    root = _root_or_404(_user, path)
    _file_under_root_or_404(_user, root, file)
    return _kb_call(_user, root, kb.backlinks, file,
                    _persist_ok(_user, path))


@router.get("/api/kb/neighbors")
def kb_neighbors(path: str = Query(..., min_length=1),
                 file: str = Query(..., min_length=1),
                 depth: int = Query(default=1, ge=1, le=2),
                 _user: Row = Depends(require_user)):
    root = _root_or_404(_user, path)
    _file_under_root_or_404(_user, root, file)
    return _kb_call(_user, root, kb.neighbors, file, depth,
                    _persist_ok(_user, path))


@router.get("/api/kb/names")
def kb_names(path: str = Query(..., min_length=1),
             _user: Row = Depends(require_user)):
    root = _root_or_404(_user, path)
    return _kb_call(_user, root, kb.names, _persist_ok(_user, path))


@router.get("/api/kb/context")
def kb_context(path: str = Query(..., min_length=1),
               file: str = Query(..., min_length=1),
               _user: Row = Depends(require_user)):
    root = _root_or_404(_user, path)
    _file_under_root_or_404(_user, root, file)
    return _kb_call(_user, root, kb.context, file, _persist_ok(_user, path))


@router.post("/api/kb/reindex")
def kb_reindex(path: str = Query(..., min_length=1),
               _user: Row = Depends(require_user)):
    root = resolve(_user, path, "write")
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")
    audit("kb.reindex", target=path)
    g = _kb_call(_user, root, kb.rebuild)
    return {"stats": g["stats"], "builtAt": g["builtAt"]}
