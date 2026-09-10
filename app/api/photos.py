"""照片时间线（07 审查批次 3B：从 system.py 按域拆出）。

增量扫描器（EXIF/ffprobe/PIL 补扫）、时间线与 facets 查询、图片元数据。
上传落盘后的扫描节流清零入口 photos_scan_bump() 由 uploads 模块调用。
"""
import json
import logging
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.config import SHARE_ROOT
from app.db import _db, _db_lock
from app.roles import ADMIN, MEMBER
from app.security import preview_user, require_user
from app.filetools import IMG_EXTS, VID_EXTS, ensure_heif_support, to_rel
from app.spaces import resolve, visible
from app.store import users as users_store
from app.api.albums import load_albums as _load_albums

ensure_heif_support()   # HEIC/HEIF 解码注册（照片时间线 EXIF/元数据需要）

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


# ─── 照片时间线 ───────────────────────────────────────────
# IMG_EXTS/VID_EXTS 单一事实源在 filetools（07 审查 §5.1-3），与预览/缩略图共用
# （旧 PHOTO_EXTS 曾缺 .gif、旧 VIDEO_EXTS 独有 .m4v/.3gp，两边已漂移）。
# 视频纳入照片时间线（App v0.6.0）：备份本就支持视频，此前时间线只扫图片，
# 手机备份的视频在照片墙完全不可见。类型由扩展名推导（media_type），不建列。
_last_photo_scan = 0.0
_last_force_scan = 0.0


def reset_scan_throttle() -> None:
    """重置照片扫描节流水位（测试专用接口，07 审查批次 2 测试解钉）。"""
    global _last_photo_scan, _last_force_scan
    _last_photo_scan = 0.0
    _last_force_scan = 0.0
_photo_scan_lock = threading.Lock()


def _media_type(rel: str) -> str:
    return "video" if Path(rel).suffix.lower() in VID_EXTS else "photo"


_FFPROBE_FALLBACKS = ("C:/env/ffmpeg/bin/ffprobe.exe",)   # NSSM 服务 PATH 常不带 ffmpeg


def _ffprobe_exe() -> str | None:
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    for p in _FFPROBE_FALLBACKS:
        if Path(p).is_file():
            return p
    return None


