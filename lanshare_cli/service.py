"""Windows 服务注册封装 —— 用 NSSM 把任意 exe+参数 挂成服务。

设计要点（为什么不用 shell 拼字符串）：
    所有参数都以 **list** 传给 subprocess，Windows 自己处理转义。
    今天踩过的坑全部来自字符串拼接：
        Inno 的 Parameters 和 bat 的 nssm set 都要写多层引号嵌套。
    任何一层错了就是 "open C:\\Program: 找不到文件"。
    在 Python 里不存在这个问题，而且能单测、能给出人话错误。

为什么不自己写 Windows 服务（pywin32 / win32serviceutil）：
    pywin32 要 +15MB，还得写 service wrapper（不能直接跑 uvicorn 命令），
    embeddable Python 装 pywin32 有已知问题。NSSM 已验证可用，
    崩溃重启、日志重定向、日志轮转都是现成的。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, Mapping


class ServiceError(RuntimeError):
    """NSSM 操作失败。message 里必须带上执行的命令和原始输出，便于排查。"""


def _decode(raw: bytes) -> str:
    """NSSM 的输出编码不固定（英文系统是 ANSI，中文系统可能混 GBK/UTF-16）。

    探测顺序很重要：UTF-16-LE 的纯 ASCII 文本能被 UTF-8 **成功**解码，
    只是结果里全是 NUL。所以必须先按 NUL 密度判断是不是 UTF-16，
    否则永远走不到那一支，日志会变成一串 S\\x00e\\x00r\\x00 的垃圾。
    """
    if not raw:
        return ""

    null_ratio = raw.count(0) / len(raw)
    candidates = (
        ("utf-16-le", "utf-16-be", "utf-8", "gbk")
        if null_ratio > 0.25
        else ("utf-8", "gbk", "utf-16-le")
    )

    for enc in candidates:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        # 解出来还带控制字符，说明这个编码不对（UTF-8 解 UTF-16 的典型症状）
        if any(ord(ch) < 9 for ch in text):
            continue
        return text.strip("\x00").strip()

    return raw.decode("utf-8", errors="replace").strip("\x00").strip()


def run(cmd: list[str], *, check: bool = True, timeout: int = 60) -> str:
    """执行命令并返回解码后的输出。失败时抛 ServiceError 而不是裸 CalledProcessError。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            # 不要 text=True：不同编码混着来，统一按 bytes 收再自己解
        )
    except FileNotFoundError as exc:
        raise ServiceError(f"找不到可执行文件: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceError(
            f"命令超时（{timeout}s）: {' '.join(cmd)}\n"
            f"常见原因：nssm 不带参数时会弹出 GUI 配置窗口，永远不返回。"
        ) from exc

    out = _decode(proc.stdout) + ("\n" + _decode(proc.stderr) if proc.stderr else "")
    out = out.strip()

    if check and proc.returncode != 0:
        raise ServiceError(
            f"命令失败（exit {proc.returncode}）: {' '.join(cmd)}\n"
            f"输出: {out or '(空)'}"
        )
    return out


