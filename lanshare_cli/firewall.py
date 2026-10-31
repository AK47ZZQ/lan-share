"""防火墙规则管理 + 提权执行。

核心设计（这三条决定了整个 LAN 功能的安全性和可维护性）：

1. **规则绑 -Program，路径从服务配置动态读**
   服务名（lanshare-caddy）是不变锚点，它的 ImagePath 里永远是最新的
   caddy.exe 路径。用户把程序搬家了，下次 enable 自动发现不一致并更正，
   不需要任何硬编码。

2. **规则只加一次，永久保留；开关靠 Caddy 监听地址**
   关闭局域网时不删规则 —— 因为 Caddy 只监听 127.0.0.1 时，
   规则放行的那个端口上根本没东西可连，规则形同虚设。
   好处：日常开关不需要 UAC，不改任何系统设置。
   只有卸载时才删规则（还原系统）。

3. **提权用 -EncodedCommand，不落临时文件**
   写 .ps1 到临时目录再执行，会被杀软当可疑行为拦。
   base64(UTF-16LE) 直接传给 -EncodedCommand，全程不落盘。

为什么不删规则也不担心安全问题：
   防火墙规则 = 「允许访问 8765 端口的入站」。但只有 Caddy 监听
   0.0.0.0:8765 时这个放行才有意义。关掉时 Caddy 只听 127.0.0.1，
   外部连接连 TCP 握手都到不了。风险等价于没有这条规则。
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass, asdict

RULE_PREFIX = "LAN Share"          # 所有我们创建的规则都以这个开头

# ⚠️ PowerShell 5.1 的坑：`Get-NetFirewallRule -DisplayName X -Direction Inbound`
#    会报「无法使用指定的命名参数解析参数集」（ByName 与 ByFilter 互斥），
#    而且 $ErrorActionPreference='SilentlyContinue' 会把这个错误吞掉，
#    表现成「规则明明存在却查不到」—— 排查了很久才定位。
#    正确写法：只按 DisplayName 查，方向用 Where-Object 过滤。

RULE_DESCRIPTION = "LAN Share 局域网访问（由 LAN Share 管理）"


# ──────────────────────────────────────────────────────────────
# 提权执行
# ──────────────────────────────────────────────────────────────

def _encode_ps(script: str) -> str:
    """PowerShell -EncodedCommand 要求 UTF-16LE + base64。"""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def run_elevated(script: str, timeout: int = 120) -> tuple[int, str]:
    """执行需要管理员权限的 PowerShell 脚本，返回 (exit_code, output)。

    **两个必须处理的情况**：

    1. 已经是管理员 → 直接跑，不弹 UAC。
       安装器本身就以管理员运行，这时候再弹一次 UAC 是多余的骚扰。

    2. 不是管理员 → 提权执行。这里有个 PowerShell 的坑：
       `-Verb RunAs` 和 `-RedirectStandardOutput` **属于互斥的参数集**，
       一起用会报 `AmbiguousParameterSet`。所以提权时让子进程
       自己把输出写进临时文件，父进程等它结束再读。

    用户取消 UAC 时返回空输出 + 非零退出码，调用方据此判断「取消」
    而不是「失败」—— 两者的用户文案完全不同。
    """
    import os

    encoded = _encode_ps(script)

    # ── 情况 1：已有管理员权限，直接跑 ──
    if is_elevated():
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 1, "操作超时"
        except OSError as exc:
            return 1, f"无法启动 PowerShell: {exc}"
        return proc.returncode, (_decode(proc.stdout) + _decode(proc.stderr)).strip()

    # ── 情况 2：需要提权 ──
    out_file = _tmp_out()
    if os.path.exists(out_file):
        try:
            os.remove(out_file)
        except OSError:
            pass

    # 子进程把脚本输出写到文件；父进程只负责等待和读取
    child_script = f"""
