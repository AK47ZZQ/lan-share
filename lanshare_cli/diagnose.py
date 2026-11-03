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
        f"+ $(if($_.StartName){{$_.StartName}}else{{''}}) + '|' "
        # ExitCode：服务上一次退出的 win32 代码。PG 起不来时这是唯一直接证据
        # （2026-11-02 实机：报告里只有 State=Stopped，起不来的原因全靠猜）
        f"+ $(if($null -ne $_.ExitCode){{$_.ExitCode}}else{{''}}) }}",
        20)

    found: dict[str, tuple] = {}
    for line in raw.splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 6:
            found[parts[0]] = (parts[1], parts[2], parts[3], parts[4], parts[5])

    for name in services:
        if name in found:
            state, start_mode, path_name, account = found[name][:4]
            exit_code = found[name][4] if len(found[name]) > 4 else ""
            out.append(f"  {name:28s} State={state} StartType={start_mode}")
            if path_name:
                out.append(f"  {'':28s} Path={path_name}")
            if account:
                out.append(f"  {'':28s} Account={account}")
            if exit_code not in ("", "0", "1077"):
                # 1077 = 服务从未启动过，不算异常
                out.append(
                    f"  {'':28s} ExitCode={exit_code}  ← 非 0 就是启动失败的直接原因"
                )
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

    # 端口被占着、netstat 里却没有 LISTENING 行 —— 2026-11-03 实机见过：
    # Caddy（HTTP/3 + 内部 socket 池）在这台机器上就是这样，netstat 只给
    # 一条 `0.0.0.0:8765 → 0.0.0.0:0 CLOSED`，而 HTTPS 请求完全正常。
    # 靠「枚举端口」判断服务状态的逻辑在这种机器上必然误报，把话说明白。
    listening_web = any(
        f":{web} " in ln and "LISTENING" in ln.upper()
        for ln in raw.splitlines()
    )
    if not listening_web and owner_by_port.get(web):
        out.append("")
        out.append("  ⚠ 端口被占用，但 netstat 里没有 LISTENING 行。")
        out.append("     这是 Windows 对某些监听 socket 的盲区（Caddy 的 HTTP/3 +")
        out.append("     socket 池会触发）。此时不要用「查端口」判断服务是否在跑，")
        out.append("     以 healthz 探测结果为准。")
    out.append("")
    return out


