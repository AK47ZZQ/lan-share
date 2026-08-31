"""LAN Share v3 — FastAPI 应用组装入口（M0 拆包）。

组装顺序敏感：app 与中间件先定义，路由模块在文件底部导入
（各模块 `from app.main import app` 依赖此处已存在的 app 对象），
页面路由与 static 挂载殿后——与 v2 单文件的注册顺序保持一致。
"""
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import urlsplit

from app.config import (API_RATE_LIMIT, LOGIN_RATE_LIMIT, PROJECT_ROOT, SHARE_RATE_LIMIT,
                        SHARE_ROOT)
from app.db import init_db
from app.security import (allow_rate, client_ip, get_bearer_user, get_session_user,
                          is_public_entry, set_audit_user)

app = FastAPI(title="LAN Share", version="2.0.0")

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 审计上下文：本请求的登录用户名（audit() 自动带上；未登录为空）。
    # Cookie 会话与 App Bearer 设备 Token 并存（APP-DESIGN §3）
    try:
        u = get_session_user(request) or get_bearer_user(request)
        set_audit_user(u["username"] if u else "")
    except Exception:
        set_audit_user("")
    # CSRF：带 Origin 的请求必须与 Host 同源（SameSite=Lax 之外的第二道防线）
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        return JSONResponse({"detail": "CSRF check failed"}, status_code=403)
    path = request.url.path
    ip = client_ip(request)
    # 公网入口管控：healthz 不对公网暴露（磁盘水位信息泄露 + 探测面）
    if path == "/healthz" and is_public_entry(request):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if path == "/api/login":
        # 登录限速按入口分桶：公网攻击者的流量挤占不了家人的配额（公网 IP 恒为 127.0.0.1）
        limit, bucket = LOGIN_RATE_LIMIT, ("login@pub" if is_public_entry(request) else "login")
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

# ─── 路由模块（app 与中间件就绪后导入）──────────────────
from app.api import admin, auth, files, share, webdav, system, notify  # noqa: E402,F401

# CSP：脚本全部外部化（script-src 'self'）；样式保留内联（style-src 'unsafe-inline'，
# 仅 CSS 注入风险，收益/成本比合理）。前端已无任何内联事件处理器。
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; media-src 'self'; frame-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def html_page(name: str) -> HTMLResponse:
    return HTMLResponse((PROJECT_ROOT / "static" / name).read_text(encoding="utf-8"),
                        headers={"Content-Security-Policy": CSP})


# ─── v3 前端切换：Vue SPA 即整站 ─────────────────────────
# 构建产物 web/dist 存在时，所有页面路由（含 history 深链回退）都服务 SPA；
# 未构建时回退旧页，保证服务永不因前端缺失而不可用。
_spa_dist = PROJECT_ROOT / "web" / "dist"
_SPA_READY = (_spa_dist / "index.html").exists()


def spa_index() -> HTMLResponse:
    return HTMLResponse((_spa_dist / "index.html").read_text(encoding="utf-8"),
                        headers={"Content-Security-Policy": CSP})


@app.get("/up", response_class=HTMLResponse)
async def up_page():
    """手机直传页：SPA 自带未登录引导（页内"去登录"带 next 回跳）。"""
    return spa_index() if _SPA_READY else html_page("up.html")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not get_session_user(request):
        return RedirectResponse("/login", status_code=307)
    return spa_index() if _SPA_READY else html_page("index.html")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return spa_index() if _SPA_READY else html_page("login.html")


@app.get("/s/{token}", response_class=HTMLResponse)
async def share_page(token: str):
    return spa_index() if _SPA_READY else html_page("share.html")


app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")
if _SPA_READY:
    app.mount("/assets", StaticFiles(directory=str(_spa_dist / "assets")), name="spa-assets")


# history 深链回退：/photos /trash /admin 等 UI 路径 → SPA。
# 注册在 /api、/dav、/files 等真实路由之后，仅兜底 GET HTML 请求。
_PWA_PUBLIC_FILES = {"manifest.webmanifest", "sw.js"}          # 免登录（OS/浏览器拉取不带会话）
_PWA_PUBLIC_PREFIX = ("icons/",)


def _dist_file(spa_path: str):
    """dist 内真实文件直供（PWA 资产等）；防穿越，越界返回 None。"""
    candidate = (_spa_dist / spa_path).resolve()
    try:
        candidate.relative_to(_spa_dist.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        mt = "application/manifest+json" if candidate.suffix == ".webmanifest" else (
            "application/javascript" if candidate.suffix == ".js" else "image/png" if candidate.suffix == ".png" else None)
        return FileResponse(candidate, media_type=mt or "application/octet-stream")
    return None


@app.get("/{spa_path:path}", response_class=HTMLResponse)
async def spa_fallback(spa_path: str, request: Request):
    if spa_path.startswith(("api/", "dav", "files/", "preview/", "thumb/", "static/",
                            "assets/", "s/", "authcheck", "healthz", "up", "login")) or spa_path == "":
        raise HTTPException(status_code=404)
    if _SPA_READY and (spa_path in _PWA_PUBLIC_FILES or spa_path.startswith(_PWA_PUBLIC_PREFIX)):
        f = _dist_file(spa_path)
        if f:
            return f
    if not get_session_user(request):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=307)
    if _SPA_READY:
        f = _dist_file(spa_path)
        if f:
            return f
    return spa_index()


# ─── 入口 ─────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8766, reload=False, log_level="info")
