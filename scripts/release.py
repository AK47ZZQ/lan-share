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
import shutil
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


def summarize(markdown: str, max_bullets: int = 6) -> str:
    """把 CHANGELOG 的长条目压成发版用的短说明。

    ## 为什么要压

    CHANGELOG 是写给未来的自己看的：为什么这么改、踩了什么坑、试过什么
    没用的方案。但 Release 页面是写给**用户**看的，用户只想知道
    「这版有什么不一样」。把两千字的内部复盘原样贴上去，手机上一屏
    都放不下，用户直接不看。

    所以：只留一句主标题 + 前几条要点，正文段落、代码块、### 小节全部丢掉。

    ## 例外

    代码块里的命令（`lanshare_cli check-update` 这种）对用户是有用的，
    但为了短，也不留 —— 需要细节的人会去看仓库里的 CHANGELOG。
    """
    if not markdown:
        return ""

    lines = markdown.splitlines()

    # 第一步：把 markdown 拆成「块」。
    # 为什么不能按行处理：正文为了源码可读是折行写的，
    # 「一天发了两版，每版十来步全靠手走，每一步都错过：版本号散在四处改漏一处、」
    # 这种半句话就是一行。按行取首句会把一句话切成四条，读起来全是断句。
    # 所以先把连续的非空、非标题、非列表行合并成一个段落块。
    blocks: list[tuple[str, str]] = []      # (kind, text)
    buf: list[str] = []
    in_code = False

    def flush() -> None:
        if buf:
            blocks.append(("p", "".join(buf).strip()))
            buf.clear()

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()

        # 代码块要整段吞掉，包括围栏行本身和里面的内容。
        # 之前只把 ``` 那一行标 skip，块里的命令行全漏进了要点
        # （「python scripts/release.py check # 只检查python scripts/...」）。
        if s.startswith("```"):
            flush()
            in_code = not in_code
            continue
        if in_code:
            continue

        # 缩进 4 空格 = markdown 的缩进代码块。CHANGELOG 里那些命令行
        # （`    python scripts/release.py check`）就是这么写的，
        # 不收掉会被当成正文，最后变成一条「python scripts/release.py check…」
        if raw.startswith("    "):
            continue

        if not s:
            flush()
        elif s.startswith("#"):
            flush()
            blocks.append(("h", s))
        elif s.startswith("|"):
            flush()                      # 表格整段丢弃
            blocks.append(("skip", s))
        elif s.startswith(("- ", "* ", "> ")) or re.match(r"^\d+\.\s", s):
            flush()
            blocks.append(("li", s))
        else:
            buf.append(s)                # 软换行：接着上一行
    flush()

    title = ""
    bullets: list[str] = []

    # 这些小节是「怎么装」「有什么限制」，不是「这版改了什么」。
    SKIP = ("已知限制", "已知事项", "技术信息", "安装", "下载", "完整改动", "顺带",
            "注意", "升级", "如果你的虚拟机", "验证", "测试", "校验", "本版说明")

    # 这类标题是动作指引，不是这版的主题 —— 跳过，继续找下一个 ##
    SKIP_TITLE = ("装这个", "下载", "安装", "升级", "先看")

    in_skipped = False
    for kind, text in blocks:
        if kind == "h" and text.startswith("## "):
            head = text[3:].strip()
            for sep in ("：", ":", "—"):
                if sep in head:
                    head = head.split(sep)[-1].strip()
            # 「已知限制」这类 ## 小节整段跳过（不只是不算标题）。
            # 必须用「以它开头」而不是「包含」—— 用 `in` 会把
            # 「手机端下载变快了」（含「下载」）这种正经标题当噪音杀掉。
            if any(head.startswith(k) or head == k for k in SKIP):
                in_skipped = True
                continue
            # 标题：取第一个不像动作指引的，取了就不再换。
            # SKIP_TITLE 必须是精确相等判断 —— 用 `in` 会把
            # 「手机端下载变快了」这种正经标题误杀（含「下载」二字）。
            if not title and head not in SKIP_TITLE and len(head) > 3:
                title = head
            in_skipped = False
            continue

        if kind == "h" and text.startswith("### "):
            h = re.sub(r"^[一二三四五六七八九十百]+、\s*", "", text[4:].strip())
            h = re.sub(r"^\d+\.\s*", "", h)
            in_skipped = any(k in h for k in SKIP)
            if h and not in_skipped:
                bullets.append(h)
            continue

        if in_skipped or kind == "skip":
            continue

        if kind == "li":
            b = text.lstrip("-*> ").strip()
            b = re.sub(r"^\d+\.\s*", "", b)
            if len(b) > 4 and not any(k in b for k in SKIP):
                bullets.append(b)
            continue

        # 段落块：取首句，太长再截断
        if len(text) > 10:
            first = re.split(r"[。！？]", text)[0].strip()
            if len(first) > 10:
                if len(first) > 80:
                    first = first[:78] + "…"
                bullets.append(first + "。")

    # 去重（标题和段落经常重复说同一件事）。
    # 用「包含」而不是「开头相同」：同一件事的两种说法往往开头不一样
    # （「修了更新检查」vs「正文重复说一遍修了更新检查」）。
    seen: list[str] = []
    uniq: list[str] = []
    for b in bullets:
        key = re.sub(r"[\s`*_\-。，、]", "", b)
        if any(key in s or s in key for s in seen if len(s) >= 6):
            continue
        seen.append(key)
        uniq.append(b)

    out: list[str] = []
    if title:
        # 不加 ** —— App 里是 Text 直接显示，不渲染 markdown，
        # 加了会把星号原样露出来。标题本身可能自带 **（`## **加粗**`），
        # 也一并去掉。
        out.append(title.replace("**", "").strip())
        out.append("")
    for b in uniq[:max_bullets]:
        out.append("- " + b.replace("**", "").strip())
    if len(uniq) > max_bullets:
        out.append("- …（完整说明见 CHANGELOG.md）")
    return "\n".join(out).strip()


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