def _sec_pg(layout) -> list[str]:
    """PG 是「起不来」的第一嫌疑，这里要挖到底。

    ## 怎么找 pgdata

    第一版是「在 data_dir 下面猜几个可能的位置」。2026-11-02 用户实机直接
    打了这个做法的脸：诊断报「找不到 pgdata」，而实际上 pgdata 活得好好的
    —— 只是它在 D:\\LAN Share\\data\\pgdata，因为**服务定义里的 -D 指向那里**，
    而注册表的数据目录已经被改成 C:\\ProgramData\\LANShare\\data。

    所以必须以**服务定义里的 -D 为准**，猜出来的路径只用于对比和告警。
    这个「服务指向 A、注册表说 B」本身就是要报出来的头号问题。
    """
    import re

    out = ["## 5. PostgreSQL", ""]
    try:
        data = Path(layout.data_dir)
    except Exception as exc:
        out.append(f"数据目录解析失败：{exc}")
        return out + [""]

    # ── 1) 以服务定义里的 -D 为准 ──
    service_pgdata: Path | None = None
    qc_raw = ""
    try:
        qc_raw = _run(["sc.exe", "qc", layout.pg_service], 20)
        if "FAILED" not in qc_raw and "失败" not in qc_raw:
            m = re.search(r'-D\s+"?([^"\r\n]+?)"?\s*(?:-w|$)', qc_raw)
            if m:
                service_pgdata = Path(m.group(1).strip())
    except Exception as exc:
        out.append(f"  读取服务定义失败：{exc}")

    # 3.95 起 pgdata 在数据目录**同级**（数据目录是用户文件根，程序状态不能
    # 混进去）。老位置 data\pgdata 仍作为兼容候选，让老装机的诊断照样能跑。
    expected_pgdata = data.parent / "pgdata"
    legacy_pgdata = data / "pgdata"
    pgdata: Path | None = None

    if service_pgdata and service_pgdata.is_dir():
        pgdata = service_pgdata
        out.append(f"  pgdata（服务 -D）  {service_pgdata}")
        if service_pgdata.resolve() != expected_pgdata.resolve():
            # 只有「注册表推出的那个位置也真的有一套 pgdata」才算冲突。
            # 开发机上服务指向 env\pginstall\data、注册表指向 data\pgdata
            # （后者根本不存在），这是正常的开发布局，不该报成故障 ——
            # 诊断工具误报比不报更糟。
            if expected_pgdata.is_dir():
                out.append(f"  ⚠ 与数据目录不一致   注册表说 {expected_pgdata}")
                out.append("  判定：PGDATA_MISMATCH")
            elif legacy_pgdata.is_dir():
                out.append(f"  ⚠ 还在老位置（3.94 及以前）   {legacy_pgdata}")
                out.append("    下次安装会自动搬到数据目录同级")
            else:
                out.append(f"   （注册表指向的 {expected_pgdata} 不存在，按开发布局忽略）")
    else:
        if service_pgdata:
            out.append(f"  服务 -D 指向        {service_pgdata}（该目录不存在）")
        for cand in (expected_pgdata,
                     legacy_pgdata,
                     Path(layout.root) / "pgsql" / "data",
                     Path(layout.root) / "data" / "pgdata",
                     Path(layout.root) / "env" / "pginstall" / "data"):
            if cand.is_dir():
                pgdata = cand
                break
        if pgdata:
            out.append(f"  pgdata（猜到的）    {pgdata}")
        else:
            out.append("  pgdata        未找到（可能从未初始化）")
            out.append("  判定：NO_PGDATA")
            out.append("")
            return out

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

    # ACL：initdb 要独占，服务跑 NetworkService 又要能读，两边都得对
    out.append("  目录权限（icacls）：")
    acl = _run(["icacls", str(pgdata)])
    for line in acl.splitlines()[:10]:
        out.append("    " + line.strip())

    # pg_ctl status —— 实测 PG 自己认为自己有没有在跑。
    #
    # 为什么要实测：服务状态是 SCM 的看法，pg_ctl 是 PostgreSQL 自己的看法。
    # 两者不一致（服务 Running 但 PG 认为没跑 / 反过来）是「假 Running」的典型，
    # 光看服务状态会被骗。这一步不需要数据库密码。
    pgctl = None
    for cand in (Path(layout.root) / "pgsql" / "bin" / "pg_ctl.exe",
                 Path(layout.root) / "env" / "pgsql-bin" / "pg_ctl.exe"):
        if cand.is_file():
            pgctl = cand
            break
    if pgctl:
        st_out = _run([str(pgctl), "status", "-D", str(pgdata)], 20)
        first = (st_out or "").strip().splitlines()
        out.append(f"  pg_ctl status {first[0] if first else '(无输出)'}")
        if "server is running" in st_out or "正在运行" in st_out:
            out.append("  判定：PG_RUNNING")
        elif "no server running" in st_out or "没有运行" in st_out:
            out.append("  判定：PG_NOT_RUNNING")
        elif "not a database cluster" in st_out or "不是数据库集群" in st_out:
            out.append("  判定：PG_BAD_CLUSTER（这个目录不是有效的数据目录）")

    # PG 自己的日志最能说明问题
    #
    # ⚠️ 三个位置都得看：
    #   pgdata/log、pgdata/pg_log  —— PG 自己按 log_directory 写的
    #   root/logs/postgres.log     —— lanshare_cli 用 pg_ctl -l 指定的
    #   data/logs/postgres.log     —— 同上，但数据目录在 ProgramData 时
    #
    # 2026-11-02 实机踩到：诊断包只翻了前两个（那时 PG 的日志其实写在
    # 第三个位置），于是「PG 起不来」这一条在报告里**一条日志都没有**，
    # 只能靠猜。诊断工具缺证据就是缺陷。
    log_files: list[Path] = []
    for log_dir in (pgdata / "log", pgdata / "pg_log"):
        if log_dir.is_dir():
            log_files += sorted(log_dir.glob("*.log"),
                                key=lambda f: f.stat().st_mtime, reverse=True)[:2]
    for log_dir in (Path(layout.root) / "logs", Path(layout.data_dir) / "logs"):
        cand = log_dir / "postgres.log"
        if cand.is_file():
            log_files.append(cand)

    if not log_files:
        out.append("  ⚠ 没找到 PG 日志文件（pgdata/log、logs/postgres.log 都没有）")
        out.append("     若第 3 节显示 PG 服务 Stopped，说明它连日志都来不及写就退出了，")
        out.append("     重点看那里的 ExitCode 和服务账户。")
    for lf in log_files:
        out.append(f"  日志尾部 {lf}：")
        try:
            lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
            if not lines:
                out.append("    （文件为空）")
            for line in lines[-30:]:
                out.append("    " + _redact(line))
        except Exception as exc:
            out.append(f"    读取失败：{exc}")
    out.append("")
    return out


