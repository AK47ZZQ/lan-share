"""Caddyfile 生成。

为什么由 Python 生成而不是 PowerShell / 静态文件：
    1. 端口要能配。静态文件写死端口，用户装到端口冲突的机器上就得手改。
    2. PowerShell 的 here-string 里 `$` 会被展开，得用反引号转义，
       结果生成出 `{\\$VAR}` 这种东西，Caddy 读到乱码。
    3. f-string 拼字符串没有这些坑，而且能单测渲染结果。

为什么默认纯 HTTP（不启用 tls internal）：
    2026-10-30 实测：Caddy 的 tls internal 对「无主机名的站点」（:端口 这种写法）
    无法签发证书，客户端握手一律 TLSV1_ALERT_INTERNAL_ERROR。
    家用内网 HTTP 完全够用，用户也不用点「继续访问」。
    需要 HTTPS 的话属于进阶配置：要么有域名，要么装固定证书。
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = """\
# 由 lanshare_cli 生成，不要手改 —— 改配置请改安装参数后重新 install
{{
	admin off
	auto_https off
}}

{site_block} {{
	reverse_proxy 127.0.0.1:{api_port} {{
		# 告诉后端这是 HTTP，Cookie 就不加 Secure 位（内网 HTTP 下才正常）
		header_up X-Forwarded-Proto http
		header_up X-Real-IP {{remote_host}}
		header_up Host {{host}}
	}}

	encode gzip
	# 大文件上传/下载不要缓冲超时
	request_body {{
		max_size 20GB
	}}
}}
"""

# 两种监听模式。这是 LAN 开关的**唯一实现机制**：
#   关（默认）→ 只绑回环，外部连 TCP 握手都到不了
#   开         → 绑所有接口，配合防火墙规则才真正可达
BIND_ALL = "0.0.0.0"        # 对应 site block `:8765`
BIND_LOOPBACK = "127.0.0.1"  # 对应 site block `127.0.0.1:8765`


def site_block(bind_host: str, web_port: int) -> str:
    """生成站点块的监听部分。

    Caddy 语法：`:8765` = 所有接口；`127.0.0.1:8765` = 仅回环。
    不写 `0.0.0.0:8765` —— Caddy 会把 0.0.0.0 当主机名处理，触发意外行为。
    """
    if bind_host == BIND_ALL:
        return f":{web_port}"
    return f"{bind_host}:{web_port}"


def render(api_port: int, web_port: int, bind_host: str = BIND_ALL) -> str:
    """渲染 Caddyfile 内容（不落盘，方便单测）。

    Caddy 的占位符 `{remote_host}` / `{host}` 用双花括号转义，
    避免被 f-string 吃掉。模板本身用 .format，所以要写 {{ }}。

    注意：这里用 str.format，模板里 Caddy 的 {{ }} 会被还原成单个 { }。
    """
    if not (1 <= api_port <= 65535):
        raise ValueError(f"api_port 越界: {api_port}")
    if not (1 <= web_port <= 65535):
        raise ValueError(f"web_port 越界: {web_port}")
    if api_port == web_port:
        raise ValueError(f"api_port 和 web_port 不能相同（都是 {api_port}）")

    block = site_block(bind_host, web_port)
    return TEMPLATE.format(api_port=api_port, web_port=web_port, site_block=block)


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
    # newline="" 防止 Windows 把 \n 变成 \r\n（Caddy 两种都吃，但保持稳定）
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return target


# ──────────────────────────────────────────────────────────────
# 从现有 Caddyfile 反推 LAN 状态（重装时保留用户选择）
# ──────────────────────────────────────────────────────────────

_SITE_LINE = re.compile(r"^\s*(?P<host>[^\s{]+):\d+\s*\{\s*$", re.MULTILINE)


def detect_bind_host(path: str | Path) -> str | None:
    """读现有 Caddyfile，判断当前是绑所有接口还是只绑回环。

    返回：
        BIND_ALL      — 站点块是 `:8765` 或 `0.0.0.0:8765`
        BIND_LOOPBACK — 站点块是 `127.0.0.1:8765`
        None          — 文件不存在 / 解析不出（调用方按默认值处理）

    为什么需要这个：
        `install` 会重新生成 Caddyfile。如果不先读旧文件的状态，
        用户辛苦开好的局域网会在修复安装/升级后被静默重置成关闭。
    """
    target = Path(path)
    if not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for line in text.splitlines():
        stripped = line.strip()
        # 跳过全局块、注释、反向代理内部结构
        if stripped.startswith("#") or stripped in ("{", "}"):
            continue
        # 形如 `:8765 {` —— 所有接口
        if re.fullmatch(r":\d+\s*\{", stripped):
            return BIND_ALL
        # 形如 `0.0.0.0:8765 {` —— 也是所有接口
        if re.fullmatch(r"0\.0\.0\.0:\d+\s*\{", stripped):
            return BIND_ALL
        # 形如 `127.0.0.1:8765 {` —— 仅回环
        if re.fullmatch(r"127\.0\.0\.1:\d+\s*\{", stripped):
            return BIND_LOOPBACK

    return None