class NssmService:
    """一台机器的 NSSM 包装器。刻意不缓存状态 —— 服务可能在两次调用之间被别人改了。"""

    def __init__(self, nssm_path: str | Path) -> None:
        self.nssm = str(Path(nssm_path).resolve())
        if not Path(self.nssm).is_file():
            raise ServiceError(f"nssm.exe 不存在: {self.nssm}")

    # ── 查询 ──────────────────────────────────────────────

    def exists(self, name: str) -> bool:
        """服务是否已注册。用 sc query 而不是 nssm，因为 nssm status 在服务不存在时也返回 0。"""
        proc = subprocess.run(
            ["sc.exe", "query", name], capture_output=True, timeout=30
        )
        # sc query 对不存在的服务返回 1060
        return proc.returncode == 0

    def status(self, name: str) -> str:
        """返回 RUNNING / STOPPED / PAUSED / NOT_FOUND 等。"""
        if not self.exists(name):
            return "NOT_FOUND"
        proc = subprocess.run(
            ["sc.exe", "query", name], capture_output=True, timeout=30
        )
        text = _decode(proc.stdout)
        for state in ("RUNNING", "PAUSED", "STOPPED", "START_PENDING", "STOP_PENDING"):
            if f"1 {state}" in text or f" {state} " in text:
                return state
        return "UNKNOWN"

    def get(self, name: str, param: str) -> str:
        return run([self.nssm, "get", name, param], check=False)

    # ── 写操作 ────────────────────────────────────────────

    def _set(self, name: str, param: str, value: str) -> None:
        run([self.nssm, "set", name, param, value])

    def install(
        self,
        name: str,
        exe: str | Path,
        args: Iterable[str] | str = (),
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        stdout: str | Path | None = None,
        stderr: str | Path | None = None,
        description: str | None = None,
        display_name: str | None = None,
        auto_start: bool = True,
        rotate_bytes: int | None = 10 * 1024 * 1024,
        force: bool = False,
    ) -> None:
        """注册服务。

        幂等策略：默认**拒绝**覆盖已存在的服务。
        理由：nssm install 对已存在的服务是静默跳过，但后面的 set 调用照跑，
        会把正在运行的服务重新指向新路径 —— 这就是之前误改生产 AppDirectory 的成因。
        要覆盖必须显式 force=True。
        """
        if self.exists(name):
            if not force:
                raise ServiceError(
                    f"服务 {name} 已存在。如需重装请先卸载：\n"
                    f"  python -m lanshare_cli uninstall --service {name}\n"
                    f"或使用 --force（会重新指向新路径，正在跑的服务会被打断）"
                )
            self.remove(name, missing_ok=True)

        exe_path = Path(exe).resolve()
        if not exe_path.is_file():
            raise ServiceError(f"服务程序不存在: {exe_path}")

        # 关键：install 必须带 exe。不带参数时 nssm 会弹出 GUI 配置窗口，
        # 调用方 wait 下去就永远不返回（今天的"安装卡死 271 秒"就是这么来的）。
        if isinstance(args, str):
            arg_list = [args]
        else:
            arg_list = [str(a) for a in args]

        run([self.nssm, "install", name, str(exe_path)])

        # 从这里往下是「半成品风险区」：nssm install 已经把服务条目建出来了，
        # 后面任何一步 set 失败都会留下一个配错的僵尸服务。
        # 之前的 bug 就是 AppExit 参数写错，服务建了但没配完，
        # 上层回滚列表里还没有它，结果残留一个 TestShare-api。
        # 所以失败必须在这里就地清理，对上层保证「要么完整成功，要么不留痕迹」。
        try:
            self._configure(
                name,
                arg_list=arg_list,
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                description=description,
                display_name=display_name,
                auto_start=auto_start,
                rotate_bytes=rotate_bytes,
            )
        except Exception:
            self.remove(name, missing_ok=True)
            raise

    def _configure(
        self,
        name: str,
        *,
        arg_list: list[str],
        cwd: str | Path | None,
        env: Mapping[str, str] | None,
        stdout: str | Path | None,
        stderr: str | Path | None,
        description: str | None,
        display_name: str | None,
        auto_start: bool,
        rotate_bytes: int | None,
    ) -> None:
        """install() 的第二阶段。拆出来只为一件事：让失败回滚的范围清晰。"""
        # AppParameters 单独 set。
        # ⚠️ 不能用 " ".join(arg_list)：NSSM 把这一串当命令行喂给程序，
        # 没有引号的话 "C:\Program Files\x\server.py" 会被切成 "C:\Program"。
        # subprocess.list2cmdline 是 Windows 命令行的标准拼接规则，会正确加引号。
        if arg_list:
            self._set(name, "AppParameters", subprocess.list2cmdline(arg_list))
        if cwd is not None:
            self._set(name, "AppDirectory", str(Path(cwd).resolve()))

        # 环境变量：多参数形式，NSSM 会存成独立条目。
        # 注意不能写成 "K1=V1 K2=V2" 单个字符串 —— NSSM 会当成一个变量，
        # 值变成 "V1 K2=V2"，Caddy 读到的端口就永远是默认值。
        if env:
            pairs = [f"{k}={v}" for k, v in env.items()]
            run([self.nssm, "set", name, "AppEnvironmentExtra", *pairs])

        if stdout:
            path = Path(stdout)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._set(name, "AppStdout", str(path))
        if stderr:
            path = Path(stderr)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._set(name, "AppStderr", str(path))

        if rotate_bytes:
            self._set(name, "AppRotateFiles", "1")
            self._set(name, "AppRotateOnline", "1")
            self._set(name, "AppRotateBytes", str(rotate_bytes))

        # 崩溃自动重启，不用人半夜爬起来。
        # 注意 AppExit 要两个独立位置参数（exitcode + action）：
        #   nssm set <svc> AppExit Default Restart
        # 传成 "Default Restart" 单个字符串会报
        #   Parameter "AppExit" requires a subparameter!
        run([self.nssm, "set", name, "AppExit", "Default", "Restart"])
        self._set(name, "AppRestartDelay", "3000")

        if description:
            self._set(name, "Description", description)
        if display_name:
            self._set(name, "DisplayName", display_name)

        self._set(
            name, "Start", "SERVICE_AUTO_START" if auto_start else "SERVICE_DEMAND_START"
        )

    def remove(self, name: str, *, missing_ok: bool = True) -> None:
        """停服务并删除注册。绝不碰数据目录。"""
        if not self.exists(name):
            if missing_ok:
                return
            raise ServiceError(f"服务 {name} 不存在")

        # 先停再删：正在跑的服务 remove 会失败
        run(["sc.exe", "stop", name], check=False, timeout=60)
        # 等服务真的停下来（sc stop 是异步的）
        import time

        for _ in range(20):
            if self.status(name) == "STOPPED":
                break
            time.sleep(0.5)

        run([self.nssm, "remove", name, "confirm"], timeout=60)

    def start(self, name: str, *, wait_seconds: int = 0) -> str:
        """启动服务并（可选）等待它进入 RUNNING。

        返回值是最终状态。PAUSED 通常意味着程序启动后立刻崩了，
        NSSM 正在按 AppRestartDelay 重试 —— 这时要去看 AppStderr 指向的日志。
        """
        if not self.exists(name):
            raise ServiceError(f"服务 {name} 不存在")
        run(["sc.exe", "start", name], check=False, timeout=60)

        if wait_seconds:
            import time

            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                st = self.status(name)
                if st in ("RUNNING", "PAUSED"):
                    return st
                time.sleep(0.5)
        return self.status(name)

    def stop(self, name: str, *, timeout: int = 60) -> str:
        if not self.exists(name):
            return "NOT_FOUND"
        run(["sc.exe", "stop", name], check=False, timeout=timeout)
        return self.status(name)