def _sec_backup(layout) -> list[str]:
    """备份能不能跑。

    2026-11-02 用户报「备份失败」，但诊断包里**完全没有备份相关信息** ——
    只能靠猜。这里把三件事都摆出来：目录在哪、里面有什么、pg_dump 能不能找到。
    """
    out = ["## 5b. 备份", ""]
    try:
        root = Path(layout.root)
        data = Path(layout.data_dir)
    except Exception as exc:
        out.append(f"路径解析失败：{exc}")
        return out + [""]

    # pg_dump 能不能找到 —— 这是「当前引擎无可用 dump 工具」的直接判据
    try:
        from app.services.backup import _resolve_tool
        dump = _resolve_tool("pg_dump")
        restore = _resolve_tool("pg_restore")
        out.append(f"  pg_dump       {dump or '找不到（备份必然失败）'}")
        out.append(f"  pg_restore    {restore or '找不到（恢复演练会失败）'}")
        if not dump:
            out.append("  判定：NO_PG_TOOL")
    except Exception as exc:
        out.append(f"  pg_dump 解析失败：{exc}")

    # 备份目录（可能在 settings 里被改过，也可能是默认的 data/.backups）
    cands = [data / ".backups", root / "data" / ".backups"]
    found_dir = None
    for c in cands:
        if c.is_dir():
            found_dir = c
            break
    if found_dir:
        out.append(f"  备份目录      {found_dir}")
        try:
            files = sorted(found_dir.iterdir(),
                           key=lambda f: f.stat().st_mtime, reverse=True)
            out.append(f"  文件数        {len(files)}")
            for f in files[:5]:
                try:
                    size = f.stat().st_size
                    age_d = (time.time() - f.stat().st_mtime) / 86400
                    out.append(f"    {f.name}  {size} 字节  {age_d:.1f} 天前")
                except OSError:
                    out.append(f"    {f.name}  （读不到大小）")
            if not files:
                out.append("  判定：EMPTY（从没成功备份过）")
        except OSError as exc:
            out.append(f"  列目录失败：{exc}")
    else:
        out.append("  备份目录      不存在（从未备份过）")
        out.append("  判定：NO_DIR")

    out.append("")
    return out


