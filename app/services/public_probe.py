"""公网入口可达性探测 —— 给托盘「公网访问 → Cloudflare Tunnel」用。

这个模块只管**判断**，不落库、不改配置。调用方是
`POST /local/public-url/probe`，它把结果原样返回给托盘渲染。

## 为什么要有「同源校验」

只验证「HTTPS 能通」是不够的：用户少打一个字母，域名指向了别人的
LANShare，验证照样通过，然后 App 就会连到别人的服务器上。

所以最后一步必须证明「这个域名指向的就是这台部署」——做法是比对本机与
公网的 `/api/instance-id` 是否返回同一个 UUID。

## 为什么环境检测不阻塞

存在这些合法场景，任何一个都不该拦住用户：
- cloudflared 跑在 A 机器，指向 B 机器的 LANShare
- cloudflared 装在非标准路径，没找到
- 用 Docker / 群晖跑 cloudflared，本机根本没有这个进程

所以「cloudflared 没装」「进程没跑」只出提示，不进 `ok` 的判定。
唯一给红色强警告的是 TUN 模式——那是百分之百会导致隧道连不上的因素。

## 非 Windows 平台

本模块有若干 Windows 专用探测（tasklist）。在别的平台上这些项返回
`state="skip"` 而不是崩溃 —— CI 跑在 Linux 上，不能因为探测不了就红。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from app import settings

# ── 常量 ────────────────────────────────────────────────

# Cloudflare 的 IPv4 段（来源 https://api.cloudflare.com/client/v4/ips）。
# 变动极少，硬编码即可；真要更新，改这一处。
_CF_V4 = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
)
_CF_NETS = tuple(ipaddress.ip_network(c) for c in _CF_V4)

# 必须伪装 UA：Cloudflare 会把 `Python-urllib/*` 当爬虫直接回 1010
# （2026-11-18 实测：同一 URL 用浏览器 UA / Dio UA / 空 UA 全是 200，
#  只有 Python-urllib 是 403）。探针用 Python 发请求，不加这条必挂。
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Cloudflare 自己的域名后缀 —— 用户不该把自己的入口填成这些
_CF_OWN_SUFFIXES = (".pages.dev", ".workers.dev", ".r2.dev",
                    ".cfargotunnel.com", ".trycloudflare.com")

# 主机名白名单。对抗测试（2026-11-18 A1）证明：不加这条，
# `ok.example.com%2fevil` 这类编码绕过会整个通到 DNS 那一步。
# punycode 的 `xn--` 也落在 [a-z0-9-] 里，不会被误伤。
_HOST_RE = re.compile(r"[a-z0-9.\-]+")

# 系统命令用绝对路径：lan-share-api 以 LocalSystem 运行，裸名会先搜应用
# 目录和当前目录，给了 PATH/当前目录劫持面（2026-11-18 审查 P2）。
_SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")

PROBE_TIMEOUT = 15      # 比普通请求宽：慢上行下首次 TLS 握手就要 1-3 秒
PROBE_RETRIES = 1       # 超时 / 5xx 重试一次；4xx 不重试


# ── 单步检测 ────────────────────────────────────────────


def _sys_exe(name: str) -> str:
    path = os.path.join(_SYSTEM32, name)
    return path if os.path.isfile(path) else name


def _step(key: str, label: str, ok: bool, detail: str,
          hint: str = "", blocking: bool = True, level: str = "") -> dict:
    """统一的结果项形状。level 只用于非阻塞项：warn（黄） / danger（红）。"""
    return {"key": key, "label": label, "ok": ok, "detail": detail,
            "hint": hint, "blocking": blocking, "level": level}


def check_format(raw: str) -> tuple:
    """第一步：纯字符串校验，不碰网络、**不抛异常**。

    返回 (ok, detail, hint, host)。

    `urlsplit` 对畸形 IPv6 写法（`https://[`、`https://[::1`）会抛 ValueError，
    而这个函数的契约是不抛 —— 它被 probe 和保存端点直接调用，抛出去就是 500。
    """
    raw = (raw or "").strip()
    if not raw:
        return False, "地址为空", "请填写 https://你的域名", ""

    try:
        parts = urlsplit(raw)
    except ValueError:
        return False, "地址格式不合法", \
            "检查域名写法（方括号只用于 IPv6 地址）", ""

    if parts.scheme != "https":
        got = parts.scheme or "空"
        return False, f"协议是 {got}", "必须以 https:// 开头", ""

    # Cloudflare 只走 443。用户从 FRP 那套带过来的习惯会填端口。
    try:
        port = parts.port
    except ValueError:
        return False, "端口不合法", "不要填端口，Cloudflare 固定 443", ""
    if port not in (None, 443):
        return False, f"带了端口 {port}", "Cloudflare 固定 443，不要填端口", ""

    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return False, "带了路径或参数", "只填域名，不要带路径", ""

    host = (parts.hostname or "").lower().rstrip(".")

    if not host:
        return False, "没有主机名", "请填写完整域名", ""

    # ── 下面几条是对抗测试（2026-11-18 A1 轮）抓出来的绕过 ──────────
    #
    # ① 反斜杠：`urlsplit` 把它当主机分隔符，于是
    #    `https://你的域名.com\@evil.com` 解析出的 host 是 **evil.com** ——
    #    用户看着像自己的域名，实际连到攻击者那里。这是最危险的一条，
    #    它正好绕过「同源校验」，因为探针会去 evil.com 拿 UUID。
    if "\\" in raw:
        return False, "地址里不能有反斜杠", \
            "只填域名。反斜杠会被解析成主机分隔符，可能把你带到别的站点", ""

    # ② userinfo：`https://user:pass@host` 里的凭据会被一起发出去，
    #    而且 `https://自己的域名@evil.com` 同样是钓鱼形状。Cloudflare
    #    这条链路不需要任何 userinfo。
    if "@" in (parts.netloc or ""):
        return False, "地址里不能带 @", "只填域名，不要带用户名或凭据", ""

    # ③ 主机名白名单：只允许字母、数字、点、连字符。
    #    这样 `ok.example.com%2fevil`（编码斜杠）、各种编码绕过都进不来。
    #    IDN 走 punycode（xn-- 开头）时仍在白名单内，所以没有被误伤。
    if not _HOST_RE.fullmatch(host):
        return False, f"域名里有非法字符：{host[:40]}", \
            "只填域名本身，不要带编码符号或其它字符", ""

    # ④ 长度：RFC 1035 规定单个 label ≤63、整个域名 ≤253。
    #    放过去只会让 DNS 解析失败，不如在这里给人话。
    if len(host) > 253:
        return False, "域名太长", "整个域名最多 253 个字符", ""
    for label in host.split("."):
        if not label or len(label) > 63:
            return False, "域名分段不合法", \
                "两个点之间的部分不能超过 63 个字符，也不能为空", ""

    try:
        ipaddress.ip_address(host)
        return False, "填的是 IP 地址", "要填域名，不是 IP", ""
    except ValueError:
        pass

    if "." not in host:
        return False, "不是完整域名", "至少两级，例如 example.com", ""

    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False, "这是本地域名", "只填公网域名", ""

    for suffix in _CF_OWN_SUFFIXES:
        if host.endswith(suffix):
            return False, f"这是 Cloudflare 自己的域名（{suffix}）", \
                "请填你自己的域名（托管在你账号下的那个）", ""

    return True, "格式正确", "", host


def _resolve_public_ips(host: str) -> tuple:
    """解析 host 并把结果分成「公网」与「非公网」。

    返回 (公网IP列表, 错误说明)。**有错误说明就代表不能继续** —— 这是 SSRF 防线：
    probe 会向用户填的地址发请求，如果这个地址指向内网，就等于给了调用方
    一个内网扫描器（对抗测试实测 `127.0.0.1.nip.io` 能连通本机 443）。

    判据用 `ipaddress.is_global`：private / loopback / link_local / reserved /
    multicast / unspecified / CGNAT(100.64/10) 全部落在 False。
    """
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return [], ("域名还没有解析记录。去 Cloudflare 加一条 CNAME 指向 "
                    "<隧道ID>.cfargotunnel.com，并打开橙色云朵")
    except Exception as exc:
        return [], f"解析出错：{type(exc).__name__}"

    ips = sorted({str(info[4][0]) for info in infos})
    if not ips:
        return [], "域名没有解析到任何地址"

    public, bad = [], []
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            bad.append(ip)
            continue
        if addr.is_global:
            public.append(ip)
        else:
            bad.append(ip)

    if not public:
        return [], (f"解析到非公网地址：{', '.join(bad[:3])}。"
                    "这个域名指向内网/回环/保留地址，不能作为公网入口")
    return public, ""


def check_dns(host: str) -> tuple:
    """第二步：解析 + 判断是否走 Cloudflare。**仅用于诊断，不阻塞。**

    返回 (ok, detail, hint)。

    真正的「非公网地址」拦截在 _resolve_public_ips，那一步是阻塞的；
    这里只负责把解析结果讲清楚（用户看得懂才排得动障）。

    为什么不要求「必须是 CNAME 到 cfargotunnel.com」：橙云代理会把 CNAME
    **压平**，查询只能拿到 Cloudflare 的 A 记录。按 CNAME 判必然误判。
    """
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, "解析失败", (
            "域名还没有解析记录。去 Cloudflare 加一条 CNAME 指向 "
            "<隧道ID>.cfargotunnel.com，并打开橙色云朵")
    except Exception as exc:
        return False, f"解析出错：{type(exc).__name__}", "检查域名拼写"

    ips = sorted({str(info[4][0]) for info in infos})
    v4 = [ip for ip in ips if ":" not in ip]

    if not v4:
        return False, f"只解析到 IPv6：{', '.join(ips[:3])}", \
            "本机可能没有 IPv6 出口，建议在 Cloudflare 保留 A 记录"

    private = []
    for ip in v4:
        try:
            if not ipaddress.ip_address(ip).is_global:
                private.append(ip)
        except ValueError:
            pass
    if private:
        return False, f"解析到非公网地址：{', '.join(private[:3])}", \
            "这个域名指向内网或保留地址，不能作为公网入口"

    on_cf = [ip for ip in v4
             if any(ipaddress.ip_address(ip) in net for net in _CF_NETS)]
    if on_cf:
        return True, f"解析到 {on_cf[0]}（Cloudflare 边缘）", ""

    return False, f"解析到 {v4[0]}（不是 Cloudflare）", \
        "域名没走 Cloudflare 代理。把 DNS 记录的橙色云朵打开"


def _http_get(url: str, timeout: int = PROBE_TIMEOUT) -> tuple:
    """带重试的 GET。返回 (status, body_bytes, error_kind)。

    error_kind: "" | "timeout" | "conn" | "tls"
    超时与 5xx 重试一次（500ms 间隔），4xx 立即返回 —— 重试也没用。
    """
    last_err = ("", b"")
    for attempt in range(PROBE_RETRIES + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept": "application/json",
        })
        # 不跟随重定向：3xx 说明入口配错了，跟过去只会把错误藏起来
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read()[:4096], ""
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()[:4096]
            except Exception:
                pass
            if 500 <= exc.code < 600 and attempt < PROBE_RETRIES:
                time.sleep(0.5)
                last_err = (f"http{exc.code}", body)
                continue
            return exc.code, body, ""
        except socket.timeout:
            last_err = ("timeout", b"")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                last_err = ("timeout", b"")
            elif "certificate" in str(reason).lower() or "ssl" in str(reason).lower():
                return 0, b"", "tls"
            else:
                last_err = ("conn", str(reason).encode()[:300])
        except Exception as exc:
            last_err = ("conn", str(exc).encode()[:300])

        if attempt < PROBE_RETRIES:
            time.sleep(0.5)
    return 0, last_err[1], last_err[0]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """把 3xx 当普通响应处理，不自动跳转。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _cf_error_hint(status: int, body: bytes) -> tuple:
    """把 Cloudflare 的错误码翻译成用户能照做的话。

    返回 (detail, hint)。detail 里必须带具体码 —— 「Cloudflare 5xx」这种
    笼统说法对排查毫无帮助，用户拿不到能搜的关键词。
    """
    text = ""
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        pass

    match = re.search(r"error code:\s*(\d+)", text, re.IGNORECASE)
    cf_code = match.group(1) if match else ""

    _MAP = {
        "1033": ("隧道未连接",
                 "隧道建好了但没连上。检查 cloudflared 是否在运行，"
                 "或者用下面「启动隧道」按钮把它拉起来"),
        "1034": ("边缘解析失败",
                 "DNS 记录的 CNAME 目标不对，确认指向 <隧道ID>.cfargotunnel.com"),
        "1010": ("请求被指纹拦截",
                 "检测请求被当成爬虫拦了。这不影响 App（Dio 的 UA 不会被拦），"
                 "本次检测结果可以忽略"),
        "1016": ("域名不在本账号",
                 "这个域名没有接入你的 Cloudflare 账号"),
        "526": ("源站证书无效",
                "边缘到源站的证书有问题。把 SSL/TLS 模式改成 Full 或 Flexible"),
        "502": ("隧道通了但源站不通",
                "隧道是通的，但本机 8766 没响应。检查 LANShare 主服务是否在跑"),
        "503": ("隧道通了但源站不通",
                "隧道是通的，但本机 8766 没响应。检查 LANShare 主服务是否在跑"),
        "504": ("源站响应超时",
                "本机服务在但太慢没回。检查磁盘 IO 或服务是否卡住"),
    }

    if cf_code in _MAP:
        title, hint = _MAP[cf_code]
        return f"Cloudflare {cf_code}：{title}", hint

    if status == 526:
        return "HTTP 526：源站证书无效",             "把 Cloudflare 的 SSL/TLS 模式改成 Full（或 Flexible）"
    if status == 530:
        return "HTTP 530：隧道或 DNS 未就绪",             "确认隧道在线、DNS 记录已生效（刚建好要等一两分钟）"

    if status in (502, 504):
        return f"HTTP {status}：隧道到源站失败", \
            "隧道通了但本机服务没响应，检查 LANShare 主服务是否在跑"
    if status == 404:
        return "HTTP 404：路径不存在", \
            "域名通了，但对面不是 LANShare（或者版本太旧没有这个接口）"
    if status in (401, 403):
        return f"HTTP {status}", "对方有响应，但拒绝了这次探测（可能是 WAF 规则）"
    return f"HTTP {status}", ""


