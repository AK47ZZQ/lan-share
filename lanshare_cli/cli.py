"""LAN Share 安装/运维命令行。

核心思想：**让程序自己安装自己**。
    安装器（Inno Setup）只做三件事：解压文件、调 `python -m lanshare_cli install`、打开浏览器。
    所有服务编排逻辑都在这里 —— 能单测、能 dry-run、出错能给人话提示。

用法：
    python -m lanshare_cli install                    # 默认装到当前目录，数据在 ProgramData
    python -m lanshare_cli install --dry-run          # 只打印将要做什么，不改任何东西
    python -m lanshare_cli install --data-dir "D:\\LANShare"
    python -m lanshare_cli install --api-port 9911 --web-port 9912 --service-prefix TestShare
    python -m lanshare_cli status
    python -m lanshare_cli uninstall                  # 停服务删注册，数据保留
    python -m lanshare_cli uninstall --purge-data     # 连数据一起删（会二次确认）
    python -m lanshare_cli doctor                     # 环境自检，不动任何东西

为什么要有 --dry-run：
    在已经跑着生产的机器上验证安装逻辑时，必须能做到"只看不动"。
    没有这个开关，测试安装器就得拿生产环境赌运气。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import caddyfile
from .pg import Postgres, PostgresError, generate_password, DEFAULT_PORT as PG_DEFAULT_PORT
from .service import NssmService, ServiceError


DEFAULT_API_PORT = 8766
DEFAULT_WEB_PORT = 8765
# 服务名前缀。**必须跟现役部署区分开**：
# 生产环境用的是 lan-share-api / lan-share-caddy，而 Windows 的服务名比较
# 不区分大小写 —— 写成 "LANShare" 会生成 LANShare-api，跟 lan-share-api
# 撞成同一个服务。所以这里用全小写无连字符的 lanshare。
DEFAULT_SERVICE_PREFIX = "lanshare"


# ──────────────────────────────────────────────────────────────
# 环境探测
# ──────────────────────────────────────────────────────────────


def is_admin() -> bool:
    """注册 Windows 服务必须提权。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """尝试 bind 判断端口是否被占。

    比 netstat 解析可靠：netstat 的输出会随系统语言变化。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # 关掉 TIME_WAIT 复用，避免刚停的服务被误判为"空闲"
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            sock.bind((host, port))
            return False
        except OSError:
            return True


def probe(url: str, timeout: float = 10.0) -> tuple[bool, str]:
    """探针：请求一个 URL，返回 (是否成功, 描述)。全部走标准库，不依赖 httpx。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "lanshare-cli"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(200).decode("utf-8", "replace")
            return resp.status == 200, f"HTTP {resp.status} {body}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ──────────────────────────────────────────────────────────────
# 安装布局
# ──────────────────────────────────────────────────────────────


def resolve_prefix(value: str | None) -> str:
    """把可能为 None 的服务前缀解析成具体值。

    None 的语义是「没指定」，此时用 DEFAULT_SERVICE_PREFIX。
    注意 lan 子命令不用这个 —— 它需要 None 来触发本机服务自动探测。
    """
    return value or DEFAULT_SERVICE_PREFIX


class Layout:
    """一个安装布局的所有路径。测试时指向临时目录即可，不碰真实安装。"""

    def __init__(
        self,
        root: Path,
        data_dir: Path,
        service_prefix: str,
        python_override: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.data_dir = data_dir.resolve()
        self.service_prefix = service_prefix

        # 安装态用 runtime/python.exe（embeddable）；
        # 开发态可以 --python 指向 .venv/Scripts/python.exe 复用它，
        # 免去为了测一次安装又下一遍 82MB 运行时。
        self.python = (
            python_override.resolve() if python_override else self.root / "runtime" / "python.exe"
        )
        self.nssm = self.root / "bin" / "nssm.exe"
        self.caddy = self.root / "bin" / "caddy.exe"
        self.entry = self.root / "server.py"
        self.conf_dir = self.root / "conf"
        self.caddyfile = self.conf_dir / "Caddyfile"
        self.logs = self.root / "logs"
        self.certs = self.root / "certs"
        self.web_dist = self.root / "web" / "dist"
        self.static = self.root / "static"
        self.app_pkg = self.root / "app"

        self.api_service = f"{service_prefix}-api"
        self.caddy_service = f"{service_prefix}-caddy"
        self.pg_service = f"{service_prefix}-postgresql"

        # PostgreSQL 二进制跟着程序走（安装包自带），数据跟着 data_dir 走
        # —— 这样用户把数据目录放到 D 盘时，库和文件一起搬。
        self.pgsql_dir = self.root / "pgsql"
        self.pgdata = self.data_dir / "pgdata"

    def required_files(self) -> list[tuple[Path, str]]:
        return [
            (self.python, "embeddable Python 运行时"),
            (self.nssm, "NSSM（服务包装器）"),
            (self.caddy, "Caddy（Web 反代）"),
            (self.entry, "server.py（服务入口）"),
            (self.pgsql_dir / "bin" / "initdb.exe", "PostgreSQL（initdb）"),
            (self.pgsql_dir / "bin" / "pg_ctl.exe", "PostgreSQL（pg_ctl）"),
        ]

    def required_dirs(self) -> list[tuple[Path, str]]:
        return [
            (self.app_pkg, "后端代码 app/"),
            (self.web_dist, "前端产物 web/dist/"),
            (self.static, "静态资源 static/"),
        ]


# ──────────────────────────────────────────────────────────────
# 输出
# ──────────────────────────────────────────────────────────────


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, total: int, msg: str) -> None:
    say(f"\n[{n}/{total}] {msg}")