def _sec_runtime(layout) -> list[str]:
    """运行时环境：残留进程、系统代理、hosts、时区。

    这几项各自对应一类实际踩过的故障：
      · 残留进程   → 端口被占、服务起不来、旧实例抢锁
      · 系统代理   → 检查更新失败、Caddy 出网异常（而且报错完全不指向代理）
      · hosts      → 域名解析到奇怪的地方，TLS 握手失败
      · 时区/时间  → 证书有效期判断错，明明没过期却被判过期
    """
    out = ["## 6b. 运行时环境", ""]

    # ── 1) 相关进程（含残留）──
    try:
        raw = _ps(
            "Get-Process -ErrorAction SilentlyContinue | "
            "Where-Object { $_.ProcessName -in @('caddy','python','postgres','nssm','LANShareTray') } | "
            "ForEach-Object { $_.ProcessName + '|' + $_.Id + '|' + "
            "$(try { $_.Path } catch { '' }) + '|' + "
            "$(try { $_.StartTime.ToString('MM-dd HH:mm') } catch { '' }) }",
            15)
        procs = [ln.strip().split("|") for ln in (raw or "").splitlines() if "|" in ln]
        out.append(f"  相关进程      {len(procs)} 个")
        for row in procs[:12]:
            if len(row) >= 4:
                out.append(f"    {row[0]:14s} PID {row[1]:<7s} {row[3]}  {row[2]}")
        # 🔴 进程数不是残留的判据。
        #
        # 第一版写的是「caddy/postgres 超过 2 个就是残留」，实机一跑就发现是
        # 纯误报：PostgreSQL 本来就是多进程架构（主进程 + 每个连接一个 backend），
        # 20 个进程完全正常；nssm 也是每个服务一个。
        #
        # 真正的判据只有一个：**服务已经 Stopped，却还有它的进程活着**。
        # 那才是上次没退干净。
        out.append("  说明：PostgreSQL 与 NSSM 都是多进程架构，数量多属正常；"
                   "只有「服务已停止但进程还在」才算残留")
    except Exception as exc:
        out.append(f"  进程检查失败：{exc}")

    # ── 2) 系统代理 ──
    #
    # 代理是「检查更新失败」的头号隐形原因：urllib 会读 HTTP(S)_PROXY，
    # 而报错只会说「连不上 GitHub」，完全指不到代理身上。
    try:
        # 🔴 NO_PROXY 是**例外列表**，不是代理。把它算成「开着代理」是误报。
        env_proxy = {k: v for k, v in os.environ.items()
                     if k.lower() in ("http_proxy", "https_proxy", "all_proxy")}
        no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
        if no_proxy:
            out.append(f"  例外列表 NO_PROXY = {no_proxy}（这不是代理，是排除项）")
        if env_proxy:
            for k, v in env_proxy.items():
                out.append(f"  环境变量 {k} = {_redact(v)}")
            out.append("  判定：PROXY_SET（检查更新/出网可能走代理）")
        ie = _ps(
            "try { $p = Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion"
            "\\Internet Settings' -ErrorAction Stop; "
            "'Enable=' + $p.ProxyEnable + ' Server=' + $p.ProxyServer } catch { '' }", 10)
        if ie.strip():
            out.append(f"  系统代理      {ie.strip()}")
            if "Enable=1" in ie:
                out.append("  判定：SYSTEM_PROXY_ON")
        if not env_proxy and "Enable=1" not in (ie or ""):
            out.append("  代理          未启用")
    except Exception as exc:
        out.append(f"  代理检查失败：{exc}")

    # ── 3) hosts 文件里和本机相关的条目 ──
    try:
        hosts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
        if hosts.is_file():
            lines = hosts.read_text(encoding="utf-8", errors="replace").splitlines()
            interesting = [ln.strip() for ln in lines
                           if ln.strip() and not ln.strip().startswith("#")
                           and any(k in ln.lower() for k in
                                   ("localhost", "lan", "share", "127.0.0.1", "::1"))]
            if interesting:
                out.append(f"  hosts 相关行  {len(interesting)} 条")
                for ln in interesting[:8]:
                    out.append(f"    {ln}")
            else:
                out.append("  hosts         没有本机相关条目")
        else:
            out.append("  hosts         读不到")
    except Exception as exc:
        out.append(f"  hosts 检查失败：{exc}")

    # ── 4) 时间与时区（证书有效期判断依赖它）──
    try:
        tz = _ps("(Get-TimeZone).Id", 8).strip()
        out.append(f"  时区          {tz}")
        out.append(f"  本机时间      {time.strftime('%Y-%m-%d %H:%M:%S')}")
        # 和外部时间源比一下：偏差大会导致 TLS 握手被判过期
        try:
            import urllib.request
            with urllib.request.urlopen("https://www.baidu.com", timeout=5) as r:
                server_date = r.headers.get("Date", "")
            out.append(f"  网络时间头    {server_date or '(取不到)'}")
        except Exception:
            out.append("  网络时间头    取不到（出网可能有问题）")
    except Exception as exc:
        out.append(f"  时间检查失败：{exc}")

    out.append("")
    return out


