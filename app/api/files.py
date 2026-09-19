"""文件管理 API：列表/下载/建目录/移动/复制/删除/重命名/搜索/最近/磁盘统计。

（07 审查批次 3A 按域拆分：分块与直传 → api/uploads，回收站 → api/trash，
预览缩略图 → api/previews，zip 票据表 → services/archive；私人空间守卫
上收 app/spaces 公共 API。）
"""
import mimetypes
import os
import secrets
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.config import SHARE_ROOT
from app.roles import ADMIN
from app.api.deps import preview_viewer
from app.security import audit, client_ip, require_user
from app.filetools import (clean_filename, dir_tree, file_dict, human_size,
                           move_to_trash, to_rel)
from app.spaces import (cross_space_guard, enforce_quota, is_own_private_root,
                        resolve, sync_grants_on_move, visible)
from app.services import archive
from app.services import note_index

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


# ⚠️ 只注册 `/files/{path:path}`，**不注册裸 `/files`**（bug·249b 根修，2026-09-13）：
# 网页版文件页的 Vue 路由是 `/files`（web/src/router.ts），而裸 `/files` 曾注册为
# 下载端点 → 二者冲突：刷新页面时请求落到下载端点，`resolve("")` 得到 SHARE_ROOT
# 目录 → 返回 JSON 400「目录请在界面打包下载」，SPA 永远没机会启动 = **用户看到的
# 白屏**（原话就是这句 detail）。`{path:path}` 不匹配无路径的裸 `/files`，
# 故删掉该注册项后裸 `/files` 自然落到 main.py 的 SPA 深链回退（其排除清单只排
# `files/` 带斜杠形态），页面恢复正常；下载功能只走 `/files/<路径>`，
# Caddy 的 `handle /files/*` 也要求带斜杠，前端 `download()` 一律拼 `/files/<path>`
# ——三方语义一致，无功能回退。
@router.get("/files/{path:path}")
def download_file(path: str = "", _user=Depends(require_user)):
    """静态直连下载（Caddy /files/* 反代至此）：与 /preview 同样走 resolve() 判权。
    Caddyfile 不再直接 file_server 挂 SHARE_ROOT（越权旁路，review 2026-09-02 P0-1）；
    FileResponse 的 filename 参数自带 Content-Disposition: attachment。"""
    target = resolve(_user, path, "read")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="目录请在界面打包下载")
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=mime or "application/octet-stream",
                        filename=target.name)


@router.get("/api/files/{path:path}")
@router.get("/api/files")
def list_files(request: Request, path: str = "", user=Depends(preview_viewer)):
    # admin 成员视角预览（X-As-User，只读）——preview_viewer 依赖（B2 收编）
    target = resolve(user, path, "read")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Directory not found")
    if target.is_file():
        raise HTTPException(status_code=400, detail="Not a directory")
    tree = dir_tree(target)
    # 根目录的 private 入口显示为"私人空间"（点击进入的是本人私人目录）
    if target == SHARE_ROOT:
        for item in tree["items"]:
            if item["name"] == "private" and item["is_dir"]:
                item["name"] = "🔒 私人空间"
                item["is_private_root"] = True
    return tree
@router.post("/api/mkdir/{path:path}")
async def create_directory(request: Request, path: str, _user=Depends(require_user)):
    if not path.strip():
        raise HTTPException(status_code=400, detail="Invalid directory name")
    # 逐段清洗（保留名/冒号/结尾点等），再过空间解析（含写权限判定）
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    cleaned = [clean_filename(p) for p in parts]
    target = resolve(_user, "/".join(cleaned), "write")
    target.mkdir(parents=True, exist_ok=True)
    rel = to_rel(target)
    audit("mkdir", rel, ip=client_ip(request))
    return {"ok": True, "path": rel}


class MoveBody(BaseModel):
    sources: list[str]
    dest_dir: str = ""


