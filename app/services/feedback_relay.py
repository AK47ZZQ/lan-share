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

import logging
from typing import Any

import httpx

from app import settings

log = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _on(key: str) -> bool:
    return (settings.get_setting(key, "") or "").strip() == "1"


def build_payload(item: dict[str, Any]) -> dict[str, Any]:
    """组装上报体。只挑这几个字段 —— 其余一律不出本机。"""
    return {
        "id": item.get("id", ""),
        "title": (item.get("title") or "")[:80],
        "type": item.get("type", "other"),
        "body": (item.get("body") or "")[:2000],
        "app_ver": (item.get("app_ver") or "")[:40],
        "device": (item.get("device") or "")[:80],
        "user": (item.get("user") or "")[:60],
        "created": item.get("created", ""),
        "source": (settings.get_setting(settings.FEEDBACK_SOURCE, "") or "")[:40],
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
