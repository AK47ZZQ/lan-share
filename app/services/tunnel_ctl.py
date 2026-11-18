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

import re
import subprocess
import sys

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 按可能性排序。第一项是我们自己的安装器用的名字。
_SERVICE_CANDIDATES = ("LANShareTunnel", "cloudflared", "Cloudflared",
                       "CloudflareTunnel", "cloudflared-tunnel")

# sc 的 STATE 码
_STATE_NAMES = {1: "stopped", 2: "start_pending", 3: "stop_pending",
                4: "running", 5: "continue_pending", 6: "pause_pending",
                7: "paused"}


def _run(args: list, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          creationflags=_CREATE_NO_WINDOW)


def _service_state(name: str) -> str:
    """查服务状态。不存在返回 ""，存在返回 running/stopped/... """
    try:
        proc = _run(["sc", "query", name])
    except Exception:
        return ""
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 and "1060" in out:
        return ""                                 # 服务不存在
    match = re.search(r"STATE\s*:\s*(\d+)", out)
    if not match:
        return ""
    return _STATE_NAMES.get(int(match.group(1)), "unknown")


def _find_service() -> tuple:
    """找出一条已安装的 cloudflared 服务。返回 (name, state)，找不到 ("", "")。"""
    for name in _SERVICE_CANDIDATES:
        state = _service_state(name)
        if state:
            return name, state
    return "", ""


def _process_running() -> tuple:
    """cloudflared 进程是否在跑。返回 (running, pid)。"""
    if sys.platform != "win32":
        return False, 0
    try:
        proc = _run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe", "/FO", "CSV", "/NH"])
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
    if name and state == "running":
        try:
            proc = _run(["sc", "stop", name], timeout=30)
        except Exception as exc:
            return {"ok": False, "method": "service", "detail": f"停止服务失败：{exc}"}
        if proc.returncode != 0:
            return {"ok": False, "method": "service",
                    "detail": (proc.stderr or proc.stdout or "sc stop 失败").strip()[:200]}
        after = _service_state(name)
        if after in ("stopped", "stop_pending"):
            return {"ok": True, "method": "service",
                    "detail": f"已停止服务 {name}（可通过「启动隧道」恢复）"}
        return {"ok": False, "method": "service",
                "detail": f"下发停止指令后状态仍是 {after}"}

    running, pid = _process_running()
    if running and pid:
        try:
            proc = _run(["taskkill", "/PID", str(pid), "/F"], timeout=15)
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
        proc = _run(["sc", "start", name], timeout=40)
    except Exception as exc:
        return {"ok": False, "method": "service", "detail": f"启动服务失败：{exc}"}

    after = _service_state(name)
    if after in ("running", "start_pending"):
        return {"ok": True, "method": "service", "detail": f"已启动服务 {name}"}

    detail = (proc.stderr or proc.stdout or "").strip()[:200] or f"状态仍是 {after}"
    return {"ok": False, "method": "service", "detail": detail}
