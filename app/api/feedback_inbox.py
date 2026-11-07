"""反馈收件箱：接收其它机器上报上来的反馈。

这是**开发端**用的端点。别人装了 LAN Share，在他的「我的 → 反馈上报」里
填上你的地址和 token，他提的反馈就会 POST 到你这里，落在
`data/反馈/收件箱/<来源>/` 下，和你自己机器的反馈放一起。

鉴权用 Bearer token，token 在**接收方**的 settings 表里配
（`feedback_inbox_token`），跟发送方的 `feedback_relay_token` 配成同一个值。

安全约束：
  * 没配 token 直接 403 —— 不能因为「忘了配」就变成开放接口
  * 正文/标题/设备全部截断，防止有人拿这个端点当存储桶
  * 来源目录名经过白名单过滤，杜绝 `../` 穿越
  * 每天每来源有条数上限，被刷了也不至于写爆磁盘
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import settings
from app.config import SHARE_ROOT
from app.security import require_admin

log = logging.getLogger(__name__)
router = APIRouter()

# 代发 issue 用的超时。relay 本身是后台任务，用户感知不到这段等待。
_GH_TIMEOUT = 15.0

_MAX_PER_SOURCE_PER_DAY = 200
_SAFE_SOURCE = re.compile(r"[^0-9A-Za-z_\-\u4e00-\u9fff]")


class InboxItem(BaseModel):
    id: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=80)
    type: str = Field(default="other", max_length=16)
    body: str = Field(default="", max_length=2000)
    app_ver: str = Field(default="", max_length=40)
    device: str = Field(default="", max_length=80)
    user: str = Field(default="", max_length=60)
    created: str = Field(default="", max_length=32)
    # P4（2026-11-06）：提交方要求「公开到 GitHub」。
    # 客户端**不持有** GitHub token —— 那个动作只能在接收端发生。
    public: bool = False
    source: str = Field(default="", max_length=40)


def _inbox_dir() -> Path:
    d = SHARE_ROOT / "反馈" / "收件箱"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(name: str) -> str:
    """来源名只留安全字符；空了就叫 unknown。"""
    cleaned = _SAFE_SOURCE.sub("", name or "")[:40]
    return cleaned or "unknown"


def _oneline(s: str) -> str:
    """单行归一:换行/制表折叠成空格。

    收件箱 md 同样是「键: 值」平铺 frontmatter;Pydantic 的 max_length 不挡
    \n —— 持 token 的上报方(或被攻破的客户端)能在文件里插任意键行。
    与 app/api/feedback.py 的 _oneline 同语义,两处各自独立、
    不为两行代码建共享模块(奥卡姆)。
    """
    return re.sub(r"\s+", " ", (s or "").strip())


def _quota_ok(source: str) -> bool:
    today = time.strftime("%Y%m%d")
    d = _inbox_dir() / source
    if not d.is_dir():
        return True
    return sum(1 for f in d.glob(f"{today}*.md")) < _MAX_PER_SOURCE_PER_DAY


def _expected_token() -> str:
    return (settings.get_setting(settings.FEEDBACK_INBOX_TOKEN, "") or "").strip()


def _token_ok(got: str, want: str) -> bool:
    """恒定时间比较。

    直接 `got != want` 会在第一个不同的字符处提前返回，逐字符的响应时间差
    足以在多次请求中把 token 爆破出来。这里用 hmac.compare_digest：
    长度一致时比较耗时与内容无关。
    """
    import hmac

    return hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8"))


async def _publish_issue(item: InboxItem, source: str) -> str:
    """把一条要求公开的反馈变成 GitHub Issue，返回 html_url（失败返回空串）。

    **为什么在这里做、而不是在用户机器上做**：建 issue 需要仓库写权限的 token。
    分发出去的安装包里绝不能带这东西 —— 一旦带上，任何人拆包就能往仓库灌垃圾。
    所以客户端只勾一个「公开」，真正落地的动作由接收端用自己的凭据完成。
    """
    repo = (settings.get_setting(settings.FEEDBACK_GH_REPO, "") or "").strip()
    token = (settings.get_setting(settings.FEEDBACK_GH_TOKEN, "") or "").strip()
    if not repo or not token:
        log.info("收到公开请求，但接收端没配 feedback_gh_repo / feedback_gh_token，跳过代发")
        return ""
    kind = item.type or "other"
    body = "\n".join([
        item.body or "(无正文)",
        "",
        "---",
        f"- 编号：`{item.id}`",
        f"- 类型：{kind}",
        f"- 版本：{item.app_ver or '-'}",
        f"- 设备：{item.device or '-'}",
        f"- 来源：{source}",
        f"- 创建：{item.created or '-'}",
        "",
        "_由 LAN Share 反馈中继代发（提交者在客户端勾选「公开」）_",
    ])
    try:
        async with httpx.AsyncClient(timeout=_GH_TIMEOUT) as c:
            r = await c.post(
                f"https://api.github.com/repos/{repo}/issues",
                json={"title": f"[反馈/{kind}] {(item.title or '(无标题)')[:240]}", "body": body},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if r.status_code < 300:
            return str(r.json().get("html_url") or "")
        log.warning("代发 issue 失败：HTTP %s %s", r.status_code, (r.text or "")[:200])
    except Exception as exc:
        log.warning("代发 issue 异常：%s", exc)
    return ""


@router.post("/api/feedback/inbox")
async def feedback_inbox(
    item: InboxItem,
    request: Request,
    authorization: str = Header(default=""),
):
    want = _expected_token()
    if not want:
        # 没配 token 就不开门。宁可开发端收不到，也不能变成公网垃圾桶。
        raise HTTPException(
            status_code=403,
            detail="收件箱未启用：请在系统设置里配置 feedback_inbox_token",
        )

    got = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not got or not _token_ok(got, want):
        raise HTTPException(status_code=403, detail="token 不正确")

    source = _safe(item.source)
    if not _quota_ok(source):
        raise HTTPException(status_code=429, detail="该来源今日反馈已达上限")

    # 单行归一(frontmatter 键行注入面,见 _oneline docstring);body 是
    # frontmatter 之后的多行正文,不在其列
    item = item.model_copy(update={
        "title": _oneline(item.title),
        "type": _oneline(item.type),
        "app_ver": _oneline(item.app_ver),
        "device": _oneline(item.device),
        "user": _oneline(item.user),
    })

    fid = (item.id or time.strftime("%Y%m%d%H%M%S")).strip()
    # 编号也过一遍白名单，避免有人拿它做路径穿越
    fid = re.sub(r"[^0-9A-Za-z\-]", "", fid)[:64] or time.strftime("%Y%m%d%H%M%S")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    d = _inbox_dir() / source
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{fid}.md"
    if path.exists():  # 重发不重复写
        return {"ok": True, "id": fid, "dup": True}

    # P4：要求公开的，先在接收端代发 issue。失败不影响收件 ——
    # 反馈本身比「公开」这个附加动作重要得多。
    issue_url = await _publish_issue(item, source) if item.public else ""

    md = "\n".join(
        [
            "---",
            f"编号: {fid}",
            f"类型: {item.type}",
            "状态: 已提交",
            "备注:",
            f"标题: {item.title}",
            f"版本: {item.app_ver}",
            f"设备: {item.device}",
            f"用户: {item.user}",
            f"来源: {source}",
            f"创建: {item.created or now}",
            f"更新: {now}",
            f"公开: {'是' if item.public else ''}",
            f"issue: {issue_url}",
            "---",
            "",
            item.body,
            "",
        ]
    )
    path.write_text(md, encoding="utf-8")

    # 附带原始的 JSON 存档，方便排查上报体本身的问题
    (d / f"{fid}.json").write_text(
        json.dumps(item.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "id": fid, "issue_url": issue_url}


@router.get("/api/feedback/inbox/status")
def inbox_status():
    """给管理面板看：收件箱开没开、收了多少。"""
    enabled = bool(_expected_token())
    d = _inbox_dir()
    stats = {}
    if d.is_dir():
        for sub in d.iterdir():
            if sub.is_dir():
                stats[sub.name] = len(list(sub.glob("*.md")))
    return {"enabled": enabled, "sources": stats, "total": sum(stats.values())}


class RelayConfig(BaseModel):
    """上报/收件配置。全部可选 —— 只改传上来的那几项。"""

    relay_enabled: bool | None = None
    relay_url: str | None = Field(default=None, max_length=300)
    relay_token: str | None = Field(default=None, max_length=200)
    gh_enabled: bool | None = None
    gh_repo: str | None = Field(default=None, max_length=120)
    gh_token: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=40)
    inbox_token: str | None = Field(default=None, max_length=200)


_FLAGS = {
    "relay_enabled": settings.FEEDBACK_RELAY_ENABLED,
    "gh_enabled": settings.FEEDBACK_GH_ENABLED,
}
_STRINGS = {
    "relay_url": settings.FEEDBACK_RELAY_URL,
    "relay_token": settings.FEEDBACK_RELAY_TOKEN,
    "gh_repo": settings.FEEDBACK_GH_REPO,
    "gh_token": settings.FEEDBACK_GH_TOKEN,
    "source": settings.FEEDBACK_SOURCE,
    "inbox_token": settings.FEEDBACK_INBOX_TOKEN,
}


@router.get("/api/admin/feedback-relay")
def get_relay_config(_admin=Depends(require_admin)):
    """读上报配置。token 类字段只回「设没设」，不回原值。"""
    # 显式标成 dict[str, object]：这个字典里既有字符串（URL、来源标识）
    # 又有布尔（各项开关），下面几行会把 str 换成 bool。用默认推导出的
    # dict[str, str] 会跟赋值打架（mypy: Incompatible types in assignment）。
    out: dict[str, object] = {k: settings.get_setting(k, "") for k in _STRINGS}
    for k in ("relay_token", "gh_token", "inbox_token"):
        out[k + "_set"] = bool(out.pop(k))
    out["relay_enabled"] = settings.get_setting(settings.FEEDBACK_RELAY_ENABLED, "") == "1"
    out["gh_enabled"] = settings.get_setting(settings.FEEDBACK_GH_ENABLED, "") == "1"
    out["inbox_enabled"] = bool(
        (settings.get_setting(settings.FEEDBACK_INBOX_TOKEN, "") or "").strip()
    )
    return out


@router.put("/api/admin/feedback-relay")
def set_relay_config(cfg: RelayConfig, _admin=Depends(require_admin)):
    """改上报配置。传空字符串表示清空该 token。"""
    for field, key in _FLAGS.items():
        v = getattr(cfg, field)
        if v is not None:
            settings.set_setting(key, "1" if v else "")
    for field, key in _STRINGS.items():
        v = getattr(cfg, field)
        if v is not None:
            settings.set_setting(key, v.strip())
    return {"ok": True}
