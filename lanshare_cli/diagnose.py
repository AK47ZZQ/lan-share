"""诊断包：把「这台机器现在到底什么状态」一次性收齐，导出成一份能直接发人看的报告。

## 为什么要有这个

2026-11-02 用户装完新版本报「数据库一直 stopped」。我在自己机器上复现不了，
只能让他开管理员 PowerShell 跑三条命令贴给我 —— 来回问了三轮才定位。

诊断包要解决的就是这个：用户点一个按钮，导出 `lanshare-诊断-<时间>.txt`，
里面把服务状态、PG 日志、目录权限、端口、防火墙、证书、配置全文全带上。
**每一个 bug 的定位成本从「来回问三轮」降到「看一眼」。**

## 纪律

1. **只读**。绝不改任何东西，绝不重启服务。用户是在出故障时点它的。
2. **不许崩**。每一项检查单独 try，失败就记「检查失败：原因」，继续下一项。
   诊断工具自己崩溃是最坏的情况 —— 用户正需要它的时候它没了。
3. **不泄漏口令**。数据库密码、会话密钥、公网 token 一律替换成 <已隐藏>。
   这是要发给别人的文件，默认按「会被外传」处理。

用法：
    python -m lanshare_cli diagnose                 # 写入临时目录并打印路径
    python -m lanshare_cli diagnose --out D:\\diag.txt
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

# 这些字段出现时，值一律替换（大小写不敏感）
_SECRET_HINTS = (
    "password", "passwd", "pwd", "secret", "token", "apikey",
    "api_key", "private", "credential",
)
_REDACTED = "<已隐藏>"


def _redact(text: str) -> str:
    """把疑似口令的值打码。

    两种形态都要管：
        password=hunter2            （env / ini）
        "password": "s3cret"        （JSON，Caddyfile 之类）
        secret='quoted val'         （带引号、值里还有空格）
    """
    import re

    def secret(key: str) -> bool:
        return any(h in key.strip("\"'").lower() for h in _SECRET_HINTS)

    # 0) URL 里的 userinfo：postgres://user:p4ss@host/db
    #
    # 这条必须单独处理。`DATABASE_URL` 这个 key 本身不含 password/token 之类的
    # 词，走不到下面按 key 判断的规则，可它的值里明明白白带着数据库口令。
    # 诊断包是要发出去的，这种漏法最要命。
    text = re.sub(
        r"(?i)\b([a-z][a-z0-9+.\-]*://)([^:@/\s]*):([^@\s]+)@",
        r"\1\2:***@",
        text,
    )

    # 1) 带引号的值：靠后向引用配对同一个引号，允许值里含空格
    text = re.sub(
        r"""(?ix)
        (\b[A-Za-z_][A-Za-z0-9_\-]{2,40}["']?)     # key，允许跟随一个尾引号
        (\s*[=:]\s*)                                # 分隔符
        (["'])(.*?)\3                               # 引号成对的值
        """,
        lambda m: (
            m.group(1) + m.group(2) + m.group(3) + m.group(4) + m.group(3)
            if not secret(m.group(1))
            else m.group(1) + m.group(2) + m.group(3) + _REDACTED + m.group(3)
        ),
        text,
    )

    # 2) 裸值（没有引号）
    text = re.sub(
        r"""(?ix)
        (\b[A-Za-z_][A-Za-z0-9_\-]{2,40}["']?)
        (\s*[=:]\s*)
        ([^\s,;'"]+)
        """,
        lambda m: (
            m.group(1) + m.group(2) + m.group(3)
            if not secret(m.group(1))
            else m.group(1) + m.group(2) + _REDACTED
        ),
        text,
    )
    return text


def _run(cmd: list[str], timeout: float = 12.0) -> str:
    """跑一条命令，永远返回字符串（失败返回「失败：原因」）。"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        if out:
            return out
        if err:
            return "stderr: " + err
        return f"(退出码 {r.returncode}，无输出)"
    except subprocess.TimeoutExpired:
        return f"失败：超过 {timeout:.0f} 秒没返回"
    except FileNotFoundError:
        return "失败：命令不存在"
    except Exception as exc:
        return f"失败：{exc}"


def _psq(value: object) -> str:
    """把值安全地嵌进 PowerShell 单引号字符串。

    PowerShell 单引号串里，转义方式是**写两遍**（'It''s'）。

    为什么要这个：`--service-prefix` 是用户可传的参数，直接拼进查询语句时
    只要带一个单引号就能把脚本截断、执行任意命令。诊断工具声称「只读」，
    那就必须真的只读 —— 一个能被打成命令执行的只读工具比没有更危险。
    """
    return str(value).replace("'", "''")


def _ps(script: str, timeout: float = 15.0) -> str:
    return _run(["powershell", "-NoProfile", "-NonInteractive",
                 "-Command", script], timeout)


# ── 各项检查 ────────────────────────────────────────────────

def _sec_basic(layout) -> list[str]:
    out = ["## 1. 基本情况", ""]
    try:
        out.append(f"生成时间      {time.strftime('%Y-%m-%d %H:%M:%S')}")
        out.append(f"主机名        {socket.gethostname()}")
        out.append(f"系统          {platform.platform()}")
        out.append(f"架构          {platform.machine()}")
        out.append(f"Python        {sys.version.split()[0]} ({sys.executable})")
        try:
            from .cli import is_admin
            out.append(f"管理员权限    {'是' if is_admin() else '否'}")
        except Exception as exc:
            out.append(f"管理员权限    检查失败：{exc}")
        try:
            v = (Path(layout.root) / "VERSION").read_text(encoding="utf-8").strip()
            out.append(f"安装版本      {v}")
        except Exception as exc:
            out.append(f"安装版本      读不到 VERSION（{exc}）")
    except Exception as exc:
        out.append(f"基本情况收集失败：{exc}")
    out.append("")
    return out


def _sec_layout(layout) -> list[str]:
    out = ["## 2. 安装布局", ""]
    try:
        root = Path(layout.root)
        data = Path(layout.data_dir)
        out.append(f"程序目录      {root}")
        out.append(f"数据目录      {data}")
        # 路径里的坑要先说，这是最常见的安装期事故来源
        warnings = []
        if " " in str(root):
            warnings.append("程序目录含空格（Caddyfile / 命令行参数容易切错 token）")
        if any(ord(c) > 127 for c in str(root)):
            warnings.append("程序目录含非 ASCII 字符（中文路径）")
        if str(root).lower().startswith(os.environ.get("PROGRAMFILES", "\0").lower()):
            warnings.append("程序目录在 Program Files（ACL 严格，PG 初始化易失败）")
        if data.resolve() == root.resolve():
            warnings.append("数据目录与程序目录相同（卸载时容易误删数据）")
        for w in warnings:
            out.append(f"  ⚠ {w}")
        out.append("")
        try:
            files = layout.required_files()
            dirs = layout.required_dirs()
        except Exception as exc:
            out.append(f"  必需项清单获取失败：{exc}")
            files, dirs = [], []
        for path, desc in list(files) + list(dirs):
            state = "OK     " if path.exists() else "缺失   "
            out.append(f"  {desc:22s} {state} {path}")
    except Exception as exc:
        out.append(f"布局检查失败：{exc}")
    out.append("")
    return out


def _sec_services(layout) -> list[str]:
    """三个服务的状态与启动参数。

    一次 PowerShell 拿全部 —— 原来每个服务跑两条命令（状态 + PathName），
    6 次进程启动在慢机器上要十几秒，而 GUI 那边的超时是有上限的。
    诊断工具自己超时是最尴尬的失败。
    """
    out = ["## 3. 服务状态", ""]
    names: list[str] = []
    try:
        names = [layout.api_service, layout.caddy_service, layout.pg_service]
    except Exception as exc:
        out.append(f"服务名解析失败：{exc}")
        return out + [""]

    services = list(names)

    # 用前缀一次性捞出所有相关服务，避免逐个查
    prefix = getattr(layout, "service_prefix", "") or ""
    flt = (f"$_.Name -like '{_psq(prefix)}-*'" if prefix
           else " or ".join(f"$_.Name -eq '{_psq(n)}'" for n in services) or "$false")

    raw = _ps(
        f"Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | "
        f"Where-Object {{ {flt} }} | "
        f"ForEach-Object {{ "
        f"  $_.Name + '|' + $_.State + '|' + $_.StartMode + '|' "
        f"+ $(if($_.PathName){{$_.PathName}}else{{''}}) + '|' "
        f"+ $(if($_.StartName){{$_.StartName}}else{{''}}) }}",
        20)

    found: dict[str, tuple[str, str, str, str]] = {}
    for line in raw.splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 5:
            found[parts[0]] = (parts[1], parts[2], parts[3], parts[4])

    for name in services:
        if name in found:
            state, start_mode, path_name, account = found[name]
            out.append(f"  {name:28s} State={state} StartType={start_mode}")
            if path_name:
                out.append(f"  {'':28s} Path={path_name}")
            if account:
                out.append(f"  {'':28s} Account={account}")
        else:
            out.append(f"  {name:28s} NOT INSTALLED")

    # 前缀下还有别的服务也列出来（换过前缀的残留就是这么发现的）
    extra = [n for n in found if n not in services]
    if extra:
        out.append("")
        out.append(f"  同前缀的其他服务：{', '.join(extra)}")

    out.append("")
    return out


def _sec_ports(layout) -> list[str]:
    out = ["## 4. 端口", ""]
    try:
        api, web = layout.api_port, layout.web_port
    except Exception:
        api, web = 8766, 8765
    try:
        from .cli import port_in_use
    except Exception:
        port_in_use = None
    raw = _run(["netstat", "-ano", "-p", "TCP"])
    # 端口被占用本身不是问题 —— 自己的服务占着才叫正常。所以要带上进程名。
    owner_by_port: dict[int, str] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local, pid = parts[1], parts[-1]
        try:
            port_num = int(local.rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            continue
        if port_num in (api, web) and port_num not in owner_by_port:
            owner_by_port[port_num] = pid

    for port, label in ((api, "API"), (web, "Web(HTTPS)")):
        try:
            used = port_in_use(port) if port_in_use else None
            pid = owner_by_port.get(port, "")
            who = ""
            if pid:
                who = _ps(f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName", 6)
                who = who.strip()
            if used:
                out.append(f"  {label:12s} {port:<6} 已被占用（PID {pid or '?'} {who or '?'}）")
            else:
                out.append(f"  {label:12s} {port:<6} 空闲")
        except Exception as exc:
            out.append(f"  {label:12s} {port:<6} 检查失败：{exc}")
    out.append("")
    out.append("  netstat 实况：")
    for line in raw.splitlines():
        if f":{api} " in line or f":{web} " in line:
            out.append("    " + " ".join(line.split()))
    out.append("")
    return out


def _sec_pg(layout) -> list[str]:
    """PG 是「起不来」的第一嫌疑，这里要挖到底。"""
    out = ["## 5. PostgreSQL", ""]
    try:
        data = Path(layout.data_dir)
    except Exception as exc:
        out.append(f"数据目录解析失败：{exc}")
        return out + [""]

    pgdata = None
    for cand in (data / "pgdata", Path(layout.root) / "pgsql" / "data",
                 Path(layout.root) / "env" / "pginstall" / "data"):
        if cand.is_dir():
            pgdata = cand
            break
    out.append(f"  pgdata        {pgdata if pgdata else '未找到（可能从未初始化）'}")
    if not pgdata:
        return out + [""]

    # postmaster.pid 残留是「关机后起不来」最常见的原因。
    #
    # 但要注意：PG **正在运行** 时这个文件本来就存在。把它一律报成问题
    # 就是误报，而诊断工具误报比不报更糟 —— 用户会去删一个不该删的文件。
    # 判据只有一个：文件里的 PID 还活着吗。
    pid = pgdata / "postmaster.pid"
    if pid.is_file():
        try:
            head = pid.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            head = []
            out.append(f"  postmaster.pid 读取失败：{exc}")
        first_pid = None
        if head:
            try:
                first_pid = int(head[0].strip())
            except (ValueError, IndexError):
                first_pid = None
        alive_txt = ""
        alive = None
        if first_pid is not None:
            alive_txt = _ps(
                f"(Get-Process -Id {first_pid} -ErrorAction SilentlyContinue) -ne $null", 6)
            alive = "True" in alive_txt
        if alive:
            out.append(f"  postmaster.pid 存在，且 PID {first_pid} 正在运行 —— 正常（PG 在跑）")
            out.append("  判定：NORMAL_RUNNING")
        else:
            out.append(f"  ⚠ postmaster.pid 残留（PID {first_pid} 已不在）—— 上次没有干净退出")
            out.append("  判定：STALE_LOCK")
            for line in head[:5]:
                out.append("      " + line.strip())
        out.append(f"      该 PID 是否还活着：{alive_txt or '未知'}")

    # ACL：initdb 要独占，服务跑 LocalSystem 又要能读，两边都得对
    out.append("  目录权限（icacls）：")
    acl = _run(["icacls", str(pgdata)])
    for line in acl.splitlines()[:10]:
        out.append("    " + line.strip())

    # PG 自己的日志最能说明问题
    for log_dir in (pgdata / "log", pgdata / "pg_log"):
        if log_dir.is_dir():
            logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime,
                          reverse=True)[:2]
            for lf in logs:
                out.append(f"  日志尾部 {lf.name}：")
                try:
                    lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in lines[-25:]:
                        out.append("    " + line)
                except Exception as exc:
                    out.append(f"    读取失败：{exc}")
            break
    out.append("")
    return out


def _sec_network() -> list[str]:
    out = ["## 6. 网络", ""]
    try:
        out.append("  网卡：")
        raw = _ps(
            "Get-NetIPAddress -AddressFamily IPv4 | "
            "Where-Object {$_.IPAddress -notlike '127.*'} | "
            "Select-Object InterfaceAlias,IPAddress,PrefixLength | Format-Table -AutoSize | Out-String -Width 120", 12)
        for line in raw.splitlines()[:12]:
            out.append("    " + line.rstrip())
    except Exception as exc:
        out.append(f"    失败：{exc}")
    try:
        out.append("  默认路由：")
        raw = _ps("Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
                  "Select-Object InterfaceAlias,NextHop | Format-Table -AutoSize | Out-String -Width 120", 10)
        for line in raw.splitlines()[:8]:
            out.append("    " + line.rstrip())
    except Exception as exc:
        out.append(f"    失败：{exc}")
    out.append("")
    return out


def _sec_firewall(layout) -> list[str]:
    out = ["## 7. 防火墙", ""]
    try:
        port = layout.web_port
    except Exception:
        port = 8765
    try:
        raw = _ps(
            "Get-NetFirewallRule -DisplayName '*LAN*Share*' -ErrorAction SilentlyContinue | "
            "Select-Object DisplayName,Enabled,Direction,Action,Profile | "
            "Format-Table -AutoSize | Out-String -Width 140", 12)
        out.append(raw.strip() or "  没有 LAN Share 相关规则")
        profiles = _ps("Get-NetFirewallProfile | "
                       "Select-Object Name,Enabled | Format-Table -AutoSize | Out-String -Width 80", 10)
        out.append("  各配置文件状态：")
        for line in profiles.splitlines()[:8]:
            out.append("    " + line.rstrip())
        out.append(f"  （局域网访问需要 TCP {port} 入站放行）")
    except Exception as exc:
        out.append(f"  检查失败：{exc}")
    out.append("")
    return out


def _sec_cert(layout) -> list[str]:
    out = ["## 8. 证书", ""]
    try:
        root = Path(layout.root)
    except Exception:
        root = Path(".")
    cands = [
        root / "certs" / "caddy" / "pki" / "authorities" / "local" / "root.crt",
        Path(os.environ.get("APPDATA", "")) / "Caddy" / "pki" / "authorities" / "local" / "root.crt",
        Path(os.environ.get("PROGRAMDATA", "")) / ".." / "Windows" / "System32" / "config"
        / "systemprofile" / "AppData" / "Roaming" / "Caddy" / "pki" / "authorities" / "local" / "root.crt",
    ]
    found = False
    for c in cands:
        try:
            if c.is_file():
                found = True
                out.append(f"  根证书        {c}")
                txt = _run(["certutil", "-dump", str(c)])
                for line in txt.splitlines():
                    if "NotAfter" in line or "不会在此时间后" in line:
                        out.append("    " + line.strip())
        except Exception as exc:
            out.append(f"    {c} 检查失败：{exc}")
    if not found:
        out.append("  未找到 Caddy 根证书（首次启动才生成）")
    try:
        trusted = _ps("Get-ChildItem Cert:\\LocalMachine\\Root | "
                      "Where-Object {$_.Subject -like '*Caddy*'} | "
                      "Select-Object Subject,NotAfter | Format-Table -AutoSize | Out-String -Width 120", 12)
        out.append("  系统信任库里的 Caddy 根证书：")
        out.append(trusted.strip() or "    没有（浏览器会报证书错误）")
    except Exception as exc:
        out.append(f"    检查失败：{exc}")
    out.append("")
    return out


def _sec_config(layout) -> list[str]:
    out = ["## 9. 配置", ""]
    try:
        root = Path(layout.root)
    except Exception:
        root = Path(".")
    for rel in ("conf/Caddyfile", "VERSION"):
        f = root / rel
        out.append(f"  --- {rel} ---")
        try:
            if f.is_file():
                out.append(_redact(f.read_text(encoding="utf-8", errors="replace")))
            else:
                out.append("  （文件不存在）")
        except Exception as exc:
            out.append(f"  读取失败：{exc}")
        out.append("")
    return out


def _sec_logs(layout) -> list[str]:
    out = ["## 10. 日志尾部", ""]
    try:
        root = Path(layout.root)
    except Exception:
        root = Path(".")
    for name in ("logs/api-stderr.log", "logs/api-stdout.log",
                 "logs/caddy-stderr.log", "logs/tray.log"):
        f = root / name
        out.append(f"  --- {name}（最后 30 行）---")
        try:
            if f.is_file():
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[-30:]:
                    out.append("    " + _redact(line))
            else:
                out.append("  （文件不存在）")
        except Exception as exc:
            out.append(f"  读取失败：{exc}")
        out.append("")
    return out


def _sec_path() -> list[str]:
    out = ["## 11. PATH 与环境变量", ""]
    try:
        path = os.environ.get("PATH", "")
        parts = path.split(os.pathsep)
        hit = [p for p in parts if "pgsql" in p.lower() or "postgres" in p.lower()]
        out.append(f"  PATH 条目数   {len(parts)}")
        out.append(f"  含 pgsql 的   {hit if hit else '没有（pg_dump 只能靠回退目录找）'}")
        for k in ("SHARE_ROOT", "LANSHARE_PG_BIN", "DATABASE_URL",
                  "LANSHARE_ROOT", "LANSHARE_DATA"):
            v = os.environ.get(k)
            if v is not None:
                out.append(f"  {k} = {_redact(v)}")
    except Exception as exc:
        out.append(f"  收集失败：{exc}")
    out.append("")
    return out


def _sec_verdict(sections: dict) -> list[str]:
    """把最可能的病因顶到最前面 —— 用户不需要读完 11 节才知道问题在哪。"""
    out = ["## 0. 优先看这里", ""]
    hints = []

    services = "\n".join(sections.get("services", []))
    if "NOT INSTALLED" in services:
        hints.append("有服务没装上 —— 安装流程没走完，或服务名和安装记录不一致。")
    if "State=Stopped" in services:
        hints.append("有服务处于 Stopped —— 看第 5 节 PG 日志和第 3 节服务启动参数。")
    if "State=StartPending" in services:
        hints.append("有服务卡在 StartPending —— 通常是被依赖的服务没起来。")

    # PG：只有「锁文件在、进程已死」才是问题。
    # 正常运行时 postmaster.pid 本来就在，报它就是误报。
    pg = "\n".join(sections.get("pg", []))
    if "判定：STALE_LOCK" in pg:
        hints.append(
            "PG 留下了 postmaster.pid 但进程已经不在了（上次非正常退出），"
            "这会让 PG 拒绝启动。删掉该文件后再启动服务。")
    if "未找到（可能从未初始化）" in pg:
        hints.append("找不到 pgdata —— 数据库从未初始化成功，重装时留意第 7 步的输出。")

    # 端口：只有「服务没在跑、端口却被占」才是问题。
    # 服务正常运行时端口当然被自己占着。
    ports = "\n".join(sections.get("ports", []))
    api_running = "State=Running" in services
    if "已被占用" in ports and not api_running:
        hints.append("端口被占用但服务没在跑 —— 可能是上次残留的进程，或别的程序占了这个端口。")

    basic = "\n".join(sections.get("basic", []))
    if "管理员权限    否" in basic:
        hints.append("当前不是管理员 —— 注册/重启服务会失败。")

    layout_txt = "\n".join(sections.get("layout", []))
    for line in layout_txt.splitlines():
        if line.strip().startswith("⚠"):
            hints.append(line.strip()[1:].strip())

    cert = "\n".join(sections.get("cert", []))
    if "没有（浏览器会报证书错误）" in cert:
        hints.append("根证书没进系统信任库 —— 浏览器会拦，需要重新信任一次。")

    if hints:
        for h in hints:
            out.append(f"  · {h}")
    else:
        out.append("  没有发现明显问题。把这份报告连同具体现象一起发出来。")
    out.append("")
    return out


def build_report(layout) -> str:
    """生成完整诊断报告（纯文本）。任何一项失败都不影响其余部分。"""
    sections: dict[str, list[str]] = {}

    def grab(key, fn, *a):
        try:
            sections[key] = fn(*a)
        except Exception as exc:
            sections[key] = [f"（这一节整体失败：{exc}）", ""]

    grab("basic", _sec_basic, layout)
    grab("layout", _sec_layout, layout)
    grab("services", _sec_services, layout)
    grab("ports", _sec_ports, layout)
    grab("pg", _sec_pg, layout)
    grab("network", _sec_network)
    grab("firewall", _sec_firewall, layout)
    grab("cert", _sec_cert, layout)
    grab("config", _sec_config, layout)
    grab("logs", _sec_logs, layout)
    grab("path", _sec_path)

    head = [
        "# LAN Share 诊断报告",
        "",
        "> 这份文件用于排查问题。口令已隐藏；包含本机路径和局域网 IP，",
        "> 发给别人之前自己扫一眼。",
        "",
    ]
    body = _sec_verdict(sections)
    for key in ("basic", "layout", "services", "ports", "pg", "network",
                "firewall", "cert", "config", "logs", "path"):
        body += sections.get(key, [])
    return "\n".join(head + body) + "\n"


def export(layout, out_path: Path | None = None) -> Path:
    """生成并写盘，返回文件路径。"""
    text = build_report(layout)
    if out_path is None:
        stem = time.strftime("lanshare-诊断-%Y%m%d-%H%M%S")
        base = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")
        out_path = base / f"{stem}.txt"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path