def ok(msg: str) -> None:
    say(f"    OK   {msg}")


def warn(msg: str) -> None:
    say(f"    WARN {msg}")


def fail(msg: str) -> None:
    say(f"    FAIL {msg}")


# ──────────────────────────────────────────────────────────────
# install
# ──────────────────────────────────────────────────────────────


REGISTRY_KEY_DEFAULT = "LANShare"


def read_registry_value(name: str, key: str = REGISTRY_KEY_DEFAULT) -> str:
    """从 HKLM\SOFTWARE\<key> 读一个值，读不到返回空串。

    升级时要用它拿回旧密码 —— 每次安装都重新生成密码会让
    已经建好的 PG 角色对不上，库直接连不进去。
    """
    from .lan import read_registry

    return (read_registry(key) or {}).get(name, "") or ""


def write_registry_value(
    name: str, value: str, key: str = REGISTRY_KEY_DEFAULT
) -> None:
    """写一个注册表值。"""
    if not key or any(c in key for c in "\'\"; "):
        raise ValueError(f"非法的注册表键名: {key!r}")

    script = (
        f"$k='HKLM:\SOFTWARE\{key}'; "
        "if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }; "
        f"Set-ItemProperty -Path $k -Name '{name}' -Value '{value}'"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, timeout=30,
    )


