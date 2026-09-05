"""WebDAV：PROPFIND/GET/PUT/MKCOL/MOVE/COPY/DELETE/LOCK，挂载为网络磁盘。"""
import base64
import mimetypes
import os
import secrets
import shutil
import time
from datetime import datetime
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import aiofiles
from fastapi import HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.config import MAX_WEBDAV_SIZE, SHARE_ROOT
from app.db import _db
from app.security import (audit, allow_rate, client_ip, get_session_user, is_public_entry,
                          login_locked_remaining, record_login_fail, verify_password)
from app.filetools import _RESERVED_NAME, clean_filename, human_size
from app.spaces import enforce_quota, resolve
from app.api.files import _cross_space_guard, _is_own_private_root, _move_to_trash
from app.main import app

# ─── WebDAV ───────────────────────────────────────────────
# 挂载为网络磁盘：RaiDrive / WinSCP / Cyberduck / ES文件管理器等客户端连 https://<host>:8765/dav/
# 认证：WebDAV 客户端走 Basic（优先账号的 WebDAV 独立密码，未设置则回退登录密码；
# 复用 scrypt 校验与防爆破锁定），浏览器会话同样有效。

DAV_METHODS = ["OPTIONS", "PROPFIND", "PROPPATCH", "GET", "HEAD", "PUT",
               "MKCOL", "DELETE", "MOVE", "COPY", "LOCK", "UNLOCK"]


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


async def _dav_auth(request: Request):
    """WebDAV 认证：Cookie 会话或 Basic（复用登录防爆破）。scrypt 校验在线程池执行。"""
    # Cookie 会话查询含 DB 往返（每 DAV 请求必经，PROPFIND 高频），进线程池
    user = await run_in_threadpool(get_session_user, request)
    if user:
        return user
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": 'Basic realm="LAN Share"'})
    try:
        username, password = base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": 'Basic realm="LAN Share"'})
    pub = is_public_entry(request)   # 公网侧失败只锁公网侧（DoS 隔离）
    remaining = login_locked_remaining(username, pub)
    if remaining:
        audit("login_locked", username, f"{'pub ' if pub else ''}{remaining}s", client_ip(request))
        raise HTTPException(status_code=401, detail="Locked",
                            headers={"WWW-Authenticate": 'Basic realm="LAN Share"'})
    row = _db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row and not row["is_active"]:
        audit("login_disabled", username, ip=client_ip(request))
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": 'Basic realm="LAN Share"'})
    # 独立密码启用后 WebDAV 只认它（与登录密码隔离：泄露面独立、可单独轮换）；
    # 未设置时回退登录密码。TOTP 对 Basic Auth 不可用（协议无第二步，见 SECURITY.md）
    stored = row["webdav_password_hash"] if row and row["webdav_password_hash"] else (
        row["password_hash"] if row else None)
    ok = bool(row) and stored and await run_in_threadpool(verify_password, password, stored)
    if not ok:
        # IP 级失败限速（R1-P2-1）：/dav 不在应用层限速桶内，公网匿名者可对
        # 每个账号发 5 次坏凭据滚动锁死全家 DAV+登录（15 分钟窗口补 5 发）。
        # 只在失败时消耗令牌，不影响正常 DAV 客户端的高频 Basic 认证
        if not allow_rate(f"davfail:{client_ip(request)}", 15, 60):
            raise HTTPException(status_code=429, detail="Too many attempts",
                                headers={"WWW-Authenticate": 'Basic realm="LAN Share"'})
        record_login_fail(username, pub)
        audit("login_fail", username, "pub" if pub else "", client_ip(request))
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": 'Basic realm="LAN Share"'})
    return row


def _dav_prop_entry(href_path: str, target: Path) -> str:
    is_dir = target.is_dir()
    stat = target.stat()
    if is_dir:
        resource_type = "<D:resourcetype><D:collection/></D:resourcetype>"
        length = ""
        ctype = ""
    else:
        resource_type = "<D:resourcetype/>"
        length = f"<D:getcontentlength>{stat.st_size}</D:getcontentlength>"
        ctype = f"<D:getcontenttype>{_xml_escape(mimetypes.guess_type(str(target))[0] or 'application/octet-stream')}</D:getcontenttype>"
    last_mod = formatdate(stat.st_mtime, usegmt=True)
    display = _xml_escape(target.name or "dav")
    href = _xml_escape(quote(href_path)) + ("/" if is_dir and not href_path.endswith("/") else "")
    return (f"<D:response><D:href>{href}</D:href><D:propstat><D:prop>"
            f"<D:displayname>{display}</D:displayname>{length}{ctype}"
            f"<D:getlastmodified>{last_mod}</D:getlastmodified>"
            f"<D:creationdate>{datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%dT%H:%M:%SZ')}</D:creationdate>"
            f"{resource_type}</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")


