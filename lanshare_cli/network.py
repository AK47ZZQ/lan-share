"""网络环境探测：找局域网地址、查网络类别、查端口占用。

为什么要单独一个模块：
    这些判断每一处都很容易写错（虚拟网卡、APIPA、多网段），
    而它们决定了「该给用户显示哪个 IP」「该不该警告」。
    集中在一处 + 能单测，比散在 GUI/CLI 里各写一遍靠谱。

踩过的具体坑：
    - 这台机器有 4 个 IPv4：192.168.1.12(以太网) / 192.168.1.13(WLAN) /
      169.254.115.140 / 169.254.140.197 —— 后两个是 APIPA（无 DHCP 时的自分配地址），
      拿它去自检永远失败。
    - 虚拟网卡（VMware / vEthernet / Tailscale / TAP）会混进来，
      它们的 IP 对局域网访问毫无意义。
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
from dataclasses import dataclass, asdict

# PowerShell 输出统一用 UTF-8，避免中文别名在 GBK 下变乱码
_PS_PREFIX = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
_PS_ENV = "chcp 65001 >$null; "


def _ps(script: str, timeout: int = 30) -> str:
    """跑一段 PowerShell，返回 stdout。失败返回空串而不是抛异常 ——
    这些探测失败不应该让整个流程崩，调用方按"查不到"处理即可。"""
    try:
        proc = subprocess.run(
            _PS_PREFIX + [_PS_ENV + script],
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    raw = proc.stdout or b""
    for enc in ("utf-8", "gbk", "utf-16-le"):
        try:
            return raw.decode(enc).strip("\x00").strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip("\x00").strip()


# 虚拟网卡的别名关键词（小写匹配）。这些网卡上的地址不能当局域网地址用。
_VIRTUAL_HINTS = (
    "vmware", "virtualbox", "vethernet", "hyper-v",
    "tailscale", "zerotier", "tap-", "tun", "loopback",
    "bluetooth", "wsl", "docker", "npcap", "vmnet",
)


@dataclass
class LanAddress:
    """一个可用的局域网地址。"""

    ip: str
    alias: str            # 网卡友好名，如「以太网」
    prefix_length: int
    has_gateway: bool     # 有默认网关 = 更可能是用户想连的那个网段
    metric: int           # 路由 metric，越小越优先

    @property
    def url_host(self) -> str:
        return self.ip

    @property
    def is_private(self) -> bool:
        """是否是真正可用的内网地址。

        注意 APIPA（169.254.x.x）比较坑：Python 的 `is_private` 对它返回 True，
        因为它在 IANA 特殊用途地址段里。但它代表「DHCP 没拿到地址」，
        连不通任何东西，绝不能当内网地址用。所以要单独排除。
        """
        try:
            addr = ipaddress.ip_address(self.ip)
        except ValueError:
            return False
        if addr.is_link_local or addr.is_loopback or addr.is_multicast:
            return False
        return addr.is_private

    def as_dict(self) -> dict:
        d = asdict(self)
        d["is_private"] = self.is_private
        return d


def list_lan_addresses() -> list[LanAddress]:
    """列出可作为局域网访问入口的 IPv4 地址，按优先级排序。

    过滤规则（顺序即优先级）：
      1. 排除 127.*（回环）
      2. 排除 169.254.*（APIPA，无 DHCP 时的自分配，连不通任何东西）
      3. 排除虚拟网卡（别名命中 _VIRTUAL_HINTS）
      4. 有默认网关的排在前面
      5. 路由 metric 小的排在前面

    返回空列表 = 当前没有可用网络。
    """
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$out = @()
# 默认路由：记录哪些网卡有网关 + 它们的 metric
$routes = @{}
Get-NetRoute -DestinationPrefix '0.0.0.0/0' | ForEach-Object {
    if (-not $routes.ContainsKey($_.InterfaceAlias)) {
        $routes[$_.InterfaceAlias] = @{ Metric = $_.RouteMetric }
    }
}
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'
} | ForEach-Object {
    $alias = $_.InterfaceAlias
    $gw = $routes.ContainsKey($alias)
    $metric = if ($gw) { $routes[$alias].Metric } else { 99999 }
    $out += [PSCustomObject]@{
        Ip           = $_.IPAddress
        Alias        = $alias
        PrefixLength = $_.PrefixLength
        HasGateway   = $gw
        Metric       = $metric
    }
}
$out | ConvertTo-Json -Compress -Depth 4
"""
    text = _ps(script)
    if not text:
        return []

    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        # ConvertTo-Json 对单元素数组的处理在不同版本有差异，兜底
        return []

    if isinstance(rows, dict):
        rows = [rows]

    result: list[LanAddress] = []
    for row in rows:
        alias = str(row.get("Alias", "")).strip()
        ip = str(row.get("Ip", "")).strip()
        if not ip or not alias:
            continue
        if any(hint in alias.lower() for hint in _VIRTUAL_HINTS):
            continue

        result.append(
            LanAddress(
                ip=ip,
                alias=alias,
                prefix_length=int(row.get("PrefixLength", 24) or 24),
                has_gateway=bool(row.get("HasGateway")),
                metric=int(row.get("Metric", 99999) or 99999),
            )
        )

    # 有网关的优先，然后 metric 小的优先
    result.sort(key=lambda a: (not a.has_gateway, a.metric, a.ip))
    return result


