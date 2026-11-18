# -*- coding: utf-8 -*-
"""传输隧道（上传 + 下载共用）的解析与校验。

设计要点见 docs/_internal/设计-传输隧道-20261118.md。
这里是唯一的写入口，托盘和 CLI 都走它 —— 校验规则必须在服务端，
不能只靠前端拦（前端可绕过，且将来 CLI 也会调）。
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from app import settings

MAX_TUNNELS = 5

# 只接受 origin（scheme + host + port）。路径/查询串一律拒绝：
# 上传/下载路径由 App 按文件自己拼，用户只需要给「从哪个入口进」。
_HOST_RE = re.compile(r"^[a-z0-9.-]+$")


def _bad(url: str) -> tuple[bool, str, str]:
    """(ok, detail, hint) —— 与 public_probe.check_format 同样的三元组约定。"""
    raw = (url or "").strip()
    if not raw:
        return False, "地址为空", "填一个 https:// 开头的入口地址"
    if "\\" in raw:
        # 反斜杠会被 urlsplit 当普通字符，但某些客户端会当路径分隔符 ——
        # `https://你的域名\\@evil.com` 的 host 会解析成 evil.com（钓鱼形状）。
        return False, "地址里不能有反斜杠", "检查是否从别处复制时带进了转义符"
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        return False, f"地址格式不合法：{exc}", "检查域名写法（方括号只用于 IPv6）"

    if parts.scheme != "https":
        return False, "必须是 https", "明文信道上的文件内容可以被链路内篡改"
    if "@" in parts.netloc:
        # user:pass@ 会把凭据随 URL 一起发出去，也会落进日志
        return False, "地址里不能带账号密码", "去掉 user:pass@ 部分"
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return False, "只填到端口为止，不要带路径", "例如 https://example.com:48429"
    host = (parts.hostname or "").lower()
    if not host:
        return False, "缺少主机名", ""
    if not _HOST_RE.match(host):
        return False, "主机名含非法字符", "只能用字母、数字、点、减号"
    if len(host) > 253:
        return False, "主机名过长", ""
    if host.endswith(".trycloudflare.com"):
        # quick tunnel 的域名每次重启都变，存下来必然失效
        return False, "这是临时隧道域名", "换用固定的自定义域名"
    try:
        # 只验证端口能解析，不取值 —— urlsplit 的 .port 在非法端口时抛 ValueError
        _ = parts.port
    except ValueError:
        return False, "端口不合法", ""
    return True, "格式正确", ""


def normalize(url: str) -> str:
    """归一化成 https://host[:port]（去掉尾斜杠、统一小写主机名）。"""
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    port = parts.port
    default = port is None or port == 443
    return f"https://{host}" if default else f"https://{host}:{port}"


def load() -> tuple[str, list[str]]:
    """读 (mode, tunnels)。mode 为 "custom" 且列表非空时才算自定义。"""
    try:
        raw = settings.get_setting(settings.TRANSFER_TUNNELS) or "[]"
        data = json.loads(raw)
    except Exception:
        return "follow", []
    if not isinstance(data, list):
        return "follow", []
    out = []
    for item in data[:MAX_TUNNELS]:
        url = item.get("url") if isinstance(item, dict) else item
        ok, _d, _h = _bad(str(url or ""))
        if ok:
            out.append(normalize(str(url)))
    return ("custom" if out else "follow"), out


def save(tunnels: list[str]) -> None:
    """写库（调用方负责先校验）。空列表等价于 follow。"""
    clean = [{"url": normalize(u)} for u in tunnels[:MAX_TUNNELS]]
    settings.set_settings({settings.TRANSFER_TUNNELS: json.dumps(clean)})
