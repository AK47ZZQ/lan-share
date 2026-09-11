"""FastAPI 应用对象与全局安全中间件（07 审查 §5.2 批次 2：消循环前置）。

本模块是依赖图的最底层之一：只允许 import config/db/security，
绝不 import 任何 api 模块或 app.main——历史包袱是各 api 模块
`from app.main import app` 与 main.py 底部反向 import 形成 11 对循环，
app 对象无法工厂化；现在 app 住在这里，路由模块改用 APIRouter，
main.py 作为组装根统一 include_router，循环消除。
"""
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from urllib.parse import urlsplit
import time

from app.config import (API_RATE_LIMIT, API_RATE_LIMIT_PUB, AUTHCHECK_RATE_LIMIT,
                        LOGIN_RATE_LIMIT, LOGIN_RATE_LIMIT_PUB, SHARE_RATE_LIMIT)
from app.security import (allow_rate, client_ip, get_bearer_user, get_session_user,
                          is_public_entry, public_netlocs, set_audit_user)
from app.services import metrics as _metrics

app = FastAPI(title="LAN Share", version="3.8.0")


def _healthz_blocked(request: Request) -> bool:
    """healthz 兜底管控：公网地址漏配（public_netlocs 为空、is_public_entry 恒
    False）时，按 Host 判定——只有私网 IP/loopback/localhost 放行，其余当公网拦下。"""
    if public_netlocs():
        return False   # 已配置：交给 is_public_entry 判定
    # urlsplit().hostname 同时解决 IPv6 字面量与纯主机名（nas:8765）两种形态
    host = (urlsplit(f"//{request.headers.get('host') or ''}").hostname or "").lower()
    if not host:
        return True
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback)
    except ValueError:
        return host not in ("localhost",)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 审计上下文：本请求的登录用户名（audit() 自动带上；未登录为空）。
    # Cookie 会话与 App Bearer 设备 Token 并存（APP-DESIGN §3）。
    # 会话/Token 校验含 DB 读写（过期删除、滑动续期），同步 PG 往返必须
    # 进线程池，否则每个请求都在事件循环里串行排队。
    try:
        u = await run_in_threadpool(
            lambda: get_session_user(request) or get_bearer_user(request))
        set_audit_user(u["username"] if u else "")
        # 认证结果挂 request.state：require_user 复用，省掉每请求第二次
        # 会话查询（R1-P3-3）
        request.state._ls_user = u or False
    except Exception:
        set_audit_user("")
    # CSRF：带 Origin 的请求必须与 Host 同源（SameSite=Lax 之外的第二道防线）
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        return JSONResponse({"detail": "CSRF check failed"}, status_code=403)
    path = request.url.path
    ip = client_ip(request)
    # 公网入口管控：healthz 不对公网暴露（磁盘水位信息泄露 + 探测面）。
    # 兜底：公网地址未配置时按 Host 判定——非私网 IP/localhost 的一律当公网
    #（漏配 public_base_url 时 healthz 也不会裸奔到公网域名上）
    if path == "/healthz" and (is_public_entry(request) or _healthz_blocked(request)):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # 请求体大小上限（R2-P1）：Caddy 放行 25GB 且 starlette 会把 body 全量
    # 缓冲进内存后才交给端点校验——未认证端点（login/pair/prepare）灌巨包
    # 即内存 DoS。大体积白名单（R4-P0 收紧）：DAV 全部、分片收块
    # /api/upload/chunk/、直传 multipart（/api/upload 与 /api/upload/<文件名>）。
    # /api/upload/init、/state、/finish 是小 JSON 端点，必须留在检查内
    #（此前按 /api/upload 前缀整条豁免，init 被未认证灌 20GB body 在鉴权前
    # 全量缓冲——FastAPI 先读 body 后做依赖鉴权，单请求即 OOM）
    if request.method in ("POST", "PUT", "PATCH") and not path.startswith("/dav"):
        big_body = path == "/api/upload" or path.startswith("/api/upload/chunk/")
        # 头像端点专用限额（review P1-1）：不能整条豁免——FastAPI 先收 body 后
        # 鉴权，未认证整条豁免 = 灌包洞在新端点复活。multipart 正常客户端都带
        # CL：≤10MB 放行（端点内还有 8MB 第二道闸），chunked 一律拒
        if path == "/api/auth/avatar":
            if request.headers.get("transfer-encoding"):
                return JSONResponse({"detail": "Transfer-Encoding not allowed"},
                                    status_code=413)
            cl = request.headers.get("content-length")
            if not cl or not cl.isdigit() or int(cl) > 10 * 1024 * 1024:
                return JSONResponse({"detail": "Payload too large"},
                                    status_code=413)
            big_body = True
        if not big_body and path.startswith("/api/upload/"):
            first = path[len("/api/upload/"):].split("/", 1)[0]
            big_body = first not in ("init", "state", "finish")
        if not big_body:
            # chunked 请求无 Content-Length（R3-P1）：正常 JSON API 客户端从不
            # 使用，直接拒绝，堵住"缺 CL 头绕过 1MB 检查"的口子
            if request.headers.get("transfer-encoding"):
                return JSONResponse({"detail": "Transfer-Encoding not allowed"},
                                    status_code=413)
            cl = request.headers.get("content-length")
            if not cl or not cl.isdigit():
                # 缺 Content-Length 一律 411（R3-P1）：无法预判体积就必须收完
                # body 才能校验，等于没有上限
                return JSONResponse({"detail": "Content-Length required"},
                                    status_code=411)
            if int(cl) > 1024 * 1024:
                return JSONResponse({"detail": "Payload too large"}, status_code=413)
    if path == "/api/login":
        # 登录限速按入口分桶：公网攻击者的流量挤占不了家人的配额（公网 IP 恒为 127.0.0.1）。
        # 公网侧全体共享一个 IP 桶，阈值放大防单人刷满卡死全家（R1-P2-3）
        limit = LOGIN_RATE_LIMIT_PUB if is_public_entry(request) else LOGIN_RATE_LIMIT
        bucket = "login@pub" if is_public_entry(request) else "login"
    elif path.startswith("/api/upload/chunk/"):
        limit, bucket = 0, None  # 分块上传按块数计频，不受常规限速（仍有会话认证保护）
    elif path.startswith("/api/share/") or path == "/api/share":
        limit, bucket = SHARE_RATE_LIMIT, "share"
    elif path.startswith("/api/") or path == "/authcheck":
        # authcheck 是 Caddy forward_auth 子请求，跟随 /files 静态请求高频出现，
        # 单独桶；但外部客户端也能直打该未认证 DB 往返端点，必须节流（R1-P2-2）
        if path == "/authcheck":
            limit, bucket = AUTHCHECK_RATE_LIMIT, "authcheck"
        else:
            # 弱网加固（v0.10.1）：Caddy 反代使内外网 client_ip 都是 127.0.0.1，
            # 全家共享一个桶会让公网设备互相挤掉——按入口分账，
            # 家里三个人同时翻照片墙不再互相 429
            pub = is_public_entry(request)
            limit = API_RATE_LIMIT_PUB if pub else API_RATE_LIMIT
            bucket = "api:pub" if pub else "api:lan"
    else:
        limit, bucket = 0, None
    if limit and bucket and not allow_rate(f"{ip}:{bucket}", limit):
        return JSONResponse({"detail": "Too many requests"}, status_code=429,
                            headers={"Retry-After": "30"})
    _t0 = time.monotonic()   # B5：只记真正到达路由的请求（早退的安全拒绝不计）
    resp = await call_next(request)
    _metrics.record(resp.status_code, (time.monotonic() - _t0) * 1000)
    return resp