# ──────────────────────────────────────────────────────────────
# 网络类别（公用 / 专用）—— 决定要不要防呆提示
# ──────────────────────────────────────────────────────────────

NETWORK_CATEGORIES = {"Public", "Private", "DomainAuthenticated"}


@dataclass
class NetworkProfile:
    name: str          # 网络名，如「ZZQFttr-2k_5G」
    alias: str         # 网卡名，如「以太网」
    category: str      # Public / Private / DomainAuthenticated

    @property
    def is_public(self) -> bool:
        return self.category == "Public"

    @property
    def is_private(self) -> bool:
        return self.category in ("Private", "DomainAuthenticated")


def list_network_profiles() -> list[NetworkProfile]:
    """列出当前已连接的网络及其类别。空列表 = 没有网络连接。"""
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-NetConnectionProfile | ForEach-Object {
    [PSCustomObject]@{
        Name     = $_.Name
        Alias    = $_.InterfaceAlias
        Category = $_.NetworkCategory.ToString()
    }
} | ConvertTo-Json -Compress -Depth 4
"""
    text = _ps(script)
    if not text:
        return []
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(rows, dict):
        rows = [rows]

    return [
        NetworkProfile(
            name=str(r.get("Name", "")),
            alias=str(r.get("Alias", "")),
            category=str(r.get("Category", "Public")),
        )
        for r in rows
        if r.get("Name")
    ]


def set_network_private(alias: str) -> tuple[bool, str]:
    """把指定网卡的网络类别改成「专用」。

    刻意**按 InterfaceAlias 精确匹配**，不用 -Name（网络显示名可能重名，
    而且改错网络的影响面比不改还大）。

    这个操作需要管理员权限，调用方负责提权。
    返回 (成功?, 消息)。
    """
    if not alias:
        return False, "网卡名为空"
    # 别名来自系统枚举，理论上没有引号，但拼进命令前还是挡一下
    safe = alias.replace("'", "''")
    script = (
        f"Set-NetConnectionProfile -InterfaceAlias '{safe}' "
        f"-NetworkCategory Private -ErrorAction Stop; "
        f"Write-Output 'OK'"
    )
    out = _ps(script)
    if "OK" in out:
        return True, f"已把「{alias}」改成专用网络"
    return False, out or "修改失败（可能没有管理员权限，或被组策略限制）"


# ──────────────────────────────────────────────────────────────
# 端口占用
# ──────────────────────────────────────────────────────────────

@dataclass
class PortOwner:
    port: int
    pid: int
    process_name: str
    process_path: str

    def as_dict(self) -> dict:
        return asdict(self)


def port_owner(port: int) -> PortOwner | None:
    """查某端口正在监听的进程。没被占用返回 None。"""
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$c = Get-NetTCPConnection -LocalPort {int(port)} -State Listen | Select-Object -First 1
if (-not $c) {{ Write-Output 'NONE'; exit }}
$p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
[PSCustomObject]@{{
    Pid  = $c.OwningProcess
    Name = if ($p) {{ $p.ProcessName }} else {{ '?' }}
    Path = if ($p) {{ $p.Path }} else {{ '' }}
}} | ConvertTo-Json -Compress
"""
    text = _ps(script)
    if not text or "NONE" in text:
        return None
    try:
        row = json.loads(text)
    except json.JSONDecodeError:
        return None
    return PortOwner(
        port=port,
        pid=int(row.get("Pid", 0) or 0),
        process_name=str(row.get("Name", "?")),
        process_path=str(row.get("Path", "") or ""),
    )


def is_port_listening(port: int) -> bool:
    return port_owner(port) is not None


# ──────────────────────────────────────────────────────────────
# 监听地址（判断 LAN 开关状态的唯一真相）
# ──────────────────────────────────────────────────────────────

def listen_addresses(port: int) -> list[str]:
    """某端口正在监听的本地地址列表。

    这是判断「局域网开了没」的唯一依据：
        0.0.0.0 / ::  → 所有接口（开了）
        127.0.0.1     → 只有本机（关了）
    """
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
Get-NetTCPConnection -LocalPort {int(port)} -State Listen |
  Select-Object -ExpandProperty LocalAddress
"""
    text = _ps(script)
    if not text:
        return []
    # 每行一个地址，去空、去重、保序
    seen: list[str] = []
    for line in text.splitlines():
        addr = line.strip()
        if addr and addr not in seen:
            seen.append(addr)
    return seen


def listens_on_all_interfaces(port: int) -> bool:
    """是否监听在所有接口上（= 局域网可达的前置条件）。"""
    addrs = listen_addresses(port)
    return any(a in ("0.0.0.0", "::") for a in addrs)


def listens_on_loopback_only(port: int) -> bool:
    """是否只监听回环（= 局域网不可达）。"""
    addrs = listen_addresses(port)
    if not addrs:
        return False
    return all(a in ("127.0.0.1", "::1") for a in addrs)


# ──────────────────────────────────────────────────────────────
# 端口空闲检测（bind 实测，比解析 netstat 可靠）
# ──────────────────────────────────────────────────────────────

def port_free(port: int, host: str = "0.0.0.0") -> bool:
    """尝试 bind 判断端口是否可用。

    用 0.0.0.0 而不是 127.0.0.1：我们要的是「能不能监听所有接口」，
    只测回环会漏掉「别的程序已经占着 0.0.0.0」的情况。
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False