$ErrorActionPreference = 'Continue'
try {{
    & {{
{script}
    }} | Out-String | Set-Content -Path '{out_file}' -Encoding UTF8
}} catch {{
    ('ERROR:' + $_.Exception.Message) | Set-Content -Path '{out_file}' -Encoding UTF8
}}
"""
    encoded_child = _encode_ps(child_script)

    launcher = (
        f"$p = Start-Process -FilePath 'powershell.exe' "
        f"-ArgumentList '-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',"
        f"'-EncodedCommand','{encoded_child}' "
        f"-Verb RunAs -Wait -PassThru -WindowStyle Hidden; "
        f"exit $p.ExitCode"
    )

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", launcher],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, "操作超时（可能卡在 UAC 授权窗口）"
    except OSError as exc:
        return 1, f"无法启动提权进程: {exc}"

    # 读子进程写下的输出
    output = ""
    if os.path.exists(out_file):
        try:
            with open(out_file, "r", encoding="utf-8-sig", errors="replace") as fh:
                output = fh.read().strip()
        except OSError:
            pass
        finally:
            try:
                os.remove(out_file)
            except OSError:
                pass

    if not output and proc.returncode != 0:
        # 无输出 + 失败 —— 多半是用户取消了 UAC
        err = _decode(proc.stderr)
        return proc.returncode, err.strip()

    return proc.returncode, output


def _tmp_out() -> str:
    """提权子进程的输出落点。放在用户临时目录，用完即删。"""
    import tempfile
    import os

    return os.path.join(tempfile.gettempdir(), "lanshare_elevated.out")


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    for enc in ("utf-8", "gbk", "utf-16-le"):
        try:
            return raw.decode(enc).strip("\x00")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip("\x00")


def is_elevated() -> bool:
    """当前进程是否已有管理员权限。"""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - 拿不到就按「没有」处理
        return False


# ──────────────────────────────────────────────────────────────
# 规则查询（只读，不需要提权）
# ──────────────────────────────────────────────────────────────

@dataclass
class FirewallRule:
    display_name: str
    enabled: bool
    action: str
    profiles: list[str]
    local_ports: list[str]
    program: str

    def as_dict(self) -> dict:
        return asdict(self)

    def matches(self, port: int, exe_path: str) -> bool:
        """是否符合我们期望的形态：端口对、程序对、允许、已启用、不含 Public。

        这是「规则是否需要重建」的判断依据。任何一项不符都重建，
        因为规则被手动改过的话，我们无法预判它的实际效果。
        """
        if not self.enabled or self.action != "Allow":
            return False
        if str(port) not in self.local_ports:
            return False
        if "Public" in self.profiles:
            return False
        if exe_path and self.program:
            # 路径比较忽略大小写和正反斜杠
            a = self.program.replace("/", "\\").lower()
            b = exe_path.replace("/", "\\").lower()
            if a != b:
                return False
        return True


def find_rules(port: int | None = None) -> list[FirewallRule]:
    """列出所有我们创建的防火墙规则（按 DisplayName 前缀识别）。

    **为什么不用 JSON 传数据**：`Get-NetFirewallRule` 返回的是 CIM 对象，
    对它（以及 `Select-Object` 的结果）执行 `ConvertTo-Json` 会**静默返回空字符串**
    —— 退出码还是 0，看不出任何错误。2026-10-31 实测：普通 PSCustomObject
    的 JSON 序列化完全正常，唯独防火墙规则对象是空。

    所以改用 TSV：每行一条规则，字段用制表符分隔，Python 侧 split 解析。
    字段顺序固定，解析失败就跳过该行（不让一条脏数据毁掉整个查询）。
    """
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$rules = Get-NetFirewallRule -DisplayName '{RULE_PREFIX}*' |
    Where-Object {{ $_.Direction.ToString() -eq 'Inbound' }}
foreach ($r in $rules) {{
    $pf = $r | Get-NetFirewallPortFilter
    $af = $r | Get-NetFirewallApplicationFilter

    # 数组字段展开成逗号分隔的字符串，避免嵌套结构
    $ports = (@($pf.LocalPort) | Where-Object {{ $_ -and $_ -ne 'Any' }}) -join ','
    $profs = (@($r.Profile.ToString() -split ',') | ForEach-Object {{ $_.Trim() }}) -join ','

    $enabled = if ($r.Enabled.ToString() -eq 'True') {{ '1' }} else {{ '0' }}
    $name = $r.DisplayName -replace "`t", ' '
    $action = $r.Action.ToString()
    $program = if ($af.Program) {{ $af.Program }} else {{ '' }}

    Write-Output ("$name`t$enabled`t$action`t$profs`t$ports`t$program")
}}
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    text = _decode(proc.stdout)
    if not text:
        return []

    result: list[FirewallRule] = []
    for line in text.splitlines():
        line = line.strip("\r")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            continue

        name, enabled, action, profs, ports, program = parts[:6]
        rule = FirewallRule(
            display_name=name.strip(),
            enabled=(enabled.strip() == "1"),
            action=action.strip(),
            profiles=[p for p in (x.strip() for x in profs.split(",")) if p],
            local_ports=[p for p in (x.strip() for x in ports.split(",")) if p],
            program=program.strip(),
        )
        if port is not None and str(port) not in rule.local_ports:
            continue
        result.append(rule)
    return result


def rule_state(port: int, exe_path: str) -> str:
    """规则对我们的期望形态的满足程度。

    返回：
        "ok"        — 存在且形态正确
        "missing"   — 不存在
        "stale"     — 存在但形态不对（路径变了 / 被禁用 / 被改成 Public / 端口不符）
    """
    rules = find_rules(port)
    if not rules:
        return "missing"
    for rule in rules:
        if rule.matches(port, exe_path):
            return "ok"
    return "stale"


# ──────────────────────────────────────────────────────────────
# 规则写入（需要提权）
# ──────────────────────────────────────────────────────────────

def ensure_rule(port: int, exe_path: str, dry_run: bool = False) -> tuple[bool, str]:
    """确保存在一条符合期望的规则。已存在且正确则什么都不做。

    返回 (成功?, 消息)。用户取消 UAC 时返回 (False, "已取消")。

    幂等：先删掉所有同名/同端口前缀规则再建新的 —— 避免出现多条冲突规则。
    """
    state = rule_state(port, exe_path)
    if state == "ok":
        return True, "防火墙规则已存在且正确，无需改动"

    if dry_run:
        action = "创建" if state == "missing" else "重建（现有规则形态不符）"
        return True, f"[dry-run] 将{action}防火墙规则：TCP {port} → {exe_path}"

    script = rf"""
