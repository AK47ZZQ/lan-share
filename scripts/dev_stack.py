"""一次性预发全栈（v3.70）：临时 PG 集群 + 隔离数据目录 + 独立端口，零接触生产。

背景（2026-10-24 评审 #4）：此前唯一的"环境"就是生产——E2E 实时弹到主人手机、
探针/重启直接打线上。本脚本把"预发"变成一条命令：

    python scripts/dev_stack.py start           # 起栈并自检（healthz 非 ok 自动回滚）
    python scripts/dev_stack.py stop            # 停 uvicorn + 停 PG + 清理临时目录
    python scripts/dev_stack.py stop --keep     # 停服务但保留数据（下次 start --keep 复用）

布局（全部在 %TEMP%/lanshare-dev/ 下，绝不触碰 data/ 与 5433/8766/8765）：
    pg/         一次性 PG 集群（zonky initdb，127.0.0.1:5534，trust 仅本机回环）
    data/       SHARE_ROOT（首启初始化 schema；首账号按首启流程在 Web 里建）
    logs/       uvicorn 输出
    state.json  pid/端口记录

工具取位（03 清单 2026-10-24 条）：服务端 initdb/pg_ctl 用 env/pginstall/zonky/bin
（服务器最小集），客户端 createdb 用 env/pgsql-bin。pg_ctl 免 -w 启动、按端口
轮询判活（交互式 shell 下 -w 会挂着不返回——也是当天踩过的坑）。

E2E 纪律：能用预发验证的先打预发（http://127.0.0.1:9766），生产只留给
"必须真机/真隧道"的最后一验。
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZONKY = ROOT / "env" / "pginstall" / "zonky" / "bin"
CLIENT = ROOT / "env" / "pgsql-bin"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
DEV = Path(tempfile.gettempdir()) / "lanshare-dev"
PG_PORT = 5534
API_PORT = 9766


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_port(port: int, want: bool, timeout: float = 40) -> bool:
    """轮询端口直到与期望状态一致（pg_ctl 免 -w 的替代判活法）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_busy(port) == want:
            return True
        time.sleep(1)
    return False


def _run(cmd: list, timeout: float = 60, **kw) -> subprocess.CompletedProcess:
    """capture_output 包管道——任何子进程都不许有无限等待的机会。"""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def _run_nocap(cmd: list, timeout: float = 60) -> subprocess.CompletedProcess:
    """pg_ctl 专用：绝不捕获管道。它 spawn 的 postgres（孙进程）会继承管道
    句柄且长期存活——subprocess.run 的 communicate() 等不到 EOF，即使
    timeout 杀掉 pg_ctl 本体也照样死锁（2026-10-24 实证：捕获管道的两次
    全挂、>/dev/null 直重定向的秒过）。输出交给 pg_ctl 自己的 -l 日志。"""
    return subprocess.run(cmd, timeout=timeout,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start(keep: bool) -> int:
    if (DEV / "state.json").exists():
        print("已有栈在跑或未清理：先 stop（数据保留用 stop --keep）")
        return 2
    for port, name in ((PG_PORT, "PG 5534"), (API_PORT, "API 9766")):
        if _port_busy(port):
            print(f"{name} 端口被占用——预发端口撞了别的进程，拒绝启动")
            return 2
    if not keep:
        shutil.rmtree(DEV, ignore_errors=True)
    (DEV / "logs").mkdir(parents=True, exist_ok=True)

    print("· initdb …")
    r = _run([str(ZONKY / "initdb.exe"), "-D", str(DEV / "pg"), "-U", "lanshare",
              "--auth=trust", "--encoding=UTF8", "--locale=C"], timeout=180)
    if r.returncode != 0:
        print("initdb 失败：", (r.stderr or r.stdout)[-400:])
        return 1
    print("· pg_ctl start（-W 免等待 + 无管道捕获，两坑见 03 清单 2026-10-24 条）…")
    _run_nocap([str(ZONKY / "pg_ctl.exe"), "-D", str(DEV / "pg"),
                "-o", f"-p {PG_PORT} -h 127.0.0.1", "-l", str(DEV / "logs" / "pg.log"),
                "-W", "start"], timeout=30)
    if not _wait_port(PG_PORT, want=True):
        print("临时 PG 未在 40s 内就绪，日志尾部：")
        print((DEV / "logs" / "pg.log").read_text(errors="replace")[-600:] if
              (DEV / "logs" / "pg.log").exists() else "(无日志)")
        return _cleanup_fail()
    print("· createdb lanshare …")
    r = _run([str(CLIENT / "createdb.exe"), "-h", "127.0.0.1",
              "-p", str(PG_PORT), "-U", "lanshare", "lanshare"], timeout=30)
    if r.returncode != 0:
        print("createdb 失败：", r.stderr[-300:])
        return _cleanup_fail()

    env = {
        **os.environ,
        "SHARE_ROOT": str(DEV / "data"),
        "DATABASE_URL": f"postgresql://lanshare@127.0.0.1:{PG_PORT}/lanshare",
        "PYTHONIOENCODING": "utf-8",
    }
    print("· uvicorn 启动（9766）…")
    log_f = open(DEV / "logs" / "uvicorn.log", "ab")
    proc = subprocess.Popen(
        [str(VENV_PY), "-m", "uvicorn", "server:app",
         "--host", "127.0.0.1", "--port", str(API_PORT), "--log-level", "info"],
        cwd=str(ROOT), env=env, stdout=log_f, stderr=subprocess.STDOUT)
    (DEV / "state.json").write_text(json.dumps(
        {"uvicorn_pid": proc.pid, "ts": time.time()}), encoding="utf-8")
    if not _wait_port(API_PORT, want=True, timeout=60):
        print("uvicorn 60s 未就绪，日志尾部：")
        print((DEV / "logs" / "uvicorn.log").read_text(errors="replace")[-800:])
        return _cleanup_fail()
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{API_PORT}/healthz", timeout=5) as resp:
            body = json.loads(resp.read().decode())
        assert body.get("status") == "ok", body
    except Exception as e:
        print("healthz 自检失败：", e)
        return _cleanup_fail()
    print(f"预发栈就绪：API http://127.0.0.1:{API_PORT} "
          f"(PG :{PG_PORT}, data={DEV / 'data'})")
    print("首账号：浏览器打开上面地址按首启流程建管理员（这是预发库，随便建）。")
    print("停栈：python scripts/dev_stack.py stop")
    return 0


def _cleanup_fail() -> int:
    stop(keep=False)
    return 1


def stop(keep: bool) -> int:
    state_p = DEV / "state.json"
    if state_p.exists():
        st = json.loads(state_p.read_text(encoding="utf-8"))
        pid = st.get("uvicorn_pid")
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
        state_p.unlink()
    _run_nocap([str(ZONKY / "pg_ctl.exe"), "-D", str(DEV / "pg"),
                "-m", "fast", "-t", "15", "stop"], timeout=60)
    if not keep:
        shutil.rmtree(DEV, ignore_errors=True)
        print("预发栈已停并清理临时目录")
    else:
        print("预发栈已停（保留数据目录，下次 start --keep 复用）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["start", "stop"])
    ap.add_argument("--keep", action="store_true",
                    help="start: 复用既有数据；stop: 保留数据目录")
    a = ap.parse_args()
    return start(a.keep) if a.action == "start" else stop(a.keep)


if __name__ == "__main__":
    sys.exit(main())
