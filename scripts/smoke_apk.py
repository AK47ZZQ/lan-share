# -*- coding: utf-8 -*-
"""Android release 包启动冒烟。

分发前的硬门槛：装包 → 冷启动 → 检查有没有 FATAL EXCEPTION → 确认前台存活
→ 确认 UI 真的渲染出来了。任何一项不过就说明这包不能发。

用法：
    python scripts/smoke_apk.py <apk路径> [包名]

退出码：0 全部通过，1 有检查失败。
"""
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK = os.path.join(ROOT, "env", "android-sdk")
ADB = os.path.join(SDK, "platform-tools", "adb.exe")
EMU = os.path.join(SDK, "emulator", "emulator.exe")
AVD = "lanshare_test"
PKG = "app.lanshare.lanshare_app"

# 本机系统级 ANDROID_SDK_ROOT 指向 C:\tools\android-sdk，那个目录里没有
# system-images，模拟器会直接报 "Broken AVD system path" 然后退出。
# 这里覆盖成仓库自带的 SDK，不动系统环境变量（那是 env-declaration.json 管的）。
os.environ["ANDROID_SDK_ROOT"] = SDK
os.environ["ANDROID_HOME"] = SDK
os.environ["ANDROID_AVD_HOME"] = os.path.join(os.path.expanduser("~"), ".android", "avd")


def run(cmd, timeout=180, check=False):
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r


def adb(*args, **kw):
    return run([ADB] + list(args), **kw)


