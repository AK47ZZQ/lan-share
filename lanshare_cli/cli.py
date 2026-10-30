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

    def required_files(self) -> list[tuple[Path, str]]:
        return [
            (self.python, "embeddable Python 运行时"),
            (self.nssm, "NSSM（服务包装器）"),
            (self.caddy, "Caddy（Web 反代）"),
            (self.entry, "server.py（服务入口）"),
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


def cmd_install(args: argparse.Namespace) -> int:
    layout = Layout(
        root=Path(args.root),
        data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
        service_prefix=args.service_prefix,
        python_override=Path(args.python) if args.python else None,
    )
    api_port, web_port = args.api_port, args.web_port
    dry = args.dry_run

    say("=" * 64)
    say("  LAN Share 安装")
    say("=" * 64)
    say(f"  安装目录  {layout.root}")
    say(f"  数据目录  {layout.data_dir}")
    say(f"  API 端口  {api_port}")
    say(f"  Web 端口  {web_port}")
    say(f"  服务名前缀 {layout.service_prefix}")
    if dry:
        say("\n  *** DRY RUN —— 只检查不执行，不会改动任何东西 ***")

    total = 8

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
    for port, label in ((api_port, "API"), (web_port, "Web")):
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
        caddyfile.write(layout.caddyfile, api_port=api_port, web_port=web_port)
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

    # ── 7. 注册服务 ──
    step(7, total, "注册服务")
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
    except ServiceError as exc:
        fail(str(exc))
        say("\n    回滚：删掉本次已注册的服务")
        for name in registered:
            try:
                nssm.remove(name)
                ok(f"已回滚 {name}")
            except ServiceError as rexc:
                warn(f"回滚 {name} 失败: {rexc}")
        return 1

    # ── 8. 启动 + 探针 ──
    step(8, total, "启动并做健康检查")
    try:
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


def cmd_uninstall(args: argparse.Namespace) -> int:
    layout = Layout(
        root=Path(args.root),
        data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
        service_prefix=args.service_prefix,
        python_override=Path(args.python) if args.python else None,
    )
    try:
        nssm = NssmService(layout.nssm)
    except ServiceError as exc:
        fail(str(exc))
        return 1

    say("卸载 LAN Share")
    say(f"  安装目录 {layout.root}")
    say(f"  数据目录 {layout.data_dir}")

    for name in (layout.caddy_service, layout.api_service):  # 先停 Web 再停 API
        if nssm.exists(name):
            try:
                nssm.remove(name)
                ok(f"已移除服务 {name}")
            except ServiceError as exc:
                fail(f"{name}: {exc}")
        else:
            ok(f"{name} 本来就不存在")

    if args.purge_data:
        if layout.data_dir.is_dir():
            say(f"\n  即将永久删除数据目录: {layout.data_dir}")
            answer = input("  确认删除输入 yes（其他任何输入都会保留数据）: ").strip()
            if answer.lower() == "yes":
                shutil.rmtree(layout.data_dir, ignore_errors=True)
                ok("数据已删除")
            else:
                ok("数据已保留")
    else:
        ok(f"数据保留在 {layout.data_dir}")

    say("\n卸载完成。安装文件可以手动删除了。")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    layout = Layout(
        root=Path(args.root),
        data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
        service_prefix=args.service_prefix,
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
        service_prefix=args.service_prefix,
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
        "--service-prefix",
        default=DEFAULT_SERVICE_PREFIX,
        help="Windows 服务名前缀（测试时改成别的，避免和现役服务冲突）",
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
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_status = sub.add_parser("status", parents=[common], help="查看服务与端口状态")
    p_status.set_defaults(func=cmd_status)

    p_doctor = sub.add_parser("doctor", parents=[common], help="环境自检（只读）")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 子命令的端口/服务前缀没定义时，用顶层参数的值（argparse 的经典问题：
    # 顶层 --api-port 和子命令解析出来的 namespace 会互相覆盖）
    for attr, default in (
        ("api_port", DEFAULT_API_PORT),
        ("web_port", DEFAULT_WEB_PORT),
        ("service_prefix", DEFAULT_SERVICE_PREFIX),
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
