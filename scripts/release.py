#!/usr/bin/env python3
"""一条命令发版：检查 → 构建 → 冒烟 → 发布 → 发完再验一遍。

## 为什么需要这个脚本

2026-11-01 一天发了两版，每版都手工走同样十来步，每一步都出过错：

- 版本号散在 `lanshare.iss` / `pubspec.yaml` / `CHANGELOG.md` / `HANDOFF.md`
  四处，改漏一处就「发了但 App 收不到」
- 资产命名靠手打。`LANShare-App-1.1.18.apk` 少了 `+125`，客户端的 build
  号解析直接放弃整个源 —— 查了半天才发现
- 忘了跑隐私扫描，作者真实内网 IP 在公开仓库躺了一天
- 发完没验证。`gh release create` 返回 0 不等于「客户端能选到这个包」
- 模拟器的 `ANDROID_SDK_ROOT` 指向机器级 SDK（没有 AVD 要的 system-image），
  每次都得手动覆盖，忘了就起不来

这些都不是「逻辑写错」，是「漏了一步」。脚本能防的就是这一类。

## 用法

    python scripts/release.py check                    # 只检查，不动手
    python scripts/release.py server 3.78              # 发服务端
    python scripts/release.py app 1.1.21               # 发 App（build 自动 +1）
    python scripts/release.py app 1.1.21 --skip-smoke  # 跳过模拟器冒烟（不推荐）

    --dry-run    只打印将要做什么，不执行
    --yes        不交互确认

## 它不管什么

- **不写 CHANGELOG**。发版说明从 CHANGELOG 里对应版本那一节取。写不出
  好文案是人的事，脚本只负责搬运。
- **不 force push、不改历史**。那些不可逆，永远手动。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "AK47ZZQ/lan-share"

# 机器级 ANDROID_SDK_ROOT 指向 C:\tools\android-sdk，那里没有 AVD 需要的
# system-image（模拟器起来报 Broken AVD system path）。构建和冒烟都必须用
# 仓库自带这份 —— build-apk.bat 已经这么做了，这里补上冒烟那条路。
SDK = ROOT / "env" / "android-sdk"
ADB = SDK / "platform-tools" / "adb.exe"
EMULATOR = SDK / "emulator" / "emulator.exe"
AVD = "lanshare_test"
PACKAGE = "app.lanshare.lanshare_app"

ISS = ROOT / "installer" / "lanshare.iss"
PUBSPEC = ROOT / "mobile" / "pubspec.yaml"
CHANGELOG = ROOT / "CHANGELOG.md"
HANDOFF = ROOT / "HANDOFF.md"
OUT = ROOT / "build" / "out"


# ──────────────────────────────────────────────────────────────
# 输出
# ──────────────────────────────────────────────────────────────

def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, total: int, msg: str) -> None:
    say(f"\n[{n}/{total}] {msg}")


def ok(msg: str) -> None:
    say(f"  ok   {msg}")


def warn(msg: str) -> None:
    say(f"  !    {msg}")


def fail(msg: str) -> None:
    say(f"  FAIL {msg}")


class Abort(Exception):
    """检查没过 / 构建失败。停在这里，不留半成品。"""


# ──────────────────────────────────────────────────────────────
# 外部命令
# ──────────────────────────────────────────────────────────────

def run(cmd, *, shell: bool = False, timeout: int = 1800,
        cwd: Path | None = None) -> subprocess.CompletedProcess:
    """跑命令。cwd 默认仓库根 —— 但 Flutter 必须在 mobile/ 里跑
    （那里才有 pubspec.yaml），所以必须能传。"""
    return subprocess.run(
        cmd, shell=shell, cwd=str(cwd or ROOT), timeout=timeout,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def run_checked(cmd, *, shell: bool = False, timeout: int = 1800,
                cwd: Path | None = None) -> str:
    res = run(cmd, shell=shell, timeout=timeout, cwd=cwd)
    if res.returncode != 0:
        tail = ((res.stdout or "")[-1200:] + (res.stderr or "")[-1200:]).strip()
        raise Abort(f"命令失败（exit {res.returncode}）：{cmd}\n{tail}")
    return res.stdout or ""


def git(*args: str) -> str:
    return run_checked(["git", *args]).strip()


# ──────────────────────────────────────────────────────────────
# 版本号读写
# ──────────────────────────────────────────────────────────────

def read_server_version() -> str:
    m = re.search(r'#define\s+AppVersion\s+"([^"]+)"', ISS.read_text(encoding="utf-8"))
    if not m:
        raise Abort("lanshare.iss 里找不到 AppVersion")
    return m.group(1)


def write_server_version(ver: str) -> None:
    s = ISS.read_text(encoding="utf-8")
    s2 = re.sub(r'(#define\s+AppVersion\s+")[^"]+(")', rf'\g<1>{ver}\g<2>', s, count=1)
    if s2 == s:
        # 内容没变有两种可能：一是已经是这个版本（重跑发版，正常），
        # 二是那行根本没匹配上（真出问题）。必须分清 —— 上一次就是在这里
        # 把「重跑」误判成「写不进去」，白停一轮。
        if read_server_version() == ver:
            return
        raise Abort("写不进去：lanshare.iss 的 AppVersion 行没匹配上")
    ISS.write_text(s2, encoding="utf-8", newline="")


def read_app_version() -> tuple[str, int]:
    s = PUBSPEC.read_text(encoding="utf-8")
    m = re.search(r"^version:\s*([\d.]+)\+(\d+)", s, re.M)
    if not m:
        raise Abort("pubspec.yaml 里找不到 version: x.y.z+build")
    return m.group(1), int(m.group(2))


def write_app_version(ver: str, build: int) -> None:
    s = PUBSPEC.read_text(encoding="utf-8")
    s2 = re.sub(r"^version:\s*[\d.]+\+\d+", f"version: {ver}+{build}", s, count=1, flags=re.M)
    if s2 == s:
        # 同 write_server_version —— 已经是这个版本就静默跳过
        if read_app_version() == (ver, build):
            return
        raise Abort("写不进去：pubspec.yaml 的 version 行没匹配上")
    PUBSPEC.write_text(s2, encoding="utf-8", newline="")


# ──────────────────────────────────────────────────────────────
# 检查项
# ──────────────────────────────────────────────────────────────

def check_git_clean() -> None:
    dirty = git("status", "--porcelain")
    if dirty:
        raise Abort(
            "工作区不干净，先提交或还原：\n" +
            "\n".join("    " + ln for ln in dirty.splitlines()[:15])
        )
    ok("工作区干净")


def check_privacy() -> None:
    """隐私扫描 —— 这是硬门槛，不过就不许发。"""
    script = ROOT / "scripts" / "pre-open-source-check.py"
    if not script.is_file():
        warn("没有 pre-open-source-check.py，跳过（新仓库要先补上）")
        return
    res = run([sys.executable, str(script)], timeout=600)
    out = (res.stdout or "") + (res.stderr or "")
    if "ALL CLEAR" not in out:
        tail = "\n".join(out.strip().splitlines()[-12:])
        raise Abort(f"隐私检查没过：\n{tail}")
    ok("隐私检查 ALL CLEAR")


def check_tests() -> None:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        warn("找不到 .venv，跳过 Python 测试")
    else:
        res = run([str(py), "-m", "pytest", "tests/", "-q"], timeout=1800)
        tail = (res.stdout or "").strip().splitlines()[-3:]
        if res.returncode != 0:
            raise Abort("Python 测试失败：\n" + "\n".join(tail))
        ok("Python 测试通过：" + (tail[-1] if tail else "?"))

    flutter = ROOT / "env" / "flutter" / "bin" / "flutter.bat"
    if flutter.is_file():
        # 必须在 mobile/ 里跑 —— 那里才有 pubspec.yaml。在仓库根跑会
        # 「没找到项目」直接退出，而且 stdout 是空的，报错看起来像
        # 「Flutter 测试失败：（空白）」，完全没法排查。
        res = run([str(flutter), "test"], timeout=1800, cwd=ROOT / "mobile")
        if res.returncode != 0:
            out = ((res.stdout or "")[-1500:] + (res.stderr or "")[-1500:]).strip()
            raise Abort("Flutter 测试失败：\n" + (out or "（命令没有输出，退出码 "
                                                  f"{res.returncode}）"))
        ok("Flutter 测试通过")
    else:
        warn("找不到 flutter，跳过 Dart 测试")


def commit_if_changed(paths: list[Path], message: str) -> bool:
    """只在真有变化时提交，返回是否提交了。

    为什么要这个：发版脚本重跑时，版本号可能已经是目标值（上一轮写过），
    `git add` 之后暂存区是空的，`git commit` 会以 exit 1 报
    「nothing to commit」—— 整个发版就此中断，明明什么事都没有。
    幂等不只是「不写坏文件」，还包括「没活干时安静地跳过」。
    """
    git("add", *[str(p) for p in paths])
    staged = git("diff", "--cached", "--name-only")
    if not staged.strip():
        ok("版本号无变化，跳过提交")
        return False
    run_checked([
        "git", "-c", "user.name=Hermes",
        "-c", "user.email=128774958+AK47ZZQ@users.noreply.github.com",
        "commit", "-m", message,
    ])
    ok("已提交")
    return True


def check_changelog_has(ver: str) -> str:
    """从 CHANGELOG 里取这个版本那一节，当 Release 说明用。"""
    text = CHANGELOG.read_text(encoding="utf-8")
    # 匹配 "## 2026-11-01 — 3.77：标题" 这类标题。版本号后面可能跟冒号和
    # 标题文字，所以不能用 \s*$；用「前后都不是数字或点」锁边界，
    # 这样查 3.7 不会误中 3.77。
    pattern = re.compile(
        r"^## .*?(?<![\d.])" + re.escape(ver) + r"(?![\d.])[\s\S]*?(?=^## |\Z)",
        re.M,
    )
    m = pattern.search(text)
    if not m:
        raise Abort(
            f"CHANGELOG.md 里没有 {ver} 的条目。\n"
            f"    先加一节 `## YYYY-MM-DD — {ver}：一句话标题`，发版说明从那里取。"
        )
    body = m.group(0).strip()
    ok(f"CHANGELOG 有 {ver} 条目（{len(body)} 字符）")
    return body


# ──────────────────────────────────────────────────────────────
# 构建
# ──────────────────────────────────────────────────────────────

def build_server() -> Path:
    run_checked(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "installer" / "build.ps1"),
         "-SkipFrontend", "-SkipDeps"],
        timeout=1800,
    )
    iscc = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
    if not iscc.is_file():
        raise Abort("找不到 ISCC.exe")
    run_checked([str(iscc), str(ISS)], timeout=1800)

    exe = OUT / f"LANShare-Setup-{read_server_version()}.exe"
    if not exe.is_file():
        raise Abort(f"构建结束但没有产物：{exe}")
    ok(f"安装包 {exe.name}  {exe.stat().st_size / 1048576:.1f} MB")
    return exe


def build_app() -> Path:
    run_checked(["cmd.exe", "/c", str(ROOT / "scripts" / "build-apk.bat"), "release"],
                timeout=3600)
    src = ROOT / "mobile" / "build" / "apk" / "lanshare-release.apk"
    if not src.is_file():
        raise Abort(f"构建结束但没有 APK：{src}")

    ver, build = read_app_version()
    # 资产名必须带 +build —— 客户端靠它取 build 号，少了解析会放弃整个源
    dst = OUT / f"LANShare-App-{ver}+{build}.apk"
    OUT.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    ok(f"APK {dst.name}  {dst.stat().st_size / 1048576:.1f} MB")
    return dst


# ──────────────────────────────────────────────────────────────
# 冒烟
# ──────────────────────────────────────────────────────────────

def _adb(*args: str, timeout: int = 120) -> str:
    res = run([str(ADB), *args], timeout=timeout)
    return (res.stdout or "") + (res.stderr or "")


def smoke_app(apk: Path) -> None:
    """模拟器装 release 包 → 启动 → 查崩溃。铁律，别跳过。

    0.6.3 就是漏了这步，真机上闪退。
    """
    if not ADB.is_file():
        warn("找不到 adb，跳过冒烟（强烈建议装上 SDK 后重跑）")
        return

    say("      等模拟器就绪…")
    online = False
    for _ in range(30):
        if "emulator-" in _adb("devices"):
            online = True
            break
        import time
        time.sleep(5)
    if not online:
        warn(f"没有在线模拟器，跳过冒烟。要真冒烟请先起 `{AVD}`")
        return

    _adb("uninstall", PACKAGE)
    install = _adb("install", "-r", str(apk), timeout=600)
    if "Success" not in install:
        raise Abort(f"安装失败：{install[-500:]}")
    ok("安装成功")

    _adb("logcat", "-c")
    _adb("shell", "am", "start", "-n", f"{PACKAGE}/.MainActivity")
    import time
    time.sleep(10)

    pid = _adb("shell", "pidof", PACKAGE).strip()
    if not pid:
        raise Abort("进程没起来 —— 启动就崩了")
    ok(f"进程存活 pid={pid}")

    logs = _adb("logcat", "-d")
    fatals = [ln for ln in logs.splitlines() if "FATAL EXCEPTION" in ln]
    if fatals:
        raise Abort("有 FATAL EXCEPTION：\n" + "\n".join(fatals[:5]))
    ok("无 FATAL")


# ──────────────────────────────────────────────────────────────
# 发布
# ──────────────────────────────────────────────────────────────

def github_release(tag: str, title: str, notes: str, asset: Path,
                   *, latest: bool) -> str:
    notes_file = ROOT / "build" / "_release_notes.md"
    notes_file.write_text(notes, encoding="utf-8")
    cmd = ["gh", "release", "create", tag, str(asset),
           "--title", title, "--notes-file", str(notes_file)]
    if latest:
        cmd.append("--latest")
    url = run_checked(cmd, timeout=1800).strip().splitlines()[-1]
    ok(f"已发布 {url}")
    return url


def verify_release(tag: str, asset: Path) -> None:
    """发完真的去拉一次，确认客户端能选到刚发的包。

    `gh release create` 返回 0 只能说明「上传成功」，不能说明
    「App 点检查更新能选到它」—— 2026-11-01 那个 bug 就是这么来的。
    """
    raw = run_checked([
        "gh", "api", f"repos/{REPO}/releases/tags/{tag}",
    ], timeout=300)
    data = json.loads(raw)
    assets = data.get("assets") or []
    if not assets:
        raise Abort("Release 建好了但没有资产")
    got = assets[0]
    if got.get("size") != asset.stat().st_size:
        raise Abort(
            f"资产字节数对不上：远端 {got.get('size')} / 本地 {asset.stat().st_size}"
        )
    if got.get("state") != "uploaded":
        raise Abort(f"资产状态不是 uploaded：{got.get('state')}")
    ok(f"资产校验通过 {got['name']} {got['size']} bytes")

    # 再验一遍「客户端能不能选到」—— 排序/前缀筛选逻辑
    raw = run_checked(["gh", "api", f"repos/{REPO}/releases?per_page=30"], timeout=300)
    rels = json.loads(raw)
    kind = "app-" if tag.startswith("app-") else "server-"
    mine = [r for r in rels if str(r.get("tag_name", "")).startswith(kind)]
    if not mine:
        raise Abort(f"按前缀 {kind} 筛选后一个都没选中")
    if mine[0].get("tag_name") != tag:
        warn(f"最新一条不是刚发的（{mine[0].get('tag_name')}），"
             f"但发布成功；如果客户端按时间取最新会有偏差")
    else:
        ok(f"客户端按 {kind} 前缀能选到 {tag}")


# ──────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────

def do_check(which: str, ver: str | None) -> str:
    steps = 4
    step(1, steps, "工作区")
    check_git_clean()
    step(2, steps, "版本号")
    if which == "server":
        cur = read_server_version()
        target = ver or cur
        ok(f"服务端版本 {cur}" + ("" if target == cur else f" → 将改为 {target}"))
    else:
        cur, build = read_app_version()
        target = ver or cur
        ok(f"App 版本 {cur}+{build}" + ("" if target == cur else f" → 将改为 {target}+{build+1}"))
    step(3, steps, "隐私扫描")
    check_privacy()
    step(4, steps, "CHANGELOG")
    check_changelog_has(target)
    return target


def cmd_check(args) -> int:
    for which in ("server", "app"):
        say(f"\n=== {which} ===")
        try:
            do_check(which, None)
        except Abort as exc:
            fail(str(exc))
    say("\n（check 不跑测试和构建，那些在真发版时做）")
    return 0


def cmd_server(args) -> int:
    ver = args.version
    total = 7
    step(1, total, "前置检查")
    check_git_clean()
    check_privacy()
    target = ver or read_server_version()
    notes = check_changelog_has(target)

    step(2, total, "写版本号")
    write_server_version(target)
    ok(f"lanshare.iss → {target}")

    step(3, total, "跑测试")
    if args.skip_tests:
        warn("已跳过测试（仅当刚跑过全绿时才该这么用）")
    else:
        check_tests()

    step(4, total, "构建安装包")
    exe = build_server()

    step(5, total, "提交版本号")
    commit_if_changed([ISS], f"chore(release): {target}")

    step(6, total, "发布")
    url = github_release(f"server-v{target}", f"LAN Share 服务端 {target}", notes, exe,
                         latest=True)

    step(7, total, "发版后验证")
    verify_release(f"server-v{target}", exe)

    say(f"\n完成：{url}")
    say("别忘了 push 代码：git push origin main")
    return 0


def cmd_app(args) -> int:
    cur_ver, cur_build = read_app_version()
    target = args.version or cur_ver
    new_build = cur_build if target == cur_ver else cur_build + 1
    total = 7

    step(1, total, "前置检查")
    check_git_clean()
    check_privacy()
    notes = check_changelog_has(target)

    step(2, total, "写版本号")
    write_app_version(target, new_build)
    ok(f"pubspec.yaml → {target}+{new_build}")

    step(3, total, "跑 Dart 测试")
    flutter = ROOT / "env" / "flutter" / "bin" / "flutter.bat"
    if args.skip_tests:
        warn("已跳过测试（仅当刚跑过全绿时才该这么用）")
    elif flutter.is_file():
        res = run([str(flutter), "test"], timeout=1800, cwd=ROOT / "mobile")
        if res.returncode != 0:
            out = ((res.stdout or "")[-1500:] + (res.stderr or "")[-1500:]).strip()
            raise Abort("Flutter 测试失败：\n" + (out or f"（无输出，退出码 {res.returncode}）"))
        ok("测试通过")

    step(4, total, "构建 APK")
    apk = build_app()

    step(5, total, "模拟器冒烟")
    if args.skip_smoke:
        warn("已按要求跳过（不推荐：0.6.3 就是漏了这步才真机闪退）")
    else:
        smoke_app(apk)

    step(6, total, "提交版本号")
    commit_if_changed([PUBSPEC], f"chore(release): App {target}+{new_build}")

    step(7, total, "发布并验证")
    url = github_release(f"app-v{target}", f"LAN Share App {target} (build {new_build})",
                         notes, apk, latest=False)
    verify_release(f"app-v{target}", apk)

    say(f"\n完成：{url}")
    say("别忘了 push 代码：git push origin main")
    return 0


def main() -> int:
    # --dry-run / --yes 挂在父 parser 上，同时用 parent 挂到每个子命令 ——
    # argparse 的全局参数只能写在子命令前面（`release.py --yes server 3.78`），
    # 写在后面会报 unrecognized arguments。而人的直觉是写在最后
    # （`release.py server 3.78 --yes`），这个落差没必要让使用者记住。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="只检查，不执行")
    common.add_argument("--yes", action="store_true", help="不交互确认")

    ap = argparse.ArgumentParser(description="一条命令发版")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不执行")
    ap.add_argument("--yes", action="store_true", help="不交互确认")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", parents=[common], help="只检查，不发版")

    p_srv = sub.add_parser("server", parents=[common], help="发服务端")
    p_srv.add_argument("version", nargs="?", help="新版本号，省略则沿用当前")
    p_srv.add_argument("--skip-smoke", action="store_true")
    p_srv.add_argument("--skip-tests", action="store_true",
                       help="跳过测试（只在刚跑过全绿、本轮只是补发版时用）")

    p_app = sub.add_parser("app", parents=[common], help="发 App")
    p_app.add_argument("version", nargs="?", help="新版本号，省略则沿用当前（build 不变）")
    p_app.add_argument("--skip-smoke", action="store_true", help="跳过模拟器冒烟")
    p_app.add_argument("--skip-tests", action="store_true",
                       help="跳过测试（只在刚跑过全绿、本轮只是补发版时用）")

    args = ap.parse_args()

    if args.dry_run:
        say("=== DRY RUN：只检查，不写不改不发 ===")
        return cmd_check(args)

    try:
        if args.cmd == "check":
            return cmd_check(args)
        if not args.yes:
            say("\n即将执行：构建 + 测试 + 发版。中途任何一步失败都会停下。")
            if input("继续？[y/N] ").strip().lower() != "y":
                say("已取消")
                return 1
        return cmd_server(args) if args.cmd == "server" else cmd_app(args)
    except Abort as exc:
        say("")
        fail(str(exc))
        say("\n已停止。工作区可能停在中途（版本号已改但没发），检查后重跑即可。")
        return 1
    except KeyboardInterrupt:
        say("\n已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
