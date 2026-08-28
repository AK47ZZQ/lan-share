"""WebDAV：PROPFIND/GET/PUT/MKCOL/MOVE/COPY/DELETE/LOCK，挂载为网络磁盘。"""
import base64
import mimetypes
import os
import secrets
import shutil
from datetime import datetime
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import aiofiles
from fastapi import HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.config import MAX_UPLOAD_SIZE, SHARE_ROOT
from app.db import _db
from app.security import (audit, client_ip, get_session_user, login_locked_remaining,
                          record_login_fail, verify_password)
from app.filetools import _RESERVED_NAME, human_size
from app.spaces import enforce_quota, resolve
from app.api.files import _move_to_trash
from app.main import app

# ─── WebDAV ───────────────────────────────────────────────
# 挂载为网络磁盘：RaiDrive / WinSCP / Cyberduck / ES文件管理器等客户端连 https://<host>:8765/dav/
# 认证：WebDAV 客户端走 Basic（管理员账号密码，复用 scrypt 校验与防爆破锁定），浏览器会话同样有效。

DAV_METHODS = ["OPTIONS", "PROPFIND", "PROPPATCH", "GET", "HEAD", "PUT",
               "MKCOL", "DELETE", "MOVE", "COPY", "LOCK", "UNLOCK"]


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


async def _dav_auth(request: Request):
    """WebDAV 认证：Cookie 会话或 Basic（复用登录防爆破）。scrypt 校验在线程池执行。"""
    user = get_session_user(request)
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
    remaining = login_locked_remaining(username)
    if remaining:
        audit("login_locked", username, f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=401, detail="Locked",
                            headers={"WWW-Authenticate": 'Basic realm="LAN Share"'})
    row = _db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    ok = bool(row) and await run_in_threadpool(verify_password, password, row["password_hash"])
    if not ok:
        record_login_fail(username)
        audit("login_fail", username, ip=client_ip(request))
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
    return resolve(user, p[len("/dav/"):], "write")


_READ_METHODS = {"GET", "HEAD", "PROPFIND", "PROPPATCH", "OPTIONS", "LOCK", "UNLOCK"}


@app.api_route("/dav", methods=DAV_METHODS)
@app.api_route("/dav/{path:path}", methods=DAV_METHODS)
async def dav_handler(request: Request, path: str = ""):
    user = await _dav_auth(request)
    method = request.method
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
                            headers={"Content-Disposition": f'inline; filename="{_xml_escape(target.name)}"'})

    if method == "PROPFIND":
        if not exists:
            raise HTTPException(status_code=404)
        depth = request.headers.get("depth", "1")
        entries = [_dav_prop_entry(f"/dav/{path}".rstrip("/") if path else "/dav/", target)]
        if target.is_dir() and depth != "0":
            for child in sorted(target.iterdir(), key=lambda e: e.name.lower()):
                if child.name.startswith("."):
                    continue
                rel = f"/dav/{path}/{child.name}" if path else f"/dav/{child.name}"
                entries.append(_dav_prop_entry(rel, child))
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
        tmp = target.with_name(target.name + ".dav-uploading")
        size = 0
        async with aiofiles.open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    tmp.unlink(missing_ok=True)
                    raise HTTPException(status_code=413)
                await f.write(chunk)
        if exists:
            # 覆盖的旧版本进回收站，而不是直接丢失
            _move_to_trash(target, str(target.relative_to(SHARE_ROOT)).replace("\\", "/"))
        enforce_quota(user, target, size)
        os.replace(tmp, target)
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
        # 与界面删除一致：进回收站
        _move_to_trash(target, path)
        audit("dav_delete", path, ip=client_ip(request))
        return Response(status_code=204)

    if method in ("MOVE", "COPY"):
        if not exists:
            raise HTTPException(status_code=404)
        dest = _dav_destination(request, user)
        if dest == target:
            raise HTTPException(status_code=403, detail="Same resource")
        overwrite = request.headers.get("overwrite", "T").upper() != "F"
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
        return Response(status_code=201 if not dest.exists() or method == "COPY" else 204)

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
