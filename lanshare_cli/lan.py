"""局域网访问开关：状态机 + 开启/关闭/诊断。

核心设计（为什么简单到只有一个开关动作）：

    **开关 = 改 Caddy 的监听地址，不是改防火墙。**

    开：Caddy 绑 0.0.0.0:8765  + 防火墙规则存在  → 局域网可达
    关：Caddy 绑 localhost:8765                  → 只有本机能访问

    防火墙规则加一次就留着，永远不删（卸载时除外）。理由：
    Caddy 只听回环时，那个被放行的端口上根本没东西可连，规则形同虚设。
    带来的好处是**关闭不需要 UAC、不改任何系统设置** —— 只改自己的
    配置文件 + 重启自己的服务。

为什么是四态而不是「开/关」：

    LAN_BLOCKED（绑了所有接口但自检不通）是最需要帮助的状态。
    把它混进「已开启」等于告诉用户「成了」，结果手机连不上。

所有外部依赖（网络探测、防火墙、服务控制）都通过参数注入，
单测里可以完全替换掉，不需要真防火墙也不需要真服务。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

from . import caddyfile, firewall, network


class LanState(str, Enum):
    """局域网访问的五种状态。"""

    UNINSTALLED = "uninstalled"    # 没装 / 服务不存在
    NO_NETWORK = "no_network"      # 装好了但没有可用网络
    OFF = "off"                    # 只监听回环 —— 只有本机能访问
    ON = "on"                      # 监听所有接口 + 自检通过
    BLOCKED = "blocked"            # 监听所有接口但自检失败（异常，需诊断）

    @property
    def label(self) -> str:
        return {
            LanState.UNINSTALLED: "未安装",
            LanState.NO_NETWORK: "无网络",
            LanState.OFF: "未开启（仅本机）",
            LanState.ON: "已开启",
            LanState.BLOCKED: "已开启但连不上",
        }[self]


# ──────────────────────────────────────────────────────────────
# 安装信息（端口、路径）—— 从注册表读，命令行参数兜底
# ──────────────────────────────────────────────────────────────

REG_PATH = r"HKLM\SOFTWARE\LANShare"


def _resolve_caddyfile_from_service(service_name: str) -> Path | None:
    """从 Caddy 服务的启动参数里解析出 --config 指向的配置文件。

    NSSM 服务的参数长这样：
        AppDirectory  = C:\lan-share
        AppParameters = run --config Caddyfile --adapter caddyfile

    所以配置路径 = AppDirectory / (--config 后面的值)。
    值可能是相对路径，也可能是绝对路径，两种都要处理。

    返回 None 表示解析不出来（服务不存在、没有 --config 参数等）。
    """
    import re as _re
    import subprocess

    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$p = 'HKLM:\SYSTEM\CurrentControlSet\Services\{service_name}\Parameters'
if (Test-Path $p) {{
    $item = Get-ItemProperty $p
    Write-Output ("DIR=" + $item.AppDirectory)
    Write-Output ("ARGS=" + $item.AppParameters)
}}
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    raw = proc.stdout or b""
    text = ""
    for enc in ("utf-8", "gbk", "utf-16-le"):
        try:
            text = raw.decode(enc).strip("")
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return None

    app_dir = ""
    args = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("DIR="):
            app_dir = line[4:].strip()
        elif line.startswith("ARGS="):
            args = line[5:].strip()

    if not args:
        return None

    # 匹配 --config <path> 或 --config=<path>（路径可能带引号，可能没有）
    m = _re.search(r'--config(?:=|\s+)(?:"([^"]+)"|(\S+))', args)
    if not m:
        return None
    cfg = m.group(1) or m.group(2)
    if not cfg:
        return None

    cfg_path = Path(cfg)
    if not cfg_path.is_absolute():
        if not app_dir:
            return None
        cfg_path = Path(app_dir) / cfg_path

    return cfg_path


@dataclass
class InstallInfo:
    root: Path
    data_dir: Path
    api_port: int
    web_port: int
    service_prefix: str

    # Caddyfile 路径解析结果的缓存（从服务参数读，可能有点慢，只查一次）
    _caddyfile_cache: Path | None = field(default=None, repr=False, compare=False)

    @property
    def caddyfile_path(self) -> Path:
        """Caddy 实际读的配置文件路径。

        **不能想当然用 root/conf/Caddyfile**：这个项目的老部署把 Caddyfile
        放在安装根目录，里面还有 FRP 隧道、自签证书等一堆自定义配置。
        猜错位置的后果不是"改了不生效"那么轻——如果我们按模板重写，
        会把用户辛苦维护的整份配置**覆盖掉**。

        真相在服务的 AppParameters 里：
            run --config Caddyfile --adapter caddyfile
        配合 AppDirectory 拼出绝对路径。
        """
        if self._caddyfile_cache is not None:
            return self._caddyfile_cache

        resolved = _resolve_caddyfile_from_service(self.caddy_service)
        if resolved is not None:
            self._caddyfile_cache = resolved
            return self._caddyfile_cache

        # 服务读不到（未安装 / 测试态）：退回约定位置
        self._caddyfile_cache = self.root / "conf" / "Caddyfile"
        return self._caddyfile_cache

    @property
    def api_service(self) -> str:
        return f"{self.service_prefix}-api"

    @property
    def caddy_service(self) -> str:
        return f"{self.service_prefix}-caddy"


def read_registry(key: str = "LANShare") -> dict[str, str]:
    """读 HKLM\\SOFTWARE\\<key>。读不到返回空字典（非安装态）。

    key 默认 LANShare（正式安装）。测试实例必须用独立键（如 LANShareE2E），
    否则 e2e 安装会把生产配置一起改掉 —— 2026-10-31 真实踩过：跑完 e2e 冒烟，
    生产注册表的 PgPort 被写成 9955，服务却还连着 5433。
    """
    import subprocess

    if not key or any(c in key for c in "\\'\"; "):
        raise ValueError(f"非法的注册表键名: {key!r}")

    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$k = 'HKLM:\SOFTWARE\%s'
if (Test-Path $k) {
    $p = Get-ItemProperty $k
    # InstallFolder 是安装器写的名字；Install 是早期版本/文档里出现过的名字，两者都认
    $install = if ($p.InstallFolder) { $p.InstallFolder } else { $p.Install }
    [PSCustomObject]@{
        InstallFolder = $install
        DataFolder    = $p.DataFolder
        ApiPort       = $p.ApiPort
        WebPort       = $p.WebPort
        Prefix        = $p.ServicePrefix
        # PgPassword / PgPort 必须一起返回：安装时靠 PgPassword 沿用旧密码，
        # 漏了它每次 install 都生成新密码，已建好的 PG 角色直接锁死。
        # （2026-10-31 查出来的：这个字段一直没被读出来过。）
        PgPort        = $p.PgPort
        PgPassword    = $p.PgPassword
        Version       = $p.Version
    } | ConvertTo-Json -Compress
}
""" % key
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}

    raw = proc.stdout or b""
    text = ""
    for enc in ("utf-8", "gbk", "utf-16-le"):
        try:
            text = raw.decode(enc).strip("\x00").strip()
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return {}
    try:
        row = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return {k: str(v) for k, v in row.items() if v is not None}