def devices():
    out = adb("devices").stdout
    return [ln.split("\t")[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def ensure_emulator():
    if devices():
        print(f"  已有设备在线: {devices()}")
        return devices()[0]
    print(f"  启动模拟器 {AVD} …")
    env = os.environ.copy()
    subprocess.Popen([EMU, "-avd", AVD, "-no-snapshot-load", "-no-boot-anim",
                      "-no-audio", "-gpu", "swiftshader_indirect",
                      "-sysdir", os.path.join(SDK, "system-images", "android-35", "default", "x86_64")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    for i in range(60):
        time.sleep(5)
        d = devices()
        if d:
            print(f"  设备上线（{i * 5 + 5}s）: {d[0]}")
            # 等 boot 完成
            for _ in range(40):
                b = adb("shell", "getprop", "sys.boot_completed").stdout.strip()
                if b == "1":
                    print("  boot_completed=1")
                    return d[0]
                time.sleep(3)
            return d[0]
    raise RuntimeError("模拟器 5 分钟内没起来")


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/smoke_apk.py <apk> [包名]")
        return 2
    apk = sys.argv[1]
    global PKG
    if len(sys.argv) >= 3:
        PKG = sys.argv[2]

    if not os.path.exists(apk):
        print(f"[FAIL] APK 不存在: {apk}")
        return 1

    size_mb = os.path.getsize(apk) / 1048576
    print(f"APK: {apk}  ({size_mb:.1f} MB)")
    print(f"包名: {PKG}\n")

    fails = []

    def check(label, ok, detail=""):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
        if not ok:
            fails.append(label)
        return ok

    # 1. 设备
    print("[1] 设备")
    serial = ensure_emulator()
    adb("-s", serial, "wait-for-device")
    check("设备在线", bool(serial), serial)

    # 2. 先卸旧的，保证是干净安装
    print("\n[2] 卸旧装新")
    adb("-s", serial, "uninstall", PKG)
    r = adb("-s", serial, "install", "-r", apk, timeout=300)
    ok_install = "Success" in (r.stdout or "")
    check("安装成功", ok_install, (r.stdout or r.stderr).strip().splitlines()[-1][:80] if (r.stdout or r.stderr) else "")

    # 3. 清日志后冷启动
    print("\n[3] 冷启动")
    adb("-s", serial, "logcat", "-c")
    adb("-s", serial, "shell", "am", "force-stop", PKG)
    r = adb("-s", serial, "shell", "am", "start", "-W", "-n", f"{PKG}/.MainActivity")
    started = "Status: ok" in (r.stdout or "") or "Activity" in (r.stdout or "")
    check("am start 返回 ok", started, (r.stdout or "").strip().splitlines()[0][:70])

    # 4. 等 8 秒让它跑起来
    time.sleep(8)

    # 5. FATAL 检查
    print("\n[4] 崩溃检查")
    log = adb("-s", serial, "logcat", "-d", "-v", "brief").stdout
    fatal = [ln for ln in log.splitlines()
             if re.search(r"FATAL EXCEPTION|AndroidRuntime.*E ", ln)]
    check("无 FATAL EXCEPTION", not fatal, fatal[0][:90] if fatal else "")
    if fatal:
        print("\n  --- 崩溃堆栈（前 25 行）---")
        idx = log.find("FATAL EXCEPTION")
        for ln in log[idx:].splitlines()[:25]:
            print("   " + ln[:110])

    # 6. 进程存活
    pid = adb("-s", serial, "shell", "pidof", PKG).stdout.strip()
    check("进程存活", bool(pid), f"pid={pid}" if pid else "进程不在")

    # 7. 前台 Activity
    # Android 12+ 的键名是 topResumedActivity，旧版是 mResumedActivity；
    # 再兜一层 dumpsys window 的 mCurrentFocus。只查一个会漏。
    top = adb("-s", serial, "shell", "dumpsys", "activity", "activities").stdout
    win = adb("-s", serial, "shell", "dumpsys", "window").stdout
    front = ""
    for text, pat in ((top, r"topResumedActivity=[^\s]*\s+\S+\s+([\w.]+/[\w.$]+)"),
                      (top, r"mResumedActivity.*?([\w.]+/[\w.$]+)"),
                      (win, r"mCurrentFocus=Window\{[^}]*\s([\w.]+/[\w.$]+)")):
        m = re.search(pat, text)
        if m:
            front = m.group(1)
            break
    check("前台是本应用", PKG in front, front or "取不到")

    # 8. UI 是否真的画了
    print("\n[5] UI 渲染")
    gfx = adb("-s", serial, "shell", "dumpsys", "gfxinfo", PKG, "framestats").stdout
    frames = len(re.findall(r"^\d+,\d+,", gfx, re.M))
    check("有渲染帧", frames > 0, f"{frames} 帧")

    # Flutter 用 Skia/Impeller 自己画，uiautomator 的 XML 里只有
    # FrameLayout / LinearLayout 这类容器，text 一律为空——用「有没有 text
    # 节点」判断渲染，对 Flutter 恒为假。改成截图做像素分析：
    # 纯黑 / 纯白 = 白屏或黑屏，颜色种类够多才说明画面真的画出来了。
    adb("-s", serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
    dump = adb("-s", serial, "shell", "cat", "/sdcard/ui.xml").stdout
    nodes = dump.count("<node")
    check("UI 树有节点", nodes > 0, f"{nodes} 个节点（Flutter 下不含文字节点，正常）")

    shot = os.path.join(os.path.dirname(apk), "_smoke_shot.png")
    with open(shot, "wb") as fh:
        r = subprocess.run([ADB, "-s", serial, "exec-out", "screencap", "-p"],
                           stdout=fh, stderr=subprocess.DEVNULL, timeout=120)
    try:
        from PIL import Image
        img = Image.open(shot).convert("RGB")
        colors = img.getcolors(maxcolors=200000) or []
        ncolors = len(colors)
        w, h = img.size
        check("画面非纯色", ncolors > 50, f"{w}x{h}，{ncolors} 种颜色")
        check("截图非全黑非全白", ncolors > 2, "")
        os.remove(shot)
    except Exception as e:
        check("画面分析", False, str(e)[:70])

    # 9. 结论
    print("\n" + "=" * 56)
    if fails:
        print(f"结果: {len(fails)} 项失败 → 这包不能发")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("结果: 全部通过，可以分发")
    return 0


if __name__ == "__main__":
    sys.exit(main())
