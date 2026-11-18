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
_CF_OWN_SUFFIXES = (".pages.dev", ".workers.dev", ".r2.dev", ".cfargotunnel.com")

PROBE_TIMEOUT = 15      # 比普通请求宽：慢上行下首次 TLS 握手就要 1-3 秒
PROBE_RETRIES = 1       # 超时 / 5xx 重试一次；4xx 不重试


# ── 单步检测 ────────────────────────────────────────────


def _step(key: str, label: str, ok: bool, detail: str,
          hint: str = "", blocking: bool = True, level: str = "") -> dict:
    """统一的结果项形状。level 只用于非阻塞项：warn（黄） / danger（红）。"""
    return {"key": key, "label": label, "ok": ok, "detail": detail,
            "hint": hint, "blocking": blocking, "level": level}


def check_format(raw: str) -> tuple:
    """第一步：纯字符串校验，不碰网络。

    返回 (ok, detail, hint, host)。
    """
    raw = (raw or "").strip()
    if not raw:
        return False, "地址为空", "请填写 https://你的域名", ""

    parts = urlsplit(raw)

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


def check_dns(host: str) -> tuple:
    """第二步：解析 + 判断是否走 Cloudflare。**仅用于诊断，不阻塞。**

    返回 (ok, detail, hint)。

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
        return False, f"只解析到 IPv6：{', '.join(ips)}", \
            "本机可能没有 IPv6 出口，建议在 Cloudflare 保留 A 记录"

    private = []
    for ip in v4:
        try:
            if ipaddress.ip_address(ip).is_private:
                private.append(ip)
        except ValueError:
            pass
    if private:
        return False, f"解析到内网地址：{', '.join(private)}", \
            "外部访问不了内网地址，检查 DNS 记录"

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

    detail, hint = _cf_error_hint(status, body)
    if status in (401, 403) and not hint:
        # 有响应就说明链路是通的，只是被拦了
        return True, detail, "链路是通的（对方有响应），可以继续"
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
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return image.lower() in (out.stdout or "").lower()
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
        items.append(_step("env_installed", "环境", True,
                           f"已找到 cloudflared：{path}",
                           blocking=False))
    else:
        items.append(_step("env_installed", "环境", False,
                           "本机没找到 cloudflared",
                           "如果隧道跑在别的机器上可以忽略。本机要跑的话去 "
                           "Cloudflare 下载 cloudflared-windows-amd64.exe",
                           blocking=False, level="warn"))

    # 2) 进程是否在跑
    if not is_win:
        items.append(_step("env_process", "环境", True,
                           "当前平台不支持进程检测（已跳过）", blocking=False))
    elif _windows_task_running("cloudflared.exe"):
        items.append(_step("env_process", "环境", True,
                           "cloudflared 进程正在运行", blocking=False))
    else:
        items.append(_step("env_process", "环境", False,
                           "cloudflared 进程没在跑",
                           "域名大概率连不上。启动隧道客户端，或用「启动隧道」按钮",
                           blocking=False, level="warn"))

    # 3) TUN 模式 —— 唯一给红色的一项
    if not is_win:
        items.append(_step("env_tun", "环境", True,
                           "当前平台不支持 TUN 检测（已跳过）", blocking=False))
    else:
        tun_hit = ""
        for image in ("clash-verge.exe", "verge-mihomo.exe",
                      "clash.exe", "mihomo.exe", "Clash for Windows.exe"):
            if _windows_task_running(image):
                tun_hit = image
                break
        if tun_hit:
            items.append(_step("env_tun", "环境", False,
                               f"检测到代理客户端在运行（{tun_hit}）",
                               "如果它的 TUN 模式开着，隧道一定连不上：TUN 会把 "
                               "DNS 换成 198.18.x.x 假地址。先关掉 TUN 再测",
                               blocking=False, level="danger"))
        else:
            items.append(_step("env_tun", "环境", True,
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

    # ② 解析 —— 非阻塞，只做诊断
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