def _ffprobe_meta(path: Path) -> tuple[float | None, str | None, str | None]:
    """视频元数据：(时长秒, 拍摄日期YYYY-MM-DD, 视频编码)。ffprobe 不可用时静默
    降级（三项留空，App 只显示 ▶ 角标）；date 取容器 creation_time（手机拍摄的
    视频 mtime 常是"拷入时间"，比 EXIF 不可用的图片更需要这一层）；编码取第一
    视频流 codec_name，前端据此对 HEVC 等浏览器播不了的格式给下载兜底提示。"""
    exe = _ffprobe_exe()
    if not exe:
        return None, None, None
    try:
        r = subprocess.run(
            [exe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=15)
        if r.returncode != 0:
            return None, None, None
        data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
        fmt = data.get("format") or {}
        dur = float(fmt["duration"]) if fmt.get("duration") else None
        ct = (fmt.get("tags") or {}).get("creation_time")
        taken = str(ct)[:10] if ct else None
        codec = next((s.get("codec_name") for s in data.get("streams") or []
                      if s.get("codec_type") == "video" and s.get("codec_name")), None)
        return dur, taken, codec
    except Exception:
        return None, None, None


def _probe_video_durations(cap: int = 20):
    """补扫照片行缺失的时长/编码/尺寸。duration 语义：NULL=待补扫（仅视频），
    0=非视频/已处理。图片行入库即写 0；存量 NULL 图片行在此收编，之后补扫查询
    不再被图片行白拉全表。codec/width/height 是 v0.11 新列：duration 已有而
    codec 或尺寸缺失的行同样纳入补扫（探到啥刷啥，互不阻塞）；尺寸用 PIL 只
    读文件头，约 1-5ms/张，视频行不探尺寸。两段式更新，PG 友好：CASE 里悬空
    NULL 参数会让 psycopg 报 IndeterminateDatatype；taken_at 仅在有值时单独刷。"""
    rows = _db().execute("SELECT path, duration, codec, width FROM photos "
                         "WHERE duration IS NULL OR codec IS NULL OR width IS NULL").fetchall()
    if not rows:
        return
    todo, others = [], []
    for r in rows:
        if Path(r["path"]).suffix.lower() in VID_EXTS:
            todo.append(r["path"])
        elif r["duration"] is None:
            others.append(r["path"])   # codec/尺寸 IS NULL 的图片行不占视频补扫配额
    updates = []
    for rel in todo[:cap]:
        f = SHARE_ROOT / rel
        if not f.is_file():
            continue   # 留 NULL：下次全量扫描的 gone 清理会删行
        dur, taken, codec = _ffprobe_meta(f)
        if dur is not None or codec:
            updates.append((dur, taken, codec, None, None, rel))
    if not others and not updates:
        return
    with _db_lock:
        if others:
            _db().executemany("UPDATE photos SET duration=0 WHERE path=?",
                              [(p,) for p in others])
        if updates:
            _db().executemany("UPDATE photos SET duration=?, codec=?, width=?, height=? WHERE path=?",
                              [(d, c, w, h, p) for (d, t, c, w, h, p) in updates])
            taken_updates = [(t, p) for (d, t, c, w, h, p) in updates if t]
            if taken_updates:
                _db().executemany("UPDATE photos SET taken_at=? WHERE path=?",
                                  taken_updates)
        _db().commit()


def _probe_image_sizes(cap: int = 200):
    """补扫图片尺寸（瀑布流需要真实宽高比；未知的格子 App 回退方格）。
    PIL 只读文件头不解码像素，单张约 1-5ms；每轮 200 张，几千张存量图
    十几分钟内自然收敛，不与视频补扫抢配额。"""
    rows = _db().execute(
        "SELECT path FROM photos WHERE width IS NULL AND duration=0 LIMIT ?",
        (cap,)).fetchall()
    if not rows:
        return
    from PIL import Image
    updates = []
    gone = []
    for r in rows:
        f = SHARE_ROOT / r["path"]
        if not f.is_file():
            gone.append((r["path"],))
            continue
        try:
            with Image.open(f) as im:
                updates.append((im.width, im.height, r["path"]))
        except Exception:
            # 打不开的图：写 0x0 占位，避免每轮重扫
            updates.append((0, 0, r["path"]))
    if not updates and not gone:
        return
    with _db_lock:
        if updates:
            _db().executemany("UPDATE photos SET width=?, height=? WHERE path=?",
                              updates)
        if gone:
            _db().executemany("DELETE FROM photos WHERE path=?", gone)
        _db().commit()


def _photo_taken_at(f: Path) -> str:
    """优先 EXIF DateTimeOriginal，其次 EXIF DateTime，最后文件修改时间。返回 'YYYY-MM-DD'。"""
    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
    if f.suffix.lower() not in (".jpg", ".jpeg", ".webp", ".bmp", ".heic", ".heif"):
        return mtime
    try:
        from PIL import Image
        with Image.open(f) as img:
            exif = img.getexif()
            raw = exif.get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
            if raw:
                return str(raw)[:10].replace(":", "-")
    except Exception:
        pass
    return mtime


def _scan_photos(force: bool = False):
    """增量扫描图片库：新/变更文件提取拍摄日期，消失的文件清理条目。
    普通请求 60s 内不重复扫；force（客户端已知有新照片，如备份刚完成/
    照片页脉搏轮询）走**独立的 5s 节流**——强刷必须真扫，不被其他客户端
    的普通请求顶掉（0.7.2 前共用一个时间戳，网页正刷照片墙时 App 的
    refresh=1 常被 5s 内的普通命中吞掉，强刷扫了个寂寞）。扫描范围是
    SHARE_ROOT 全盘 rglob，新建文件夹里的照片天然覆盖，无固定目录清单。"""
    global _last_photo_scan, _last_force_scan
    now = time.time()
    if force:
        if now - _last_force_scan < 5:
            return
    else:
        if now - _last_photo_scan < 60:
            return
    with _photo_scan_lock:
        if force:
            if time.time() - _last_force_scan < 5:
                return
        else:
            if time.time() - _last_photo_scan < 60:
                return
        seen = set()
        updates = []
        # 一次性载入现有 photos 索引做内存比对（循环里逐条 SELECT 是 N+1，
        # 几万文件的库每轮扫描就是几万次往返）；gone 的差集也直接由它推导
        known = {r["path"]: (r["mtime_ns"], r["size"])
                 for r in _db().execute("SELECT path, mtime_ns, size FROM photos").fetchall()}
        for f in SHARE_ROOT.rglob("*"):
            rel_parts = f.relative_to(SHARE_ROOT).parts
            if any(p.startswith(".") for p in rel_parts):
                continue
            if not f.is_file() or f.suffix.lower() not in IMG_EXTS | VID_EXTS:
                continue
            stat = f.stat()
            rel = to_rel(f)
            seen.add(rel)
            row = known.get(rel)
            if row and row[0] == stat.st_mtime_ns and row[1] == stat.st_size:
                continue
            # 图片行 duration 写 0（非视频即"已处理"），视频留 NULL 交给补扫；
            # 内容变更（mtime 变）时 duration 一并重置，换过的视频会被重新探测。
            # added_at：首次入库打点（ON CONFLICT 更新分支不碰它——拍照时间
            # 排序不变，只有新文件才拿到现在时刻）
            updates.append((rel, _photo_taken_at(f), stat.st_mtime_ns, stat.st_size,
                            None if f.suffix.lower() in VID_EXTS else 0,
                            time.time()))
        with _db_lock:
            if updates:
                _db().executemany(
                    "INSERT INTO photos(path, taken_at, mtime_ns, size, duration, added_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(path) DO UPDATE SET taken_at=excluded.taken_at, "
                    "mtime_ns=excluded.mtime_ns, size=excluded.size, duration=excluded.duration", updates)
                _db().execute("DELETE FROM photos WHERE mtime_ns=0")
            gone = [p for p in known if p not in seen]
            if gone:
                _db().executemany("DELETE FROM photos WHERE path=?", [(g,) for g in gone])
            _db().commit()
        _last_photo_scan = time.time()
        _last_force_scan = time.time()
    # 补扫放扫描锁外：ffprobe 批量最坏 20×15s，不占住扫描锁拖慢时间线刷新
    _probe_video_durations()
    _probe_image_sizes()   # 瀑布流宽高补扫（PIL 读头，200 张/轮）


def photos_scan_bump():
    """上传落盘后调用（v0.20）：清零普通/强刷两个节流时间戳，下一次
    /api/photos 请求必定真扫。二维码/网页/App 上传的图片此前要等有人
    打开照片页且过了 60s 普通节流才入库——"刚传的照片不出现"尾巴收口。
    两个赋值零成本，上传端点同步调用即可。"""
    global _last_photo_scan, _last_force_scan
    _last_photo_scan = 0.0
    _last_force_scan = 0.0


def _known_users() -> set[str]:
    return users_store.list_usernames()


def _photo_uploader(rel: str, users: set[str] | None = None) -> str:
    """拍摄者维度：按路径归属推导（private/<u>/、incoming/<u>/ 归属本人；
    照片/手机相册/<u>/ 为旧版 App 备份落点（0.7.0 前），归属 <u>；
    照片|视频/<u>/<相册>/… 为 0.7.0 新落点——第二段必须命中真实用户名
    才判归属，否则手工整理的二级目录（照片/同学&生活&学校/…）会被误判
    成用户；其余算共享。"""
    parts = rel.split("/")
    if len(parts) > 1 and parts[0].lower() in ("private", "incoming"):
        return parts[1]
    if len(parts) > 3 and parts[0] == "照片" and parts[1] == "手机相册":
        return parts[2]
    if (users and len(parts) > 3 and parts[0] in ("照片", "视频")
            and parts[1] in users):
        return parts[1]
    return "共享空间"
@router.get("/api/photos")
def list_photos(request: Request, limit: int = Query(default=120, ge=0, le=500),
                offset: int = Query(default=0, ge=0),
                month: str | None = Query(default=None),
                uploader: str | None = Query(default=None),
                media_type: str | None = Query(default=None),
                album: str | None = Query(default=None),
                sort: str = Query(default="taken", pattern="^(taken|added)$"),
                refresh: bool = Query(default=False),
                _user=Depends(require_user)):
    """照片时间线：默认按拍摄日期倒序（EXIF 优先，视频取容器 creation_time）；
    sort=added 按入库时间倒序（二维码/手机上传的照片按 EXIF 落历史月份，
    时间线顶部"看不到新照片"的感知问题——入库排序让新上传永远在最前）。
    可按 month=YYYY-MM 与 uploader、media_type=photo|video、album=<精选专辑id>
    过滤。refresh=1 时放宽扫描节流（App 在备份完成后带此参数拉最新）。"""
    user = preview_user(request, _user)   # admin 成员视角预览
    _scan_photos(force=refresh)
    users = _known_users()
    # 可见性下推 SQL（R1-P2-10）：member/admin 的 visible() 语义可用路径前缀
    # 完整表达——shared 全可见、private/incoming 仅本人子树；点前缀隐藏段
    # 一并排除（与 visible() 对齐）。guest 仍走 Python 过滤（grants 已有缓存）
    role = user["role"] if "role" in user.keys() else MEMBER
    pushdown = False
    # 非 ASCII 用户名不做下推（R3-P3-4）：SQLite LIKE 只折叠 ASCII 大小写，
    # 与 _normcase 的全 Unicode 折叠在改名（Windows 保留旧大小写）场景下
    # 会分叉——本人照片 fail-closed 消失；退回 Python 过滤
    if role in (ADMIN, MEMBER) and user["username"].isascii():
        pushdown = True
        esc = (user["username"].replace("\\", "\\\\")
               .replace("%", r"\%").replace("_", r"\_"))
        own_priv = f"private/{esc}/%"
        if role == ADMIN:
            cond = ("(path NOT LIKE 'private/%' OR path LIKE ? ESCAPE '\\')"
                    " AND path NOT LIKE '.%' AND path NOT LIKE '%/.%'")
            args: list = [own_priv]
        else:
            cond = ("((path NOT LIKE 'private/%' AND path NOT LIKE 'incoming/%')"
                    " OR path LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\')"
                    " AND path NOT LIKE '.%' AND path NOT LIKE '%/.%'")
            args = [own_priv, f"incoming/{esc}/%"]
    # 入库排序（v0.20）：COALESCE 双引擎通用（SQLite/PG 均无布尔短路差异），
    # added_at 为空的存量行沉底
    order = ("COALESCE(added_at, 0) DESC, path"
             if sort == "added" else "taken_at DESC, path")
    if pushdown:
        all_rows = _db().execute(
            f"SELECT path, taken_at, duration, codec, width, height FROM photos "
            f"WHERE {cond} ORDER BY {order}", args).fetchall()
    else:
        all_rows = _db().execute(
            f"SELECT path, taken_at, duration, codec, width, height FROM photos ORDER BY {order}").fetchall()
    rows = [r for r in all_rows if visible(user, r["path"].split("/"))] \
        if not pushdown else list(all_rows)
    if month:
        rows = [r for r in rows if (r["taken_at"] or "")[:7] == month]
    if uploader:
        rows = [r for r in rows if _photo_uploader(r["path"], users) == uploader]
    if media_type in ("photo", "video"):
        rows = [r for r in rows if _media_type(r["path"]) == media_type]
    if album:
        # 精选专辑过滤（v0.14）：只出专辑勾选的条目，仍按拍摄日排序
        hit = next((a for a in _load_albums() if a.get("id") == album), None)
        if not hit:
            raise HTTPException(status_code=404, detail="专辑不存在")
        wanted = set(hit.get("items") or [])
        rows = [r for r in rows if r["path"] in wanted]
    total = len(rows)
    rows = rows[offset:offset + limit]
    # 拍摄者昵称映射（v0.17 资料体系）：客户端显示优先昵称，筛选仍传 username
    nicks: dict[str, str] = {}
    try:
        for u in users_store.list_nickname_rows():
            nicks[u["username"]] = u["nickname"]
    except (sqlite3.Error, OSError) as e:
        # 只吞 DB 瞬时故障并留痕；其它异常照常上抛（别把故障降级成假 200）
        logging.warning("photo uploader nick map failed: %s", e)
        nicks = {}
    return {
        "total": total,
        "items": [{
            "path": r["path"],
            "name": r["path"].split("/")[-1],
            "taken": r["taken_at"],
            "month": (r["taken_at"] or "")[:7],
            "uploader": _photo_uploader(r["path"], users),
            "uploader_nick": nicks.get(_photo_uploader(r["path"], users), ""),
            "media_type": _media_type(r["path"]),
            "duration": r["duration"],
            "codec": r["codec"],
            "width": r["width"],
            "height": r["height"],
            "thumb": f"/thumb/{r['path']}",
            # 服务器真实存储路径（改进建议 9.6-P1-3），"复制路径"用
            "abs_path": str(SHARE_ROOT / r["path"]).replace("\\", "/"),
        } for r in rows],
    }


@router.get("/api/photos/facets")
def photo_facets(request: Request, _user=Depends(require_user)):
    """相册筛选维度：月份与拍摄者分布（对当前用户可见范围统计）。"""
    user = preview_user(request, _user)   # admin 成员视角预览
    _scan_photos()
    users = _known_users()
    rows = _db().execute("SELECT path, taken_at FROM photos ORDER BY taken_at DESC").fetchall()
    months: dict[str, int] = {}
    uploaders: dict[str, int] = {}
    for r in rows:
        if not visible(user, r["path"].split("/")):
            continue
        m = (r["taken_at"] or "")[:7]
        if len(m) == 7:
            months[m] = months.get(m, 0) + 1
        u = _photo_uploader(r["path"], users)
        uploaders[u] = uploaders.get(u, 0) + 1
    return {
        "months": [{"month": m, "count": c} for m, c in sorted(months.items(), reverse=True)],
        "uploaders": sorted(
            [{"name": u, "count": c} for u, c in uploaders.items()],
            key=lambda x: -x["count"]),
    }
@router.get("/api/photo/meta")
def photo_meta(request: Request, path: str = Query(..., min_length=1),
               _user=Depends(require_user)):
    """图片元数据：原始尺寸 + 拍摄设备（EXIF）。预览信息面板用。"""
    user = preview_user(request, _user)   # 与列表接口一致：admin 视角下按成员身份解析
    target = resolve(user, path, "read")
    if not target.is_file() or target.suffix.lower() not in IMG_EXTS:
        raise HTTPException(status_code=404, detail="Not an image")
    width = height = None
    camera = None
    try:
        from PIL import Image
        with Image.open(target) as im:
            width, height = im.size
            exif = im.getexif()
            make = exif.get(271)
            model = exif.get(272)
            def _s(v):
                return v.decode("utf-8", "ignore").strip() if isinstance(v, bytes) else str(v).strip()
            if model:
                camera = f"{_s(make)} {_s(model)}".strip()
    except Exception:
        pass
    return {"width": width, "height": height, "camera": camera}