def _dav_destination(request: Request, user) -> Path:
    raw = request.headers.get("destination", "")
    p = unquote(urlsplit(raw).path)
    if p == "/dav":
        return resolve(user, "", "write")
    if not p.startswith("/dav/"):
        raise HTTPException(status_code=400, detail="Invalid Destination")
    segs = [s for s in p[len("/dav/"):].replace("\\", "/").split("/") if s not in ("", ".")]
    cleaned = "/".join(clean_filename(s) for s in segs)  # 目标名同界面规则清洗
    return resolve(user, cleaned, "write")


def _dav_tree_size(p: Path) -> int:
    """COPY 进私人空间前的配额计价（与 /api/copy 同语义）。"""
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


_READ_METHODS = {"GET", "HEAD", "PROPFIND", "PROPPATCH", "OPTIONS", "LOCK", "UNLOCK"}


@app.api_route("/dav", methods=DAV_METHODS)
@app.api_route("/dav/{path:path}", methods=DAV_METHODS)
async def dav_handler(request: Request, path: str = ""):
    user = await _dav_auth(request)
    method = request.method
    if path:  # 逐段清洗 Windows 非法字符（与界面 mkdir/rename 同规则），防落盘 OSError
        segs = [s for s in path.replace("\\", "/").split("/") if s not in ("", ".")]
        path = "/".join(clean_filename(s) for s in segs)
    need = "read" if method in _READ_METHODS or method == "COPY" else "write"
    target = resolve(user, path, need) if path else (
        SHARE_ROOT if need == "read" else resolve(user, "", "write"))
    exists = target.exists()

    if method == "OPTIONS":
        return Response(status_code=200, headers={
            "DAV": "1, 2", "MS-Author-Via": "DAV",
            "Allow": ", ".join(DAV_METHODS)})

    if method in ("GET", "HEAD"):
        if not exists:
            raise HTTPException(status_code=404)
        if target.is_dir():
            raise HTTPException(status_code=400, detail="Collection")
        mime, _ = mimetypes.guess_type(str(target))
        return FileResponse(target, media_type=mime or "application/octet-stream",
                            filename=target.name)

    if method == "PROPFIND":
        if not exists:
            raise HTTPException(status_code=404)
        depth = request.headers.get("depth", "1")
        entries = [_dav_prop_entry(f"/dav/{path}".rstrip("/") if path else "/dav/", target)]
        if target.is_dir() and depth != "0":
            for child in sorted(target.iterdir(), key=lambda e: e.name.lower()):
                if child.name.startswith("."):
                    continue
                # iterdir 与 stat 之间条目可能被并发删除（R1-P2-2）：一次竞态
                # OSError 不能让整个 PROPFIND 500
                try:
                    rel = f"/dav/{path}/{child.name}" if path else f"/dav/{child.name}"
                    entries.append(_dav_prop_entry(rel, child))
                except OSError:
                    continue
        body = ('<?xml version="1.0" encoding="utf-8"?>\n'
                '<D:multistatus xmlns:D="DAV:">' + "".join(entries) + "</D:multistatus>")
        return Response(content=body, status_code=207, media_type="application/xml; charset=utf-8")

    if method == "PROPPATCH":
        # 客户端改属性（如文件时间）：假装接受
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<D:multistatus xmlns:D="DAV:"><D:response>'
                f'<D:href>/dav/{_xml_escape(quote(path))}</D:href>'
                '<D:propstat><D:prop/><D:status>HTTP/1.1 200 OK</D:status></D:propstat>'
                '</D:response></D:multistatus>')
        return Response(content=body, status_code=207, media_type="application/xml; charset=utf-8")

    if method == "PUT":
        if target.is_dir():
            raise HTTPException(status_code=409, detail="Is collection")
        parent = target.parent
        if not parent.exists():
            raise HTTPException(status_code=409, detail="Parent collection missing")
        created = not exists
        tmp = target.with_name(f"{target.name}.dav-uploading-{os.getpid()}-{time.time_ns()}")
        size = 0
        try:
            async with aiofiles.open(tmp, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_WEBDAV_SIZE:
                        tmp.unlink(missing_ok=True)
                        raise HTTPException(status_code=413)
                    await f.write(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if exists:
            # 覆盖的旧版本进回收站，而不是直接丢失（.trash 在 private 树外，
            # 不计入配额扫描；先移走旧文件，下一步的配额扫描即为最终占用）
            _move_to_trash(target, str(target.relative_to(SHARE_ROOT)).replace("\\", "/"))
        # 配额计价：此刻 private 树里已有 tmp（=即将落位的最终内容），旧版已
        # 移出——扫描值即落位后的占用，incoming 传 0 才是精确校验。传 size 会
        # 把 tmp 双计、误报配额不足。失败必须清掉 tmp 再抛，别留半截残留。
        try:
            enforce_quota(user, target, 0)
        except HTTPException:
            tmp.unlink(missing_ok=True)
            raise
        # 落位前复查 + 失败清理（R1-P2-5）：流传输期间目标处可能被并发建出
        # 同名目录（exists 是流开始前的旧值），os.replace 顶目录会 PermissionError
        if target.is_dir():
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="Is collection")
        try:
            os.replace(tmp, target)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        audit("dav_put", str(target.relative_to(SHARE_ROOT)).replace("\\", "/"),
              human_size(size), client_ip(request))
        return Response(status_code=201 if created else 204)

    if method == "MKCOL":
        if exists:
            raise HTTPException(status_code=405, detail="Already exists")
        if not target.parent.exists():
            raise HTTPException(status_code=409, detail="Parent collection missing")
        if _RESERVED_NAME.match(target.name):
            raise HTTPException(status_code=400, detail="Reserved name")
        target.mkdir(parents=False)
        audit("dav_mkdir", path, ip=client_ip(request))
        return Response(status_code=201)

    if method == "DELETE":
        if not exists:
            raise HTTPException(status_code=404)
        if target == SHARE_ROOT:
            raise HTTPException(status_code=403)
        if _is_own_private_root(user, target):
            raise HTTPException(status_code=403, detail="私人空间根目录不可删除")
        # 与界面删除一致：进回收站（登记物理相对路径）
        _move_to_trash(target, str(target.relative_to(SHARE_ROOT)).replace("\\", "/"))
        audit("dav_delete", path, ip=client_ip(request))
        return Response(status_code=204)

    if method in ("MOVE", "COPY"):
        if not exists:
            raise HTTPException(status_code=404)
        dest = _dav_destination(request, user)
        if dest == target:
            raise HTTPException(status_code=403, detail="Same resource")
        # 与界面 /api/move 同款成环守卫：把目录搬进自己的子目录
        if target.is_dir() and target in dest.parents:
            raise HTTPException(status_code=400, detail="Cannot move/copy into itself")
        overwrite = request.headers.get("overwrite", "T").upper() != "F"

        # 拷贝/移动可能是 10GB 级操作（含 _dav_tree_size 全树扫描），必须离开
        # 事件循环跑线程池，否则整个服务所有用户一起冻结
        def _do_transfer() -> int:
            if method == "MOVE":
                # 与界面 /api/move 同款守卫：私人根不可移动、成员不得跨空间搬运（E9）
                if _is_own_private_root(user, target):
                    raise HTTPException(status_code=403, detail="私人空间根目录不可移动")
                _cross_space_guard(user, target, dest)
            else:
                enforce_quota(user, dest, _dav_tree_size(target))
            if dest.exists():
                if not overwrite:
                    raise HTTPException(status_code=412, detail="Destination exists")
                # 被覆盖的旧目标进回收站，与界面行为一致
                _move_to_trash(dest, str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"))
            if method == "MOVE":
                shutil.move(str(target), str(dest))
                audit("dav_move", path, str(dest.relative_to(SHARE_ROOT)), client_ip(request))
            else:
                if target.is_dir():
                    shutil.copytree(target, dest)
                else:
                    shutil.copy2(target, dest)
                audit("dav_copy", path, str(dest.relative_to(SHARE_ROOT)), client_ip(request))
            return 201 if not dest.exists() or method == "COPY" else 204

        return Response(status_code=await run_in_threadpool(_do_transfer))

    if method == "LOCK":
        token = f"opaquelocktoken:{secrets.token_hex(8)}"
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
                '<D:locktype><D:write/></D:locktype><D:lockscope><D:exclusive/></D:lockscope>'
                '<D:depth>0</D:depth><D:timeout>Second-3600</D:timeout>'
                f'<D:locktoken><D:href>{token}</D:href></D:locktoken>'
                '</D:activelock></D:lockdiscovery></D:prop>')
        return Response(content=body, status_code=200, media_type="application/xml; charset=utf-8",
                        headers={"Lock-Token": f"<{token}>"})

    if method == "UNLOCK":
        return Response(status_code=204)

    raise HTTPException(status_code=405, detail="Method not supported")