@router.post("/api/move")
async def move_items(request: Request, body: MoveBody, _user=Depends(require_user)):
    """批量移动到目标目录。同盘 rename，硬链接去重与秒传指纹不受影响。"""
    if not body.sources:
        raise HTTPException(status_code=400, detail="未选择要移动的文件")
    if len(body.sources) > 500:
        raise HTTPException(status_code=400, detail="单次最多移动 500 项")
    dest_dir = resolve(_user, body.dest_dir, "write")
    if not dest_dir.is_dir():
        raise HTTPException(status_code=404, detail="目标目录不存在")
    results = []
    for src in body.sources:
        try:
            # 移动=从源删除：源也需要写权（不能移走只有读权的共享文件）
            target = resolve(_user, src, "write")
            if is_own_private_root(_user, target):
                raise HTTPException(status_code=400, detail="私人空间根目录不可移动")
            cross_space_guard(_user, target, dest_dir)
            if not target.exists():
                raise HTTPException(status_code=404, detail="源文件不存在")
            if target.parent == dest_dir:
                raise HTTPException(status_code=400, detail="已在目标目录")
            if target == dest_dir or (target.is_dir() and target in dest_dir.parents):
                # 只有"目录移进自己的后代"会成环；移到祖先目录是合法操作
                raise HTTPException(status_code=400, detail="不能移动到自身的子目录")
            # os.rename 在 Windows 下目标已存在必抛 FileExistsError（原子防覆盖，
            # 无 _unique_dest 预检 + os.replace 的并发竞态窗口）
            # 重名序号与资源管理器一致：首个冲突即 "name (1)"（0.6.0 起修正，
            # 此前从 (2) 起跳）
            # 目录判定必须在 rename 之前——rename 之后旧路径已不存在，
            # is_dir() 恒 False，授权同步会变成永不执行的死代码
            #（review 2026-09-03 P1：移动目录后 grants.path 全部失联）
            was_dir = target.is_dir()
            try:
                old_rel = to_rel(target)
            except ValueError:
                old_rel = ""
            placed = None
            final = dest_dir / target.name
            for cand in [final] + [
                    final.with_name(f"{final.stem} ({i}){final.suffix}")
                    for i in range(1, 999)]:
                # Linux 的 rename(2) 目标已存在时**静默替换**（Windows 必抛
                # FileExistsError）——不预检会把目标同名文件无声覆盖掉
                #（bug·27ce：CI Linux 上测试抓到真实覆盖）。预检 + 异常双路径：
                # Linux 靠预检（TOCTOU 窗口家庭场景可接受，远好于静默覆盖），
                # Windows 保持原子防覆盖。
                if cand.exists() or cand.is_symlink():
                    continue
                try:
                    os.rename(target, cand)
                    placed = cand
                    break
                except FileExistsError:
                    continue
            if placed is None:
                raise HTTPException(status_code=409, detail="无法生成不重名的目标名")
            final_rel = to_rel(placed)
            if was_dir:
                sync_grants_on_move(old_rel, final_rel)
            results.append({"src": src, "ok": True, "dest": final_rel})
            audit("move", old_rel or src, f"→ {final_rel}", client_ip(request))
        except HTTPException as e:
            results.append({"src": src, "ok": False, "error": e.detail})
        except OSError as e:
            results.append({"src": src, "ok": False, "error": f"系统错误（{type(e).__name__}）"})
    return {"ok": True, "results": results}
