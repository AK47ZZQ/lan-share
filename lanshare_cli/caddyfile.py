"""Caddyfile 生成。

为什么由 Python 生成而不是 PowerShell / 静态文件：
    1. 端口要能配。静态文件写死端口，用户装到端口冲突的机器上就得手改。
    2. PowerShell 的 here-string 里 `$` 会被展开，得用反引号转义，
       结果生成出乱码，Caddy 读到就崩。
    3. f-string 拼字符串没有这些坑，而且能单测渲染结果。

为什么默认 HTTPS 而且要带主机名（2026-10-31 修正）：
    之前这里写着「tls internal 对无主机名站点签不出证书，所以默认纯 HTTP」
    —— **结论是错的**，真相是开关用错了：

        auto_https off                → 自动 HTTPS 整块关掉，证书签发一起失效
        auto_https disable_redirects  → 只关 80→443 重定向，证书照签

    本机实测：`https://localhost:9902` 和 `https://192.168.1.12:9903` 都是 200，
    证书 issuer = CN=Caddy Local Authority - ECC Intermediate，
    SAN 里带 `IP Address:192.168.1.12` —— 手机用 IP 直连不会证书不匹配。

    为什么必须 HTTPS 不能回退 HTTP：手机端扫码只收 https（scan_page.dart
    的 _accept 里只有 https:// 一条。理由很实在：http 二维码 = 明文上送
    账号密码）。如果生成 HTTP 站点，二维码就是 http://192.168.x.x:8765，
    App 扫到会**静默忽略**，用户完全不知道为什么没反应。

    代价：Caddy 内部 CA 是自签的，浏览器首次要点「继续访问」，App 首次
    连接走 TOFU。和手工 openssl 签一套效果一样，但不用往包里塞
    cryptography（省 3~4 MB）。

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
\t# 只关 80→443 自动重定向（80 端口在 Windows 上常被别的程序占着），
\t# HTTPS 和证书签发照常工作。写成 auto_https off 会把证书也一起关掉。
\tauto_https disable_redirects
\t# 证书和内部 CA 存在安装目录里，不要用 Caddy 的默认位置。
\t#
\t# 默认位置是「当前用户的 AppData」。Caddy 作为 Windows 服务跑在 SYSTEM
\t# 账户下，于是落到 C:\Windows\system32\config\systemprofile\AppData\Roaming\Caddy
\t# —— 那是全机共用的一个坑：卸载重装、装第二个实例、换个安装目录，全都
\t# 挤在同一个 storage 里，旧 CA 残留会让新证书签不出来，客户端握手直接
\t# SEC_E_ILLEGAL_MESSAGE。2026-10-31 在虚拟机里踩到（同一台机器重装后才
\t# 出现，干净系统首次安装是好的，所以之前的沙箱测试没抓到）。
\tstorage file_system "{storage_dir}"
}}

{site_addrs} {{
{bind_line}\ttls internal
\treverse_proxy 127.0.0.1:{api_port} {{
\t\t# 告诉后端这是 HTTPS，Cookie 才会加 Secure 位
\t\theader_up X-Forwarded-Proto https
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


def default_hosts() -> list[str]:
    """站点该带哪些主机名。

    每个主机名都会进证书 SAN，所以手机用 IP 直连时这个 IP 必须在内，
    否则证书只对 localhost 有效，IP 访问会报证书不匹配。

    真实网卡（has_gateway=True）排前面，虚拟网卡（VirtualBox 的
    192.168.56.1 之类）排后面 —— 签进去无害，但不能当主要入口。
    """
    hosts: list[str] = ["localhost"]
    try:
        from .network import list_lan_addresses

        addrs = list_lan_addresses()
        preferred = [a.ip for a in addrs if getattr(a, "has_gateway", False)]
        others = [a.ip for a in addrs if not getattr(a, "has_gateway", False)]
        for ip in preferred + others:
            if ip and ip not in hosts:
                hosts.append(ip)
    except Exception:
        # 探测失败不该阻断安装：至少 localhost 可用
        pass
    return hosts


_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,252}[A-Za-z0-9])?")


def _safe_host(h: str) -> str:
    """主机名白名单校验。

    主机名直接拼进站点地址行，含换行/空格/花括号就能注入 Caddyfile 指令 ——
    对抗性测试里 `192.168.1.1 {}` 成功闭合了站点块。
    合法主机名只可能是字母、数字、点、连字符（IPv4 和域名共用这套字符集）。
    """
    v = (h or "").strip()
    if not v or not _HOST_RE.fullmatch(v):
        raise ValueError(f"非法主机名: {h!r}")
    return v


def site_block(
    bind_host: str, web_port: int, hosts: list[str] | None = None
) -> str:
    """生成站点块的地址行，形如：

        https://localhost:8765, https://192.168.1.12:8765

    必须带 scheme 和主机名 —— 裸 `:8765` 会被 Caddy 当成 HTTP 站点，
    tls internal 就不生效了（2026-10-31 找到的真正原因）。
    """
    names = hosts or ["localhost"]
    return ", ".join(f"https://{_safe_host(h)}:{web_port}" for h in names)


def bind_line(bind_host: str, indent: str = "\t") -> str:
    """生成站点块内的 bind 行。

    只绑回环时返回一行 bind；绑所有接口时返回空串（Caddy 的默认行为）。
    """
    if bind_host == BIND_LOOPBACK:
        return f"{indent}bind {BIND_LOOPBACK}\n"
    return ""


def render(
    api_port: int,
    web_port: int,
    bind_host: str = BIND_ALL,
    hosts: list[str] | None = None,
    storage_dir: str = "",
) -> str:
    """渲染 Caddyfile 内容（不落盘，方便单测）。

    storage_dir：证书和内部 CA 的落盘位置。传空串时退回 Caddy 默认位置
    （当前用户 AppData）—— 只该在单测里出现，CLI 必须传具体目录。
    """
    if not (1 <= api_port <= 65535):
        raise ValueError(f"api_port 越界: {api_port}")
    if not (1 <= web_port <= 65535):
        raise ValueError(f"web_port 越界: {web_port}")
    if api_port == web_port:
        raise ValueError(f"api_port 和 web_port 不能相同（都是 {api_port}）")

    names = hosts if hosts is not None else default_hosts()

    return TEMPLATE.format(
        api_port=api_port,
        web_port=web_port,
        site_addrs=site_block(bind_host, web_port, names),
        bind_line=bind_line(bind_host),
        # Caddyfile 按空格切 token：路径含空格（"LAN Share"）必须加引号，
        # Windows 反斜杠在 Caddy 里也不稳妥，统一换正斜杠。
        storage_dir=(storage_dir or "${HOME}/.local/share/caddy").replace(chr(92), "/"),
    )


def write(
    path: str | Path,
    api_port: int,
    web_port: int,
    bind_host: str = BIND_ALL,
    hosts: list[str] | None = None,
    storage_dir: str = "",
) -> Path:
    """渲染并写入。用 LF 换行 —— Caddy 不在乎，而且避免 diff 噪音。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render(api_port, web_port, bind_host, hosts, storage_dir)
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