def detect_service_prefix(candidates: list[str] | None = None) -> str | None:
    """探测本机上实际存在的 LAN Share 服务前缀。

    为什么需要：默认前缀是 `lanshare`，但已有的部署（包括这台机器）
    用的是 `lan-share`。硬编码任一个都会让另一半环境失灵。

    做法：拿候选前缀去查服务是否存在，命中就返回。
    """
    if candidates is None:
        candidates = ["lanshare", "lan-share", "LANShare", "LAN-Share"]

    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-Service | Where-Object { $_.Name -like '*-caddy' } | Select-Object -ExpandProperty Name
"""
    import subprocess

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None

    existing: list[str] = []
    if proc is not None:
        raw = proc.stdout or b""
        for enc in ("utf-8", "gbk", "utf-16-le"):
            try:
                text = raw.decode(enc).strip("\x00")
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        existing = [line.strip() for line in text.splitlines() if line.strip()]

    for name in existing:
        prefix = name[: -len("-caddy")]
        if prefix in candidates:
            return prefix

    if existing:
        # 有 caddy 服务但前缀不在候选里 —— 照样用那个前缀，别自作主张
        return existing[0][: -len("-caddy")]

    return None


def load_install_info(
    root: str | Path | None = None,
    api_port: int | None = None,
    web_port: int | None = None,
    service_prefix: str | None = None,
    data_dir: str | Path | None = None,
) -> InstallInfo:
    """确定安装信息。

    优先级：显式参数 > 注册表 > 自动探测 > 默认值。
    这样同一个 CLI 既能管安装版（读注册表），
    也能管开发态 / 测试态（全用参数），不互相干扰。
    """
    reg = read_registry()

    resolved_root = root or reg.get("InstallFolder") or Path.cwd()

    # 服务前缀：显式 > 注册表 > 本机实际存在的服务 > 默认
    resolved_prefix = service_prefix or reg.get("Prefix")
    if not resolved_prefix:
        resolved_prefix = detect_service_prefix() or "lanshare"

    resolved_data = data_dir or reg.get("DataFolder") or (Path(resolved_root) / "data")

    def _int(value, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    return InstallInfo(
        root=Path(resolved_root),
        data_dir=Path(resolved_data),
        api_port=_int(api_port if api_port is not None else reg.get("ApiPort"), 8766),
        web_port=_int(web_port if web_port is not None else reg.get("WebPort"), 8765),
        service_prefix=resolved_prefix,
    )


# ──────────────────────────────────────────────────────────────
# 自检
# ──────────────────────────────────────────────────────────────

def probe_healthz(url: str, timeout: float = 3.0, insecure: bool = False) -> tuple[bool, str]:
    """请求 /healthz，返回 (通?, 说明)。

    insecure=True 时跳过证书校验 —— 家用自签证书必须这样，
    否则永远报 SSL 错误，用户没法区分「服务挂了」和「证书没认」。
    """
    import ssl

    context = None
    if insecure and url.startswith("https://"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as resp:
            body = resp.read(300).decode("utf-8", errors="replace")
            if resp.status == 200 and '"status"' in body:
                return True, "ok"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        if "certificate" in reason.lower() or "ssl" in reason.lower():
            return False, f"证书错误：{reason}"
        return False, f"连接失败：{reason}"
    except (TimeoutError, OSError) as exc:
        return False, f"超时或网络错误：{exc}"


def candidate_urls(host: str, port: int, use_https: bool | None = None) -> list[str]:
    """生成自检用的候选 URL。

    为什么要多个候选：同一个程序在不同部署下协议可能不同 ——
    老部署用了自签 HTTPS，新安装包默认纯 HTTP。硬编码任一个都会
    在另一半环境里误报「连不上」。

    use_https=True  → 只试 https
    use_https=False → 只试 http
    use_https=None  → 两个都试（默认，按 http 优先，因为新安装包是 http）
    """
    if use_https is True:
        return [f"https://{host}:{port}/healthz"]
    if use_https is False:
        return [f"http://{host}:{port}/healthz"]
    return [
        f"http://{host}:{port}/healthz",
        f"https://{host}:{port}/healthz",
    ]


def probe_lan(
    host: str,
    port: int,
    windows: int = 3,
    interval: float = 1.0,
    use_https: bool | None = None,
) -> tuple[bool, str, str]:
    """带重试的局域网自检。返回 (通?, 说明, 成功的URL)。

    为什么要重试：防火墙规则刚加/服务刚重启时，网络栈生效有延迟，
    第一次探测失败是正常的。重试 3 次（间隔 1 秒）能过滤掉瞬时失败。

    协议自适应：HTTP 和 HTTPS 都试，谁通用谁 —— 见 candidate_urls 的说明。
    """
    urls = candidate_urls(host, port, use_https)
    last_msg = "未执行"
    last_url = urls[0]

    for attempt in range(1, windows + 1):
        for url in urls:
            last_url = url
            ok_flag, msg = probe_healthz(url, insecure=True)
            if ok_flag:
                return True, "ok", url
            last_msg = f"{msg} @ {url}"
        if attempt < windows:
            time.sleep(interval)

    return False, last_msg, last_url


# ──────────────────────────────────────────────────────────────
# 状态查询
# ──────────────────────────────────────────────────────────────

@dataclass
class LanStatus:
    state: LanState
    web_port: int
    api_port: int

    # 三个维度的明细，诊断时逐条展示
    listening: list[str] = field(default_factory=list)
    bind_host: str = ""
    firewall: str = ""            # ok / missing / stale
    caddy_exe: str = ""

    # 网络
    addresses: list[dict] = field(default_factory=list)
    profiles: list[dict] = field(default_factory=list)

    # 自检
    probe_ok: bool = False
    probe_msg: str = ""
    probe_url: str = ""

    hints: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["state_label"] = self.state.label
        return d

    def best_address(self) -> dict | None:
        """最适合展示给用户的局域网地址（有网关 + metric 最小）。"""
        return self.addresses[0] if self.addresses else None

    def url_for(self, addr: dict) -> str:
        """展示给用户/生成二维码用的地址。

        必须写 https —— 3.72 起 Caddy 只监听 HTTPS，拼 http 的话用户在
        浏览器里输入这个地址会得到一片空白（Caddy 不接明文）。

        2026-11-01 沙箱实测：`lan status` 的「地址」行显示
        http://192.0.2.14:8765，而下面一行自检却是
        https://192.0.2.14:8765/healthz → ok。同一个块里两个协议，
        用户当然会照着上面那行点。
        """
        return f"https://{addr['ip']}:{self.web_port}"


def collect_status(
    info: InstallInfo,
    *,
    services: object | None = None,
    skip_probe: bool = False,
) -> LanStatus:
    """收集当前局域网访问状态。只读，无副作用。

    ## 为什么要并发

    这里面有五次「启动子进程问系统」的调用：

        firewall.service_image_path()    → sc.exe 查服务路径
        network.listen_addresses()       → netstat/GetActiveTcpListeners
        firewall.rule_state()            → PowerShell 查防火墙规则
        network.list_lan_addresses()     → PowerShell 枚举网卡
        network.list_network_profiles()  → PowerShell 查网络类别

    每次起 PowerShell 在慢机器上 1~3 秒，串行就是 5~15 秒。GUI 每刷新一次
    等这么久，用户看到的就是「状态更新很卡」。2026-11-02 改成按依赖分三批，
    批内并发：

        批 1（并行）：service_image_path + listen_addresses
        批 2（并行）：rule_state + list_lan_addresses + list_network_profiles
        批 3       ：probe_lan（依赖批 2 选出的地址）

    实测从十几秒降到 2~3 秒（取决于最慢的那一个）。

    线程池只在这里短命存在，不复用连接、不持有资源 —— 每次 status 一个新池，
    退出即销毁，不会像常驻池那样在长跑系统里积累线程。
    """
    from concurrent.futures import ThreadPoolExecutor

    status = LanStatus(
        state=LanState.UNINSTALLED,
        web_port=info.web_port,
        api_port=info.api_port,
    )

    with ThreadPoolExecutor(max_workers=4) as pool:
        # ── 批 1：服务路径 + 监听地址（互不依赖） ──
        f_exe = pool.submit(firewall.service_image_path, info.caddy_service)
        f_listen = pool.submit(network.listen_addresses, info.web_port)
        caddy_exe = f_exe.result()
        status.caddy_exe = caddy_exe
        status.listening = f_listen.result()

        if not caddy_exe:
            status.hints.append(f"找不到服务「{info.caddy_service}」，可能未安装")
            return status

        if not status.listening:
            # ⚠️ 端口枚举不到 ≠ 服务没在跑（2026-11-02 第三台实机）。
            #
            # Caddy 开了 HTTP/3（UDP 8765）+ 内部 socket 池，这台机器上
            # Get-NetTCPConnection 和 netstat 都拿不到 8765 的 LISTENING 行
            # —— netstat 只给了一条 `0.0.0.0:8765 → 0.0.0.0:0 CLOSED`。
            # 而 https 请求完全正常（healthz 每 5 秒 200、手机扫码能登录）。
            # 结果 CLI 判「未安装」，界面显示「局域网未开启」、二维码消失，
            # 用户看到的就是「明明能用，界面说没开」。
            #
            # 判据换成「真连一下」：能从局域网地址连上就是开着。
            addrs_probe = network.list_lan_addresses()

            # 调用方明确说「别探测」（enable 成功后只是要打印一行地址）时
            # 不要为了这个再等好几秒 —— 那时服务刚自检过，肯定是开的。
            if skip_probe:
                # 🔴 必须 as_dict()。status.addresses 的声明是 list[dict]，
                # 下游（lan.py 的 stepss/JSON 组装）一律按 a["ip"] 取值。
                # 直接塞 LanAddress 对象会让那条兜底路径本身崩掉 ——
                # 而它恰恰是为「端口枚举异常」准备的，等于是安全网自己破了。
                status.addresses = [a.as_dict() for a in addrs_probe]
                if addrs_probe:
                    status.listening = [addrs_probe[0].ip]
                    status.bind_host = caddyfile.BIND_ALL
                    status.state = LanState.ON
                return status

            for addr in addrs_probe[:2]:
                ok_p, msg_p, url_p = probe_lan(addr.ip, info.web_port, windows=3)
                if ok_p:
                    status.listening = [addr.ip]
                    status.bind_host = caddyfile.BIND_ALL
                    status.state = LanState.ON
                    status.probe_ok = True
                    status.probe_msg = msg_p
                    status.probe_url = url_p
                    status.addresses = [a.as_dict() for a in addrs_probe]
                    status.hints.append(
                        "端口枚举不到监听记录（Windows socket 查询异常），"
                        f"但实测 {url_p} 可连，按开启处理"
                    )
                    return status

            status.state = LanState.UNINSTALLED
            status.hints.append(
                f"端口 {info.web_port} 没有监听且连不上，Caddy 服务可能没在运行"
            )
            return status

        # 2. 监听地址（判断开/关的唯一依据）
        #
        # ⚠️ 判据是「有没有非回环地址在监听」，不是「有没有绑 0.0.0.0」。
        # v3.90 起站点块把每个网卡 IP 分别列进去（证书 SAN 需要），Caddy 于是
        # 给每个 IP 建独立 socket，永远不会出现通吃地址 —— 只认 0.0.0.0 的话，
        # 明明开着也会被判成没开（2026-11-02 实机）。
        if network.listens_on_lan(info.web_port):
            status.bind_host = caddyfile.BIND_ALL
            status.state = LanState.ON          # 待自检确认
        elif network.listens_on_loopback_only(info.web_port):
            status.bind_host = caddyfile.BIND_LOOPBACK
            status.state = LanState.OFF
        else:
            # 端口在监听、却既没非回环地址也不全是回环 —— 理论上不可达，
            # 留着当兜底：按“开着但状态可疑”处理，让它走自检暴露问题。
            status.bind_host = status.listening[0]
            status.state = LanState.ON

        # ── 批 2：防火墙 + 网卡 + 网络类别（互相独立，都依赖批 1 的 caddy_exe） ──
        f_fw = pool.submit(firewall.rule_state, info.web_port, caddy_exe)
        f_addrs = pool.submit(network.list_lan_addresses)
        f_profiles = pool.submit(network.list_network_profiles)

        status.firewall = f_fw.result()
        addrs = f_addrs.result()
        profiles = f_profiles.result()

    status.addresses = [a.as_dict() for a in addrs]
    status.profiles = [
        {"name": p.name, "alias": p.alias, "category": p.category}
        for p in profiles
    ]

    if not status.addresses:
        status.state = LanState.NO_NETWORK
        status.hints.append("当前没有可用的局域网连接（网卡未连网或只有虚拟网卡）")
        return status

    # 5. 自检：状态为 ON 时才需要验证「真的连得上吗」
    if status.state == LanState.ON and not skip_probe:
        best = status.best_address()
        if best:
            ok_flag, msg, used_url = probe_lan(best["ip"], info.web_port)
            status.probe_url = used_url
            status.probe_ok = ok_flag
            status.probe_msg = msg
            if not ok_flag:
                status.state = LanState.BLOCKED
                status.hints.extend(_diagnose_blocked(status))
            elif status.firewall != "ok":
                # 自检通了但我们的规则不在 —— 说明是别的机制在放行
                # （旧版留下的规则、或防火墙整体关闭）。
                # 这不算问题，但要如实说明，免得用户以为是我们配的。
                status.hints.append(
                    "当前可访问，但 LAN Share 的防火墙规则不存在 —— "
                    "可能是旧版本留下的规则或系统防火墙已关闭。"
                    "重新执行 enable 会补建我们自己的规则。"
                )

    return status


def _diagnose_blocked(status: LanStatus) -> list[str]:
    """LAN_BLOCKED 时的原因排查清单，按概率排序。"""
    hints: list[str] = []

    # 公用网络是最常见的原因：规则只对 Private/Domain 生效
    public_names = [p["name"] for p in status.profiles if p.get("category") == "Public"]
    if public_names:
        hints.append(
            f"当前网络被识别为「公用」，防火墙规则不覆盖公用网络 —— "
            f"网络：{'、'.join(public_names)}。可以改成专用网络后重试。"
        )

    if status.firewall == "missing":
        hints.append("防火墙规则不存在 —— 用 allow-lan 重新开启一次")
    elif status.firewall == "stale":
        hints.append("防火墙规则形态不符（可能被手动改过或被禁用）—— 重新开启会自动重建")

    if not any(p.get("category") in ("Private", "DomainAuthenticated") for p in status.profiles):
        hints.append("没有任何专用/域网络 —— 局域网访问需要至少一个专用网络")

    hints.append("如果手机和电脑不在同一个 WiFi，或者路由器开了 AP 隔离，也会连不上")

    return hints


# ──────────────────────────────────────────────────────────────
# 开关
# ──────────────────────────────────────────────────────────────

@dataclass
class ActionReport:
    success: bool
    steps: list[tuple[str, bool, str]] = field(default_factory=list)
    cancelled: bool = False
    # 公网接入用：接好之后该填进界面的完整地址（GUI 直接拿去填输入框）
    public_url: str = ""

    def add(self, name: str, ok_flag: bool, detail: str = "") -> None:
        self.steps.append((name, ok_flag, detail))

    def as_dict(self) -> dict:
        return {
            "success": self.success,
            "cancelled": self.cancelled,
            "public_url": self.public_url,
            "steps": [
                {"name": n, "ok": o, "detail": d} for n, o, d in self.steps
            ],
        }


def _restart_service(service_name: str, *, dry_run: bool = False) -> tuple[bool, str]:
    """重启一个 Windows 服务（stop + start）。改自己的服务，不需要提权。"""
    if dry_run:
        return True, f"[dry-run] 将重启服务 {service_name}"

    import subprocess

    for verb in ("stop", "start"):
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"$ErrorActionPreference='Stop'; {verb.title()}-Service -Name '{service_name}' "
                 f"-ErrorAction Stop"],
                capture_output=True,
                timeout=90,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{verb} 服务失败：{exc}"
        if proc.returncode != 0:
            raw = (proc.stderr or b"") + (proc.stdout or b"")
            msg = raw.decode("utf-8", errors="replace").strip() or f"exit {proc.returncode}"
            return False, f"{verb} 服务失败：{msg}"
    return True, f"服务 {service_name} 已重启"


def _is_ours(caddyfile_content: str) -> bool:
    """判断这份 Caddyfile 是不是 lanshare_cli 生成的。

    我们的模板第一行固定写 `# 由 lanshare_cli 生成`。
    没有这个标记说明是用户手写/历史遗留的配置 —— 里面可能有
    FRP 隧道、自签证书、备用端口等一堆我们不知道的东西。

    **绝不能整份重写**：那等于把用户辛苦维护的配置删掉。
    """
    for line in caddyfile_content.splitlines()[:3]:
        if "由 lanshare_cli 生成" in line:
            return True
    return False


def _rewrite_bind_in_place(content: str, bind_host: str, web_port: int) -> tuple[str, bool]:
    """只改站点块的监听方式，其他内容原样保留。

    返回 (新内容, 是否成功)。找不到目标站点块就返回 (原内容, False)。

    ⚠️ 用的是 bind 指令，不是改首行 —— 改首行对 Caddy 无效（见 caddyfile.py 说明）。

    三种情况的处理：
        1. 要绑回环，块内没有 bind  → 在第一行之后插入 bind 127.0.0.1
        2. 要绑回环，块内已有 bind  → 把地址替换成 127.0.0.1
        3. 要绑所有接口，块内有 bind 127.0.0.1 → 删掉这一行
    """
    import re as _re

    # 找到 :web_port { 的站点块起始位置
    open_re = _re.compile(r"^[ \t]*:" + str(web_port) + r"\s*\{[^\n]*$", _re.MULTILINE)
    m = open_re.search(content)
    if not m:
        # 也接受 0.0.0.0:web_port { 这种写法（用户自定义配置里可能有）
        open_re2 = _re.compile(
            r"^[ \t]*(?:0\.0\.0\.0|127\.0\.0\.1):" + str(web_port) + r"\s*\{[^\n]*$",
            _re.MULTILINE,
        )
        m = open_re2.search(content)
        if not m:
            return content, False

    block_start = m.end()      # 站点块首行的行尾
    # 找匹配的收尾 }（简单起见：从块首往下找第一个行首的 }）
    rest = content[block_start:]
    close_m = _re.search(r"^\}[ \t]*$", rest, _re.MULTILINE)
    if not close_m:
        return content, False
    block_body = rest[: close_m.start()]

    # 块内现有的 bind 行
    bind_re = _re.compile(r"^[ \t]*bind\s+([^\s#]+)[ \t]*$", _re.MULTILINE)
    existing = bind_re.search(block_body)

    if bind_host == caddyfile.BIND_LOOPBACK:
        if existing:
            # 有 bind 就替换地址
            new_body = bind_re.sub(f"\tbind {caddyfile.BIND_LOOPBACK}", block_body, count=1)
        else:
            # 没有就在块首插一行
            new_body = f"\n\tbind {caddyfile.BIND_LOOPBACK}" + block_body
    else:
        if existing and existing.group(1).strip() in ("127.0.0.1", "::1", "localhost"):
            # 要开放：删掉那条只绑回环的 bind。
            # ⚠️ 必须连同换行一起删，否则留下一行空行，反复开关会让文件越来越长。
            new_body = _re.sub(
                r"^[ \t]*bind\s+" + _re.escape(existing.group(1)) + r"[ \t]*\n?",
                "",
                block_body,
                count=1,
                flags=_re.MULTILINE,
            )
        else:
            # 本来就是开放的，或 bind 到别的地址（不是我们能判断的），不动
            new_body = block_body

    new_content = content[:block_start] + new_body + rest[close_m.start():]
    return new_content, True


def _write_caddyfile_and_restart(
    info: InstallInfo,
    bind_host: str,
    report: ActionReport,
    *,
    dry_run: bool = False,
) -> bool:
    """改 Caddyfile 并重启 Caddy。失败时自动回滚到旧配置。

    两条安全规则（都在 2026-10-31 踩过）：

    1. **不认识的配置不动**。如果现有的 Caddyfile 没有我们的标记，
       说明是用户手写的（这个项目的老部署里有 FRP 隧道、自签证书等
       一大坨自定义内容）。整份重写等于把那些配置删掉。
       这种情况下只改监听地址那一行，其余原样保留。

    2. **改坏了要能回来**。写之前备份，重启失败自动回滚。
    """
    target = info.caddyfile_path
    backup = target.with_suffix(target.suffix + ".lanbak")

    old_content: str | None = None
    if target.is_file():
        try:
            old_content = target.read_text(encoding="utf-8")
            if not dry_run:
                backup.write_text(old_content, encoding="utf-8", newline="")
        except OSError as exc:
            report.add("备份 Caddyfile", False, str(exc))
            return False

    # 决定怎么写
    try:
        if old_content is not None and not _is_ours(old_content):
            # 用户自定义配置：只改监听那一行
            new_content, ok_flag = _rewrite_bind_in_place(old_content, bind_host, info.web_port)
            if not ok_flag:
                report.add(
                    "更新 Caddyfile",
                    False,
                    f"{target} 是自定义配置，且找不到端口 {info.web_port} 的站点块。"
                    f"为了不改坏它，这里不自动修改 —— 请手动把站点块改成 "
                    f"{caddyfile.site_block(bind_host, info.web_port)} {{",
                )
                return False
            if not dry_run:
                target.write_text(new_content, encoding="utf-8", newline="")
            mode = (
                f"bind {caddyfile.BIND_LOOPBACK}（仅本机）"
                if bind_host == caddyfile.BIND_LOOPBACK
                else "不 bind（所有网卡）"
            )
            report.add("更新 Caddyfile", True, f"自定义配置，只改站点块的 bind → {mode}")
        else:
            # 我们的配置或全新创建：整份重写
            if not dry_run:
                caddyfile.write(
                    target, info.api_port, info.web_port, bind_host,
                    storage_dir=str(info.root / "certs" / "caddy"),
                    web_root=str(info.root / "web" / "dist"),
                    updates_root=str(info.root / "updates"),
                )
            report.add(
                "更新 Caddyfile",
                True,
                f"监听地址 → {caddyfile.site_block(bind_host, info.web_port)}",
            )
    except (OSError, ValueError) as exc:
        report.add("更新 Caddyfile", False, str(exc))
        return False

    # 重启服务
    ok_flag, msg = _restart_service(info.caddy_service, dry_run=dry_run)
    report.add("重启 Caddy 服务", ok_flag, msg)
    if not ok_flag:
        # 回滚：把旧内容写回去 + 再重启一次。第二次重启的结果必须上报
        #（2026-11-04 值守批修：原实现把返回值丢掉，回滚成功与否全靠猜）
        if old_content is not None and not dry_run:
            try:
                target.write_text(old_content, encoding="utf-8", newline="")
                ok2, msg2 = _restart_service(info.caddy_service)
                report.add("回滚配置", ok2,
                           "已恢复原 Caddyfile 并重启" if ok2
                           else f"配置已恢复原样，但重启仍失败：{msg2}")
            except OSError as exc:
                report.add("回滚配置", False, f"回滚也失败了：{exc}")
        return False

    return True


def _make_profiles_private(
    aliases: list[str], *, dry_run: bool = False
) -> tuple[bool, str]:
    """把指定网卡上的网络类别改成「专用」。

    为什么需要这个：家用 WiFi 被 Windows 判成「公用网络」极其常见 —— 换过
    路由器、首次连接、网关 MAC 变了都会。以前的做法是直接拒绝开启局域网，
    用户看到的只有一句「启动失败」，真正的原因埋在 steps 里没人看。

    2026-11-02 主人指令：别拦，直接改。改不动也不许拦（上层会继续开，
    并让防火墙规则一并覆盖公用网络）。

    需要管理员权限（改的是机器级设置），走 UAC 提权。
    """
    names = [a for a in aliases if a]
    if not names:
        return False, "没有需要修改的网卡"
    if dry_run:
        return True, "[dry-run] " + "、".join(names)

    quoted = ", ".join("'" + a.replace("'", "''") + "'" for a in names)
    script = f"""