@router.post("/api/copy")
def copy_items(request: Request, body: MoveBody, _user=Depends(require_user)):
    """批量复制到目标目录（App v0.6.0 文件管理补齐）。与 move 的差别：
    复制不删源——源只需读权；文件 copy2、目录 copytree；重名自动 "name (n)"。
    同步 def 跑线程池：copytree 大目录耗时，不阻塞事件循环。"""
    if not body.sources:
        raise HTTPException(status_code=400, detail="未选择要复制的文件")
    if len(body.sources) > 500:
        raise HTTPException(status_code=400, detail="单次最多复制 500 项")
    dest_dir = resolve(_user, body.dest_dir, "write")
    if not dest_dir.is_dir():
        raise HTTPException(status_code=404, detail="目标目录不存在")

    def _tree_size(p: Path) -> int:
        if p.is_dir():
            total = 0
            for f in p.rglob("*"):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    continue
            return total
        return p.stat().st_size

    incoming = 0
    for src in body.sources:
        try:
            # 定价走 resolve 后路径：裸 src 可写 ".." 等越界形态，
            # 直接 rglob 会拿用户输入当文件系统路径遍历（IO 放大 + 配额虚高）
            incoming += _tree_size(resolve(_user, src, "read"))
        except (HTTPException, OSError):
            pass   # 源缺失/无权由下方逐项检查报错
    enforce_quota(_user, dest_dir, incoming)
    results = []
    for src in body.sources:
        try:
            target = resolve(_user, src, "read")
            if not target.exists():
                raise HTTPException(status_code=404, detail="源文件不存在")
            if target == dest_dir or (target.is_dir() and target in dest_dir.parents):
                raise HTTPException(status_code=400, detail="不能复制到自身的子目录")
            final = dest_dir / target.name
            placed = None
            if not final.exists():
                placed = final
            else:
                for i in range(1, 1000):
                    cand = final.with_name(f"{final.stem} ({i}){final.suffix}")
                    if not cand.exists():
                        placed = cand
                        break
            if placed is None:
                raise HTTPException(status_code=409, detail="无法生成不重名的目标名")
            if target.is_dir():
                shutil.copytree(target, placed)
            else:
                shutil.copy2(target, placed)
            final_rel = to_rel(placed)
            results.append({"src": src, "ok": True, "dest": final_rel})
            audit("copy", to_rel(target),
                  f"→ {final_rel}", client_ip(request))
        except HTTPException as e:
            results.append({"src": src, "ok": False, "error": e.detail})
        except OSError as e:
            results.append({"src": src, "ok": False, "error": f"系统错误（{type(e).__name__}）"})
    return {"ok": True, "results": results}


