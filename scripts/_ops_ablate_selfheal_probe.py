"""核验 `ablate-security-core.py` 的**写前备份 + 启动自愈**真的成立（不是只写着好看）。

关键手法：子进程用 `os._exit(9)` 模拟 SIGKILL —— atexit / signal 处理器统统不执行，
与被 `Stop-Process -Force` 杀掉等价。全程只碰 `app/filetools.py`，结束时自清。

覆盖的分支：改坏后强杀留下备份 → 自愈逐字节还原 → **盘上有第三方改动时 fail-closed**
（拒绝回滚、不覆盖别人、保留证据）→ 脏树检测既不漏报也不误报 → 反向消融
（把自愈改废，看"干净"这个结论是否随之消失）。

⚠️ 运行约束：它会**临时改写生产挂载目录里的源码**（`C:\lan-share` 是本仓的 junction）。
自己保证开头干净、结尾干净，但**期间若服务被重启**（NSSM 崩溃自动重启、有人手动重启、
01:00 巡检），盘上那几秒的变异形态就可能被加载。所以：避开 01:00 窗、别在别的会话
改 `app/filetools.py` 时跑、跑完看 `git status` 是否真干净。
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]      # 走 junction 还是长路径都能自定位
REL = "app/filetools.py"
OLD = "    if RESERVED_NAME.match(n):"
NEW = "    if False and RESERVED_NAME.match(n):"
BK = Path(os.environ["TEMP"]) / "lanshare-ablate"


def load():
    spec = importlib.util.spec_from_file_location("abl", ROOT / "scripts/ablate-security-core.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def git_dirty(rel):
    p = subprocess.run(["git", "status", "--porcelain", "--", rel],
                       cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip()


results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'✓' if cond else '✗ FAIL'} {name} {detail}")


fp = ROOT / REL
pristine = fp.read_bytes()
check("前置：目标文件干净、且盘上没有变异形态",
      git_dirty(REL) == "" and NEW.encode() not in pristine)
if not results[-1]:
    # fail-closed：带着别人的未提交改动继续跑，就是在赌运气
    print("\n前置不成立，拒绝继续（先让工作树干净）")
    sys.exit(2)

# ── 1. 改坏后 os._exit(9)：模拟被强杀 ──
child = r"""
import importlib.util, os, sys
from pathlib import Path
ROOT = Path(sys.argv[1]); REL = sys.argv[2]; OLD = sys.argv[3]; NEW = sys.argv[4]
spec = importlib.util.spec_from_file_location("abl", ROOT / "scripts/ablate-security-core.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
got = m._patch(ROOT / REL, REL, OLD, NEW)
assert got is not None, "锚点未命中"
assert (ROOT / REL).read_bytes() != got, "改坏未生效"
print("ARMED", flush=True)   # os._exit 不冲缓冲区，漏 flush 会让标记凭空消失
os._exit(9)          # 不跑 atexit、不跑 signal —— 等价 SIGKILL
"""
p = subprocess.run([sys.executable, "-c", child, str(ROOT), REL, OLD, NEW],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
after_kill = fp.read_bytes()
# 三条独立证据：退出码 9（说明走到了 os._exit，即 _patch 的 assert 全过）、
# stdout 标记、盘上确有变异形态。缺任何一条都不算"前提成立"。
check("强杀后线上文件确实带着变异（前提成立，不是空跑）",
      p.returncode == 9 and p.stdout.strip() == "ARMED" and NEW.encode() in after_kill,
      f"exit={p.returncode} out={p.stdout.strip()!r} err={p.stderr.strip()[-120:]!r}")
sides = sorted(BK.glob("*.json")) if BK.exists() else []
check("写前备份落在仓库外且仍在", len(sides) == 1, str([s.name for s in sides]))
if sides:
    d = json.loads(sides[0].read_bytes())
    check("备份同时存了原始与改坏两种形态（自愈要靠它判别）",
          {"rel", "pristine", "mutated", "pid"} <= set(d) and d["rel"] == REL)

# ── 2. 启动自愈 ──
m = load()
check("自愈返回成功", m.recover_stale_backups() is True)
check("自愈后字节与原始完全一致（无行尾漂移）", fp.read_bytes() == pristine)
check("自愈后 git 视角干净（最有说服力的一条）", git_dirty(REL) == "", repr(git_dirty(REL)))
check("自愈后备份记录已清除", not BK.exists() or not list(BK.glob("*.json")))

# ── 3. 对抗：期间有第三方改动 → 拒绝回滚，也不覆盖别人 ──
m = load()
assert m._patch(fp, REL, OLD, NEW) == pristine, "改坏未达预期"
sc = sorted(BK.glob("*.json"))[0]
fp.write_bytes(pristine + b"\n# third-party edit\n")
check("第三种形态下自愈拒绝（fail-closed）", load().recover_stale_backups() is False)
check("拒绝时不覆盖第三方内容", fp.read_bytes() == pristine + b"\n# third-party edit\n")
check("拒绝时保留备份记录（不销毁证据）", sc.exists())
sc.unlink(missing_ok=True)
fp.write_bytes(pristine)

# ── 4. 脏树拒绝：既不能漏报也不能误报 ──
m = load()
check("干净树不误报", m.dirty_targets([REL]) == [], repr(m.dirty_targets([REL])))
fp.write_bytes(pristine + b"\n")
check("脏树能报（开工前会因此拒绝运行）", m.dirty_targets([REL]) == [REL],
      repr(m.dirty_targets([REL])))
fp.write_bytes(pristine)
check("本脚本自清：结束时 git 完全干净", git_dirty(REL) == "", repr(git_dirty(REL)))

# ── 5. 反向消融：把自愈改废，探针必须失去"干净"这个结论 ──
m = load()
assert m._patch(fp, REL, OLD, NEW) == pristine
mut = fp.read_bytes()
m.recover_stale_backups = lambda: True          # 废掉被测实现
check("反向消融：自愈被废后变异仍在盘上（证明上面的绿确实由自愈带来）",
      m.recover_stale_backups() is True and fp.read_bytes() == mut)
m._restore_one(REL, pristine)                   # 恢复现场
check("反向消融后现场已复原", git_dirty(REL) == "" and not list(BK.glob("*.json")))

print("\n汇总：", "全部通过" if all(results) else f"{results.count(False)} 项失败")
sys.exit(0 if all(results) else 1)