def _history_notes(body: str, max_items: int = 6) -> str:
    """从 CHANGELOG 条目的正文里提炼要点，只取 `###` 小节标题。

    为什么不用 summarize()：那个是给 Release 页面用的，会带上段落首句，
    结果是一堆半截句子（「根因不是滚动冲突，是一次布局了全部内容：…」）。
    服务端这份 notes 会显示在手机「发现新版本」那里，一行一句的小标题
    才读得下去。细节留在 CHANGELOG 里，想看的人自己去看。
    """
    heads = re.findall(r"^###\s+(.+)$", body, re.M)
    skip = ("已知限制", "已知事项", "安装", "下载", "顺带", "注意", "技术信息")
    out: list[str] = []
    for h in heads:
        h = re.sub(r"^[一二三四五六七八九十百]+、\s*", "", h).strip()
        h = re.sub(r"^\d+\.\s*", "", h)
        if not h or any(k in h for k in skip):
            continue
        out.append(h)
    if not out:
        return ""
    return "\n".join("- " + h for h in out[:max_items])


def find_local_share_dir() -> Path | None:
    """找本机服务端的分发目录（`<数据目录>/App安装包`）。
    读注册表而不是猜路径 —— 用户装在哪都有可能。找不到返回 None。
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\LANShare") as k:
            data_folder = winreg.QueryValueEx(k, "DataFolder")[0]
    except OSError:
        return None
    d = Path(data_folder) / "App安装包"
    return d if d.is_dir() else None


def parse_changelog_history(markdown: str) -> list[dict]:
    """CHANGELOG.md → 更新历史列表（新到旧）。

    只挑含 App 版本号的条目：CHANGELOG 里服务端和 App 是混着写的
    （`v3.79 + App v1.1.23：…`），混进去会让用户以为手机装了 3.79。
    同一版本被拆成多个小节的（1.1.20 分了「一」「二」）只留第一条。
    """
    items: list[dict] = []
    seen: set[str] = set()
    for sec in re.split(r"^## ", markdown, flags=re.M)[1:]:
        lines = sec.splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        m = re.search(r"App\s+v?(\d+\.\d+\.\d+)", title)
        if not m:
            continue
        ver = m.group(1)
        if ver in seen:
            continue
        seen.add(ver)

        dm = re.search(r"(\d{4}-\d{2}-\d{2})", title)
        parts = [int(x) for x in ver.split(".") if x.isdigit()]
        build = (parts[0] * 10000 + parts[1] * 100 + parts[2]
                 if len(parts) == 3 else 0)
        items.append({
            "version": ver,
            "build": build,
            "date": dm.group(1) if dm else "",
            # 只留小节标题：这份 notes 会显示在手机「发现新版本」那里，
            # 段落首句拼出来全是半截话，读不下去
            "notes": _history_notes("\n".join(lines[1:])),
        })
    return items


def sync_to_local_server(apk: Path, history: list[dict]) -> None:
    """把 App 产物同步到本机服务端的 `App安装包/`。

    ## 这是「更新镜像」的落地方式

    服务端的 `/api/app/latest` 和 `/api/app/history` 已经改成**本地优先**
    （见 app/api/appver.py），所以只要这几个文件在，用户手机就完全不用
    碰 GitHub —— 局域网内走本机 IO，秒下。

    ## 找不到服务端就安静跳过

    别人的机器上跑 `release.py` 只是发 GitHub Release，不装服务端很正常，
    不该因此报错。所以这里只 warn 不 fail。
    """
    import hashlib

    dest = find_local_share_dir()
    if dest is None:
        warn("本机没装服务端（注册表无 LANShare），跳过镜像同步")
        return

    ver = read_app_version()[0]
    build = read_app_version()[1]

    # 1) APK 本体
    sha = hashlib.sha256(apk.read_bytes()).hexdigest()
    target = dest / apk.name
    shutil.copy2(apk, target)
    ok(f"APK 已同步到 {target.name}（{target.stat().st_size / 1048576:.1f} MB）")

    # 2) version.json —— /api/app/latest 读它
    (dest / "version.json").write_text(json.dumps({
        "version": ver,
        "build": build,
        "filename": apk.name,
        "sha256": sha,
        "size": apk.stat().st_size,
        "notes": history[0]["notes"] if history else "",
        "date": history[0]["date"] if history else "",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    ok("version.json 已更新")

    # 3) versions.json —— /api/app/history 读它
    #    最新那条的 build 必须用**真实值**（pubspec 里的 130），不能用
    #    从版本号推算的 10123 —— App 端拿它跟本地 buildNumber 比对，
    #    对不上就标不出「当前」。
    for h in history:
        if h["version"] == ver:
            h["build"] = build
    (dest / "versions.json").write_text(json.dumps({
        "items": [{"version": h["version"], "build": h["build"],
                   "date": h["date"], "notes": h["notes"]} for h in history],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    ok(f"versions.json 已更新（{len(history)} 个版本）")

    # 4) 顺手清掉旧 APK 和下载残留，别让目录越攒越大
    for old in dest.glob("LANShare-App-*.apk*"):
        if old.name not in (apk.name, apk.name + ".part"):
            try:
                old.unlink()
                ok(f"清掉旧包 {old.name}")
            except OSError:
                pass


def restore_latest_to_server() -> None:
    """把 GitHub 的 Latest 标记还给最新的服务端 Release。

    App 一发版就会抢走 Latest —— GitHub 的规则是「最新发布的那个」，
    而服务端才应该是主产品（用户从仓库首页点进来，该看到的是安装包）。
    客户端两个都按 tag 前缀筛，所以这个标记不影响更新检查，只影响
    仓库首页展示；不还回去纯属看着别扭。

    找不到服务端 Release 就跳过，不报错。
    """
    try:
        raw = run_checked(["gh", "api", f"repos/{REPO}/releases?per_page=100"],
                          timeout=300)
        rels = json.loads(raw)
    except Abort as exc:
        warn(f"还给服务端 Latest 时拉列表失败（不影响本次发布）：{exc}")
        return

    for r in rels:
        tag = str(r.get("tag_name") or "")
        if tag.startswith("server-"):
            res = run(["gh", "release", "edit", tag, "--latest"], timeout=300)
            if res.returncode == 0:
                ok(f"Latest 标记还给 {tag}")
            else:
                warn(f"设 Latest 失败（{res.stderr.strip()[:80]}）")
            return
    warn("没有服务端 Release，Latest 保持原样")


def prune_old_releases(kind: str, keep_tag: str) -> None:
    """删掉同类型的旧 Release，只留刚发的这个。

    ## 为什么要清

    Release 攒多了会：①仓库页一眼全是历史版本，用户找不到该下哪个；
    ②每个都挂着 50~100 MB 的安装包，白占配额；③「最新版是哪个」
    在列表里越来越不直观。

    ## 只删 Release，不删 tag

    tag 指向的是 git 提交，很轻，而且 CHANGELOG 里的历史链接、issue 引用
    都靠它。删了 tag，那些引用就全断了 —— 而删 Release 只影响「下载入口」，
    正常用户只下载最新版，不受影响。

    ## 失败不影响发版

    发布已经成功了，清理是锦上添花。任何一个删不掉都只记一行警告，
    不能让整个发版流程报失败。
    """
    prefix = "app-" if kind == "app" else "server-"
    try:
        raw = run_checked(["gh", "api", f"repos/{REPO}/releases?per_page=100"],
                          timeout=300)
        rels = json.loads(raw)
    except Abort as exc:
        warn(f"清理旧 Release 时拉列表失败（不影响本次发布）：{exc}")
        return

    removed = 0
    for r in rels:
        tag = str(r.get("tag_name") or "")
        if not tag.startswith(prefix) or tag == keep_tag:
            continue
        res = run(["gh", "release", "delete", tag, "--yes"], timeout=300)
        if res.returncode == 0:
            removed += 1
            ok(f"清掉旧版 {tag}")
        else:
            warn(f"删不掉 {tag}（{res.stderr.strip()[:80]}）")

    if removed == 0:
        ok("没有需要清理的旧 Release")


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
    raw_notes = check_changelog_has(target)
    notes = summarize(raw_notes)
    ok(f"发版说明 {len(notes)} 字符（CHANGELOG 原文 {len(raw_notes)}）")

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
    prune_old_releases("server", f"server-v{target}")

    say(f"\n完成：{url}")
    say("别忘了 push 代码：git push origin main")
    return 0


def cmd_app(args) -> int:
    cur_ver, cur_build = read_app_version()
    target = args.version or cur_ver
    new_build = cur_build if target == cur_ver else cur_build + 1
    total = 8

    step(1, total, "前置检查")
    check_git_clean()
    check_privacy()
    raw_notes = check_changelog_has(target)
    notes = summarize(raw_notes)
    ok(f"发版说明 {len(notes)} 字符（CHANGELOG 原文 {len(raw_notes)}）")

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
    prune_old_releases("app", f"app-v{target}")
    restore_latest_to_server()

    step(8, 8, "同步到本机服务端（更新镜像）")
    # 历史从完整 CHANGELOG 生成，不是只取当前版本 —— 服务端要能给出全部历史
    history = parse_changelog_history(CHANGELOG.read_text(encoding="utf-8"))
    sync_to_local_server(apk, history)

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
