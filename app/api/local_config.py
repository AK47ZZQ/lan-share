"""本机专用的配置端点：给桌面托盘 / 控制面板用。

存在的理由和 /local/qr 一样：桌面端没有登录态，调不动 /admin/* 那套接口，
但它确实需要读写几个「服务层」的配置（不是用户数据，是这台机器怎么对外）。

所有端点都只允许本机访问，判定逻辑复用 qr.py 的 _effective_client_ip ——
那套逻辑处理了「经 Caddy 反代后 client.host 全是 127.0.0.1」这个陷阱。

内容范围刻意收得很窄：只有公网入口地址。别的东西（账号、权限、文件）
一律不放这里，那些必须走正常的认证通道。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import settings
from app.api.qr import _effective_client_ip

router = APIRouter()

_LOOPBACKS = ("127.0.0.1", "::1", "localhost")


def _require_local(request: Request) -> None:
    """非本机一律 403。"""
    ip = _effective_client_ip(request)
    if ip not in _LOOPBACKS:
        raise HTTPException(status_code=403, detail="该接口仅限本机访问")


class PublicUrlBody(BaseModel):
    url: str = ""
    url_alt: str = ""


def _normalize(raw: str) -> tuple[str, str]:
    """把用户输入整理成规范形式，返回 (存库值, 错误说明)。

    用户可能输入：
        xxx.frp-any.com:35391       → 补 https://
        https://xxx.frp-any.com:35391/
        http://192.168.1.12:8765    → 允许，但要提醒这不是公网地址

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
        alt = settings.get_setting(settings.PUBLIC_BASE_URL_ALT)
    except Exception as exc:  # noqa: BLE001 - 读不到要说出来，不能装作空配置
        return {
            "url": "",
            "url_alt": "",
            "available": False,
            "error": f"读取配置失败：{exc}",
        }
    return {"url": primary or "", "url_alt": alt or "", "available": True, "error": ""}


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
    alt, err2 = _normalize(body.url_alt)
    if err2:
        raise HTTPException(status_code=422, detail=err2)

    try:
        settings.set_setting(settings.PUBLIC_BASE_URL, primary)
        settings.set_setting(settings.PUBLIC_BASE_URL_ALT, alt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"保存失败：{exc}") from exc

    # 改完立即生效（不然防爆破的公网/LAN 分账要等 60 秒才切过来）
    try:
        from app.security import reset_netloc_cache

        reset_netloc_cache()
    except Exception:  # noqa: BLE001 - 缓存清不掉不影响保存结果
        pass

    return {"ok": True, "url": primary, "url_alt": alt}