def check_https(base_url: str) -> tuple:
    """第三步：从本机请求公网地址，确认链路真的通。**阻塞项。**

    用 /api/auth/state 而不是 /up：后者返回 SPA 首页 HTML，任何一台 LANShare
    都长一样，没法区分实例；/healthz 在公网入口被有意收敛成 404。
    """
    url = base_url.rstrip("/") + "/api/auth/state"
    status, body, err = _http_get(url)

    if err == "timeout":
        return False, "连接超时（15 秒）", \
            "域名连不上。先确认隧道已建好并在线上，其次检查 cloudflared 有没有在跑"
    if err == "tls":
        return False, "TLS 握手失败", \
            "证书有问题。确认橙云已打开、Cloudflare 的证书已签发（通常几分钟）"
    if err == "conn":
        return False, "连接失败", \
            "连不上这个域名。检查 DNS、隧道状态，以及 Cloudflare 的 SSL/TLS 模式"

    if status == 200:
        return True, "证书有效，接口返回 200", ""

    # 401/403：**有响应就说明链路是通的**。
    #
    # 这里原来是死代码 —— 判断写成 `if status in (401, 403) and not hint`，
    # 而 _cf_error_hint 对 401/403 永远返回非空 hint，所以那个分支从来没执行过。
    # 后果：在 Cloudflare 上给 /api/* 加了 WAF 或 Access 规则的域名，链路明明
    # 正常，却永远判「不通」，用户根本存不进去。
    #
    # 注意 1010（UA 被当爬虫）也走这条：它同样只影响我们的探针，不影响 App。
    if status in (401, 403):
        detail, _hint = _cf_error_hint(status, body)
        return True, f"{detail}（链路是通的）", \
            "对方有响应，说明隧道和源站都正常。这个拦截只影响检测脚本，不影响 App"

    detail, hint = _cf_error_hint(status, body)
    return False, detail, hint or "链路不通，按上面的提示排查"