$ErrorActionPreference = 'Continue'
$ok = @(); $bad = @()
foreach ($a in @({quoted})) {{
    try {{
        Set-NetConnectionProfile -InterfaceAlias $a -NetworkCategory Private -ErrorAction Stop
        $ok += $a
    }} catch {{
        $bad += ($a + '：' + $_.Exception.Message)
    }}
}}
if ($ok.Count -gt 0) {{ Write-Output ('OK:' + ($ok -join '、')) }}
if ($bad.Count -gt 0) {{ Write-Output ('FAIL:' + ($bad -join '；')) }}
"""
    try:
        _code, out = firewall.run_elevated(script, timeout=120)
    except Exception as exc:                      # 改不动不该拖垮整个流程
        return False, f"调用提权进程失败：{exc}"

    if "OK:" in out:
        return True, out.split("OK:", 1)[1].splitlines()[0].strip()
    if "FAIL:" in out:
        return False, out.split("FAIL:", 1)[1].splitlines()[0].strip()[:200]
    return False, (out or "无法修改网络类别（可能取消了 UAC 授权）").strip()[:200]


# ──────────────────────────────────────────────────────────────
# 公网接入（内网穿透）
# ──────────────────────────────────────────────────────────────

def parse_tunnel_addr(raw: str) -> tuple[str, int | None]:
    """把用户填的隧道地址解析成 (域名, 公网端口)。

    穿透工具给的地址形式各家不同，别让用户背语法：

        cn-bj.frp.one:35123
        https://cn-bj.frp.one:35123
        cn-bj.frp.one
        https://cn-bj.frp.one:35123/some/path      ← 路径丢掉
    """
    from urllib.parse import urlsplit

    s = (raw or "").strip()
    if not s:
        return "", None
    if "://" not in s:
        s = "https://" + s
    try:
        u = urlsplit(s)
    except ValueError:
        return "", None
    return (u.hostname or "").strip().lower(), u.port


def probe_tunnel_host(
    info: InstallInfo, host: str, *, timeout: float = 5.0
) -> tuple[bool, str]:
    """用「SNI=域名、目标=本机 8765」验证 Caddy 认不认这个域名。

    这是唯一可靠的判据。穿透隧道把公网流量原样送进本机 8765，效果等价于
    这里做的：连 127.0.0.1:8765，但 TLS 的 SNI 和 HTTP 的 Host 都填域名。
    站点块没加这个域名、或者证书还没签好，都会在这里失败。
    """
    import socket
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # 只看「认不认」，证书信任交给客户端裁决

    try:
        raw = socket.create_connection(("127.0.0.1", info.web_port), timeout=timeout)
    except OSError as exc:
        return False, f"连不上本机 {info.web_port}：{exc}"

    try:
        with ctx.wrap_socket(raw, server_hostname=host) as ssock:
            req = f"GET /up HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            ssock.sendall(req.encode("ascii"))
            data = ssock.recv(256)
        first = data.split(b"\r\n", 1)[0].decode("latin-1", "replace").strip()
        if not first:
            return False, "服务端没有响应（站点块可能没匹配到这个域名）"
        return " 200" in first, first
    except Exception as exc:                     # 握手失败/超时都按「不认」处理
        # Caddy 对未知 SNI 是直接断连（客户端侧 SEC_E_INTERNAL_ERROR），
        # 所以这里失败基本等于「站点块里没有这个域名」
        return False, f"TLS 握手失败：{exc}"
    finally:
        try:
            raw.close()
        except Exception:
            pass


def probe_public_url(host: str, port: int, *, timeout: float = 6.0) -> tuple[bool, str]:
    """从**公网地址**验证端到端可用，而不是只连本机。

    为什么必须单独有这一步（2026-11-04 实机）：

    原来的「域名自检」连的是 `127.0.0.1:<web_port>`，SNI 填域名 —— 那只回答
    「Caddy 认不认这个域名」，和「外面的人能不能访问」是两件事。实测就撞上了：
    自检报 200 成功，用户从公网访问却 TLS 直接断。

    返回 (ok, 说明)。失败时尽量说清楚是哪一种失败，因为用户看到「接入失败」
    四个字根本不知道下一步该干嘛。

    探的是 `/up` 而不是 `/healthz`：后者在公网入口被 asgi.py 有意收敛成 404
    （磁盘水位不外泄）。用 healthz 当判据，会在公网其实已经通了的时候报失败。
    """
    import socket
    import ssl

    # ── 第一步：TLS 握手（正常情况应该拿到我们 Caddy 的证书）──
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE     # 只验「能通 + 是我们在应答」，信任交客户端
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return False, f"连不上 {host}:{port}（{exc}）—— 隧道没开或地址/端口不对"

    tls_err = ""
    try:
        with ctx.wrap_socket(raw, server_hostname=host) as ssock:
            req = f"GET /up HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            ssock.sendall(req.encode("ascii"))
            data = ssock.recv(256)
        first = data.split(b"\r\n", 1)[0].decode("latin-1", "replace").strip()
        if first:
            return (" 200" in first), first
        tls_err = "服务端没有返回内容"
    except Exception as exc:
        tls_err = f"{type(exc).__name__}: {exc}"

    # ── 第二步：TLS 失败时，发明文 HTTP 探一下对面到底是谁 ──
    #
    # 这一步专门用来识别「隧道建成了 HTTPS 类型」这个高频坑：那类隧道由
    # 穿透服务端自己接管 TLS，需要你在服务商面板上传证书；我们没有（也不该）
    # 把自己的自签 CA 交出去，于是它拿不到证书，握手直接断，HTTP 则回 501。
    # 用户看到的只是「接入失败」，根本联想不到要去改隧道类型。
    hint = ""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(f"GET /up HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
                      .encode("ascii"))
            head = s.recv(512).decode("latin-1", "replace")
        server = ""
        status = ""
        for line in head.splitlines()[:8]:
            low = line.lower()
            if low.startswith("server:"):
                server = line.split(":", 1)[1].strip()
            elif line.startswith("HTTP/"):
                status = line.strip()
        if "sakurafrp" in server.lower() or server.lower().startswith("frp"):
            hint = (f"（{server} 直接回了 {status or '非 TLS 响应'}："
                    "这条隧道大概率建成了「HTTPS/HTTP 类型」，"
                    "请到穿透面板把它改成 **TCP 类型**，"
                    "本地地址 127.0.0.1、本地端口填 Web 端口）")
        elif status:
            hint = f"（明文探测得到：{status}，Server={server or '未知'}）"
    except Exception:
        hint = "（明文探测也没有响应：隧道可能没转发到本机）"

    return False, f"TLS 握手失败：{tls_err}{hint or ''}"


def set_public_host(
    info: InstallInfo, raw_addr: str, *, dry_run: bool = False
) -> ActionReport:
    """把内网穿透的域名接进 Caddy 站点块，让公网域名能真正访问。

    🔴 为什么必须动 Caddyfile：
    Caddy 只对站点块里写明的**主机名**签证书。穿透工具给的是一个陌生域名，
    不在那个名单里 —— 客户端用这个域名访问时 TLS 握手就被直接掐断
    （2026-11-03 实测：SEC_E_INTERNAL_ERROR / HTTP 000），连 HTTP 层都到不了。
    所以「在界面里填一下地址」远远不够，得先让 Caddy 认这个域名。

    这一步只改自己的配置文件（没有我们的标记就拒绝），不碰防火墙、
    不需要 UAC、也不依赖局域网开关（隧道从本机连 127.0.0.1 就够）。
    """
    report = ActionReport(success=False)

    host, pub_port = parse_tunnel_addr(raw_addr)
    if not host:
        report.add(
            "解析地址",
            False,
            f"看不懂这个地址：{raw_addr!r}（应形如 cn-bj.frp.one:35123）",
        )
        return report
    try:
        caddyfile._safe_host(host)               # 域名要拼进配置行，必须防注入
    except ValueError as exc:
        report.add("解析地址", False, str(exc))
        return report
    report.add(
        "解析地址",
        True,
        f"域名 {host}" + (f"（公网端口 {pub_port}）" if pub_port else ""),
    )

    target = info.caddyfile_path
    if not target.is_file():
        report.add("检查配置", False, f"找不到配置文件 {target}")
        return report
    try:
        old = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.add("检查配置", False, str(exc))
        return report
    if not _is_ours(old):
        report.add(
            "检查配置",
            False,
            f"{target.name} 不是本程序生成的（缺标记），不敢自动改。"
            f"请手工把 https://{host}:{info.web_port} 加进站点地址行。",
        )
        return report

    # 保持用户当前的局域网开关状态，只把域名追加进主机名列表
    bind_host = (
        caddyfile.detect_bind_host(target, info.web_port) or caddyfile.BIND_LOOPBACK
    )
    hosts = caddyfile.default_hosts()
    if host not in hosts:
        hosts.append(host)

    public_url = f"https://{host}:{pub_port or info.web_port}"
    report.public_url = public_url

    if dry_run:
        report.add("更新 Caddyfile", True, f"[dry-run] 站点地址会包含 {host}")
        report.success = True
        return report

    try:
        target.with_suffix(target.suffix + ".wanbak").write_text(
            old, encoding="utf-8", newline=""
        )
    except OSError:
        pass                                  # 备份失败不阻断，回滚靠内存里的 old

    try:
        caddyfile.write(
            target,
            info.api_port,
            info.web_port,
            bind_host,
            hosts,
            storage_dir=str(info.root / "certs" / "caddy"),
            web_root=str(info.root / "web" / "dist"),
            updates_root=str(info.root / "updates"),
            # 用户在这一刻明确指定了新的公网域名 —— 旧的该被替换掉。
            # 保留旧的会让「换域名」变成「加域名」，而旧的再也删不掉。
            keep_existing_domains=False,
            # 一键接入就是要用隧道，隧道端口必须开（自动判断在这条路上不可靠：
            # 第一次接入时文件里既没有域名也没有 8767 条目）。
            tunnel_port=caddyfile.TUNNEL_PORT,
        )
    except (OSError, ValueError) as exc:
        report.add("更新 Caddyfile", False, str(exc))
        return report
    report.add("更新 Caddyfile", True, f"站点地址已加入 {host}")

    ok_flag, msg = _restart_service(info.caddy_service)
    report.add("重启 Caddy 服务", ok_flag, msg)
    if not ok_flag:
        # 回滚：恢复旧配置 + 再重启。第二次重启的结果必须上报
        #（2026-11-04 值守批修：原实现把返回值丢掉，回滚成功与否全靠猜）
        try:
            target.write_text(old, encoding="utf-8", newline="")
            ok2, msg2 = _restart_service(info.caddy_service)
            report.add("回滚配置", ok2,
                       "已恢复原 Caddyfile 并重启" if ok2
                       else f"配置已恢复原样，但重启仍失败：{msg2}")
        except OSError as exc:
            report.add("回滚配置", False, f"回滚也失败了：{exc}")
        return report

    # ── ① 本地验证：Caddy 认不认这个域名（证书 SAN 里有没有它）──
    ok_cert, last = False, ""
    for _ in range(12):                       # 最多等约 18 秒
        ok_cert, last = probe_tunnel_host(info, host, timeout=4.0)
        if ok_cert:
            break
        time.sleep(1.5)

    report.add(
        "域名已加入证书",
        ok_cert,
        f"本地 SNI={host} → {last}"
        + ("" if ok_cert else "（本机 Caddy 还不认这个域名，看上面的 TLS 报错）"),
    )
    if not ok_cert:
        return report

    # ── ② 公网验证：从外面的地址真的连一次 ──
    #
    # 🔴 这一步才是「接入成功」的判据（2026-11-04 实机修正）。
    #
    # 原来这里只有 ①，而 ① 连的是 127.0.0.1:<web_port>、只是把 SNI 填成域名，
    # 它证明的是「本机 Caddy 认识这个名字」—— 和「外面的人能不能访问」是三件
    # 不同的事：隧道类型对不对、公网端口对不对、转发目标是不是本机 Web 端口。
    #
    # 实测撞上的正是这个：① 报 200「成功」，用户从公网访问 TLS 直接断，
    # 因为那条隧道建成了 HTTPS 类型（由穿透服务端接管 TLS，需要上传证书）。
    # 界面显示接入成功、实际完全不通，这比报错糟糕得多。
    ok_pub, msg_pub = probe_public_url(host, pub_port or info.web_port)
    report.add("公网连通性", ok_pub, msg_pub)
    if not ok_pub:
        # 配置本身是写对了的（Caddy 已认域名），所以把地址带出去，
        # 让界面能说成「已配置但公网还不通」而不是「配置失败」。
        return report

    report.success = True
    return report


def enable_lan(
    info: InstallInfo,
    *,
    dry_run: bool = False,
    allow_public: bool = False,
) -> ActionReport:
    """开启局域网访问。

    步骤：
      1. 前置检查（服务存在、端口在听、有可用网络）
      2. 网络类别检查（公用网络需要 allow_public=True 才继续）
      3. 防火墙规则（缺了才加，需要 UAC）
      4. 改 Caddyfile + 重启 Caddy
      5. 局域网自检

    allow_public: 历史参数，保留只为兼容 CLI 的 --allow-public 开关。
        2026-11-02 起「公用网络」不再拦人：enable_lan 会先尝试自动把网络
        改成专用（Set-NetConnectionProfile），改不动也照常继续，并让防火墙
        规则一并覆盖 Public。所以这个参数目前不影响行为。
    """
    report = ActionReport(success=False)

    # ── 1. 前置检查 ──
    caddy_exe = firewall.service_image_path(info.caddy_service)
    if not caddy_exe:
        report.add("检查服务", False, f"找不到服务「{info.caddy_service}」，请先安装")
        return report
    report.add("检查服务", True, caddy_exe)

    addrs = network.list_lan_addresses()
    if not addrs:
        report.add("检查网络", False, "没有可用的局域网连接")
        return report
    report.add("检查网络", True, f"可用地址：{', '.join(a.ip for a in addrs[:3])}")

    # ── 2. 网络类别 ──
    #
    # 2026-11-02 主人指令：**别再拿「公用网络」拦人**。
    #
    # 家用 WiFi 被 Windows 判成「公用网络」太常见（换过路由器、首次连接、
    # 网关 MAC 变了都会）。以前直接 return 失败，用户只看到一句「启动失败」，
    # 真实原因藏在 steps 里没人看 —— 2026-11-02 实机正是这样。
    #
    # 现在两条腿走路：
    #   a) 先自动把公用网络改成专用（Set-NetConnectionProfile，走 UAC）
    #   b) 改不动也不拦 —— 继续开，并让防火墙规则一并覆盖公用网络
    profiles = network.list_network_profiles()
    public = [p for p in profiles if p.is_public]
    private = [p for p in profiles if p.is_private]

    if public and not private:
        fixed, net_msg = _make_profiles_private(
            [p.alias or p.name for p in public], dry_run=dry_run
        )
        if fixed:
            report.add("网络类别", True, f"公用网络已自动改为专用：{net_msg}")
            public = []                     # 规则按专用网络建即可
        else:
            report.add(
                "网络类别",
                True,
                f"仍是公用网络（{net_msg}）；照常继续，防火墙规则将一并覆盖公用网络",
            )
    else:
        report.add(
            "网络类别",
            True,
            f"专用：{'、'.join(p.name for p in private) or '无'}"
            + (f"；公用：{'、'.join(p.name for p in public)}" if public else ""),
        )

    # ── 3. 防火墙规则 ──
    # 还有公用网络没改成专用时，规则必须覆盖 Public，否则手机照样连不上。
    ok_flag, msg = firewall.ensure_rule(
        info.web_port, caddy_exe, dry_run=dry_run, include_public=bool(public)
    )
    report.add("防火墙规则", ok_flag, msg)
    if not ok_flag:
        if "取消" in msg:
            report.cancelled = True
        return report

    # ── 4. 改监听地址 ──
    if not _write_caddyfile_and_restart(
        info, caddyfile.BIND_ALL, report, dry_run=dry_run
    ):
        return report

    if dry_run:
        report.add("自检", True, "[dry-run] 跳过实际探测")
        report.success = True
        return report

    # ── 5. 自检 ──
    time.sleep(1.0)  # 给 Caddy 一点启动时间
    best = addrs[0]
    probe_ok, probe_msg, used_url = probe_lan(best.ip, info.web_port)
    report.add("局域网自检", probe_ok, f"{used_url} → {probe_msg}")

    if not probe_ok:
        # 服务起不来的可能性最大：检查端口还在不在监听
        if not network.is_port_listening(info.web_port):
            report.add("端口检查", False, f"{info.web_port} 没有监听，Caddy 可能启动失败")
        elif public:
            report.add("提示", False, "当前有公用网络，防火墙可能挡住了")
        else:
            report.add("提示", False, "手机连不上时检查是否同一 WiFi、路由器是否开了 AP 隔离")
        return report

    report.success = True
    return report


def disable_lan(info: InstallInfo, *, dry_run: bool = False) -> ActionReport:
    """关闭局域网访问。

    **不删防火墙规则、不需要 UAC、不改任何系统设置** ——
    只把 Caddy 的监听地址改回 127.0.0.1 并重启它自己的服务。
    """
    report = ActionReport(success=False)

    if not firewall.service_image_path(info.caddy_service):
        report.add("检查服务", False, f"找不到服务「{info.caddy_service}」")
        return report

    if not _write_caddyfile_and_restart(
        info, caddyfile.BIND_LOOPBACK, report, dry_run=dry_run
    ):
        return report

    if dry_run:
        report.add("验证", True, "[dry-run] 跳过实际探测")
        report.success = True
        return report

    time.sleep(1.0)

    # 验证一：已经没有非回环地址在监听了（＝局域网真的断开）
    if network.listens_on_lan(info.web_port):
        report.add(
            "验证监听",
            False,
            f"端口 {info.web_port} 仍在局域网地址上监听："
            f"{', '.join(network.listen_addresses(info.web_port))}",
        )
        return report

    # 端口枚举不到时不能直接算「关掉了」—— 同一台机器上枚举 API 本来就
    # 看不到 Caddy 的监听（见 collect_status 里的长注释）。实连一次确认。
    if not network.listen_addresses(info.web_port):
        for addr in network.list_lan_addresses()[:2]:
            still_ok, _msg, _url = probe_lan(addr.ip, info.web_port, windows=2)
            if still_ok:
                report.add(
                    "验证监听",
                    False,
                    f"枚举不到监听但实测仍能从 {addr.ip} 连上，局域网没关掉",
                )
                return report

    report.add("验证监听", True, "只监听回环地址")

    # 验证二：本机还能用（关掉局域网不该影响本机访问）
    local_ok, local_msg, _ = probe_lan("127.0.0.1", info.web_port, windows=2)
    report.add("验证本机访问", local_ok, local_msg)
    if not local_ok:
        return report

    report.success = True
    return report


# ──────────────────────────────────────────────────────────────
# 诊断
# ──────────────────────────────────────────────────────────────

def diagnose(info: InstallInfo) -> dict:
    """完整诊断：把判断局域网可达性需要的每一环都查一遍。"""
    status = collect_status(info)

    items: list[dict] = []

    # 服务
    caddy_exe = firewall.service_image_path(info.caddy_service)
    items.append({
        "check": "Caddy 服务",
        "ok": bool(caddy_exe),
        "detail": caddy_exe or f"服务 {info.caddy_service} 不存在",
    })

    # 端口监听
    addrs = network.listen_addresses(info.web_port)
    items.append({
        "check": f"端口 {info.web_port} 监听",
        "ok": bool(addrs),
        "detail": ", ".join(addrs) if addrs else "没有监听",
    })

    # 防火墙
    rules = firewall.find_rules(info.web_port)
    items.append({
        "check": "防火墙规则",
        "ok": status.firewall == "ok",
        "detail": (
            "规则正确（仅专用/域网络）"
            if status.firewall == "ok"
            else f"状态：{status.firewall}"
            + (f"，发现 {len(rules)} 条相关规则" if rules else "")
        ),
    })

    # 网络
    items.append({
        "check": "局域网地址",
        "ok": bool(status.addresses),
        "detail": ", ".join(a["ip"] for a in status.addresses) or "无",
    })

    # 网络类别
    cats = [
        f"{p['name']}={p['category']}" for p in status.profiles
    ]
    has_private = any(p.get("category") in ("Private", "DomainAuthenticated")
                      for p in status.profiles)
    items.append({
        "check": "网络类别",
        "ok": has_private,
        "detail": ", ".join(cats) or "无网络",
    })

    # 自检
    if status.probe_url:
        items.append({
            "check": "局域网自检",
            "ok": status.probe_ok,
            "detail": f"{status.probe_url} → {status.probe_msg}",
        })

    return {
        "state": status.state.value,
        "state_label": status.state.label,
        "web_port": info.web_port,
        "api_port": info.api_port,
        "checks": items,
        "hints": status.hints,
        "addresses": status.addresses,
        "urls": [f"https://{a['ip']}:{info.web_port}" for a in status.addresses],
    }
