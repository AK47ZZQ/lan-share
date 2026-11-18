"""Cloudflare 隧道的启停控制（Windows 服务 / 进程两种形态）。

## 为什么只做 Cloudflare，不做 FRP

FRP 的 `frpc` 是用户自己起的外部进程，程序既不知道它装在哪、也不知道
它叫什么（樱花客户端叫 `SakuraFrpLauncher.exe`，原版叫 `frpc.exe`，
Docker 里根本没有本机进程）。瞎猜着去杀进程，很可能误杀用户**别的**
隧道（同一个人常拿 frpc 转发多个服务）。所以 FRP 侧只给提示：
「程序管不到，请自己关」。

## 两种形态的差别

- **服务形态**（`nssm` 装的服务 / 官方 `cloudflared service install`）
  能停、能起、能查状态。这是可恢复的。

- **前台进程形态**（用户在自己终端里 `cloudflared tunnel run`）
  只能杀。**杀了之后程序没法帮你起回来** —— 因为不知道当初的命令行参数
  （隧道名、`--config` 路径都可能不同）。所以这种情况要明确告诉用户。

## 权限

本模块由 lan-share-api 进程调用，那个进程以 LocalSystem 运行，
停/起服务不需要额外提权。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 用绝对路径调系统命令。
# lan-share-api 以 LocalSystem 运行，而 CreateProcess 解析裸名会先搜应用目录
# （NSSM 的 AppDirectory=C:\lanshare）和当前目录 —— 任何能往这些目录写文件的
# 低权用户，放一个 sc.exe 就能在用户点「停止隧道」时以 SYSTEM 执行任意程序。
_SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")


def _exe(name: str) -> str:
    path = os.path.join(_SYSTEM32, name)
    return path if os.path.isfile(path) else name

# 按可能性排序。第一项是我们自己的安装器用的名字。
_SERVICE_CANDIDATES = ("LANShareTunnel", "cloudflared", "Cloudflared",
                       "CloudflareTunnel", "cloudflared-tunnel")

# sc 的 STATE 码
_STATE_NAMES = {1: "stopped", 2: "start_pending", 3: "stop_pending",
                4: "running", 5: "continue_pending", 6: "pause_pending",
                7: "paused"}


def _run(args: list, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          # 中文 Windows 的 sc / tasklist 输出是 GBK，而本机
                          # PYTHONUTF8=1 时 text=True 会按 UTF-8 解码，在
                          # subprocess 的 reader 线程里抛 UnicodeDecodeError
                          # （异常被吞、stdout 变空 → 服务被判成「不存在」）。
                          # 2026-11-18 审查实测：每次 status 刷 4 段 traceback。
                          encoding="utf-8", errors="replace",
                          creationflags=_CREATE_NO_WINDOW)


def _service_state(name: str) -> str:
    """查服务状态。不存在返回 ""，存在返回 running/stopped/... """
    try:
        proc = _run([_exe("sc.exe"), "query", name])
    except Exception:
        return ""
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 and "1060" in out:
        return ""                                 # 服务不存在
    match = re.search(r"STATE\s*:\s*(\d+)", out)
    if not match:
        return ""
    return _STATE_NAMES.get(int(match.group(1)), "unknown")


# 算「活着」的状态：这些都不该掉进进程兜底分支
_ALIVE_STATES = ("running", "start_pending", "stop_pending",
                 "continue_pending", "paused")


def _find_service() -> tuple:
    """找出一条 cloudflared 服务。**优先返回活着的那个**。

    为什么不能「第一个存在就返回」：若 LANShareTunnel 已 stopped、机器上另有
    cloudflared 在 running，返回前者会让停止/启动/状态三处全部对准错的对象 ——
    用户看到「隧道没跑」，而公网其实正连着（2026-11-18 审查实测）。
    """
    found = []
    for name in _SERVICE_CANDIDATES:
        state = _service_state(name)
        if state:
            found.append((name, state))
    if not found:
        return "", ""
    for name, state in found:
        if state in _ALIVE_STATES:
            return name, state
    return found[0]


def _process_running() -> tuple:
    """cloudflared 进程是否在跑。返回 (running, pid)。"""
    if sys.platform != "win32":
        return False, 0
    try:
        proc = _run([_exe("tasklist.exe"), "/FI", "IMAGENAME eq cloudflared.exe", "/FO", "CSV", "/NH"])
    except Exception:
        return False, 0
    text = (proc.stdout or "").strip()
    if "cloudflared.exe" not in text.lower():
        return False, 0
    match = re.search(r'"cloudflared\.exe","(\d+)"', text, re.IGNORECASE)
    return True, int(match.group(1)) if match else 0


def tunnel_status() -> dict:
    """汇总隧道当前状态。托盘用它渲染开关。

    {
      "supported": true,          # 非 Windows 为 false
      "service": "LANShareTunnel",# 找到的服务名，空 = 没有服务形态
      "service_state": "running", # 空 = 没服务
      "process": true,            # 进程在不在
      "pid": 24884,
      "controllable": true,       # 能不能可靠地启停（服务形态才行）
      "note": "…"                 # 给用户看的一句话
    }
    """
    if sys.platform != "win32":
        return {"supported": False, "service": "", "service_state": "",
                "process": False, "pid": 0, "controllable": False,
                "note": "当前平台不支持隧道控制"}

    name, state = _find_service()
    running, pid = _process_running()

    if name and state == "running":
        note = f"服务 {name} 正在运行"
    elif name:
        note = f"服务 {name} 已安装但{state}，点击启动"
    elif running:
        note = ("检测到前台运行的 cloudflared 进程，可以停止，"
                "但程序无法帮你重新启动（不知道启动参数）")
    else:
        note = "没检测到隧道在跑"

    return {
        "supported": True,
        "service": name,
        "service_state": state,
        "process": running,
        "pid": pid,
        "controllable": bool(name),   # 只有服务形态能可靠重启
        "note": note,
    }


def tunnel_stop() -> dict:
    """停隧道：服务优先，进程兜底。

    返回 {"ok": bool, "method": "service"|"process"|"", "detail": str}
    """
    if sys.platform != "win32":
        return {"ok": False, "method": "", "detail": "当前平台不支持"}

    name, state = _find_service()

    # ⚠️ 只要找到了服务，就**绝不**掉进下面的进程兜底分支。
    #
    # 原来写的是 `if name and state == "running"`，于是 stop_pending / paused /
    # stopped 全部漏到 taskkill —— 而那是**服务**进程，/F 强杀会让 SCM 记成
    # 异常终止。用户连点两次「停止」（第一次刚把服务置为 STOP_PENDING，
    # 第二次正好命中）就能触发，返回还说「程序无法帮你重新启动」，
    # 而它明明可以 sc start 起回来（2026-11-18 审查实测三种状态全中）。
    if name:
        if state == "stopped":
            return {"ok": True, "method": "service",
                    "detail": f"服务 {name} 已经是停止状态"}

        if state == "stop_pending":
            return {"ok": True, "method": "service",
                    "detail": "隧道正在停止中，请稍候几秒"}

        if state == "paused":
            # 暂停态先恢复再停止，否则 sc stop 不生效
            try:
                _run([_exe("sc.exe"), "continue", name], timeout=20)
            except Exception:
                pass

        try:
            proc = _run([_exe("sc.exe"), "stop", name], timeout=30)
        except Exception as exc:
            return {"ok": False, "method": "service", "detail": f"停止服务失败：{exc}"}
        if proc.returncode != 0:
            return {"ok": False, "method": "service",
                    "detail": (proc.stderr or proc.stdout or "sc stop 失败").strip()[:200]}

        # 等它真的停（sc stop 是异步的，最多等 10 秒）
        after = state
        for _ in range(20):
            time.sleep(0.5)
            after = _service_state(name)
            if after in ("stopped", ""):
                return {"ok": True, "method": "service",
                        "detail": f"已停止服务 {name}（可点「启动隧道」恢复）"}
            if after != "stop_pending":
                break
        return {"ok": False, "method": "service",
                "detail": f"下发停止指令后状态仍是 {after}"}

    running, pid = _process_running()
    if running and pid:
        try:
            proc = _run([_exe("taskkill.exe"), "/PID", str(pid), "/F"], timeout=15)
        except Exception as exc:
            return {"ok": False, "method": "process", "detail": f"结束进程失败：{exc}"}
        still, _ = _process_running()
        if not still:
            return {"ok": True, "method": "process",
                    "detail": "已结束前台 cloudflared 进程。"
                              "注意：程序无法帮你重新启动它，需要你手动执行原来的命令"}
        return {"ok": False, "method": "process",
                "detail": (proc.stderr or "进程仍然存在").strip()[:200]}

    return {"ok": False, "method": "", "detail": "没找到在跑的隧道"}


def tunnel_start() -> dict:
    """启动隧道。只有服务形态能可靠启动。

    返回 {"ok": bool, "method": "service"|"", "detail": str}
    """
    if sys.platform != "win32":
        return {"ok": False, "method": "", "detail": "当前平台不支持"}

    name, state = _find_service()
    if not name:
        running, _ = _process_running()
        if running:
            return {"ok": True, "method": "", "detail": "隧道已经在跑了"}
        return {"ok": False, "method": "",
                "detail": "本机没有安装 cloudflared 服务，程序无法启动。"
                          "请用原来的命令行启动，或参考文档把它装成服务"}

    if state == "running":
        return {"ok": True, "method": "service", "detail": f"服务 {name} 已经在运行"}

    try:
        proc = _run([_exe("sc.exe"), "start", name], timeout=40)
    except Exception as exc:
        return {"ok": False, "method": "service", "detail": f"启动服务失败：{exc}"}

    after = _service_state(name)
    if after in ("running", "start_pending"):
        return {"ok": True, "method": "service", "detail": f"已启动服务 {name}"}

    detail = (proc.stderr or proc.stdout or "").strip()[:200] or f"状态仍是 {after}"
    return {"ok": False, "method": "service", "detail": detail}


def frp_status() -> dict:
    """看 FRP 客户端（frpc）在不在跑。

    为什么只报状态、不提供启停：LANShare 不管 FRP 客户端的生命周期。
    现实里它可能是樱花启动器 GUI 拉起来的（本机就是），进程树和守护逻辑
    都归那个程序管 —— 从这里 taskkill 会被它自动拉起，制造「点了停止
    但马上又活了」的假象。所以面板只负责如实显示，并由文案指路。
    """
    procs = []
    r = _run([_exe("tasklist.exe"), "/FI", "IMAGENAME eq frpc.exe",
              "/FO", "CSV", "/NH"])
    if r.returncode == 0:
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line or line.startswith("INFO:"):
                continue
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == "frpc.exe":
                procs.append(parts[1])
    return {
        "running": bool(procs),
        "count": len(procs),
        "pids": procs,
        # 前端据此显示对应的操作建议，不写死在 HTML 里
        "managed_by_us": False,
    }
