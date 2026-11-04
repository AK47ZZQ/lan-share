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

    本机实测：`https://localhost:9902` 和 `https://192.0.2.14:9903` 都是 200，
    证书 issuer = CN=Caddy Local Authority - ECC Intermediate，
    SAN 里带 `IP Address:192.0.2.14` —— 手机用 IP 直连不会证书不匹配。

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

import ipaddress
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
\t# 账户下，于是落到 C:/Windows/system32/config/systemprofile/AppData/Roaming/Caddy
\t# —— 那是全机共用的一个坑：卸载重装、装第二个实例、换个安装目录，全都
\t# 挤在同一个 storage 里，旧 CA 残留会让新证书签不出来，客户端握手直接
\t# SEC_E_ILLEGAL_MESSAGE。2026-10-31 在虚拟机里踩到（同一台机器重装后才
\t# 出现，干净系统首次安装是好的，所以之前的沙箱测试没抓到）。
\tstorage file_system "{storage_dir}"
}}

{site_addrs} {{
{bind_line}\ttls {{
{bind_line}\t\t# 站点证书有效期。Caddy 内部 CA 默认只给 12 小时，靠后台任务在到期前
{bind_line}\t\t# 自动续期 —— 但那套后台维护只在服务连续运行时可靠。服务被停过、被
{bind_line}\t\t# 重装过、机器睡眠过，就可能出现「证书已过期但没人续」的状态：
{bind_line}\t\t# 此时任何走系统信任库的客户端（浏览器、Python updater、手机 App）
{bind_line}\t\t# 握手一律失败，而且报的是「连不上」而不是「证书过期」。
{bind_line}\t\t#
{bind_line}\t\t# 2026-11-04 实测：11:18 过期，11:23 检查更新就报 TLS 校验失败。
{bind_line}\t\t# 30 天足够覆盖「装了就不管」的用法，到期前 Caddy 也仍会自动续。
{bind_line}\t\tissuer internal {{
{bind_line}\t\t\tlifetime 720h
{bind_line}\t\t}}
{bind_line}\t}}

\t# ── 静态资源交给 Caddy 直接发，不经过反向代理 ──
\t# 2026-11-02 用户实测：/login 返回 200，但 /assets/*.js 和 *.css 全是 403，
\t# 页面因此白屏（HTML 出来了，JS/CSS 被拒）。同一个包在别的机器上正常，
\t# 说明是安装环境的偶发问题 —— 与其追这种环境差异，不如让静态资源根本
\t# 不进 API 进程：Caddy 直接读文件，绕开所有中间件和鉴权。
\t#
\t# 顺带也快了：JS/CSS 不再占用 Python 线程、不经过 uvicorn。
\thandle /assets/* {{
\t\troot * "{web_root}"
\t\tfile_server
\t}}
\thandle /static/* {{
\t\troot * "{web_root}"
\t\tfile_server
\t}}
\thandle /icons/* {{
\t\troot * "{web_root}"
\t\tfile_server
\t}}

\t# 更新包目录（3.96）：程序自己的东西，不放进用户文件区。
\t#
\t# 为什么需要：自定义更新地址指向本机时，清单和安装包得有个地方放。
\t# 放 <数据目录>/updates 会跟 pgdata 犯同一个错 —— 网盘把它当用户文件
\t# 列给用户看。所以放程序目录下，由 Caddy 直接发文件。
\t#
\t# 结构：
\t#   updates/index.json             服务端（.exe）
\t#   updates/app/index.json         App（.apk）
\t# 用 handle_path 而不是 handle：handle 不会剥掉匹配到的前缀，
\t# 请求 /updates/index.json 会被拼成 <root>/updates/index.json（404）。
\t# handle_path 先剥掉 /updates 再找文件，root 直接指到更新目录。
\thandle_path /updates/* {{
\t\troot * "{updates_root}"
\t\tfile_server
\t}}

\thandle {{
\t\treverse_proxy 127.0.0.1:{api_port} {{
\t\t\t# 告诉后端这是 HTTPS，Cookie 才会加 Secure 位
\t\t\theader_up X-Forwarded-Proto https
\t\t\theader_up X-Real-IP {{remote_host}}
\t\t\t# 🔴 不要写 header_up Host {{host}}。
\t\t\t#
\t\t\t# Caddy 的 {{host}} 是**不含端口**的主机名。写成 {{host}} 会把
\t\t\t# Host: <主机>:<端口> 改写成 Host: <主机>，而浏览器发的 Origin
\t\t\t# 带着端口 —— 后端 CSRF 中间件比对 netloc 时两边不相等，
\t\t\t# 于是所有 POST 一律
\t\t\t# 403 "CSRF check failed"。
\t\t\t#
\t\t\t# 2026-11-02 用户实机：装完 3.88 注册管理员直接 403，就是这条。
\t\t\t# 本地开发机用的老 Caddyfile 没有这行，所以从来没复现过。
\t\t\t#
\t\t\t# 默认行为就是原样透传 Host，正是我们要的，这里不写即可。
\t\t\t# 真要显式写，正确占位符是 {{http.request.hostport}}（含端口）。
\t\t}}
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

# 隧道端口：内网穿透工具把公网流量转进来时用的本机端口。
#
# 三个值的来历：8765 是主入口，8767 是历史上给「公网隧道」留的第二个入口
# （早期设计文档里写过，用户照那个填了隧道本地端口），8767 因此必须有人听，
# 否则那个配法永远「连接被拒绝」。用户也可以在面板里填 8765，两种都通。
TUNNEL_PORT = 8767


def default_hosts() -> list[str]:
    """站点该带哪些主机名。

    每个主机名都会进证书 SAN，所以手机用 IP 直连时这个 IP 必须在内，
    否则证书只对 localhost 有效，IP 访问会报证书不匹配。

    真实网卡（has_gateway=True）排前面，虚拟网卡（VirtualBox 的
    192.0.2.14 之类）排后面 —— 签进去无害，但不能当主要入口。
    """
    hosts: list[str] = ["localhost"]
    # 127.0.0.1 也要进 SAN。托盘的健康探测、「打开网盘」按钮、CLI 自检探针
    # 全都用 127.0.0.1 访问 —— 证书里没有它，服务端跑得好好的，托盘也会
    # 一直报 SecureChannelFailure（TLS 握手失败），界面上一片红。
    #
    # 2026-11-01 实测：干净机器装 3.80，Caddy 日志
    #   "domains":["localhost","192.0.2.14"]
    # 托盘日志
    #   探测 HTTPS https://127.0.0.1:8765/healthz → Down | SecureChannelFailure
    # 于是「启动局域网服务」按钮点了没反应、地址不显示、二维码也没有。
    if "127.0.0.1" not in hosts:
        hosts.append("127.0.0.1")
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
    对抗性测试里 `192.0.2.14 {}` 成功闭合了站点块。
    合法主机名只可能是字母、数字、点、连字符（IPv4 和域名共用这套字符集）。
    """
    v = (h or "").strip()
    if not v or not _HOST_RE.fullmatch(v):
        raise ValueError(f"非法主机名: {h!r}")
    return v


def site_block(
    bind_host: str,
    web_port: int,
    hosts: list[str] | None = None,
    tunnel_port: int | None = None,
) -> str:
    """生成站点块的地址行，形如：

        https://localhost:8765, https://192.0.2.14:8765

    必须带 scheme 和主机名 —— 裸 `:8765` 会被 Caddy 当成 HTTP 站点，
    tls internal 就不生效了（2026-10-31 找到的真正原因）。
    """
    names = hosts or ["localhost"]
    addrs = [f"https://{_safe_host(h)}:{web_port}" for h in names]

    # 隧道端口：内网穿透工具把公网流量转到本机的另一个端口时，Caddy 得在那个
    # 端口上也有 listener，否则 frpc 直接「连接被拒绝」，用户从外面看到的就是
    # 打不开（2026-11-04 实机：隧道本地端口填了 8767，Caddy 只听 8765）。
    #
    # 端口号由用户在穿透面板里填，我们没法替他决定，所以干脆两个端口都监听。
    # 同一个站点块、同一套 TLS 配置 —— 不是新入口，只是同一入口的备用口。
    if tunnel_port and tunnel_port != web_port:
        addrs += [f"https://{_safe_host(h)}:{tunnel_port}" for h in names]
    return ", ".join(addrs)


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
    web_root: str = "",
    updates_root: str = "",
    tunnel_port: "int | None" = TUNNEL_PORT,
) -> str:
    """渲染 Caddyfile 内容（不落盘，方便单测）。

    storage_dir：证书和内部 CA 的落盘位置。传空串时退回 Caddy 默认位置
    （当前用户 AppData）—— 只该在单测里出现，CLI 必须传具体目录。

    web_root：前端产物目录（含 index.html / assets / static / icons）。
    Caddy 用它直接 file_server 发静态资源，不经过 API。传空串时退回相对路径
    "../web/dist"（**相对 Caddy 进程的工作目录**，不是 Caddyfile 所在目录 ——
    服务模式下工作目录是 system32，相对 root 实际取不到文件，2026-11-03
    生产部署踩过：/updates/* 全 404）。只该在单测里出现，CLI 必须传绝对路径。

    updates_root：更新包目录（index.json + 安装包）。语义同 web_root，
    空串退回 "../updates" 同样是相对进程 cwd 的陷阱值。
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
        site_addrs=site_block(bind_host, web_port, names, tunnel_port=tunnel_port),
        bind_line=bind_line(bind_host),
        # Caddyfile 按空格切 token：路径含空格（"LAN Share"）必须加引号，
        # Windows 反斜杠在 Caddy 里也不稳妥，统一换正斜杠。
        storage_dir=(storage_dir or "${HOME}/.local/share/caddy").replace(chr(92), "/"),
        # 同样要换正斜杠 + 不能带引号问题（模板里已经加了一对引号）
        web_root=(web_root or "../web/dist").replace(chr(92), "/"),
        # 更新包目录（3.96）。放在程序目录下而不是数据目录里 ——
        # 数据目录是用户文件根，网盘会把里面的东西列给用户看。
        updates_root=(updates_root or "../updates").replace(chr(92), "/"),
    )


# 站点地址里的「主机名[:端口]」，前面的 scheme 可选 ——
# Caddy 官方文档里 `example.com:443 {` 这种裸写法很常见，只认 https:// 会漏。
_ADDR_HOST_RE = re.compile(r"^(?:https?://)?([A-Za-z0-9][A-Za-z0-9.-]*)(?::(\d+))?$")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def existing_domains(path: str | Path, ports: "list[int] | None" = None) -> list[str]:
    """从现有 Caddyfile 的站点地址行里捞出「非 IP 的主机名」。

    存在的唯一理由：**升级不能把公网域名弄丢**。

    用户配好内网穿透后，那个域名只活在 Caddyfile 的站点地址里（settings 里也有，
    但那是应用层的库，CLI 在安装早期根本读不到）。而 install / lan enable 都会
    用 default_hosts() 整份重写 Caddyfile，那个函数只认本机网卡地址 ——
    于是每次升级都把域名抹掉，证书 SAN 不再包含它，公网 TLS 直接失败。

    2026-11-04 实机：4.0.4 → 4.0.5 一键更新完，公网立刻 000，用户以为更新把
    网盘搞坏了。这种「升级顺手破坏另一个功能」的 bug 最难查，因为症状（公网打不开）
    和操作（点了个更新）看起来毫无关系。

    只保留长得像域名的东西：IP 字面量、localhost、解析不出的乱码一律丢弃 ——
    这个函数会往场景里塞可能被手改过的文件，不能照单全收。
    """
    target = Path(path)
    if not target.is_file():
        return []
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    # 只认带我们自己的端口（或裸主机名）的那些段。
    #
    # 为什么要有这个过滤：整份文件扫「像地址的东西」会把用户另外配的站点
    # （`https://admin.internal:9000 {...}`）的主机名一起吸收进网盘的站点块，
    # Caddy 会给它签证书、把本该 respond 的路径变成反代到网盘 —— 等于凭空
    # 多开一个入口。审查实测能复现。
    want_ports = {str(p) for p in (ports or [])}

    out: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()          # 去掉行尾注释
        if not line:
            continue
        for part in line.split(","):
            part = part.strip().rstrip("{").strip()
            if not part:
                continue
            m = _ADDR_HOST_RE.match(part)
            if not m:
                continue                              # `:8765 {`、IPv6、乱码
            host, port = m.group(1).lower(), m.group(2)
            if not host or host == "localhost" or _is_ip_literal(host):
                continue
            if want_ports and port and port not in want_ports:
                continue                              # 别的端口 = 别的站点
            if _HOST_RE.fullmatch(host) and host not in out:
                out.append(host)
    return out


def write(
    path: str | Path,
    api_port: int,
    web_port: int,
    bind_host: str = BIND_ALL,
    hosts: list[str] | None = None,
    storage_dir: str = "",
    web_root: str = "",
    updates_root: str = "",
    keep_existing_domains: bool = True,
    tunnel_port: "int | None | str" = "auto",
) -> Path:
    """渲染并写入。用 LF 换行 —— Caddy 不在乎，而且避免 diff 噪音。

    会**自动保留**旧文件站点地址里的公网域名（见 existing_domains）。这一步放在
    这里而不是各个调用点，是因为重写 Caddyfile 的地方有三处（install、
    lan enable/disable、set_public_host 的前置重写），逐个补必然漏一个，
    而漏掉的后果是「升级完公网静默失效」。

    keep_existing_domains=False 是给「一键接入」用的：用户在那一刻明确指定了新的
    公网域名，旧的就该被替换掉。否则他换域名时旧域名会被永久保留 ——
    自己能加不能删的配置，迟早变成一团谁也不认识的 SAN。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 先决定隧道端口要不要监听。
    #
    # 🔴 默认「自动」= 这台机器本来就活在隧道里才开：
    #    旧配置里已有域名，或已经有那个端口的站点地址。
    #
    # 为什么不能无条件开：8767 一旦被别的程序占住，Caddy 就是**整份配置加载失败**，
    # 连 8765 都不监听 —— 局域网一起挂。审查实测（bind 冲突时报
    # `listening on :8767: bind: Only one usage of each socket address`）。
    # 没在用隧道的机器不该为这个硬编码端口冒风险。
    _ftp = existing_domains(target, ports=[web_port, TUNNEL_PORT])
    _saw_tunnel = f":{TUNNEL_PORT}" in target.read_text(encoding="utf-8", errors="replace")         if target.is_file() else False
    if tunnel_port == "auto":
        real_tunnel: "int | None" = TUNNEL_PORT if (_ftp or _saw_tunnel) else None
    else:
        real_tunnel = tunnel_port                      # type: ignore[assignment]

    merged = list(hosts) if hosts is not None else default_hosts()
    if keep_existing_domains:
        for h in _ftp:
            if h not in merged:
                merged.append(h)

    content = render(api_port, web_port, bind_host, merged, storage_dir, web_root,
                     updates_root, tunnel_port=real_tunnel)
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
