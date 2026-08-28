"""LAN Share v3 — FastAPI 应用组装入口（M0 拆包）。

组装顺序敏感：app 与中间件先定义，路由模块在文件底部导入
（各模块 `from app.main import app` 依赖此处已存在的 app 对象），
页面路由与 static 挂载殿后——与 v2 单文件的注册顺序保持一致。
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import urlsplit

from app.config import (API_RATE_LIMIT, LOGIN_RATE_LIMIT, PROJECT_ROOT, SHARE_RATE_LIMIT,
                        SHARE_ROOT)
from app.db import init_db
from app.security import allow_rate, client_ip, get_session_user

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

# ─── 路由模块（app 与中间件就绪后导入）──────────────────
from app.api import admin, auth, files, share, webdav, system  # noqa: E402,F401

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