def _sec_registry(layout) -> list[str]:
    """安装注册表快照。

    `HKLM\\SOFTWARE\\LANShare` 是安装器、CLI、托盘三方共用的状态，
    「服务和注册表对不上」这类问题的现场全靠它。
    """
    out = ["## 2b. 注册表", ""]
    try:
        key = "HKLM:\\SOFTWARE\\LANShare"
        raw = _ps(
            f"$k = Get-ItemProperty '{key}' -ErrorAction SilentlyContinue; "
            f"if ($k) {{ $k.PSObject.Properties | "
            f"Where-Object {{ $_.Name -notlike 'PS*' }} | "
            f"ForEach-Object {{ $_.Name + '=' + $_.Value }} }} else {{ 'KEY_NOT_FOUND' }}",
            12)
        lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
        if not lines or lines == ["KEY_NOT_FOUND"]:
            out.append("  键不存在（HKLM\\SOFTWARE\\LANShare）")
            out.append("  判定：NO_REGKEY")
        else:
            for ln in lines:
                out.append("  " + _redact(ln))
    except Exception as exc:
        out.append(f"  读取失败：{exc}")
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


def _caddy_storage_dir(layout) -> Path | None:
    """从 Caddyfile 的 `storage file_system "..."` 读出证书实际落盘位置。

    为什么要读配置而不是猜路径：Caddy 的证书默认放「当前用户的 AppData」，
    但安装版把它改到了安装目录里（否则 SYSTEM 账户和用户账户会各存一份）。
    开发机上又可能是 AppData —— 猜出来的路径很容易指向一份**历史残留**，
    然后得出「当前根证书不在信任库里」这种完全错误的结论。
    """
    import re
    try:
        cf = Path(layout.root) / "conf" / "Caddyfile"
        if not cf.is_file():
            return None
        text = cf.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'storage\s+file_system\s+"?([^"\r\n]+)"?', text)
        if m:
            return Path(m.group(1).strip())
    except Exception:
        pass
    return None


