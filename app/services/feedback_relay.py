"""反馈上报：把用户机器上的反馈送到开发端。

**为什么需要**：`app/api/feedback.py` 把反馈落在 `data/反馈/`，那是**用户自己
服务器的目录**。别人装了 LAN Share，反馈就躺在他自己机器里，开发端永远看不见
——「反馈通道」对开发者等于不存在。2026-10-31 用户提出要统一收回开发端。

**两条通道，各自独立、互不影响**：

  A. HTTP 上报 → 开发端 `POST /api/feedback/inbox`
     适合自建分发：改一下配置就能收所有人的反馈，不依赖第三方。

  B. GitHub Issue → `POST https://api.github.com/repos/{repo}/issues`
     适合开源分发：反馈直接变 issue，自带搜索/标签/关闭状态。
     需要 fine-grained PAT（只给 issues:write），只存在**本机** settings 表里。

**设计原则**：
  1. 本地落盘优先。上报是锦上添花，用户提交成功的唯一判据是本地写盘成功。
  2. 上报失败绝不影响用户。异步执行、异常吞掉只记日志 —— 开发端挂了不该让
     用户看到「提交失败」。
  3. 不传敏感内容。只发标题/类型/正文/版本/设备/用户名。**不发**文件路径、
     文件名、附件内容、IP。设备字段在客户端已截断 80 字符。
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import httpx

from app import settings

log = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _on(key: str) -> bool:
    return (settings.get_setting(key, "") or "").strip() == "1"


def _scrub(text: str, limit: int) -> str:
    """把正文里能定位到人的东西换成占位符（2026-11-01）。

    反馈正文是用户手打的，什么都可能有：服务器地址、内网 IP、同事真名、
    文件路径。这些东西一旦进了开发者的收件箱就等于出了用户的机器 ——
    而用户提交反馈时**想不到**要去删它们。

    所以发送前统一洗一遍，宁可丢掉一点上下文，也不要悄悄带走隐私。
    """
    if not text:
        return ""
    out = text
    # URL（含协议）—— 用户的服务器地址基本都长这样
    out = re.sub(r"https?://[^\s\"'<>）)】]+", "<地址>", out)
    # 裸 IPv4
    out = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "<IP>", out)
    # 本机/用户目录绝对路径（C:\Users\xxx\... / /home/xxx/...）
    out = re.sub(r"[A-Za-z]:\\[^\s\"'<>]+", "<路径>", out)
    out = re.sub(r"/(?:home|Users)/[^\s\"'<>]+", "<路径>", out)
    # 邮箱
    out = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<邮箱>", out)
    return out[:limit]


def _pseudonym(value: str, salt: str) -> str:
    """账号名/设备名 → 稳定的短哈希。

    为什么不留原文：反馈正文里出现「admin 的网盘上传失败」这种句子时，
    `user` 字段带真名等于把「谁在哪台机器上」一起交出去了。

    为什么不用随机数：同一个人的多条反馈要能串起来看（这样才能判断
    「同一台机器报了三个问题」是环境问题还是产品问题）。哈希稳定、
    不可逆，两个目的都能满足。
    """
    v = (value or "").strip()
    if not v:
        return ""
    return hashlib.sha256((salt + v).encode("utf-8")).hexdigest()[:12]


def build_payload(item: dict[str, Any]) -> dict[str, Any]:
    """组装上报体。**只挑这几个字段，其余一律不出本机**。

    2026-11-01 起做了脱敏：`user` / `device` 换成稳定短哈希，
    正文洗掉 URL / IP / 路径 / 邮箱 —— 用户点「上报」时不会想到要去删这些。
    """
    salt = (settings.get_setting(settings.FEEDBACK_SOURCE, "") or "lanshare")

    # 正文里手打的账号名/设备名 —— 正则认不出来，但它们比 IP 更能定位到人。
    # 拿已知的值去做字面替换，太短的不动（两三个字符的账号名替换会误伤正文）。
    body = item.get("body") or ""
    for known in (item.get("user") or "", item.get("device") or ""):
        known = known.strip()
        if len(known) >= 3:
            body = body.replace(known, "<用户>")

    return {
        "id": item.get("id", ""),
        "title": _scrub(item.get("title") or "", 80),
        "type": item.get("type", "other"),
        "body": _scrub(body, 2000),
        "app_ver": (item.get("app_ver") or "")[:40],
        # 设备名可能是「ZZQ的台式机」这种带真名的，只留哈希
        "device": _pseudonym(item.get("device") or "", salt),
        "user": _pseudonym(item.get("user") or "", salt),
        "created": item.get("created", ""),
        "source": (salt or "")[:40],
    }


def targets() -> list[str]:
    """当前启用了哪些通道，给管理界面显示用。"""
    out = []
    if _on(settings.FEEDBACK_RELAY_ENABLED) and settings.get_setting(
        settings.FEEDBACK_RELAY_URL, ""
    ):
        out.append("http")
    if _on(settings.FEEDBACK_GH_ENABLED) and settings.get_setting(
        settings.FEEDBACK_GH_REPO, ""
    ):
        out.append("github")
    return out


async def _post_inbox(payload: dict[str, Any]) -> str:
    url = (settings.get_setting(settings.FEEDBACK_RELAY_URL, "") or "").strip()
    if not url:
        return "skip:未配置上报地址"
    # 只走 http/https。配置项理论上由管理员填，但填错/被改成 file://
    # 之类的时候，httpx 的行为不值得赌 —— 直接拒绝，返回原因。
    if not url.lower().startswith(("http://", "https://")):
        return "skip:上报地址必须是 http/https"
    token = (settings.get_setting(settings.FEEDBACK_RELAY_TOKEN, "") or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json=payload, headers=headers)
        if r.status_code < 300:
            return "ok"
        return f"fail:HTTP {r.status_code}"
    except Exception as exc:
        log.warning("反馈上报失败（HTTP）：%s", exc)
        return f"fail:{type(exc).__name__}"


async def _create_issue(payload: dict[str, Any]) -> str:
    repo = (settings.get_setting(settings.FEEDBACK_GH_REPO, "") or "").strip()
    token = (settings.get_setting(settings.FEEDBACK_GH_TOKEN, "") or "").strip()
    if not repo or not token:
        return "skip:未配置仓库或 token"

    kind = payload.get("type") or "other"
    title = f"[反馈/{kind}] {payload.get('title') or '(无标题)'}"
    body = "\n".join(
        [
            payload.get("body") or "(无正文)",
            "",
            "---",
            f"- 编号：`{payload.get('id', '')}`",
            f"- 类型：{kind}",
            f"- 版本：{payload.get('app_ver') or '-'}",
            f"- 设备：{payload.get('device') or '-'}",
            f"- 用户：{payload.get('user') or '-'}",
            f"- 来源：{payload.get('source') or '-'}",
            f"- 创建：{payload.get('created') or '-'}",
        ]
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                f"https://api.github.com/repos/{repo}/issues",
                json={"title": title[:250], "body": body},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if r.status_code < 300:
            return "ok"
        return f"fail:HTTP {r.status_code}"
    except Exception as exc:
        log.warning("反馈上报失败（GitHub）：%s", exc)
        return f"fail:{type(exc).__name__}"


async def relay(item: dict[str, Any]) -> dict[str, str]:
    """把一条反馈送出去，返回各通道结果。

    调用方应该放在 BackgroundTasks 里 —— 这里会真的发网络请求，
    最坏情况要等两个 15 秒超时。
    """
    payload = build_payload(item)
    out: dict[str, str] = {}

    if _on(settings.FEEDBACK_RELAY_ENABLED):
        out["http"] = await _post_inbox(payload)
    if _on(settings.FEEDBACK_GH_ENABLED):
        out["github"] = await _create_issue(payload)

    if out:
        log.info("反馈 %s 上报结果：%s", payload.get("id"), out)
    return out