@router.delete("/api/files/{path:path}")
async def delete_file(request: Request, path: str, _user=Depends(require_user)):
    target = resolve(_user, path, "write")
    if is_own_private_root(_user, target):
        raise HTTPException(status_code=400, detail="私人空间根目录不可删除（里面的文件可以删）")
    if target == SHARE_ROOT:
        raise HTTPException(status_code=400, detail="不能删除根目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    # 回收站登记物理相对路径（此前存请求原样 vpath，visible() 无法判定归属）
    move_to_trash(target, to_rel(target))
    # 审计同样记物理相对路径（R1-P3-1）：vpath 形态（private:x.jpg）进动态流
    # 后 visible() 无法归一，会泄漏到全员可见
    audit("trash", to_rel(target), ip=client_ip(request))
    return {"ok": True}


# ─── 批量打包下载 / 重命名 ─────────────────────────────────

class ZipBody(BaseModel):
    paths: list[str]





@router.post("/api/zip")
def create_zip(request: Request, body: ZipBody, _user=Depends(require_user)):
    """打包选中文件/目录为 zip，返回短时效下载 URL。"""
    if not body.paths or len(body.paths) > 500:
        raise HTTPException(status_code=400, detail="Invalid paths")
    try:
        archive.acquire_slot(_user["username"])
    except archive.SlotBusy:
        raise HTTPException(status_code=429, detail="已有打包任务在进行，请稍候")
    except archive.SlotCap:
        # 打包票据上限（9.9 复审 P2）：开工前拒绝，不留孤儿 zip 文件
        raise HTTPException(status_code=429, detail="打包任务过多，请稍后再试")
    try:
        targets = [resolve(_user, p, "read") for p in body.paths]
        for t in targets:
            if not t.exists():
                raise HTTPException(status_code=404, detail=f"Not found: {t.name}")
            # 根目录不可打包（2026-09-19 对抗审查 F6）：resolve("")/("/"）= SHARE_ROOT，
            # zip 循环里 f.relative_to(t.parent) 对根无意义必抛 ValueError → 500，
            # 且此前全树 rglob + 逐文件写 zip 已白烧数分钟 IO、.zips 留孤儿大文件
            # （实测 7 分钟+，见 tests/test_review_20260919 的红证）。UI 只打包选中项，
            # 拒绝根目标零功能损失。
            if t == SHARE_ROOT:
                raise HTTPException(status_code=400, detail="不能打包根目录，请选择具体文件或子目录")

        # 磁盘余量预计价（红队 P2-2）：打包是整树复制且 .zips 不在配额视野内，
        # 串行循环可把磁盘打满；拒绝"预计打包量 > 剩余空间 - 5GB 余量"的请求
        total_in = 0
        for t in targets:
            if t.is_dir():
                for f in t.rglob("*"):
                    try:
                        if f.is_file():
                            total_in += f.stat().st_size
                    except OSError:
                        continue
            else:
                total_in += t.stat().st_size
        free = shutil.disk_usage(str(SHARE_ROOT)).free
        if total_in > free - 5 * 1024 ** 3:
            raise HTTPException(status_code=507,
                                detail="磁盘剩余空间不足，无法打包（需为系统保留 5GB 余量）")

        zdir = SHARE_ROOT / ".zips"
        zdir.mkdir(exist_ok=True)
        zpath = zdir / f"{secrets.token_urlsafe(8)}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
            for t in targets:
                if t.is_dir():
                    for f in t.rglob("*"):
                        if f.is_file():
                            z.write(f, str(f.relative_to(t.parent)))
                else:
                    # 重名时保留路径去重
                    arc = t.name
                    base = arc.rsplit(".", 1)[0] if "." in arc else arc
                    ext = ("." + arc.rsplit(".", 1)[1]) if "." in arc else ""
                    n = 1
                    while arc in z.namelist():
                        arc = f"{base} ({n}){ext}"
                        n += 1
                    z.write(t, arc)
        token = archive.issue_ticket(zpath)
        audit("zip", f"{len(targets)} items", ip=client_ip(request))
        return {"ok": True, "url": f"/api/zip/{token}",
                "name": f"lan-share-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"}
    finally:
        archive.release_slot(_user["username"])


@router.get("/api/zip/{token}")
async def download_zip(token: str, _user=Depends(require_user)):
    info = archive.pop_ticket(token)
    if not info:
        raise HTTPException(status_code=403, detail="下载链接已失效，请重新打包")
    zpath: Path = info["path"]
    if not zpath.exists():
        raise HTTPException(status_code=404, detail="Zip missing")
    return FileResponse(zpath, media_type="application/zip",
                        filename=zpath.stem + ".zip",
                        background=BackgroundTask(os.unlink, str(zpath)))


class RenameBody(BaseModel):
    path: str
    new_name: str


@router.post("/api/rename")
async def rename_path(request: Request, body: RenameBody, _user=Depends(require_user)):
    target = resolve(_user, body.path, "write")
    if target == SHARE_ROOT:
        raise HTTPException(status_code=400, detail="不能重命名根目录")
    if is_own_private_root(_user, target):
        raise HTTPException(status_code=400, detail="私人空间根目录不可重命名")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    dest = target.parent / clean_filename(body.new_name)
    if dest == target:
        return {"ok": True}
    if dest.exists():
        raise HTTPException(status_code=400, detail="目标名称已存在")
    try:
        old_rel = to_rel(target)
    except ValueError:
        old_rel = ""
    try:
        target.rename(dest)
    except FileExistsError:
        raise HTTPException(status_code=400, detail="目标名称已存在")
    except OSError:
        raise HTTPException(status_code=500, detail="重命名失败（目标被占用或系统错误）")
    if old_rel:
        sync_grants_on_move(old_rel, to_rel(dest))
    audit("rename", old_rel or body.path, body.new_name, client_ip(request))
    return {"ok": True, "path": to_rel(dest)}


@router.get("/api/search")
def search_files(q: str = Query(..., min_length=1), _user=Depends(require_user)):
    # kind 字段（B4）：向后兼容扩展——存量消费方（App home_api.search / Web
    # HomeView）不读它，零改动；"file"=文件名命中，"note"=笔记正文全文命中
    # （附 snippet 片段）。文件名命中语义原样保留。
    results = []
    for f in SHARE_ROOT.rglob("*"):
        rel_parts = f.relative_to(SHARE_ROOT).parts
        if not visible(_user, rel_parts):
            continue
        if f.is_file() and q.lower() in f.name.lower():
            d = file_dict(f)
            d["kind"] = "file"
            results.append(d)
            if len(results) >= 50:
                break
    if len(results) < 50:
        for d in note_index.search_notes(_user, q, limit=50 - len(results)):
            results.append(d)
    return results


@router.get("/api/recent")
def recent_files(limit: int = Query(default=20, ge=0, le=100), _user=Depends(require_user)):
    files = []
    for f in SHARE_ROOT.rglob("*"):
        rel_parts = f.relative_to(SHARE_ROOT).parts
        if not visible(_user, rel_parts):
            continue
        if f.is_file():
            files.append(file_dict(f))
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files[:limit]


# 网盘全量扫描 5 分钟 TTL 缓存（R1-P2-11）：10 万文件级库上每次 /api/disk
# 全树 rglob+stat 数秒；仪表盘/存储详情是高频页面，容忍 5 分钟内的统计滞后
_scan_cache: dict = {"ts": 0.0, "physical": 0, "cats": {}}
_SCAN_TTL = 300.0


def _scan_share_sizes():
    """网盘内容统计：返回 (去重后的物理占用, 各一级目录/根文件的逻辑大小)。
    NTFS 硬链接去重的同一份内容按物理只计一次；隐藏（点前缀）路径全部排除。"""
    if _scan_cache["ts"] and time.time() - _scan_cache["ts"] < _SCAN_TTL:
        return _scan_cache["physical"], _scan_cache["cats"]
    seen = set()
    physical = 0
    categories: dict[str, int] = {}
    for f in SHARE_ROOT.rglob("*"):
        try:
            if not f.is_file():
                continue
            rel = f.relative_to(SHARE_ROOT)
            st = f.stat()
        except OSError:
            continue
        if any(p.startswith(".") for p in rel.parts):
            continue
        key = (st.st_dev, st.st_ino)
        if key not in seen:
            seen.add(key)
            physical += st.st_size
        top = rel.parts[0] if len(rel.parts) > 1 else "(根目录文件)"
        categories[top] = categories.get(top, 0) + st.st_size
    _scan_cache.update(ts=time.time(), physical=physical, cats=categories)
    return physical, categories


@router.get("/api/disk")
def disk_info(_user=Depends(require_user)):
    # 同步 def：FastAPI 自动放线程池，rglob 扫描不阻塞事件循环（大目录时尤其重要）
    usage = shutil.disk_usage(str(SHARE_ROOT))
    share_physical, _cats = _scan_share_sizes()
    return {
        "total": human_size(usage.total),
        "used": human_size(usage.used),
        "free": human_size(usage.free),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "share_size": human_size(share_physical),
        "warning": usage.free < 15 * 1024**3,
    }


@router.get("/api/disk/detail")
def disk_detail(user=Depends(require_user)):
    """存储详情：磁盘总况全员可见；分目录构成仅 admin；
    成员返回自己的配额用量（private 空间逻辑字节）。"""
    usage = shutil.disk_usage(str(SHARE_ROOT))
    share_physical, cat_sizes = _scan_share_sizes()
    resp = {
        "total": usage.total, "total_human": human_size(usage.total),
        "used": usage.used, "used_human": human_size(usage.used),
        "free": usage.free, "free_human": human_size(usage.free),
        "warning": usage.free < 15 * 1024**3,
        "share_size": share_physical, "share_size_human": human_size(share_physical),
        "categories": None, "private_used": None, "quota_bytes": None,
    }
    if user["role"] == ADMIN:
        resp["categories"] = {name: {"size": size, "size_human": human_size(size)}
                              for name, size in cat_sizes.items()}
    else:
        used = 0
        own = SHARE_ROOT / "private" / user["username"]
        if own.exists():
            for f in own.rglob("*"):
                if f.is_file():
                    try:
                        used += f.stat().st_size
                    except OSError:
                        continue
        resp["private_used"] = used
        try:
            resp["quota_bytes"] = user["quota_bytes"]
        except (IndexError, KeyError):
            resp["quota_bytes"] = None
    return resp