def _sec_cert(layout) -> list[str]:
    """Caddy 根证书。

    ## 为什么要数个数

    2026-11-02 用户实机诊断里，系统信任库有**三个** Caddy 根证书
    （NotAfter 分别是 11:03、10:26、14:02）。当前 storage 里用的是 14:02 那个，
    前两个是历史安装的残留。

    单看「根证书存在、2046 才过期」会以为一切正常，但实际上：
      · 已经按旧根信任过的设备，连新根签的证书会报错
      · 换过根却没清理旧根，会让「到底信任的是哪个」变得完全说不清

    所以这里要给出：信任库里有几个、当前用的是哪一个、它在不在信任库里。
    """
    out = ["## 8. 证书", ""]
    try:
        root = Path(layout.root)
    except Exception:
        root = Path(".")

    # ── 1) 当前 storage 里实际在用的那份 ──
    #
    # 顺序：Caddyfile 里写的 storage（最可靠）→ 安装目录默认位 → 用户 AppData。
    storage = _caddy_storage_dir(layout)
    if storage:
        out.append(f"  storage 配置  {storage}（来自 Caddyfile）")
    cands = ([storage / "pki" / "authorities" / "local" / "root.crt"] if storage else []) + [
        root / "certs" / "caddy" / "pki" / "authorities" / "local" / "root.crt",
        Path(os.environ.get("APPDATA", "")) / "Caddy" / "pki" / "authorities" / "local" / "root.crt",
        Path(os.environ.get("PROGRAMDATA", "")) / "Windows" / "System32" / "config"
        / "systemprofile" / "AppData" / "Roaming" / "Caddy" / "pki" / "authorities" / "local" / "root.crt",
    ]
    active = None
    for c in cands:
        try:
            if c.is_file():
                active = c
                break
        except Exception:
            continue

    active_thumb = ""
    if active:
        out.append(f"  当前使用      {active}")
        # 指纹是判断「是不是同一个根」的唯一可靠依据，比日期更准
        active_thumb = _ps(
            f"try {{ (New-Object System.Security.Cryptography.X509Certificates."
            f"X509Certificate2('{_psq(active)}')).Thumbprint }} catch {{ '' }}", 10).strip()
        if active_thumb:
            out.append(f"  指纹          {active_thumb}")
        dumped = _run(["certutil", "-dump", str(active)], 15)
        for line in dumped.splitlines():
            if "NotAfter" in line or "不会在此时间后" in line:
                out.append(f"  有效期至      {line.strip()}")
    else:
        out.append("  当前使用      未找到 Caddy 根证书（首次启动才生成）")
        out.append("  判定：NO_ROOT_CERT")

    # ── 2) 系统信任库里所有 Caddy 根 ──
    listed = _ps(
        "Get-ChildItem Cert:\\LocalMachine\\Root -ErrorAction SilentlyContinue | "
        "Where-Object {$_.Subject -like '*Caddy*'} | "
        "ForEach-Object { $_.Thumbprint + '|' + $_.NotAfter.ToString('yyyy-MM-dd HH:mm') + '|' + $_.Subject }",
        15)

    entries: list[tuple[str, str, str]] = []
    for line in (listed or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 3:
            entries.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))

    out.append(f"  信任库里的 Caddy 根    {len(entries)} 个")
    for thumb, when, subj in entries:
        mark = " ← 当前使用" if active_thumb and thumb.upper() == active_thumb.upper() else ""
        out.append(f"    {thumb}  {when}  {subj}{mark}")

    if not entries:
        out.append("  判定：NOT_TRUSTED（浏览器会报证书错误）")
    elif len(entries) > 1:
        out.append("  判定：MULTI_ROOT")
        if active_thumb and not any(
                t.upper() == active_thumb.upper() for t, _w, _s in entries):
            out.append("  ⚠ 当前在用的那个根**不在**信任库里 —— 浏览器会报证书错误")
            out.append("  判定：ACTIVE_NOT_TRUSTED")
    elif active_thumb and entries[0][0].upper() != active_thumb.upper():
        out.append("  ⚠ 信任库里那个和当前在用的不是同一个 —— 浏览器会报证书错误")
        out.append("  判定：ACTIVE_NOT_TRUSTED")

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

    # PG：三种情况，按严重程度分别说。
    pg = "\n".join(sections.get("pg", []))

    # ① 服务定义指向的 pgdata 和注册表的数据目录不是同一个地方。
    #    这是「重装换了数据目录、但服务没重建」的典型症状，PG 必然起不来，
    #    而且光看注册表完全看不出问题 —— 2026-11-02 用户实机就是这个。
    if "判定：PGDATA_MISMATCH" in pg:
        hints.append(
            "🔴 PG 服务定义里的数据目录和注册表的 DataFolder 不是同一个地方"
            "（重装时换过数据目录，但服务没重建）。PG 会一直 Stopped。"
            "解决办法：以「数据在哪」为准重跑一次安装，或者手动把服务的 -D 改到正确路径。")

    # ② 锁文件在、进程已死。
    if "判定：STALE_LOCK" in pg:
        hints.append(
            "PG 留下了 postmaster.pid 但进程已经不在了（上次非正常退出），"
            "这会让 PG 拒绝启动。删掉该文件后再启动服务。")

    # ③ 压根没有 pgdata。
    if "判定：NO_PGDATA" in pg:
        hints.append("找不到 pgdata —— 数据库从未初始化成功，重装时留意安装程序的输出。")

    # 备份
    bk = "\n".join(sections.get("backup", []))
    if "判定：NO_PG_TOOL" in bk:
        hints.append("找不到 pg_dump —— 备份必然失败。检查安装目录下的 pgsql\\bin。")
    if "判定：EMPTY" in bk or "判定：NO_DIR" in bk:
        hints.append("备份目录里没有可用的备份 —— 每日备份没跑成功过。")

    # 端口：只有「服务没在跑、端口却被占」才是问题。
    # 服务正常运行时端口当然被自己占着。
    ports = "\n".join(sections.get("ports", []))
    api_running = "State=Running" in services
    if "已被占用" in ports and not api_running:
        hints.append("端口被占用但服务没在跑 —— 可能是上次残留的进程，或别的程序占了这个端口。")

    # 「假 Running」：SCM 说服务在跑，PostgreSQL 自己说没跑。
    # 这种情况最坑人 —— 看服务列表一切正常，可就是连不上库。
    pg_svc_running = "lanshare-postgresql" in services and "State=Running" in services
    if "判定：PG_NOT_RUNNING" in pg and pg_svc_running:
        hints.append(
            "🔴 PG 服务显示 Running，但 pg_ctl 说数据库没在跑 —— 典型的「假运行」。"
            "通常意味着服务定义指向的目录不是真正在用的数据目录，看本报告第 5 节的 -D 路径。")
    elif "判定：PG_BAD_CLUSTER" in pg:
        hints.append("🔴 服务指向的目录不是有效的 PG 数据目录 —— 服务定义错了，要重建。")

    # 证书
    cert = "\n".join(sections.get("cert", []))
    if "判定：NO_ROOT_CERT" in cert:
        hints.append("没有 Caddy 根证书 —— 首次启动才会生成，如果服务从没起来过就会缺。")
    if "判定：ACTIVE_NOT_TRUSTED" in cert:
        hints.append(
            "🔴 当前在用的根证书不在系统信任库里 —— 浏览器会报证书错误，"
            "「打开网盘」会看到红色警告页。点面板上的「修复证书信任」。")
    if "判定：MULTI_ROOT" in cert:
        hints.append(
            "系统信任库里有多个 Caddy 根证书（历史安装残留）。当前用的那个已标注，"
            "其余可以删掉，否则「到底信任哪个」会说不清。")

    # 运行时环境
    runtime = "\n".join(sections.get("runtime", []))
    if "判定：STALE_PROCESS" in runtime:
        hints.append("有残留进程（同名进程数量异常）—— 可能导致端口占用、服务起不来。")
    if "判定：PROXY_SET" in runtime or "判定：SYSTEM_PROXY_ON" in runtime:
        hints.append(
            "系统开着代理 —— 「检查更新」失败、Caddy 出网异常很可能就是它，"
            "而且这类报错不会指向代理，只报『连不上』。")

    # 注册表
    reg = "\n".join(sections.get("registry", []))
    if "判定：NO_REGKEY" in reg:
        hints.append("安装注册表键不存在 —— 装了一半，或者被清理工具删了。")

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
        out.append(f"  发现 {len(hints)} 条值得注意的：")
        out.append("")
        for h in hints:
            out.append(f"  · {h}")
    else:
        out.append("  没有发现明显问题。把这份报告连同具体现象一起发出来。")
        out.append("  （没发现问题不等于没问题 —— 具体现象往往比机器状态更能说明问题）")
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
    grab("registry", _sec_registry, layout)
    grab("services", _sec_services, layout)
    grab("ports", _sec_ports, layout)
    grab("pg", _sec_pg, layout)
    grab("backup", _sec_backup, layout)
    grab("runtime", _sec_runtime, layout)
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
    for key in ("basic", "layout", "registry", "services", "ports", "pg", "backup",
                "runtime", "network",
                "firewall", "cert", "config", "logs", "path"):
        body += sections.get(key, [])
    return "\n".join(head + body) + "\n"


def export(layout, out_path: Path | None = None) -> Path:
    """生成并写盘，返回文件路径。"""
    text = build_report(layout)
    if out_path is None:
        # 带上主机名 —— 用户可能在不同机器上各导一份，混在一起分不清谁是谁
        try:
            host = (socket.gethostname() or "unknown").split(".")[0]
        except Exception:
            host = "unknown"
        host = "".join(c for c in host if c.isalnum() or c in "-_") or "unknown"
        stem = time.strftime(f"lanshare-诊断-{host}-%Y%m%d-%H%M%S")
        base = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")
        out_path = base / f"{stem}.txt"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path