def check_same_instance(base_url: str) -> tuple:
    """第四步：确认这个域名指向的就是本机这套部署。**阻塞项。**

    比对本机与公网的 instance_id。这是整个流程的安全底线 —— 没有它，
    用户填错一个字母就会把 App 指到别人的服务器上。
    """
    local_id = settings.ensure_instance_id()
    url = base_url.rstrip("/") + "/api/instance-id"
    status, body, err = _http_get(url)

    if err:
        return False, "读不到实例标识", "链路不通，先解决上一步"

    if status == 404:
        return False, "对面没有 /api/instance-id", \
            "目标可能不是 LANShare，或者是太旧的版本（需要 4.0.17+）"
    if status != 200:
        detail, hint = _cf_error_hint(status, body)
        return False, detail, hint or "读不到实例标识"

    try:
        remote_id = (json.loads(body.decode("utf-8", "replace"))
                     .get("instance_id") or "")
    except Exception:
        return False, "返回体不是预期的 JSON", "对面可能不是 LANShare"

    if not remote_id:
        return False, "返回体里没有 instance_id", "对面版本过旧"

    if remote_id != local_id:
        return False, "实例标识不一致", (
            "这个域名指向的不是这台机器（是另一套 LANShare）。"
            "检查 DNS 记录指向的隧道，或者你填错了域名")

    return True, f"实例标识一致（{local_id[:8]}…）", ""


