"""LAN Share v3 — 应用组装根（07 审查批次 2：消循环后 app 移居 app.asgi）。

app 对象与安全中间件住在 app/asgi.py（依赖图底层，不 import 任何 api 模块）；
各路由模块持自己的 APIRouter；本文件按固定顺序 include_router 并挂载
SPA 页面路由与静态目录——组装顺序敏感：spa_fallback 兜底路由必须最后注册。
"""
from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import quote

from app.config import PROJECT_ROOT
from app.db import init_db
from app.security import get_session_user

from app.asgi import app

# ─── 路由模块（router 统一组装；顺序与 v3 历史注册顺序一致）──
from app.api import admin, appver, auth, files, share, webdav, system, notify
from app.api import healthreport
from app.api import notes
from app.api import uploads, trash, previews          # files 按域拆出（07 批次 3A）
from app.api import photos, albums, qr                # system 按域拆出（07 批次 3B）
from app.api import console                           # 管理台（v3.39 Web 控制台）
from app.api import feedback                          # 反馈通道（v0.28）
from app.api import events                             # SSE 实时通知（B3）
from app.services import backup as backup_svc          # 每日备份编排（B5）
from app.services import metrics as metrics_svc        # 指标与水位告警（B5）
from app.services import log_sweep          # noqa: F401 —— NSSM 日志归档裁剪（import 即注册 sweep）
from app.api import totp, profile, account, devices   # auth 按域拆出（07 小尾巴）

for _mod in (admin, appver, auth, totp, profile, account, devices, files,
             uploads, trash, previews, share,
             webdav, system, notify, healthreport, photos, albums, qr, notes,
             console, feedback, events):
    app.include_router(_mod.router)
backup_svc.ensure_scheduled()   # B5：进程内每日备份兜底（外部计划任务失效时的第二道防线）
metrics_svc.ensure_scheduled()  # B5：磁盘水位 watchdog 接线（值守发现遗漏，终审未覆盖注册面）

# CSP：脚本全部外部化（script-src 'self'）；样式保留内联（style-src 'unsafe-inline'，
# 仅 CSS 注入风险，收益/成本比合理）。前端已无任何内联事件处理器。
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; media-src 'self'; frame-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


# ─── v3 前端切换：Vue SPA 即整站 ─────────────────────────
# 构建产物 web/dist 存在时，所有页面路由（含 history 深链回退）都服务 SPA。
# dist 每次请求现查（而非启动时判定一次）：服务运行期间 dist 被删/损坏时
# 返回 503 维护页，而不是对页面路由 500。
_spa_dist = PROJECT_ROOT / "web" / "dist"


def _spa_ok() -> bool:
    return (_spa_dist / "index.html").is_file()


_MAINTENANCE_PAGE = (
    "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
    "<title>LAN Share · 维护中</title>"
    "<body style='font-family:system-ui,sans-serif;padding:3em;color:#333'>"
    "<h2>前端资源缺失，维护中</h2>"
    "<p>页面构建产物 web/dist 缺失。API 仍在正常服务。</p>"
    "<p>管理员请在 web/ 目录执行 <code>npm install &amp;&amp; npm run build</code> 后刷新。</p>"
    "</body></html>"
)


# index.html 按 (mtime, size) 缓存（R1-P3-4）：up/login/s/{token} 每次请求
# 同步重读文件，内容没变就复用
_spa_index_cache: dict = {"key": None, "html": ""}


def spa_index() -> HTMLResponse:
    index = _spa_dist / "index.html"
    if not index.is_file():
        return HTMLResponse(_MAINTENANCE_PAGE, status_code=503,
                            headers={"Retry-After": "60", "Content-Security-Policy": CSP})
    try:
        st = index.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is None or _spa_index_cache["key"] != key:
        html = index.read_text(encoding="utf-8")
        if key is not None:
            _spa_index_cache.update(key=key, html=html)
        return HTMLResponse(html, headers={"Content-Security-Policy": CSP})
    return HTMLResponse(_spa_index_cache["html"], headers={"Content-Security-Policy": CSP})


@app.get("/up", response_class=HTMLResponse)
async def up_page():
    """手机直传页：SPA 自带未登录引导（页内"去登录"带 next 回跳）。"""
    return spa_index()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not await run_in_threadpool(get_session_user, request):
        return RedirectResponse("/login", status_code=307)
    return spa_index()


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return spa_index()


@app.get("/s/{token}", response_class=HTMLResponse)
async def share_page(token: str):
    return spa_index()


app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")
app.mount("/assets", StaticFiles(directory=str(_spa_dist / "assets"), check_dir=False),
          name="spa-assets")


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
    if _spa_ok() and (spa_path in _PWA_PUBLIC_FILES or spa_path.startswith(_PWA_PUBLIC_PREFIX)):
        f = _dist_file(spa_path)
        if f:
            return f
    if not await run_in_threadpool(get_session_user, request):
        return RedirectResponse(f"/login?next={quote(request.url.path, safe='/')}",
                                status_code=307)
    if _spa_ok():
        f = _dist_file(spa_path)
        if f:
            return f
    return spa_index()


# ─── 入口 ─────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8766, reload=False, log_level="info")
