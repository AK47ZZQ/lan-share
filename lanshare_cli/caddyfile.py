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

from pathlib import Path

TEMPLATE = """\
# 由 lanshare_cli 生成，不要手改 —— 改配置请改安装参数后重新 install
{{
	admin off
	auto_https off
}}

:{web_port} {{
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


def render(api_port: int, web_port: int) -> str:
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

    return TEMPLATE.format(api_port=api_port, web_port=web_port)


def write(path: str | Path, api_port: int, web_port: int) -> Path:
    """渲染并写入。用 LF 换行 —— Caddy 不在乎，而且避免 diff 噪音。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render(api_port, web_port)
    # newline="" 防止 Windows 把 \n 变成 \r\n（Caddy 两种都吃，但保持稳定）
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return target
