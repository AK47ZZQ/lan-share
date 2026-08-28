"""
LAN Share v2 — 单管理员自用网盘
FastAPI 后端：会话认证（SQLite 持久化）、目录浏览、文件上传、分享链接、QR 码

安全模型：
- 单管理员 + Cookie 会话（30 天滑动过期，DB 存 token 哈希），重启不丢登录态/分享
- 所有 /api/*（除登录/初始化/分享取件）与 /preview/* 需要会话；/files/* 由 Caddy forward_auth 保护
- 登录防爆破按账号锁定（FRP 隧道下 XFF 全为 127.0.0.1，按 IP 锁无意义）
- 分享密码走 POST body，杜绝进访问日志
"""

import asyncio
import base64
import hashlib
import hmac
import io
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from email.utils import formatdate
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

import aiofiles
import qrcode
import qrcode.image.svg
from fastapi import (Depends, FastAPI, File, Form, HTTPException, Query, Request,
                     Response, UploadFile)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

# ─── 配置 ───────────────────────────────────────────────

SHARE_ROOT = Path(os.environ.get("SHARE_ROOT", "C:/lan-share/data"))
PROJECT_ROOT = Path(__file__).parent
DB_PATH = SHARE_ROOT / ".meta.db"

MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
TMP_MAX_AGE_DAYS = 7
CLEANUP_INTERVAL = 3600  # 1 hour

CHUNK_SIZE = 5 * 1024 * 1024          # 分块上传 5MB/块
UPLOAD_SESSION_TTL = 3 * 86400        # 上传会话 3 天过期（断点续传窗口）
TRASH_DAYS = 30                       # 回收站保留天数
ZIP_TTL = 15 * 60                     # zip 打包下载票据有效期（秒）

SESSION_TTL = 30 * 86400          # 30 天
SESSION_RENEW_AFTER = 86400       # 滑动续期：距上次刷新超过 24h 才写库
COOKIE_NAME = "ls_session"
AUDIT_KEEP = 10_000
API_RATE_LIMIT = 120              # /api/* 每分钟每 IP
LOGIN_RATE_LIMIT = 10             # /api/login 每分钟每 IP
SHARE_RATE_LIMIT = 30             # 分享取件 每分钟每 IP
LOGIN_MAX_FAILS = 5               # 连续失败次数
LOGIN_LOCK_SECONDS = 15 * 60      # 账号锁定时长
TICKET_TTL = 10 * 60              # 分下载票据有效期（秒）

# ─── 数据库 ───────────────────────────────────────────────

_db_local = threading.local()
_db_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    """每线程独立连接（WAL 支持多读并发）；写操作仍由 _db_lock 全局串行，避免共享
    连接的跨线程游标交错问题。"""
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        _db_local.conn = conn
    return conn