# ── 环境检测（全部非阻塞）──────────────────────────────


def _find_cloudflared() -> str:
    """找 cloudflared 可执行文件。找不到返回空串。"""
    found = shutil.which("cloudflared")
    if found:
        return found
    candidates = [
        r"C:\lanshare\bin\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        os.path.expanduser(r"~\cloudflared.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def _windows_task_running(image: str) -> bool:
    """Windows 上查进程是否存在。非 Windows 返回 False（调用方会标 skip）。"""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(
            [_sys_exe("tasklist.exe"), "/FI", f"IMAGENAME eq {image}", "/NH"],
            capture_output=True, text=True, timeout=5,
            # 必须显式指定：中文 Windows 的 tasklist 输出是 GBK，而本机
            # PYTHONUTF8=1 时 text=True 会按 UTF-8 解码，在 reader 线程里
            # 抛 UnicodeDecodeError（异常被吞、结果恒为 False）。
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return image.lower() in (out.stdout or "").lower()
    except Exception:
        return False


def _tun_route_present() -> bool:
    """真 TUN 模式的特征：路由表里有 198.18.0.0/15（Clash 的 fake-ip 段）。

    为什么不能只看进程：Clash 系客户端（clash-verge 等）几乎人人常驻开机自启，
    但 TUN 是可关的开关。按进程判会在绝大多数正常机器上误报红色警告，
    把真正的故障淹没掉 —— 2026-11-18 在本机实测就是「Clash 在跑、TUN 关着、
    隧道好好的」被报了 danger。
    """
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(
            [_sys_exe("route.exe"), "print", "198.18.0.0"],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return "198.18.0.0" in (out.stdout or "")
    except Exception:
        return False


def env_check() -> list:
    """环境检测：四项，全部非阻塞。

    返回 list[dict]，每项带 `level`（"" / warn / danger）供 UI 上色。
    """
    items = []
    is_win = sys.platform == "win32"

    # 1) cloudflared 是否安装
    path = _find_cloudflared()
    if path:
        items.append(_step("env_installed", "cloudflared", True,
                           f"已找到 cloudflared：{path}",
                           blocking=False))
    else:
        items.append(_step("env_installed", "cloudflared", False,
                           "本机没找到 cloudflared",
                           "如果隧道跑在别的机器上可以忽略。本机要跑的话去 "
                           "Cloudflare 下载 cloudflared-windows-amd64.exe",
                           blocking=False, level="warn"))

    # 2) 进程是否在跑
    if not is_win:
        items.append(_step("env_process", "进程", True,
                           "当前平台不支持进程检测（已跳过）", blocking=False))
    elif _windows_task_running("cloudflared.exe"):
        items.append(_step("env_process", "进程", True,
                           "cloudflared 进程正在运行", blocking=False))
    else:
        items.append(_step("env_process", "进程", False,
                           "cloudflared 进程没在跑",
                           "域名大概率连不上。启动隧道客户端，或用「启动隧道」按钮",
                           blocking=False, level="warn"))

    # 3) TUN 模式 —— 按「有没有 TUN 路由」判，不按「代理进程在不在」
    if not is_win:
        items.append(_step("env_tun", "代理", True,
                           "当前平台不支持 TUN 检测（已跳过）", blocking=False))
    else:
        tun_hit = ""
        for image in ("clash-verge.exe", "verge-mihomo.exe",
                      "clash.exe", "mihomo.exe", "Clash for Windows.exe"):
            if _windows_task_running(image):
                tun_hit = image
                break
        route_on = _tun_route_present()

        if route_on:
            # 只有真的存在 fake-ip 路由才给红字 —— 那是百分之百会断的情况
            items.append(_step("env_tun", "代理", False,
                               "检测到 TUN 模式已开启（198.18.0.0/15 路由存在）",
                               "TUN 会把所有 DNS 换成 198.18.x.x 假地址，"
                               "cloudflared 连不到真边缘。先关掉 TUN 再测",
                               blocking=False, level="danger"))
        elif tun_hit:
            # 有代理进程但没 TUN 路由：绝大多数人都是这个状态，不该报警
            items.append(_step("env_tun", "代理", True,
                               f"代理客户端在运行（{tun_hit}），未检测到 TUN 路由",
                               "TCG 模式不影响隧道；只有开 TUN 时才会阻断",
                               blocking=False))
        else:
            items.append(_step("env_tun", "代理", True,
                               "没检测到代理客户端占用", blocking=False))

    return items


# ── 汇总 ────────────────────────────────────────────────


def probe(raw_url: str) -> dict:
    """跑完整检测链，返回托盘直接可渲染的结构。

    {
      "ok": bool,
      "steps": [{key,label,ok,detail,hint,blocking,level}, ...],
      "hint": "失败时的总结建议（人话）"
    }
    """
    url = (raw_url or "").strip().rstrip("/")
    steps = []

    # ① 格式 —— 阻塞
    ok, detail, hint, host = check_format(url)
    steps.append(_step("format", "格式", ok, detail, hint, blocking=True))
    if not ok:
        return {"ok": False, "steps": steps, "hint": hint}

    # 用归一化后的 URL，不要用 raw_url。
    # `https://example.com.` 这种带尾点的写法格式上合法（FQDN 表示法），
    # 但直接拿去做 SNI 会握手失败 —— 用户被引去查证书和橙云，方向完全错。
    # 格式检查已经把 host 归一化（去尾点、转小写），这里重建最稳：
    # 端口已证明是 443 或未写、路径/query/fragment 已证明为空，重建是安全的。
    url = f"https://{host}"

    # ② 解析 —— 指向非公网地址是**阻塞失败**（SSRF 防线）
    #
    # probe 会向用户填的地址发请求。如果这个地址指向内网，等于给了调用方
    # 一个内网扫描器 —— 对抗测试实测 `https://127.0.0.1.nip.io` 能让本机
    # 443 端口收到连接（那个域名解析到 127.0.0.1，但格式检查只挡 IP 字面量）。
    public_ips, resolve_err = _resolve_public_ips(host)
    if resolve_err:
        steps.append(_step("dns", "解析", False, resolve_err,
                           "域名必须指向公网地址，才能作为公网入口",
                           blocking=True))
        return {"ok": False, "steps": steps, "hint": resolve_err}

    # 解析成功后再跑一次诊断版（判断是否走 Cloudflare 代理）
    d_ok, d_detail, d_hint = check_dns(host)
    steps.append(_step("dns", "解析", d_ok, d_detail,
                       d_hint if not d_ok else "", blocking=False,
                       level="" if d_ok else "warn"))

    # ③ 连通 —— 阻塞
    h_ok, h_detail, h_hint = check_https(url)
    steps.append(_step("https", "连通", h_ok, h_detail, h_hint, blocking=True))
    if not h_ok:
        steps.extend(env_check())
        return {"ok": False, "steps": steps, "hint": h_hint}

    # ④ 同源 —— 阻塞
    s_ok, s_detail, s_hint = check_same_instance(url)
    steps.append(_step("same", "同源", s_ok, s_detail, s_hint, blocking=True))

    # ⑤ 环境 —— 非阻塞
    steps.extend(env_check())

    return {"ok": s_ok, "steps": steps, "hint": "" if s_ok else s_hint}