$ErrorActionPreference = 'Stop'
try {{
    # 1. 清掉我们之前建的所有规则（幂等，避免累积冲突规则）
    Get-NetFirewallRule -DisplayName '{RULE_PREFIX}*' -ErrorAction SilentlyContinue |
        Where-Object {{ $_.Direction.ToString() -eq 'Inbound' }} |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue

    # 2. 建新规则
    #    Profile 只含 Private,Domain —— 绝不含 Public。
    #    公用网络（咖啡厅/机场 WiFi）下不开放，这是安全底线。
    New-NetFirewallRule `
        -DisplayName '{RULE_PREFIX} (TCP {port})' `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort {int(port)} `
        -Action Allow `
        -Profile Private,Domain `
        -Program '{exe_path}' `
        -Description '{RULE_DESCRIPTION}' `
        -ErrorAction Stop | Out-Null

    Write-Output 'OK'
}}
catch {{
    Write-Output ('ERROR:' + $_.Exception.Message)
}}
"""
    code, out = run_elevated(script)

    if "OK" in out:
        return True, f"防火墙规则已就绪（TCP {port}，仅专用/域网络）"

    if "ERROR:" in out:
        err = out.split("ERROR:", 1)[1].strip()
        return False, f"创建防火墙规则失败：{err}"

    if not out.strip() or code != 0:
        # 没有输出 + 非零退出码，最常见的是用户点了 UAC 的「否」
        return False, "已取消（需要管理员权限才能配置防火墙）"

    return False, out.strip()


def remove_rules(dry_run: bool = False) -> tuple[bool, str]:
    """删除我们创建的所有规则。卸载时调用。

    删不存在的规则不报错，所以可以无脑执行。
    """
    if dry_run:
        return True, f"[dry-run] 将删除所有「{RULE_PREFIX}」防火墙规则"

    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$before = @(Get-NetFirewallRule -DisplayName '{RULE_PREFIX}*' -ErrorAction SilentlyContinue |
    Where-Object {{ $_.Direction.ToString() -eq 'Inbound' }}).Count
Get-NetFirewallRule -DisplayName '{RULE_PREFIX}*' -ErrorAction SilentlyContinue |
    Where-Object {{ $_.Direction.ToString() -eq 'Inbound' }} |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
$after = @(Get-NetFirewallRule -DisplayName '{RULE_PREFIX}*' -ErrorAction SilentlyContinue |
    Where-Object {{ $_.Direction.ToString() -eq 'Inbound' }}).Count
Write-Output "REMOVED:$($before - $after)"
"""
    code, out = run_elevated(script)
    if "REMOVED:" in out:
        count = out.split("REMOVED:", 1)[1].strip().split()[0] if out.split("REMOVED:", 1)[1].strip() else "0"
        return True, f"已清理 {count} 条防火墙规则"
    if not out.strip():
        return False, "已取消（需要管理员权限）"
    return False, out.strip()


# ──────────────────────────────────────────────────────────────
# 从服务配置里读 exe 路径（路径自适应的关键）
# ──────────────────────────────────────────────────────────────

def service_image_path(service_name: str) -> str:
    """读出服务真正运行的 exe 路径。

    **NSSM 服务的坑**：`Win32_Service.PathName` 返回的是 `nssm.exe` 的路径，
    不是它代理的那个程序。真正的 Application 在注册表里：

        HKLM\\SYSTEM\\CurrentControlSet\\Services\\<name>\\Parameters\\Application

    2026-10-31 实测：`lan-share-caddy` 的 PathName 是
    `C:\\Users\\Administrator\\.openclaw\\nssm\\nssm.exe`，
    而 Application 才是 `C:\\lan-share\\bin\\caddy.exe`。
    只看 PathName 会把防火墙规则绑到 nssm.exe 上 —— 那样任何
    通过 NSSM 跑的服务都会被这条规则放行，不是我们想要的。

    所以顺序是：先查 Parameters\\Application（NSSM），
    没有再退回 PathName（普通服务）。
    """
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$params = "HKLM:\SYSTEM\CurrentControlSet\Services\{service_name}\Parameters"
if (Test-Path $params) {{
    $app = (Get-ItemProperty $params).Application
    if ($app) {{ Write-Output ("APP=" + $app); exit }}
}}
$svc = Get-CimInstance Win32_Service -Filter "Name='{service_name}'"
if ($svc) {{ Write-Output ("PATH=" + $svc.PathName) }}
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""

    text = _decode(proc.stdout).strip()
    if not text:
        return ""

    if text.startswith("APP="):
        # NSSM 的 Application 通常是干净路径，但也可能带引号，统一走解析
        return parse_image_path(text[len("APP="):])
    if text.startswith("PATH="):
        return parse_image_path(text[len("PATH="):])
    return parse_image_path(text)


def parse_image_path(image_path: str) -> str:
    """从 ImagePath 里解析出 exe 完整路径。

    规则：
      1. 有引号 → 取引号内内容（最可靠，Windows 推荐写法）
      2. 无引号 → 从开头取到第一个 ".exe"（含）为止
      3. 都没有 → 返回空串
    """
    if not image_path:
        return ""

    text = image_path.strip()

    # 情况 1：带引号
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 1:
            return text[1:end]

    # 情况 2：无引号，找 .exe
    lower = text.lower()
    idx = lower.find(".exe")
    if idx != -1:
        return text[: idx + 4]

    return text.split()[0] if text.split() else ""
