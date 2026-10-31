"""Caddyfile 生成。

为什么由 Python 生成而不是 PowerShell / 静态文件：
    1. 端口要能配。静态文件写死端口，用户装到端口冲突的机器上就得手改。
    2. PowerShell 的 here-string 里 `$` 会被展开，得用反引号转义，
       结果生成出乱码，Caddy 读到就崩。
    3. f-string 拼字符串没有这些坑，而且能单测渲染结果。

为什么默认纯 HTTP（不启用 tls internal）：
    2026-10-30 实测：Caddy 的 tls internal 对「无主机名的站点」（:端口 这种写法）
    无法签发证书，客户端握手一律 TLSV1_ALERT_INTERNAL_ERROR。
    家用内网 HTTP 完全够用，用户也不用点「继续访问」。

============================================================
⚠️ 控制监听地址必须用 bind 指令，不能改站点块首行
============================================================

2026-10-31 实测踩坑：把 `:8765 {` 改成 `127.0.0.1:8765 {` 看起来天经地义，
实际完全无效 —— Caddy v2 把 `127.0.0.1:8765` 理解为「主机名 127.0.0.1 +
端口 8765」，**监听地址不变**（仍是所有接口），只是多了一个 host 匹配条件。

结果：配置文件改了、服务重启了、Get-NetTCPConnection 还是显示绑在 `::` 上，
自检用局域网 IP 照样能通 —— 等于开关根本没生效。

正确写法是站点块内的 bind 指令：

    :8765 {
        bind 127.0.0.1
        ...
    }
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = """\
# 由 lanshare_cli 生成，不要手改 —— 改配置请改安装参数后重新 install
{{
\tadmin off
\tauto_https off
}}

:{web_port} {{
{bind_line}\treverse_proxy 127.0.0.1:{api_port} {{
\t\t# 告诉后端这是 HTTP，Cookie 就不加 Secure 位（内网 HTTP 下才正常）
\t\theader_up X-Forwarded-Proto http
\t\theader_up X-Real-IP {{remote_host}}
\t\theader_up Host {{host}}
\t}}

\tencode gzip
\t# 大文件上传/下载不要缓冲超时
\trequest_body {{
\t\tmax_size 20GB
\t}}
}}
"""

# 两种监听模式。这是 LAN 开关的唯一实现机制：
#   关（默认）→ 站点块内加 bind 127.0.0.1，外部连 TCP 握手都到不了
#   开         → 没有 bind 指令，监听所有接口
BIND_ALL = "0.0.0.0"
BIND_LOOPBACK = "127.0.0.1"


def site_block(bind_host: str, web_port: int) -> str:
    """生成站点块的监听首行，永远是裸端口 `:8765`。

    监听地址不在这里控制，由 bind_line() 负责 —— 见模块开头的说明。
    """
    return f":{web_port}"


def bind_line(bind_host: str, indent: str = "\t") -> str:
    """生成站点块内的 bind 行。

    只绑回环时返回一行 bind；绑所有接口时返回空串（Caddy 的默认行为）。
    """
    if bind_host == BIND_LOOPBACK:
        return f"{indent}bind {BIND_LOOPBACK}\n"
    return ""


def render(api_port: int, web_port: int, bind_host: str = BIND_ALL) -> str:
    """渲染 Caddyfile 内容（不落盘，方便单测）。"""
    if not (1 <= api_port <= 65535):
        raise ValueError(f"api_port 越界: {api_port}")
    if not (1 <= web_port <= 65535):
        raise ValueError(f"web_port 越界: {web_port}")
    if api_port == web_port:
        raise ValueError(f"api_port 和 web_port 不能相同（都是 {api_port}）")

    return TEMPLATE.format(
        api_port=api_port,
        web_port=web_port,
        bind_line=bind_line(bind_host),
    )


def write(
    path: str | Path,
    api_port: int,
    web_port: int,
    bind_host: str = BIND_ALL,
) -> Path:
    """渲染并写入。用 LF 换行 —— Caddy 不在乎，而且避免 diff 噪音。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render(api_port, web_port, bind_host)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return target


# ============================================================
# 从现有 Caddyfile 反推 LAN 状态（重装时保留用户选择）
# ============================================================

_BIND_RE = re.compile(r"^\s*bind\s+(?P<addr>[^\s#]+)", re.MULTILINE)


def detect_bind_host(path: str | Path, web_port: int | None = None) -> str | None:
    """读现有 Caddyfile，判断站点块绑的是所有接口还是只绑回环。

    判据是 bind 指令，不是站点块首行（见模块开头的说明）。

    返回：
        BIND_ALL      没有 bind 指令，或 bind 0.0.0.0 / ::
        BIND_LOOPBACK bind 127.0.0.1 / ::1 / localhost
        None          文件不存在 / 明显不是我们的配置

    为什么需要：
        install 会重新生成 Caddyfile。不先读旧文件的状态，
        用户开好的局域网会在修复安装/升级后被静默重置成关闭。
    """
    target = Path(path)
    if not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if web_port is not None and f":{web_port}" not in text:
        return None

    for m in _BIND_RE.finditer(text):
        addr = m.group("addr").strip()
        if addr in ("127.0.0.1", "::1", "localhost"):
            return BIND_LOOPBACK
        if addr in ("0.0.0.0", "::", "*"):
            return BIND_ALL

    if web_port is not None:
        return BIND_ALL

    return None