def init_db():
    SHARE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = _db()
    conn.execute("PRAGMA journal_mode=WAL")
    _db().executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            ip TEXT, ua TEXT
        );
        CREATE TABLE IF NOT EXISTS shares(
            token TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            size INTEGER,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            password_hash TEXT,
            max_downloads INTEGER,
            downloads INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            ip TEXT,
            action TEXT NOT NULL,
            target TEXT,
            detail TEXT
        );
        CREATE TABLE IF NOT EXISTS blobs(
            sha256 TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            sample_path TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trash(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orig_path TEXT NOT NULL,
            trash_path TEXT NOT NULL,
            name TEXT NOT NULL,
            size INTEGER,
            deleted_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS upload_sessions(
            id TEXT PRIMARY KEY,
            dest_dir TEXT NOT NULL,
            filename TEXT NOT NULL,
            total_size INTEGER NOT NULL,
            sha256 TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS photos(
            path TEXT PRIMARY KEY,
            taken_at TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL
        );
    """)
    _db().commit()


def audit(action: str, target: str = "", detail: str = "", ip: str = ""):
    with _db_lock:
        _db().execute(
            "INSERT INTO audit_log(ts, ip, action, target, detail) VALUES(?,?,?,?,?)",
            (time.time(), ip, action, target, detail))
        _db().execute(
            "DELETE FROM audit_log WHERE id <= (SELECT MAX(id) FROM audit_log) - ?", (AUDIT_KEEP,))
        _db().commit()


# ─── 密码与会话 ───────────────────────────────────────────

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
_SCRYPT_MAXMEM = 128 * 1024 * 1024  # OpenSSL 默认 32MB 上限不够，需显式放开


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                       dklen=32, maxmem=_SCRYPT_MAXMEM)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, h_hex = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex),
                              n=int(n), r=int(r), p=int(p), dklen=32,
                              maxmem=_SCRYPT_MAXMEM)
        return hmac.compare_digest(calc.hex(), h_hex)
    except Exception:
        return False


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto == "https"


def get_session_user(request: Request) -> Optional[sqlite3.Row]:
    """校验 Cookie 会话；有效则滑动续期。返回 users 行或 None。"""
    tok = request.cookies.get(COOKIE_NAME)
    if not tok:
        return None
    th = _sha(tok)
    row = _db().execute(
        "SELECT s.expires_at, s.user_id, u.username FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (th,)).fetchone()
    if not row:
        return None
    now = time.time()
    if now > row["expires_at"]:
        with _db_lock:
            _db().execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            _db().commit()
        return None
    if row["expires_at"] < now + SESSION_TTL - SESSION_RENEW_AFTER:
        with _db_lock:
            _db().execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                          (now + SESSION_TTL, th))
            _db().commit()
    return row


def create_session(request: Request, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _db_lock:
        _db().execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, ip, ua) "
            "VALUES(?,?,?,?,?,?)",
            (_sha(token), user_id, now, now + SESSION_TTL,
             client_ip(request), request.headers.get("user-agent", "")[:200]))
        _db().commit()
    return token


def require_user(request: Request) -> sqlite3.Row:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ─── 限速 / 防爆破 / CSRF ─────────────────────────────────

_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
_login_fails: dict[str, list[float]] = {}


def allow_rate(key: str, limit: int, window: int = 60) -> bool:
    now = time.time()
    with _rate_lock:
        q = _rate.setdefault(key, [])
        while q and q[0] <= now - window:
            q.pop(0)
        if len(q) >= limit:
            return False
        q.append(now)
        # 顺带清理空桶，防内存膨胀
        if len(_rate) > 4096:
            for k in [k for k, v in _rate.items() if not v or v[-1] <= now - window]:
                del _rate[k]
        return True


def login_locked_remaining(username: str) -> int:
    """账号连续失败 LOGIN_MAX_FAILS 次后的剩余锁定秒数，0 = 未锁。"""
    now = time.time()
    with _rate_lock:
        # key 与 record_login_fail 一致：小写化，防大小写变体绕过锁定
        q = _login_fails.get(username.strip().lower(), [])
        while q and q[0] <= now - LOGIN_LOCK_SECONDS:
            q.pop(0)
        if len(q) >= LOGIN_MAX_FAILS:
            return int(q[0] + LOGIN_LOCK_SECONDS - now) + 1
        return 0


def record_login_fail(username: str):
    with _rate_lock:
        # key 统一小写：Windows/SQLite 侧用户名大小写变体不能绕过锁定
        _login_fails.setdefault(username.strip().lower(), []).append(time.time())


def client_ip(request: Request) -> str:
    """取 XFF 的最后一个值——那才是我们自己的 Caddy 追加的、不可被客户端伪造的；
    配合 Caddyfile 的 request_header -X-Forwarded-For（剥离外部传入的 XFF）双保险。"""
    xff = request.headers.get("x-forwarded-for", "")
    values = [v.strip() for v in xff.split(",") if v.strip()]
    if values:
        return values[-1]
    return request.client.host if request.client else "?"


app = FastAPI(title="LAN Share", version="2.0.0")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CSRF：带 Origin 的请求必须与 Host 同源（SameSite=Lax 之外的第二道防线）
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        return JSONResponse({"detail": "CSRF check failed"}, status_code=403)
    path = request.url.path
    ip = client_ip(request)
    if path == "/api/login":
        limit, bucket = LOGIN_RATE_LIMIT, "login"
    elif path.startswith("/api/upload/chunk/"):
        limit, bucket = 0, None  # 分块上传按块数计频，不受常规限速（仍有会话认证保护）
    elif path.startswith("/api/share/") or path == "/api/share":
        limit, bucket = SHARE_RATE_LIMIT, "share"
    elif path.startswith("/api/") or path == "/authcheck":
        # authcheck 是 Caddy forward_auth 子请求，跟随 /files 静态请求高频出现，单独桶
        limit, bucket = (0, None) if path == "/authcheck" else (API_RATE_LIMIT, "api")
    else:
        limit, bucket = 0, None
    if limit and bucket and not allow_rate(f"{ip}:{bucket}", limit):
        return JSONResponse({"detail": "Too many requests"}, status_code=429)
    return await call_next(request)


# ─── 文件工具 ─────────────────────────────────────────────

def human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def safe_path(rel: str) -> Path:
    """防目录穿越 + 禁止访问点前缀隐藏路径（.trash/.meta.db 等）。"""
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".") for p in parts):
        raise HTTPException(status_code=403, detail="Forbidden")
    target = SHARE_ROOT.joinpath(*parts) if parts else SHARE_ROOT
    root = os.path.normcase(str(SHARE_ROOT.absolute()))
    tgt = os.path.normcase(str(target.absolute()))
    if tgt != root and not tgt.startswith(root + os.sep):
        raise HTTPException(status_code=403, detail="Forbidden")
    return target


def clean_filename(name: str) -> str:
    n = re.sub(r'[/\\:\x00-\x1f]', '_', name or 'unnamed')  # 分隔符/冒号(NTFS ADS)/控制符
    n = n.strip().rstrip('. ')                              # 结尾点/空格（NTFS 会静默剥离）
    if not n:
        n = 'unnamed'
    if n.startswith('.'):
        n = '_' + n
    if _RESERVED_NAME.match(n):
        n = '_' + n
    return n[:200] or 'unnamed'


_RESERVED_NAME = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$', re.I)


def dir_tree(path: Path) -> dict:
    items = []
    if not path.exists():
        return {"path": "", "items": []}
    for entry in sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.name.startswith("."):
            continue
        stat = entry.stat()
        rel = str(entry.relative_to(SHARE_ROOT)).replace("\\", "/")
        items.append({
            "name": entry.name,
            "path": rel,
            "is_dir": entry.is_dir(),
            "size": stat.st_size if entry.is_file() else None,
            "size_human": human_size(stat.st_size) if entry.is_file() else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "ext": entry.suffix.lower() if entry.is_file() else None,
        })
    rel_path = str(path.relative_to(SHARE_ROOT)).replace("\\", "/")
    return {"path": rel_path if rel_path != "." else "", "items": items}


async def save_upload(file: UploadFile, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    sha256 = hashlib.sha256()
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_SIZE:
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413,
                                    detail=f"File too large (max {human_size(MAX_UPLOAD_SIZE)})")
            sha256.update(chunk)
            await f.write(chunk)
    return {
        "name": dest.name,
        "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
        "size": size,
        "size_human": human_size(size),
        "sha256": sha256.hexdigest(),
    }


def _file_dict(f: Path) -> dict:
    stat = f.stat()
    return {
        "name": f.name,
        "path": str(f.relative_to(SHARE_ROOT)).replace("\\", "/"),
        "size": stat.st_size,
        "size_human": human_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "ext": f.suffix.lower(),
    }


# ─── 认证 API ─────────────────────────────────────────────

class AuthBody(BaseModel):
    username: str
    password: str


class SharePrepareBody(BaseModel):
    password: Optional[str] = None


def user_count() -> int:
    return _db().execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def set_session_cookie(response: Response, request: Request, user_id: int):
    token = create_session(request, user_id)
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_TTL, path="/",
        httponly=True, samesite="lax", secure=is_https(request))


@app.get("/api/auth/state")
async def auth_state(request: Request):
    user = get_session_user(request)
    return {"needs_setup": user_count() == 0,
            "logged_in": bool(user),
            "username": user["username"] if user else None}


@app.post("/api/setup")
async def setup(body: AuthBody, request: Request, response: Response):
    if user_count() > 0:
        raise HTTPException(status_code=403, detail="管理员已存在")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="用户名不能为空")
    with _db_lock:
        _db().execute("INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
                      (body.username.strip(), hash_password(body.password), time.time()))
        _db().commit()
    uid = _db().execute("SELECT id FROM users WHERE username=?",
                        (body.username.strip(),)).fetchone()["id"]
    audit("setup", body.username.strip(), ip=client_ip(request))
    set_session_cookie(response, request, uid)
    return {"ok": True}


@app.post("/api/login")
async def login(body: AuthBody, request: Request, response: Response):
    remaining = login_locked_remaining(body.username)
    if remaining:
        audit("login_locked", body.username, f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    row = _db().execute("SELECT * FROM users WHERE username=?",
                        (body.username,)).fetchone()
    ok = bool(row) and await run_in_threadpool(verify_password, body.password, row["password_hash"])
    if not ok:
        record_login_fail(body.username)
        audit("login_fail", body.username, ip=client_ip(request))
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    with _rate_lock:
        _login_fails.pop(body.username.strip().lower(), None)
    audit("login", body.username, ip=client_ip(request))
    set_session_cookie(response, request, row["id"])
    return {"ok": True, "username": row["username"]}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    tok = request.cookies.get(COOKIE_NAME)
    if tok:
        with _db_lock:
            _db().execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(tok),))
            _db().commit()
    audit("logout", ip=client_ip(request))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/authcheck")
async def authcheck(request: Request):
    """Caddy forward_auth 子请求入口：会话有效 200，否则 401。"""
    if get_session_user(request):
        return PlainTextResponse("ok")
    raise HTTPException(status_code=401, detail="Not authenticated")


@app.get("/api/auth/me")
async def me(request: Request):
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"username": user["username"]}


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/password")
async def change_password(request: Request, response: Response, body: PasswordBody,
                          user=Depends(require_user)):
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    # 密码确认失败同样计入防爆破锁定（否则会话被劫持后可无限试密码）
    remaining = login_locked_remaining(row["username"])
    if remaining:
        audit("login_locked", row["username"], f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    if not await run_in_threadpool(verify_password, body.old_password, row["password_hash"]):
        record_login_fail(row["username"])
        audit("password_change_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="原密码错误")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    new_hash = await run_in_threadpool(hash_password, body.new_password)
    with _db_lock:
        _db().execute("UPDATE users SET password_hash=? WHERE id=?",
                      (new_hash, row["id"]))
        # 改密后吊销全部旧会话，重新签发当前这一个
        _db().execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
        _db().commit()
    audit("password_change", row["username"], ip=client_ip(request))
    set_session_cookie(response, request, row["id"])
    return {"ok": True}


class UsernameBody(BaseModel):
    new_username: str
    password: str


@app.post("/api/auth/username")
async def change_username(request: Request, body: UsernameBody,
                          user=Depends(require_user)):
    row = _db().execute("SELECT * FROM users WHERE id=?", (user["user_id"],)).fetchone()
    # 密码确认失败同样计入防爆破锁定（否则会话被劫持后可无限试密码）
    remaining = login_locked_remaining(row["username"])
    if remaining:
        audit("login_locked", row["username"], f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"失败次数过多，已锁定，请 {remaining // 60 + 1} 分钟后重试")
    if not await run_in_threadpool(verify_password, body.password, row["password_hash"]):
        record_login_fail(row["username"])
        audit("username_change_fail", row["username"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="密码错误")
    new_name = body.new_username.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if len(new_name) > 64 or re.search(r"[\s\u0000-\u001f\u007f]", new_name):
        raise HTTPException(status_code=400,
                            detail="用户名不能含空白/控制字符，且不超过 64 字符")
    if new_name == row["username"]:
        raise HTTPException(status_code=400, detail="新用户名与当前相同")
    if _db().execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (new_name,)).fetchone():
        raise HTTPException(status_code=409, detail="该用户名已被占用")
    with _db_lock:
        _db().execute("UPDATE users SET username=? WHERE id=?", (new_name, row["id"]))
        _db().commit()
    # 会话按 user_id 关联，改名不影响登录态；旧用户名的防爆破计数自然失效
    audit("username_change", row["username"], f"→ {new_name}", client_ip(request))
    return {"ok": True, "username": new_name}


# ─── 文件 API（需登录）─────────────────────────────────────

@app.get("/api/files/{path:path}")
@app.get("/api/files")
async def list_files(path: str = "", _user=Depends(require_user)):
    target = safe_path(path) if path else SHARE_ROOT
    if not target.exists():
        raise HTTPException(status_code=404, detail="Directory not found")
    if target.is_file():
        raise HTTPException(status_code=400, detail="Not a directory")
    return dir_tree(target)


def register_blob(path: Path, sha: str, size: int) -> bool:
    """登记内容指纹；发现同内容旧文件则用 NTFS 硬链接去重（返回是否发生了去重）。

    用 link→临时名 再原子 replace 的方式，避免 unlink 后失败导致新文件丢失的窗口。"""
    row = _db().execute("SELECT sample_path FROM blobs WHERE sha256=?", (sha,)).fetchone()
    if row and row["sample_path"] != str(path):
        sample = Path(row["sample_path"])
        if sample.is_file():
            tmp_link = path.with_name(path.name + ".dedup")
            try:
                os.link(sample, tmp_link)
                os.replace(tmp_link, path)
                return True
            except OSError:
                tmp_link.unlink(missing_ok=True)  # 跨卷/权限失败则保留独立副本
    with _db_lock:
        _db().execute(
            "INSERT INTO blobs(sha256, size, sample_path, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(sha256) DO UPDATE SET sample_path=excluded.sample_path",
            (sha, size, str(path), time.time()))
        _db().commit()
    return False


# ─── 分块上传 / 断点续传 / 秒传 ────────────────────────────

class UploadInitBody(BaseModel):
    dest_dir: str = ""
    filename: str
    size: int
    sha256: Optional[str] = None  # 客户端预计算（≤100MB 才算）；finish 时服务端必校验


def _chunk_dir(sid: str) -> Path:
    return SHARE_ROOT / ".chunks" / sid


@app.post("/api/upload/init")
async def upload_init(request: Request, body: UploadInitBody, _user=Depends(require_user)):
    dest_dir = safe_path(body.dest_dir) if body.dest_dir else SHARE_ROOT
    dest = dest_dir / clean_filename(body.filename)
    if body.size <= 0:
        raise HTTPException(status_code=400, detail="Invalid size")

    # 秒传：库里有同 sha256 内容（服务端算的，可信），直接硬链接
    if body.sha256:
        row = _db().execute("SELECT sample_path FROM blobs WHERE sha256=?", (body.sha256,)).fetchone()
        if row:
            sample = Path(row["sample_path"])
            if sample.is_file():
                try:
                    if dest.exists():
                        dest.unlink()
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.link(sample, dest)
                    audit("upload_instant", str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
                          human_size(body.size), client_ip(request))
                    return {"instant": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"),
                            "size": body.size, "size_human": human_size(body.size)}
                except OSError:
                    pass  # 硬链接失败则走正常分块上传

    sid = secrets.token_urlsafe(16)
    _chunk_dir(sid).mkdir(parents=True, exist_ok=True)
    with _db_lock:
        _db().execute(
            "INSERT INTO upload_sessions(id, dest_dir, filename, total_size, sha256, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (sid, body.dest_dir, dest.name, body.size, body.sha256, time.time()))
        _db().commit()
    return {"instant": False, "session": sid, "chunk_size": CHUNK_SIZE, "received": []}


@app.get("/api/upload/state/{sid}")
async def upload_state(sid: str, _user=Depends(require_user)):
    """断点续传：查询已收到的块序号。"""
    row = _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    d = _chunk_dir(sid)
    received = sorted(int(p.stem.split("-")[1]) for p in d.glob("part-*")) if d.exists() else []
    return {"session": sid, "chunk_size": CHUNK_SIZE, "received": received}


@app.post("/api/upload/chunk/{sid}")
async def upload_chunk(request: Request, sid: str, index: int = Query(...),
                       _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    total = (row["total_size"] + CHUNK_SIZE - 1) // CHUNK_SIZE
    if index < 0 or index >= total:
        raise HTTPException(status_code=400, detail="Chunk index out of range")
    part = _chunk_dir(sid) / f"part-{index:06d}"
    async with aiofiles.open(part, "wb") as f:
        async for chunk in request.stream():
            await f.write(chunk)
    return {"ok": True, "index": index}


@app.post("/api/upload/finish/{sid}")
async def upload_finish(request: Request, sid: str, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM upload_sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload session not found")
    d = _chunk_dir(sid)
    total = (row["total_size"] + CHUNK_SIZE - 1) // CHUNK_SIZE
    parts = sorted(d.glob("part-*"))
    if len(parts) != total:
        raise HTTPException(status_code=400,
                            detail=f"Missing chunks: {len(parts)}/{total}")

    dest_dir = safe_path(row["dest_dir"]) if row["dest_dir"] else SHARE_ROOT
    dest = dest_dir / row["filename"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".uploading")

    size = 0
    sha = hashlib.sha256()
    async with aiofiles.open(tmp, "wb") as out:
        for part in parts:
            async with aiofiles.open(part, "rb") as f:
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    sha.update(chunk)
                    await out.write(chunk)
    if size != row["total_size"]:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Size mismatch")

    real_sha = sha.hexdigest()
    if row["sha256"] and row["sha256"] != real_sha:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="SHA256 mismatch, please re-upload")

    os.replace(tmp, dest)
    deduped = register_blob(dest, real_sha, size)
    shutil.rmtree(d, ignore_errors=True)
    with _db_lock:
        _db().execute("DELETE FROM upload_sessions WHERE id=?", (sid,))
        _db().commit()
    rel = str(dest.relative_to(SHARE_ROOT)).replace("\\", "/")
    audit("upload", rel, human_size(size), client_ip(request))
    return {"ok": True, "path": rel, "size": size, "size_human": human_size(size),
            "sha256": real_sha, "deduped": deduped}


@app.post("/api/upload/{path:path}")
@app.post("/api/upload")
async def upload_file(request: Request, path: str = "", file: UploadFile = File(...),
                      _user=Depends(require_user)):
    clean_name = clean_filename(file.filename)
    dest = safe_path(path) / clean_name if path else SHARE_ROOT / clean_name
    result = await save_upload(file, dest)
    deduped = register_blob(dest, result["sha256"], result["size"])
    audit("upload", result["path"], result["size_human"], client_ip(request))
    return {"ok": True, "deduped": deduped, **result}


@app.post("/api/mkdir/{path:path}")
async def create_directory(request: Request, path: str, _user=Depends(require_user)):
    if not path.strip():
        raise HTTPException(status_code=400, detail="Invalid directory name")
    # 逐段清洗（保留名/冒号/结尾点等），再过 safe_path
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    cleaned = [clean_filename(p) for p in parts]
    target = safe_path("/".join(cleaned))
    target.mkdir(parents=True, exist_ok=True)
    rel = str(target.relative_to(SHARE_ROOT)).replace("\\", "/")
    audit("mkdir", rel, ip=client_ip(request))
    return {"ok": True, "path": rel}


class MoveBody(BaseModel):
    sources: list[str]
    dest_dir: str = ""


@app.post("/api/move")
async def move_items(request: Request, body: MoveBody, _user=Depends(require_user)):
    """批量移动到目标目录。同盘 rename，硬链接去重与秒传指纹不受影响。"""
    if not body.sources:
        raise HTTPException(status_code=400, detail="未选择要移动的文件")
    if len(body.sources) > 500:
        raise HTTPException(status_code=400, detail="单次最多移动 500 项")
    dest_dir = safe_path(body.dest_dir)
    if not dest_dir.is_dir():
        raise HTTPException(status_code=404, detail="目标目录不存在")
    results = []
    for src in body.sources:
        try:
            target = safe_path(src)
            if not target.exists():
                raise HTTPException(status_code=404, detail="源文件不存在")
            if target.parent == dest_dir:
                raise HTTPException(status_code=400, detail="已在目标目录")
            if target == dest_dir or (target.is_dir() and target in dest_dir.parents):
                # 只有"目录移进自己的后代"会成环；移到祖先目录是合法操作
                raise HTTPException(status_code=400, detail="不能移动到自身的子目录")
            # os.rename 在 Windows 下目标已存在必抛 FileExistsError（原子防覆盖，
            # 无 _unique_dest 预检 + os.replace 的并发竞态窗口）
            placed = None
            final = dest_dir / target.name
            for i in range(1, 1000):
                cand = final if i == 1 else final.with_name(f"{final.stem} ({i}){final.suffix}")
                try:
                    os.rename(target, cand)
                    placed = cand
                    break
                except FileExistsError:
                    continue
            if placed is None:
                raise HTTPException(status_code=409, detail="无法生成不重名的目标名")
            final_rel = str(placed.relative_to(SHARE_ROOT)).replace("\\", "/")
            results.append({"src": src, "ok": True, "dest": final_rel})
            audit("move", src, f"→ {final_rel}", client_ip(request))
        except HTTPException as e:
            results.append({"src": src, "ok": False, "error": e.detail})
        except OSError as e:
            results.append({"src": src, "ok": False, "error": str(e)})
    return {"ok": True, "results": results}


def _move_to_trash(target: Path, orig_path: str):
    """把文件/目录移入回收站并登记（界面删除与 WebDAV 删除/覆盖共用）。"""
    trash_root = SHARE_ROOT / ".trash"
    trash_root.mkdir(exist_ok=True)
    trash_path = trash_root / f"{time.time_ns()}_{target.name}"
    try:
        os.replace(target, trash_path)
    except OSError:
        shutil.move(str(target), str(trash_path))
    size = None
    if trash_path.is_file():
        size = trash_path.stat().st_size
    with _db_lock:
        _db().execute(
            "INSERT INTO trash(orig_path, trash_path, name, size, deleted_at) VALUES(?,?,?,?,?)",
            (orig_path, f".trash/{trash_path.name}", target.name, size, time.time()))
        _db().commit()


@app.delete("/api/files/{path:path}")
async def delete_file(request: Request, path: str, _user=Depends(require_user)):
    target = safe_path(path)
    if target == SHARE_ROOT:
        raise HTTPException(status_code=400, detail="不能删除根目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    _move_to_trash(target, path)
    audit("trash", path, ip=client_ip(request))
    return {"ok": True}


@app.get("/api/trash")
async def list_trash(_user=Depends(require_user)):
    rows = _db().execute("SELECT * FROM trash ORDER BY deleted_at DESC").fetchall()
    return [{
        "id": r["id"],
        "name": r["name"],
        "orig_path": r["orig_path"],
        "size": r["size"],
        "size_human": human_size(r["size"]) if r["size"] else "-",
        "deleted_at": datetime.fromtimestamp(r["deleted_at"]).isoformat(),
        "days_left": max(0, TRASH_DAYS - int((time.time() - r["deleted_at"]) / 86400)),
    } for r in rows]


@app.post("/api/trash/{trash_id}/restore")
async def restore_trash(request: Request, trash_id: int, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    trash_path = SHARE_ROOT / row["trash_path"]
    if not trash_path.exists():
        with _db_lock:
            _db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
            _db().commit()
        raise HTTPException(status_code=404, detail="Trash file missing")
    dest = safe_path(row["orig_path"])
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        dest = dest.with_name(f"{stem} (恢复){suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(trash_path), str(dest))
    with _db_lock:
        _db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
        _db().commit()
    audit("restore", str(dest.relative_to(SHARE_ROOT)).replace("\\", "/"), ip=client_ip(request))
    return {"ok": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/")}


@app.delete("/api/trash/{trash_id}")
async def delete_trash_item(request: Request, trash_id: int, _user=Depends(require_user)):
    row = _db().execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    trash_path = SHARE_ROOT / row["trash_path"]
    if trash_path.is_dir():
        shutil.rmtree(trash_path, ignore_errors=True)
    else:
        trash_path.unlink(missing_ok=True)
    with _db_lock:
        _db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
        _db().commit()
    audit("trash_delete", row["name"], ip=client_ip(request))
    return {"ok": True}


@app.delete("/api/trash")
async def empty_trash(request: Request, _user=Depends(require_user)):
    trash_root = SHARE_ROOT / ".trash"
    if trash_root.exists():
        shutil.rmtree(trash_root, ignore_errors=True)
    with _db_lock:
        _db().execute("DELETE FROM trash")
        _db().commit()
    audit("trash_empty", ip=client_ip(request))
    return {"ok": True}


@app.get("/preview/{path:path}")
async def preview_file(path: str, _user=Depends(require_user)):
    target = safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=mime or "application/octet-stream",
                        headers={"Content-Disposition": f"inline; filename=\"{target.name}\""})


# ─── 批量打包下载 / 重命名 ─────────────────────────────────

class ZipBody(BaseModel):
    paths: list[str]


_zips: dict[str, dict] = {}
_zips_lock = threading.Lock()


@app.post("/api/zip")
def create_zip(request: Request, body: ZipBody, _user=Depends(require_user)):
    """打包选中文件/目录为 zip，返回短时效下载 URL。"""
    if not body.paths or len(body.paths) > 500:
        raise HTTPException(status_code=400, detail="Invalid paths")
    targets = [safe_path(p) for p in body.paths]
    for t in targets:
        if not t.exists():
            raise HTTPException(status_code=404, detail=f"Not found: {t.name}")

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
    token = secrets.token_urlsafe(16)
    with _zips_lock:
        _zips[token] = {"path": zpath, "exp": time.time() + ZIP_TTL}
    audit("zip", f"{len(targets)} items", ip=client_ip(request))
    return {"ok": True, "url": f"/api/zip/{token}",
            "name": f"lan-share-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"}


@app.get("/api/zip/{token}")
async def download_zip(token: str, _user=Depends(require_user)):
    with _zips_lock:
        info = _zips.pop(token, None)
    if not info or info["exp"] < time.time():
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


@app.post("/api/rename")
async def rename_path(request: Request, body: RenameBody, _user=Depends(require_user)):
    target = safe_path(body.path)
    if target == SHARE_ROOT:
        raise HTTPException(status_code=400, detail="不能重命名根目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    dest = target.parent / clean_filename(body.new_name)
    if dest == target:
        return {"ok": True}
    if dest.exists():
        raise HTTPException(status_code=400, detail="目标名称已存在")
    target.rename(dest)
    audit("rename", body.path, body.new_name, client_ip(request))
    return {"ok": True, "path": str(dest.relative_to(SHARE_ROOT)).replace("\\", "/")}


# ─── 缩略图 ───────────────────────────────────────────────

IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VID_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


@app.get("/thumb/{path:path}")
def thumb_file(path: str, _user=Depends(require_user)):
    """缩略图（同步端点，跑线程池）。图片用 Pillow，视频用 ffmpeg 抽帧，缓存于 .thumbs/。"""
    from PIL import Image
    target = safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    ext = target.suffix.lower()
    if ext not in IMG_EXTS | VID_EXTS:
        raise HTTPException(status_code=404, detail="No thumbnail for this type")

    stat = target.stat()
    key = hashlib.md5(f"{path}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()
    cache_dir = SHARE_ROOT / ".thumbs"
    cache_dir.mkdir(exist_ok=True)
    cached = cache_dir / f"{key}.jpg"
    if cached.exists():
        return FileResponse(cached, media_type="image/jpeg")

    tmp = cached.with_suffix(".tmp")
    try:
        if ext in IMG_EXTS:
            img = Image.open(target)
            img.thumbnail((320, 320))
            img.convert("RGB").save(tmp, "JPEG", quality=80)
        else:
            # 短视频 -ss 会越界抽不到帧：先试 1s 处，失败退回首帧
            for seek_args in (["-ss", "1"], []):
                tmp.unlink(missing_ok=True)
                r = subprocess.run(
                    ["ffmpeg", "-y"] + seek_args + ["-i", str(target), "-vframes", "1",
                     "-vf", "scale=320:-2", "-f", "image2", str(tmp)],
                    capture_output=True, timeout=25)
                if r.returncode == 0 and tmp.exists():
                    break
            if not tmp.exists():
                raise HTTPException(status_code=404, detail="Thumbnail failed")
        os.replace(tmp, cached)
    except HTTPException:
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Thumbnail error")
    return FileResponse(cached, media_type="image/jpeg")


@app.get("/api/search")
async def search_files(q: str = Query(..., min_length=1), _user=Depends(require_user)):
    results = []
    for f in SHARE_ROOT.rglob("*"):
        rel_parts = f.relative_to(SHARE_ROOT).parts
        if any(p.startswith(".") for p in rel_parts):
            continue
        if f.is_file() and q.lower() in f.name.lower():
            results.append(_file_dict(f))
            if len(results) >= 50:
                break
    return results


@app.get("/api/recent")
async def recent_files(limit: int = Query(default=20, le=100), _user=Depends(require_user)):
    files = []
    for f in SHARE_ROOT.rglob("*"):
        rel_parts = f.relative_to(SHARE_ROOT).parts
        if any(p.startswith(".") for p in rel_parts):
            continue
        if f.is_file():
            files.append(_file_dict(f))
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files[:limit]


def _scan_share_sizes():
    """网盘内容统计：返回 (去重后的物理占用, 各一级目录/根文件的逻辑大小)。
    NTFS 硬链接去重的同一份内容按物理只计一次；隐藏（点前缀）路径全部排除。"""
    seen = set()
    physical = 0
    categories = {}
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
    return physical, categories


@app.get("/api/disk")
def disk_info(_user=Depends(require_user)):
    # 同步 def：FastAPI 自动放线程池，rglob 扫描不阻塞事件循环（大目录时尤其重要）
    usage = shutil.disk_usage(str(SHARE_ROOT))
    share_physical, _cats = _scan_share_sizes()
    return {
        "total": human_size(usage.total),
        "used": human_size(usage.used),
        "free": human_size(usage.free),
        "free_bytes": usage.free,
        "share_size": human_size(share_physical),
        "warning": usage.free < 15 * 1024**3,
    }


@app.get("/api/disk/detail")
def disk_detail(_user=Depends(require_user)):
    usage = shutil.disk_usage(str(SHARE_ROOT))
    share_physical, cat_sizes = _scan_share_sizes()
    categories = {name: {"size": size, "size_human": human_size(size)}
                  for name, size in cat_sizes.items()}
    return {
        "total": usage.total, "total_human": human_size(usage.total),
        "used": usage.used, "used_human": human_size(usage.used),
        "free": usage.free, "free_human": human_size(usage.free),
        "warning": usage.free < 15 * 1024**3,
        "share_size": share_physical, "share_size_human": human_size(share_physical),
        "categories": categories,
    }


# ─── 分享链接 ─────────────────────────────────────────────
# 创建/管理需登录；取件公开（分享的意义就是给没登录的人），密码走 POST body。

@app.post("/api/share")
async def create_share(request: Request, path: str = Form(...), expires_hours: int = Form(default=72),
                       password: Optional[str] = Form(default=None),
                       max_downloads: Optional[int] = Form(default=None),
                       _user=Depends(require_user)):
    if expires_hours < 1:
        raise HTTPException(status_code=400, detail="expires_hours must be >= 1")
    if max_downloads is not None and max_downloads < 1:
        raise HTTPException(status_code=400, detail="max_downloads must be >= 1")
    target = safe_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="暂不支持分享文件夹，请打包后分享")

    token = secrets.token_urlsafe(16)
    pwd_hash = None
    if password:
        salt = secrets.token_bytes(16)
        pwd_hash = salt.hex() + "$" + hashlib.sha256(salt + password.encode()).hexdigest()
    with _db_lock:
        _db().execute(
            "INSERT INTO shares(token, path, name, size, created_at, expires_at, password_hash, max_downloads) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (token, str(target.relative_to(SHARE_ROOT)).replace("\\", "/"), target.name,
             target.stat().st_size if target.is_file() else None,
             time.time(), time.time() + expires_hours * 3600, pwd_hash, max_downloads))
        _db().commit()
    audit("share_create", path, f"{expires_hours}h", client_ip(request))
    return {"ok": True, "token": token, "expires_in": f"{expires_hours}h"}


def _get_active_share(token: str) -> sqlite3.Row:
    s = _db().execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()
    if not s:
        raise HTTPException(status_code=404, detail="Share not found")
    if time.time() > s["expires_at"]:
        raise HTTPException(status_code=410, detail="Share expired")
    return s


_share_fails: dict[str, list[float]] = {}
_share_fails_lock = threading.Lock()


def _share_locked_remaining(token: str) -> int:
    now = time.time()
    with _share_fails_lock:
        q = _share_fails.get(token, [])
        while q and q[0] <= now - LOGIN_LOCK_SECONDS:
            q.pop(0)
        if len(q) >= LOGIN_MAX_FAILS:
            return int(q[0] + LOGIN_LOCK_SECONDS - now) + 1
        return 0


def _record_share_fail(token: str):
    with _share_fails_lock:
        _share_fails.setdefault(token, []).append(time.time())


def _check_share_password(request: Request, s: sqlite3.Row, password: Optional[str]):
    if not s["password_hash"]:
        return
    token = s["token"]
    remaining = _share_locked_remaining(token)
    if remaining:
        audit("share_locked", s["name"], f"{remaining}s", client_ip(request))
        raise HTTPException(status_code=429,
                            detail=f"提取码错误次数过多，请 {remaining // 60 + 1} 分钟后重试")
    if not password:
        raise HTTPException(status_code=401, detail="Password required")
    salt_hex, h_hex = s["password_hash"].split("$", 1)
    calc = hashlib.sha256(bytes.fromhex(salt_hex) + password.encode()).hexdigest()
    if not hmac.compare_digest(calc, h_hex):
        _record_share_fail(token)
        audit("share_badpwd", s["name"], ip=client_ip(request))
        raise HTTPException(status_code=403, detail="Wrong password")
    with _share_fails_lock:
        _share_fails.pop(token, None)


@app.get("/api/share/meta/{token}")
async def share_meta(token: str):
    s = _get_active_share(token)
    return {
        "name": s["name"],
        "size": s["size"],
        "size_human": human_size(s["size"]) if s["size"] is not None else "-",
        "expires_in": f"{max(0, int((s['expires_at'] - time.time()) / 3600))}h",
        "needs_password": s["password_hash"] is not None,
        "max_downloads": s["max_downloads"],
        "downloads": s["downloads"],
    }


# 取件票据：POST 验证密码后换一次性短时效 URL，密码不出现在任何 URL/日志
_tickets: dict[str, dict] = {}
_tickets_lock = threading.Lock()


@app.post("/api/share/{token}/prepare")
async def share_prepare(request: Request, token: str, body: SharePrepareBody):
    s = _get_active_share(token)
    _check_share_password(request, s, body.password)
    if s["max_downloads"] and s["downloads"] >= s["max_downloads"]:
        raise HTTPException(status_code=410, detail="下载次数已达上限")
    tkt = secrets.token_urlsafe(24)
    with _tickets_lock:
        # 顺带清理过期票据
        now = time.time()
        for k in [k for k, v in _tickets.items() if v["exp"] < now]:
            del _tickets[k]
        _tickets[tkt] = {"token": token, "exp": now + TICKET_TTL}
    return {"ok": True, "url": f"/api/share/{token}/dl?tkt={tkt}",
            "name": s["name"], "expires": TICKET_TTL}


def _share_download_done(token: str, path: str, ip: str):
    """下载响应发送完成后计数与审计（客户端中途取消不计）。"""
    with _db_lock:
        _db().execute("UPDATE shares SET downloads = downloads + 1 WHERE token=?", (token,))
        _db().commit()
    audit("share_download", path, ip=ip)


@app.get("/api/share/{token}/dl")
async def share_download(request: Request, token: str, tkt: str):
    # 票据在 TTL 内可复用（大文件断线后浏览器重试/Range 续传不至于 403），过期由清理任务回收
    with _tickets_lock:
        info = _tickets.get(tkt)
    if not info or info["token"] != token or info["exp"] < time.time():
        raise HTTPException(status_code=403, detail="下载链接已失效，请刷新页面重试")
    s = _get_active_share(token)
    if s["max_downloads"] and s["downloads"] >= s["max_downloads"]:
        raise HTTPException(status_code=410, detail="下载次数已达上限")
    file_path = SHARE_ROOT / s["path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File deleted")
    return FileResponse(file_path, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{s["name"]}"'},
                        background=BackgroundTask(
                            _share_download_done, token, s["path"], client_ip(request)))


@app.get("/api/share/{token}/qr")
async def share_qr(token: str, request: Request, _user=Depends(require_user)):
    if not _db().execute("SELECT 1 FROM shares WHERE token=?", (token,)).fetchone():
        raise HTTPException(status_code=404, detail="Share not found")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    url = f"{proto}://{host}/s/{token}"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@app.get("/api/shares")
async def list_shares(_user=Depends(require_user)):
    now = time.time()
    with _db_lock:
        _db().execute("DELETE FROM shares WHERE expires_at < ?", (now,))
        _db().commit()
    active = {}
    for s in _db().execute("SELECT * FROM shares ORDER BY created_at DESC").fetchall():
        active[s["token"]] = {
            "path": s["path"],
            "name": s["name"],
            "size": s["size"],
            "size_human": human_size(s["size"]) if s["size"] else "-",
            "expires_at": datetime.fromtimestamp(s["expires_at"]).isoformat(),
            "expires_in": f"{max(0, int((s['expires_at'] - now) / 3600))}h",
            "has_password": s["password_hash"] is not None,
            "downloads": s["downloads"],
            "max_downloads": s["max_downloads"],
            "created_at": datetime.fromtimestamp(s["created_at"]).isoformat(),
        }
    return active


@app.get("/api/shares/{token}/history")
async def share_history(token: str, _user=Depends(require_user)):
    """某分享的取件记录（下载成功 + 密码错误尝试）。"""
    s = _db().execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()
    if not s:
        raise HTTPException(status_code=404, detail="Share not found")
    rows = _db().execute(
        "SELECT ts, ip, action FROM audit_log "
        "WHERE (action='share_download' AND target=?) OR (action='share_badpwd' AND target=?) "
        "ORDER BY id DESC LIMIT 50", (s["path"], s["name"])).fetchall()
    return [{"ts": datetime.fromtimestamp(r["ts"]).isoformat(),
             "ip": r["ip"], "action": r["action"]} for r in rows]


@app.delete("/api/shares/{token}")
async def revoke_share(request: Request, token: str, _user=Depends(require_user)):
    with _db_lock:
        cur = _db().execute("DELETE FROM shares WHERE token=?", (token,))
        _db().commit()
    if cur.rowcount:
        audit("share_revoke", token, ip=client_ip(request))
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Share not found")


# ─── 定时清理 ─────────────────────────────────────────────

@app.on_event("startup")
async def start_cleanup():
    async def cleanup_loop():
        while True:
            _cleanup_tmp()
            _cleanup_trash()
            _cleanup_uploads()
            _cleanup_zips()
            _cleanup_db()
            await asyncio.sleep(CLEANUP_INTERVAL)

    _cleanup_tmp()
    _cleanup_trash()
    _cleanup_uploads()
    _cleanup_zips()
    _cleanup_db()
    asyncio.create_task(cleanup_loop())


def _cleanup_tmp():
    tmp = SHARE_ROOT / "tmp"
    if not tmp.exists():
        return
    cutoff = time.time() - TMP_MAX_AGE_DAYS * 86400
    for f in tmp.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            if f.parent != tmp and not any(f.parent.iterdir()):
                f.parent.rmdir()
    # 孤儿上传临时文件（崩溃残留）：超过 1 天的 .uploading/.dav-uploading 一律回收
    orphan_cutoff = time.time() - 86400
    for f in SHARE_ROOT.rglob("*"):
        if f.is_file() and f.name.endswith((".uploading", ".dav-uploading", ".dedup")) \
                and f.stat().st_mtime < orphan_cutoff:
            f.unlink(missing_ok=True)


def _cleanup_trash():
    trash_root = SHARE_ROOT / ".trash"
    if not trash_root.exists():
        return
    cutoff = time.time() - TRASH_DAYS * 86400
    for f in trash_root.iterdir():
        if f.stat().st_mtime < cutoff:
            if f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink(missing_ok=True)
    with _db_lock:
        _db().execute("DELETE FROM trash WHERE deleted_at < ?", (cutoff,))
        _db().commit()


def _cleanup_uploads():
    with _db_lock:
        stale = _db().execute("SELECT id FROM upload_sessions WHERE created_at < ?",
                              (time.time() - UPLOAD_SESSION_TTL,)).fetchall()
        if stale:
            _db().execute("DELETE FROM upload_sessions WHERE created_at < ?",
                          (time.time() - UPLOAD_SESSION_TTL,))
            _db().commit()
    for r in stale:
        shutil.rmtree(_chunk_dir(r["id"]), ignore_errors=True)
    # 孤儿分块目录（无会话记录）
    chunks_root = SHARE_ROOT / ".chunks"
    if chunks_root.exists():
        known = {r["id"] for r in _db().execute("SELECT id FROM upload_sessions").fetchall()}
        for d in chunks_root.iterdir():
            if d.is_dir() and d.name not in known:
                shutil.rmtree(d, ignore_errors=True)


def _cleanup_zips():
    zdir = SHARE_ROOT / ".zips"
    if zdir.exists():
        cutoff = time.time() - ZIP_TTL * 2
        for f in zdir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    with _zips_lock:
        now = time.time()
        for k in [k for k, v in _zips.items() if v["exp"] < now]:
            del _zips[k]


def _cleanup_db():
    now = time.time()
    with _db_lock:
        _db().execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        _db().execute("DELETE FROM shares WHERE expires_at < ?", (now,))
        _db().commit()
    with _tickets_lock:
        for k in [k for k, v in _tickets.items() if v["exp"] < now]:
            del _tickets[k]


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


def _dav_destination(request: Request) -> Path:
    raw = request.headers.get("destination", "")
    p = unquote(urlsplit(raw).path)
    if p == "/dav":
        return SHARE_ROOT
    if not p.startswith("/dav/"):
        raise HTTPException(status_code=400, detail="Invalid Destination")
    return safe_path(p[len("/dav/"):])


@app.api_route("/dav", methods=DAV_METHODS)
@app.api_route("/dav/{path:path}", methods=DAV_METHODS)
async def dav_handler(request: Request, path: str = ""):
    await _dav_auth(request)
    method = request.method
    target = safe_path(path) if path else SHARE_ROOT
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
        dest = _dav_destination(request)
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


# ─── 照片时间线 ───────────────────────────────────────────

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_last_photo_scan = 0.0
_photo_scan_lock = threading.Lock()


def _photo_taken_at(f: Path) -> str:
    """优先 EXIF DateTimeOriginal，其次 EXIF DateTime，最后文件修改时间。返回 'YYYY-MM-DD'。"""
    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
    if f.suffix.lower() not in (".jpg", ".jpeg", ".webp", ".bmp"):
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


def _scan_photos():
    """增量扫描图片库：新/变更文件提取拍摄日期，消失的文件清理条目。60s 内不重复扫。"""
    global _last_photo_scan
    now = time.time()
    if now - _last_photo_scan < 60:
        return
    with _photo_scan_lock:
        if time.time() - _last_photo_scan < 60:
            return
        seen = set()
        updates = []
        for f in SHARE_ROOT.rglob("*"):
            rel_parts = f.relative_to(SHARE_ROOT).parts
            if any(p.startswith(".") for p in rel_parts):
                continue
            if not f.is_file() or f.suffix.lower() not in PHOTO_EXTS:
                continue
            stat = f.stat()
            rel = str(f.relative_to(SHARE_ROOT)).replace("\\", "/")
            seen.add(rel)
            row = _db().execute("SELECT mtime_ns, size FROM photos WHERE path=?", (rel,)).fetchone()
            if row and row["mtime_ns"] == stat.st_mtime_ns and row["size"] == stat.st_size:
                continue
            updates.append((rel, _photo_taken_at(f), stat.st_mtime_ns, stat.st_size))
        with _db_lock:
            if updates:
                _db().executemany(
                    "INSERT INTO photos(path, taken_at, mtime_ns, size) VALUES(?,?,?,?) "
                    "ON CONFLICT(path) DO UPDATE SET taken_at=excluded.taken_at, "
                    "mtime_ns=excluded.mtime_ns, size=excluded.size", updates)
                _db().execute("DELETE FROM photos WHERE mtime_ns=0")
            gone = [r["path"] for r in _db().execute("SELECT path FROM photos").fetchall()
                    if r["path"] not in seen]
            if gone:
                _db().executemany("DELETE FROM photos WHERE path=?", [(g,) for g in gone])
            _db().commit()
        _last_photo_scan = time.time()


@app.get("/api/photos")
def list_photos(limit: int = Query(default=120, le=500), offset: int = Query(default=0, ge=0),
                _user=Depends(require_user)):
    """照片时间线：按拍摄日期倒序（EXIF 优先）。"""
    _scan_photos()
    rows = _db().execute(
        "SELECT path, taken_at FROM photos ORDER BY taken_at DESC, path LIMIT ? OFFSET ?",
        (limit, offset)).fetchall()
    total = _db().execute("SELECT COUNT(*) AS c FROM photos").fetchone()["c"]
    return {
        "total": total,
        "items": [{
            "path": r["path"],
            "name": r["path"].split("/")[-1],
            "taken": r["taken_at"],
            "thumb": f"/thumb/{r['path']}",
        } for r in rows],
    }

# ─── 页面路由 ─────────────────────────────────────────────

@app.get("/api/qr")
async def generic_qr(text: str = Query(..., min_length=1, max_length=512),
                     _user=Depends(require_user)):
    """任意文本的 QR 码 SVG（登录后可用，如手机上传页二维码）。"""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


# CSP：脚本全部外部化（script-src 'self'）；样式保留内联（style-src 'unsafe-inline'，
# 仅 CSS 注入风险，收益/成本比合理）。前端已无任何内联事件处理器。
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; media-src 'self'; frame-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def html_page(name: str) -> HTMLResponse:
    return HTMLResponse((PROJECT_ROOT / "static" / name).read_text(encoding="utf-8"),
                        headers={"Content-Security-Policy": CSP})


@app.get("/up", response_class=HTMLResponse)
async def up_page(request: Request):
    """手机扫码直传页（需登录）。"""
    if not get_session_user(request):
        return RedirectResponse("/login?next=/up", status_code=307)
    return html_page("up.html")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not get_session_user(request):
        return RedirectResponse("/login", status_code=307)
    return html_page("index.html")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return html_page("login.html")


@app.get("/s/{token}", response_class=HTMLResponse)
async def share_page(token: str):
    return html_page("share.html")


app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")


# ─── 入口 ─────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8766, reload=False, log_level="info")
