"""站外事件推送通道（通知 bot）：B2 自 app/api/notify.py 拆出。

SSRF 防护（公网白名单 + 重定向逐跳复校验）+ 有界线程池异步发送。
消费方：notify.py（广播/提醒站外推送）、share.py（分享互动通知）、
healthreport.py（报表推送）、admin.py（配置读写与测试按钮）、B5 备份/水位告警。

**桩点契约**：出网唯一出口是本模块的 `_webhook_open`（原在 notify.py，随实现
搬家；测试 patch 目标已同步）。管理端测试按钮走同步 `webhook_send`，
业务事件走 `push_webhook`（2 worker 池，失败只记审计绝不拖累调用方）。
"""
import ipaddress
import socket
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app.security import audit
from app.settings import NOTIFY_WEBHOOK_URL, get_setting


# 模板占位符 {title}/{body} 会被 URL 编码后替换，GET 请求发出——一套格式通吃
# ntfy（https://ntfy.sh/主题）、Bark（https://api.day.app/key）、Server酱、
# Telegram（bot API sendMessage 的 GET 形式）等常见推送渠道。

def get_webhook_url() -> str:
    return get_setting(NOTIFY_WEBHOOK_URL).strip()


def _ip_blocked(ip: str) -> bool:
    """私网/回环/链路本地/保留/多播/未指定——SSRF 的目标特征集。"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True   # 解析不出的一律当不可信
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
            or a.is_reserved or a.is_unspecified)


def assert_public_webhook_url(url: str) -> None:
    """SSRF 防护（06 审查 P2）：webhook 只允许指向公网地址。

    - scheme 必须 http/https；
    - 主机是**字面 IP** 时直接判私网/保留段（挡住 http://192.168.x / 127.0.0.1 / [::1]）；
    - 主机是**域名**时尽力解析，能解析则要求全部地址都是公网地址
      （挡住"域名指向内网"的常见绕过）；解析失败不阻断——DNS 暂不可用的环境
      仍要允许保存配置，实际出网成败由 urlopen 决定。

    残留风险（有意接受）：不防 DNS rebinding（解析时校验、连接时复解析之间的
    窗口）。webhook 仅 admin 可配置，作为纵深防线足够。
    **重定向已闭环**（11 号复审 P2-4）：发送走 `_safe_opener`，每一跳复校验。
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("webhook 地址需以 http(s):// 开头")
    host = parts.hostname
    if not host:
        raise ValueError("webhook 地址缺少主机名")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except OSError:
        return   # 解析不了：不阻断（见 docstring）
    for info in infos:
        if _ip_blocked(str(info[4][0])):
            raise ValueError("webhook 目标指向内网/保留地址，已拒绝")


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向逐跳复校验（11 号复审 P2-4）。

    `urlopen` 默认**静默跟随 302**——只在发送前校验初始 URL 等于没校验终点：
    一个合法的公网域名只要 302 到 `http://127.0.0.1/...` 即可绕过 SSRF 防线。
    这里在每一跳真正发起请求前重新跑一遍 `assert_public_webhook_url()`，
    非公网目标直接拒绝（抛 ValueError，由调用方按 webhook 失败处理）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_public_webhook_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# 走自定义 opener：urlopen 的默认 opener 不校验重定向目标
_safe_opener = urllib.request.build_opener(_ValidatingRedirectHandler)


def _webhook_open(req, timeout: float = 6):
    """出网发送的唯一出口。

    独立成函数有两个目的：① 是 SSRF 逐跳校验的落点；② 是**测试桩点**——
    此前测试 patch 的是 `urllib.request.urlopen`，那是 stdlib 全局，改动实现方式
    就会静默绕过桩点（本质是"桩在错误的接缝上"）。把接缝挪到本模块自己的名字上，
    测试与实现之间就有了显式契约。
    """
    return _safe_opener.open(req, timeout=timeout)


def webhook_send(url: str, title: str, body: str = "") -> None:
    """同步发送（管理端测试按钮用，异常向上抛）。"""
    final = (url.replace("{title}", urllib.parse.quote_plus(title))
                .replace("{body}", urllib.parse.quote_plus(body)))
    assert_public_webhook_url(final)
    req = urllib.request.Request(final, headers={"User-Agent": "LANShare-webhook/1.0"})
    with _webhook_open(req, timeout=6) as resp:
        resp.read(64)   # 读完一小段确保送达，随即关闭


_webhook_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webhook-push")


def push_webhook(title: str, body: str = "") -> None:
    """业务事件出口：有界线程池（2 worker）发送，失败只记审计、绝不阻塞/影响请求方；
    突发事件在池的默认无界队列中排队（Python 标准库无有界队列，极端洪峰靠
    限速/低频业务语义兜底）。"""
    url = get_webhook_url()
    if not url:
        return

    def _run():
        try:
            webhook_send(url, title, body)
        except Exception as e:
            audit("webhook_fail", title[:60], str(e)[:120])

    _webhook_pool.submit(_run)