def cmd_install(args: argparse.Namespace) -> int:
    layout = Layout(
        root=Path(args.root),
        data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
        service_prefix=resolve_prefix(args.service_prefix),
        python_override=Path(args.python) if args.python else None,
    )
    api_port, web_port = args.api_port, args.web_port
    pg_port = getattr(args, "pg_port", PG_DEFAULT_PORT)
    dry = args.dry_run

    # 密码策略：升级时沿用注册表里的旧密码，全新安装才生成新的。
    # 每次安装都换密码会让 PG 里的旧角色对不上，库直接连不上。
    reg_key = getattr(args, "registry_key", None) or REGISTRY_KEY_DEFAULT
    pg_password = read_registry_value("PgPassword", reg_key) or generate_password()

    say("=" * 64)
    say("  LAN Share 安装")
    say("=" * 64)
    say(f"  安装目录  {layout.root}")
    say(f"  数据目录  {layout.data_dir}")
    say(f"  API 端口  {api_port}")
    say(f"  Web 端口  {web_port}")
    say(f"  数据库端口 {pg_port}")
    say(f"  服务名前缀 {layout.service_prefix}")
    if dry:
        say("\n  *** DRY RUN —— 只检查不执行，不会改动任何东西 ***")

    total = 10

    # ── 1. 权限 ──
    step(1, total, "检查管理员权限")
    if not is_admin():
        fail("需要管理员权限（注册 Windows 服务、写 ProgramData 都要提权）")
        say("\n    右键终端选「以管理员身份运行」，或用安装器启动。")
        return 1
    ok("已是管理员")

    # ── 2. 依赖文件 ──
    step(2, total, "检查运行时文件")
    missing = []
    for path, desc in layout.required_files():
        if path.is_file():
            ok(f"{desc}: {path.name}")
        else:
            fail(f"{desc} 缺失: {path}")
            missing.append(str(path))
    for path, desc in layout.required_dirs():
        if path.is_dir():
            ok(f"{desc}")
        else:
            fail(f"{desc} 缺失: {path}")
            missing.append(str(path))
    if missing:
        say("\n    安装包不完整，无法继续。")
        return 1

    # ── 3. 端口 ──
    step(3, total, "检查端口占用")
    conflict = False
    for port, label in ((api_port, "API"), (web_port, "Web"), (pg_port, "数据库")):
        if port_in_use(port):
            fail(f"{label} 端口 {port} 已被占用")
            conflict = True
        else:
            ok(f"{label} 端口 {port} 空闲")
    if conflict:
        # 提示要带上实际冲突的端口，别写死 8766（换端口后提示就对不上了）
        say(f"\n    换个端口重试，例如：--api-port {api_port + 1} --web-port {web_port + 1}")
        busy = [str(p) for p, lbl in ((api_port, "API"), (web_port, "Web"))
                if port_in_use(p)]
        say(f"    查看谁占用了：netstat -ano | findstr \":{'|:'.join(busy)}\"")
        return 1

    # ── 4. 已有服务 ──
    step(4, total, "检查是否已安装")
    try:
        nssm = NssmService(layout.nssm)
    except ServiceError as exc:
        fail(str(exc))
        return 1

    existing = [n for n in (layout.api_service, layout.caddy_service) if nssm.exists(n)]
    if existing:
        if not args.force:
            fail(f"服务已存在: {', '.join(existing)}")
            say("\n    要重装请加 --force（会先卸载旧服务，数据不删）")
            return 1
        warn(f"将覆盖已有服务: {', '.join(existing)}")
    else:
        ok("无冲突服务")

    if dry:
        say("\n  DRY RUN 检查全部通过。实际安装会执行：")
        say(f"    1. 建目录 {layout.data_dir}, {layout.logs}")
        say(f"    2. 生成 {layout.caddyfile}")
        say(f"    3. 注册服务 {layout.api_service} / {layout.caddy_service}")
        say("    4. 启动并做健康检查")
        return 0

    # ── 5. 目录 ──
    step(5, total, "创建目录")
    for d in (layout.data_dir, layout.logs, layout.certs):
        try:
            d.mkdir(parents=True, exist_ok=True)
            ok(str(d))
        except OSError as exc:
            fail(f"建不了 {d}: {exc}")
            say("\n    数据目录如果选在 C 盘以外，确认那个盘存在且有写权限。")
            return 1

    # ── 6. Caddyfile ──
    step(6, total, "生成 Caddyfile")
    try:
        # 先看旧配置里用户选的是「局域网开」还是「关」。
        # 不看的话，修复安装 / 升级会把用户开好的局域网静默重置成关闭 ——
        # 这个重置没有任何提示，用户只会觉得「怎么突然手机又连不上了」。
        previous_bind = caddyfile.detect_bind_host(layout.caddyfile)
        if previous_bind is None:
            bind_host = caddyfile.BIND_ALL      # 全新安装：默认开启（原有行为）
            say("    全新安装，局域网访问默认开启")
        else:
            bind_host = previous_bind
            mode = "开启" if bind_host == caddyfile.BIND_ALL else "关闭（仅本机）"
            ok(f"沿用上次的局域网设置：{mode}")

        caddyfile.write(
            layout.caddyfile,
            api_port=api_port,
            web_port=web_port,
            bind_host=bind_host,
        )
        ok(str(layout.caddyfile))
        # 用 caddy 自己校验，别等启动才发现语法错
        result = subprocess.run(
            [str(layout.caddy), "validate", "--config", str(layout.caddyfile),
             "--adapter", "caddyfile"],
            capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            fail("Caddyfile 校验失败")
            say(result.stderr.decode("utf-8", "replace")[:800])
            return 1
        ok("Caddy 语法校验通过")
    except (OSError, ValueError) as exc:
        fail(f"{exc}")
        return 1

    # ── 7. 初始化数据库 ──
    # 只跑 PostgreSQL 一个数据库（2026-10-31 主人拍板：不再在 SQLite/PG 之间切）
    step(7, total, "初始化 PostgreSQL")

    pg = Postgres(
        root=layout.root,
        data_dir=layout.data_dir,
        prefix=layout.service_prefix,
        port=pg_port,
        password=pg_password,
    )

    try:
        if pg.is_initialized():
            ok(f"数据目录已存在（PG {pg.installed_version()}）：{pg.pgdata}")
        else:
            say(f"    初始化数据目录 {pg.pgdata}")
            pg.initdb()
            ok(f"已初始化（PG {pg.installed_version()}）")

        # 每次重写 conf，保证端口跟着参数走（用户可能换端口重装）
        pg.write_conf()
        ok(f"端口 {pg.port}，仅监听 127.0.0.1")

        # 刻意不在这里 pg_ctl start。
        #
        # 服务托管和手动启动是两种模式，同时用会打架：手动起的 PG 占着端口，
        # 服务再启动时发现端口被占，NSSM 直接把服务置成 PAUSED。
        # 所以让服务来启动（服务里跑的就是 pg_ctl runservice），
        # 建库也挪到服务起来之后做。

    except (PostgresError, OSError) as exc:
        fail(f"数据库初始化失败：{exc}")
        say("")
        say(f"    日志: {layout.logs / 'postgres.log'}")
        return 1

    # ── 8. 注册服务 ──
    step(8, total, "注册服务")
    registered: list[str] = []
    try:
        nssm.install(
            layout.api_service,
            exe=layout.python,
            args=[str(layout.entry)],
            cwd=layout.root,
            env={
                "SHARE_ROOT": str(layout.data_dir),
                "LANSHARE_API_PORT": str(api_port),
                # 只跑 PostgreSQL。不设这个变量时 app/db.py 会退到 SQLite，
                # 那不是我们要的行为 —— 必须显式给。
                "DATABASE_URL": pg.connection_url(),
            },
            stdout=layout.logs / "api-stdout.log",
            stderr=layout.logs / "api-stderr.log",
            description=f"LAN Share 后端 API（端口 {api_port}）",
            display_name="LAN Share API",
            force=args.force,
        )
        registered.append(layout.api_service)
        ok(layout.api_service)

        nssm.install(
            layout.caddy_service,
            exe=layout.caddy,
            # 相对路径：AppDirectory 已经是 root，配置文件就不用写绝对路径，
            # 也就不会遇到 "C:\Program Files" 被空格切断的老坑
            args=["run", "--config", "conf\\Caddyfile", "--adapter", "caddyfile"],
            cwd=layout.root,
            stdout=layout.logs / "caddy-stdout.log",
            stderr=layout.logs / "caddy-stderr.log",
            description=f"LAN Share Web 前端（端口 {web_port}）",
            display_name="LAN Share Web",
            force=args.force,
        )
        registered.append(layout.caddy_service)
        ok(layout.caddy_service)

        # PG 用 pg_ctl runservice 托管（理由见 pg.py）
        pg.install_service(nssm)
        registered.append(layout.pg_service)
        ok(layout.pg_service)
    except (ServiceError, PostgresError) as exc:
        fail(str(exc))
        say("\n    回滚：删掉本次已注册的服务")
        for name in registered:
            try:
                nssm.remove(name)
                ok(f"已回滚 {name}")
            except ServiceError as rexc:
                warn(f"回滚 {name} 失败: {rexc}")
        return 1

    # ── 9. 启动 + 探针 ──
    # 顺序必须 数据库 → API → Web：
    # API 启动时就连库，库没好 API 直接崩；Caddy 反代到 API，API 没好是 502。
    step(9, total, "启动并做健康检查")
    try:
        st = nssm.start(layout.pg_service, wait_seconds=60)
        if st == "RUNNING":
            ok(f"{layout.pg_service}: {st}")
        else:
            fail(f"{layout.pg_service}: {st}")
            show_tail(layout.logs / "postgres.log")
            return 1

        pg_ready = False
        for _ in range(30):
            try:
                if pg.query_one("SELECT 1;") == "1":
                    pg_ready = True
                    break
            except PostgresError:
                pass
            time.sleep(1)
        if not pg_ready:
            fail("PostgreSQL 在 30 秒内没有接受连接")
            show_tail(layout.logs / "postgres.log")
            return 1
        ok("数据库探针: SELECT 1 通过")

        # 库必须在 API 启动前建好 —— API 一起来就连库，库不存在会直接崩
        pg.ensure_database()
        ok(f"库 {pg.db_name} / 用户 {pg.db_user} 就绪")

        st = nssm.start(layout.api_service, wait_seconds=30)
        if st == "RUNNING":
            ok(f"{layout.api_service}: {st}")
        else:
            fail(f"{layout.api_service}: {st}")
            say(f"\n    服务没起来。看日志：{layout.logs / 'api-stderr.log'}")
            show_tail(layout.logs / "api-stderr.log")
            return 1

        # 等 API 真的能响应（服务状态 RUNNING 不代表端口已监听）
        healthy = False
        for _ in range(30):
            okay, desc = probe(f"http://127.0.0.1:{api_port}/healthz", timeout=3)
            if okay:
                ok(f"API 探针: {desc[:120]}")
                healthy = True
                break
            time.sleep(1)
        if not healthy:
            fail(f"API 端口 {api_port} 在 30 秒内没有响应")
            show_tail(layout.logs / "api-stderr.log")
            return 1

        st = nssm.start(layout.caddy_service, wait_seconds=30)
        if st == "RUNNING":
            ok(f"{layout.caddy_service}: {st}")
        else:
            fail(f"{layout.caddy_service}: {st}")
            show_tail(layout.logs / "caddy-stderr.log")
            return 1

        healthy = False
        for _ in range(20):
            okay, desc = probe(f"http://127.0.0.1:{web_port}/healthz", timeout=3)
            if okay:
                ok(f"Web 探针: {desc[:120]}")
                healthy = True
                break
            time.sleep(1)
        if not healthy:
            fail(f"Web 端口 {web_port} 在 20 秒内没有响应")
            show_tail(layout.logs / "caddy-stderr.log")
            return 1
    except ServiceError as exc:
        fail(str(exc))
        return 1

    say("\n" + "=" * 64)
    # 记下密码和端口，下次升级要用同一条 —— 换密码等于把库锁死
    write_registry_value("PgPassword", pg_password, reg_key)
    write_registry_value("PgPort", str(pg_port), reg_key)
    # 前缀必须回写：安装器的 [Registry] 段写的是静态值，万一和 CLI 实际用的
    # 前缀不一致，托盘会按错的名字去找服务（lanshare-api vs lan-share-api）。
    # 这里用真正生效的 layout.service_prefix 覆盖，保证唯一事实源。
    write_registry_value("ServicePrefix", layout.service_prefix, reg_key)

    say("  安装完成")
    say("=" * 64)
    say(f"  浏览器打开  http://127.0.0.1:{web_port}")
    say(f"  局域网访问  http://<本机IP>:{web_port}")
    say(f"  数据目录    {layout.data_dir}")
    say("  卸载        python -m lanshare_cli uninstall")
    return 0


def show_tail(path: Path, lines: int = 12) -> None:
    """打印日志尾部。日志文件可能不存在（服务根本没启动到写日志那步）。"""
    if not path.is_file():
        say(f"    (日志文件不存在: {path})")
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    tail = text.strip().splitlines()[-lines:]
    if tail:
        say("    ---- 日志尾部 ----")
        for line in tail:
            say(f"    {line}")


# ──────────────────────────────────────────────────────────────
# 其他子命令
# ──────────────────────────────────────────────────────────────


def _sc_exists(name: str) -> bool:
    r = subprocess.run(["sc.exe", "query", name], capture_output=True, timeout=20)
    return r.returncode == 0


def _sc_delete(name: str) -> bool:
    """停止并删除一个非 NSSM 的服务（PG 用 pg_ctl register 注册，不走 NSSM）。"""
    subprocess.run(["sc.exe", "stop", name], capture_output=True, timeout=90)

    # 等它真的停下来 —— 还在跑就删不掉，而且 PG 不停的话数据文件被占用，
    # 后面 --purge-data 会删不干净
    for _ in range(30):
        r = subprocess.run(
            ["sc.exe", "query", name], capture_output=True, timeout=20
        )
        if r.returncode != 0 or b"STOPPED" in r.stdout:
            break
        time.sleep(1)

    r = subprocess.run(["sc.exe", "delete", name], capture_output=True, timeout=30)
    return r.returncode == 0


def _stop_tray() -> None:
    """停掉托盘进程。

    不停的话它占着 LanShareTray.exe，卸载器删不掉安装目录，
    用户会看到「文件正在使用」然后留一个半残目录。
    """
    script = (
        "Get-Process -Name 'LanShareTray' -ErrorAction SilentlyContinue | "
        "ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, timeout=30,
    )
    time.sleep(1)


def _delete_registry_key(key: str) -> bool:
    """删除 HKLM\SOFTWARE\<key>。"""
    if not key or any(c in key for c in "\'\"; "):
        return False
    script = (
        f"$k='HKLM:\SOFTWARE\{key}'; "
        "if (Test-Path $k) { Remove-Item -Path $k -Recurse -Force }"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, timeout=30,
    )
    return r.returncode == 0


def _clean_path_entries(install_root: str) -> int:
    """从系统 PATH 移除指向本安装目录的条目，返回移除条数。

    老版本安装器用 NeedsAddPath 做去重，那个检查实际失效了 ——
    2026-10-31 在本机查到 PATH 里同一路径重复了 17 次。卸载时清掉，
    免得越积越多（PATH 太长会导致部分老程序读不全）。
    """
    root = install_root.rstrip("\\")
    script = (
        "$old = [Environment]::GetEnvironmentVariable('Path','Machine'); "
        "$parts = $old -split ';'; "
        f"$kept = $parts | Where-Object {{ $_ -notlike '*{root}*' }}; "
        "if ($kept.Count -ne $parts.Count) { "
        "  [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'Machine'); "
        "  Write-Output ($parts.Count - $kept.Count) "
        "} else { Write-Output '0' }"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    try:
        return int((r.stdout or "0").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    layout = Layout(
        root=Path(args.root),
        data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
        service_prefix=resolve_prefix(args.service_prefix),
        python_override=Path(args.python) if args.python else None,
    )
    try:
        nssm = NssmService(layout.nssm)
    except ServiceError as exc:
        fail(str(exc))
        return 1

    reg_key = getattr(args, "registry_key", None) or REGISTRY_KEY_DEFAULT
    dry = getattr(args, "dry_run", False)

    say("卸载 LAN Share")
    say(f"  安装目录 {layout.root}")
    say(f"  数据目录 {layout.data_dir}")
    if dry:
        say("  （dry-run，不会真的改动系统）")

    # ── 1. 先停托盘 ──
    # 它占着 exe 文件，不先停，后面删目录会失败。
    if not dry:
        _stop_tray()
        ok("已停止托盘程序")

    # ── 2. 停服务 ──
    # 顺序：Web → API → 数据库。API 连着库，先停 API 再停库；
    # Web 反代到 API，所以 Web 最先停。
    for name in (layout.caddy_service, layout.api_service):
        if dry:
            ok(f"[dry-run] 将移除服务 {name}")
            continue
        if nssm.exists(name):
            try:
                nssm.remove(name)
                ok(f"已移除服务 {name}")
            except ServiceError as exc:
                fail(f"{name}: {exc}")
        else:
            ok(f"{name} 本来就不存在")

    # ── 3. 数据库服务 ──
    # PG 服务不是 NSSM 管的（pg_ctl register 创建），要用 sc delete
    if dry:
        ok(f"[dry-run] 将移除服务 {layout.pg_service}")
    elif _sc_exists(layout.pg_service):
        if _sc_delete(layout.pg_service):
            ok(f"已移除服务 {layout.pg_service}")
        else:
            fail(f"{layout.pg_service}: 删除失败（可能需要管理员权限）")
    else:
        ok(f"{layout.pg_service} 本来就不存在")

    # ── 4. 防火墙规则 ──
    # 不清的话，下次换端口重装会积一堆废旧放行规则；
    # 更糟的是端口被别的程序占了，那些规则等于给别人开门。
    try:
        from . import firewall

        # 只删本实例端口的规则 —— 同机多实例时别把别人的一起删了。
        # 端口从命令行参数取，没传就按默认值（和 install 一致）。
        fw_ports = [
            getattr(args, "web_port", 8765),
            getattr(args, "api_port", 8766),
        ]
        done, msg = firewall.remove_rules(ports=fw_ports, dry_run=dry)
        (ok if done else fail)(msg)
    except Exception as exc:
        fail(f"清理防火墙规则失败：{exc}")

    # ── 5. 注册表 ──
    if dry:
        ok(f"[dry-run] 将删除 HKLM\SOFTWARE\{reg_key}")
    elif _delete_registry_key(reg_key):
        ok(f"已删除注册表 HKLM\SOFTWARE\{reg_key}")
    else:
        fail(f"删除注册表 HKLM\SOFTWARE\{reg_key} 失败")

    # ── 6. 系统 PATH ──
    if not dry:
        n = _clean_path_entries(str(layout.root))
        if n:
            ok(f"已从系统 PATH 移除 {n} 条残留")
        else:
            ok("系统 PATH 无残留")

    # ── 7. 数据 ──
    if args.purge_data:
        if layout.data_dir.is_dir():
            say("")
            say(f"  即将永久删除数据目录: {layout.data_dir}")
            answer = input("  确认删除输入 yes（其他任何输入都会保留数据）: ").strip()
            if answer.lower() == "yes":
                shutil.rmtree(layout.data_dir, ignore_errors=True)
                ok("数据已删除")
            else:
                ok("数据已保留")
        else:
            ok("数据目录本来就不存在")
    else:
        ok(f"数据保留在 {layout.data_dir}")

    say("")
    say("卸载完成。安装文件可以手动删除了。" if not dry else "dry-run 结束，什么都没改。")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    layout = Layout(
        root=Path(args.root),
        data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
        service_prefix=resolve_prefix(args.service_prefix),
        python_override=Path(args.python) if args.python else None,
    )
    try:
        nssm = NssmService(layout.nssm)
    except ServiceError as exc:
        fail(str(exc))
        return 1

    say("LAN Share 状态")
    for name in (layout.api_service, layout.caddy_service):
        st = nssm.status(name)
        marker = "OK  " if st == "RUNNING" else "    "
        say(f"  {marker}{name:28s} {st}")

    for port, label in ((args.api_port, "API"), (args.web_port, "Web")):
        okay, desc = probe(f"http://127.0.0.1:{port}/healthz", timeout=3)
        say(f"  {'OK  ' if okay else '    '}{label} :{port:<20} {desc[:80]}")

    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """环境自检。只读，不改任何东西。"""
    layout = Layout(
        root=Path(args.root),
        data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
        service_prefix=resolve_prefix(args.service_prefix),
        python_override=Path(args.python) if args.python else None,
    )
    say("LAN Share 环境自检\n")

    say(f"  管理员权限      {'OK' if is_admin() else 'FAIL（注册服务需要）'}")

    for path, desc in layout.required_files() + layout.required_dirs():
        say(f"  {desc:24s} {'OK  ' if path.exists() else 'MISSING'} {path}")

    for port, label in ((args.api_port, "API"), (args.web_port, "Web")):
        used = port_in_use(port)
        say(f"  {label} 端口 {port:<6} {'占用' if used else '空闲'}")

    say("  数据目录可写    ", )
    try:
        layout.data_dir.mkdir(parents=True, exist_ok=True)
        probe_file = layout.data_dir / ".write-test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        say("    OK")
    except OSError as exc:
        say(f"    FAIL {exc}")

    return 0


def default_data_dir() -> Path:
    """默认数据目录：ProgramData 下。用户可以在安装时改。"""
    base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return Path(base) / "LANShare" / "data"


# ──────────────────────────────────────────────────────────────
# lan（局域网访问开关）
# ──────────────────────────────────────────────────────────────


def _print_report(report, verbose: bool = True) -> None:
    """把 ActionReport 打成人类可读的样子。"""
    for name, ok_flag, detail in report.steps:
        mark = "OK  " if ok_flag else "FAIL"
        say(f"    {mark} {name}" + (f"：{detail}" if detail else ""))


def cmd_lan(args: argparse.Namespace) -> int:
    """局域网访问开关：status / enable / disable / diagnose。"""
    from . import lan

    action = getattr(args, "lan_action", "status")

    info = lan.load_install_info(
        root=getattr(args, "root", None),
        api_port=getattr(args, "api_port", None),
        web_port=getattr(args, "web_port", None),
        service_prefix=getattr(args, "service_prefix", None),
        data_dir=getattr(args, "data_dir", None),
    )

    if action == "status":
        status = lan.collect_status(info)

        if args.json:
            print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2))
            return 0 if status.state in (lan.LanState.ON, lan.LanState.OFF) else 1

        say()
        say(f"  状态    {status.state.label}")
        say(f"  端口    Web {status.web_port}  API {status.api_port}")
        if status.listening:
            say(f"  监听    {', '.join(status.listening)}")
        if status.firewall:
            say(f"  防火墙  {status.firewall}")
        if status.addresses:
            say("  地址")
            for addr in status.addresses:
                url = status.url_for(addr)
                tag = "（默认路由）" if addr["has_gateway"] else ""
                say(f"          {url}  {addr['alias']} {tag}")
        else:
            say("  地址    无可用局域网连接")
        if status.probe_url:
            say(f"  自检    {status.probe_url} → {status.probe_msg}")
        if status.hints:
            say()
            for hint in status.hints:
                say(f"  ! {hint}")
        say()
        return 0 if status.state in (lan.LanState.ON, lan.LanState.OFF) else 1

    if action == "enable":
        report = lan.enable_lan(
            info, dry_run=args.dry_run, allow_public=getattr(args, "allow_public", False)
        )
        if args.json:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            return 0 if report.success else 1

        say()
        _print_report(report)
        say()
        if report.success:
            say("  局域网访问已开启。")
            for addr in lan.collect_status(info, skip_probe=True).addresses:
                say(f"    http://{addr['ip']}:{info.web_port}")
        elif report.cancelled:
            say("  已取消（需要管理员权限才能配置防火墙）。局域网仍为关闭状态。")
        else:
            say("  开启失败，上面是失败的那一步。用 `lan diagnose` 看详情。")
        say()
        return 0 if report.success else 1

    if action == "disable":
        report = lan.disable_lan(info, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            return 0 if report.success else 1

        say()
        _print_report(report)
        say()
        if report.success:
            say("  局域网访问已关闭，现在只有这台电脑能访问。")
            say("  （防火墙规则保留未动，卸载时才清理）")
        else:
            say("  关闭失败，上面是失败的那一步。")
        say()
        return 0 if report.success else 1

    if action == "diagnose":
        result = lan.diagnose(info)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        say()
        say(f"  状态  {result['state_label']}")
        say()
        for item in result["checks"]:
            mark = "OK  " if item["ok"] else "FAIL"
            say(f"    {mark} {item['check']}")
            if item["detail"]:
                say(f"         {item['detail']}")
        if result["hints"]:
            say()
            say("  可能的原因：")
            for hint in result["hints"]:
                say(f"    - {hint}")
        say()
        return 0

    fail(f"未知动作: {action}")
    return 2


# ──────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lanshare_cli",
        description="LAN Share 安装与运维命令行",
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="安装根目录（默认：本包所在目录的上一级）",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help=f"数据目录（默认：{default_data_dir()}）",
    )
    parser.add_argument(
        "--python",
        default=None,
        help="覆盖服务用的 python.exe（开发态指 .venv/Scripts/python.exe）",
    )
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument(
        "--registry-key",
        default=REGISTRY_KEY_DEFAULT,
        help="注册表键名（HKLM\SOFTWARE\<key>），默认 LANShare。"
             "测试实例务必用独立键，否则会改到正式安装的配置",
    )
    parser.add_argument(
        "--pg-port",
        type=int,
        default=PG_DEFAULT_PORT,
        help=f"PostgreSQL 端口（默认 {PG_DEFAULT_PORT}，选默认值是为了跟本机自装的 PG 错开）",
    )
    parser.add_argument(
        "--service-prefix",
        default=None,
        help=f"Windows 服务名前缀（默认自动探测，探测不到用 {DEFAULT_SERVICE_PREFIX}）",
    )

    # 子命令也要能接受这些参数（`install --api-port 9911` 这种写法更自然）。
    # 用 SUPPRESS 做默认值：没显式指定就不写入 namespace，
    # 这样 `--api-port 9911 install` 和 `install --api-port 9911` 两种写法都对，
    # 不会出现子命令的默认值把顶层值覆盖回去的经典问题。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS)
    common.add_argument("--data-dir", default=argparse.SUPPRESS)
    common.add_argument("--python", default=argparse.SUPPRESS)
    common.add_argument("--api-port", type=int, default=argparse.SUPPRESS)
    common.add_argument("--web-port", type=int, default=argparse.SUPPRESS)
    common.add_argument("--pg-port", type=int, default=argparse.SUPPRESS)
    common.add_argument("--registry-key", default=argparse.SUPPRESS)
    common.add_argument("--service-prefix", default=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", parents=[common], help="注册服务并启动")
    p_install.add_argument("--dry-run", action="store_true", help="只检查不执行")
    p_install.add_argument("--force", action="store_true", help="覆盖已存在的服务")
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", parents=[common], help="停止并删除服务")
    p_uninstall.add_argument(
        "--purge-data", action="store_true", help="连数据一起删（需二次确认）"
    )
    p_uninstall.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印会做什么，不改系统",
    )
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_status = sub.add_parser("status", parents=[common], help="查看服务与端口状态")
    p_status.set_defaults(func=cmd_status)

    p_doctor = sub.add_parser("doctor", parents=[common], help="环境自检（只读）")
    p_doctor.set_defaults(func=cmd_doctor)

    p_lan = sub.add_parser(
        "lan", parents=[common], help="局域网访问开关（status/enable/disable/diagnose）"
    )
    p_lan.add_argument(
        "lan_action",
        nargs="?",
        default="status",
        choices=["status", "enable", "disable", "diagnose"],
        help="默认 status",
    )
    p_lan.add_argument("--json", action="store_true", help="输出 JSON（给 GUI 用）")
    p_lan.add_argument("--dry-run", action="store_true", help="只检查不执行")
    p_lan.add_argument(
        "--allow-public",
        action="store_true",
        help="当前是公用网络时也继续（调用方已向用户确认过风险）",
    )
    p_lan.set_defaults(func=cmd_lan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 子命令的端口/服务前缀没定义时，用顶层参数的值（argparse 的经典问题：
    # 顶层 --api-port 和子命令解析出来的 namespace 会互相覆盖）
    for attr, default in (
        ("api_port", DEFAULT_API_PORT),
        ("web_port", DEFAULT_WEB_PORT),
        ("service_prefix", None),
        ("root", str(Path(__file__).resolve().parent.parent)),
        ("data_dir", None),
        ("python", None),
    ):
        if not hasattr(args, attr):
            setattr(args, attr, default)

    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        say("\n已中断")
        return 130
    except ServiceError as exc:
        fail(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
