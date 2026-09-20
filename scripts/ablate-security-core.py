"""安全核心消融实验（mutation testing）——反向验证"测试是否真的在守着不变量"。

为什么需要它：AGENTS.md §6 要求"改了测试覆盖就做反向消融"，但仓库只有人工习惯、
没有工具，于是同类缺陷反复逃逸——2026-09-03 的 grant 前缀 bug、v3.54 的 rowid、
以及本轮 D1（WebDAV 读根绕过 resolve()）在**全绿门禁**下存活。

做法：把一条安全不变量的实现改坏（或删除），跑全套测试。
  · 变红 = 该不变量确实被测试守着（测试有效）。
  · 全绿 = 测试空转 / 覆盖缺失（缺陷清单，比改对代码更值钱）。

用法（仓库根，venv python）：
    python scripts/ablate-security-core.py            # 全部
    python scripts/ablate-security-core.py --only M1,M6
    python scripts/ablate-security-core.py --only M15 \
        --tests tests/test_adversarial_filename.py    # 子集：只为拿这一条的结论
每条实验打印结果；退出码非 0 表示存在**未判定**的存活变异（即覆盖缺口）或超时轮。

子集会削弱结论（全套里别处的守护看不见），所以只在"这一条卡住、需要一个
有界结论"时用；打印会带上子集标记，别让子集全绿被误读成全套全绿。

**存活 ≠ 一定有缺口**（首轮实测到的两类误报，故须复核后再入 EXPECTED）：
  · 等价变异：M3 删掉 `p == ".."` 后行为不变——`".."` 本来就以 `.` 开头，
    同一条件的点前缀支已经覆盖它。任何测试都抓不到一个不改行为的改动。
  · 构造上不可达：M11 的根前缀兜底——safe_path 先按 `/` 拆段再 joinpath，
    绝对路径被就地中性化（实测 `C:/Windows/win.ini` → `<root>/Windows/win.ini`、
    `/etc/passwd` → `<root>/etc/passwd`），永远落在根内，判据无从触发。
    它是防"未来有人改了拆段逻辑"的保险，不是当前防线。

安全：只改工作区文件，**改坏之前**先把原始字节写到仓库外的写前备份
（`%TEMP%/lanshare-ablate/`），正常退出靠 atexit/signal 还原，被 SIGKILL
或断电打断则靠**下次启动自愈**。全程走字节：本仓混合行尾，read_text/
write_text 往返会留下数百行纯行尾 diff。不碰 git、不碰 data/ 真实数据。
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 正在被改坏的文件 -> 原始字节。本工具改的是**工作区文件**，而仓库根经 junction
# 就是线上目录（C:\lan-share），所以"跑一半被打断"绝不能留下改坏的状态。
_active: dict[str, bytes] = {}

# 写前备份目录：刻意放在仓库**外面**——不经 junction、不进 git、也不会被
# `git checkout` 顺手带走；进程被强杀它照样在盘上，这才是自愈的依据。
BACKUP_DIR = Path(tempfile.gettempdir()) / "lanshare-ablate"


def _sidecar(rel: str) -> Path:
    return BACKUP_DIR / (re.sub(r"[^A-Za-z0-9._-]", "_", rel) + ".json")


def _arm(rel: str, pristine: bytes, mutated: bytes) -> None:
    """改坏**之前**落盘。崩溃窗口里唯一还在的东西就是它。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _sidecar(rel).write_bytes(json.dumps({
        "rel": rel, "pid": os.getpid(),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pristine": base64.b64encode(pristine).decode(),
        "mutated": base64.b64encode(mutated).decode(),
    }, ensure_ascii=False).encode("utf-8"))


def _restore_one(rel: str, pristine: bytes) -> bool:
    fp = ROOT / rel
    fp.write_bytes(pristine)
    if fp.read_bytes() != pristine:
        print(f"!! 还原未生效 {rel}，请立即 git checkout -- {rel}")
        return False
    _active.pop(rel, None)
    try:
        _sidecar(rel).unlink()
    except OSError:
        pass
    return True


def _restore_all() -> None:
    for rel, pristine in list(_active.items()):
        try:
            _restore_one(rel, pristine)
        except OSError:
            print(f"!! 还原异常 {rel}，请立即 git checkout -- {rel}")


atexit.register(_restore_all)


def _on_signal(signum, _frame):
    _restore_all()
    sys.exit(128 + signum)


for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
    try:
        signal.signal(_sig, _on_signal)
    except (ValueError, OSError, RuntimeError):   # 非主线程 / 平台不支持
        pass
# SIGKILL（kill -9 / Stop-Process -Force）拦不住，所以**不把安全寄托在它身上**：
# 写前备份 + 启动自愈负责那条路径（2026-09-20 实测踩过：强杀后 filetools.py
# 留着 `if False and RESERVED_NAME.match(n)`，等于线上带着"保留名守卫已关闭"
# 的状态放在盘上，直到手工 git checkout）。


def recover_stale_backups() -> bool:
    """启动自愈：上一轮被强杀/断电留下的写前备份，在这里回滚。

    只在盘上字节**正好等于我们写进去的改坏形态**时才回滚——否则说明第三方
    在这期间动过该文件，宁可拒绝运行也不要覆盖别人的编辑。
    """
    if not BACKUP_DIR.exists():
        return True
    ok = True
    for sc in sorted(BACKUP_DIR.glob("*.json")):
        try:
            d = json.loads(sc.read_bytes())
            rel = d["rel"]
            pris = base64.b64decode(d["pristine"])
            mut = base64.b64decode(d["mutated"])
        except (ValueError, KeyError, OSError, TypeError):
            print(f"!! 写前备份损坏无法解析：{sc}，请人工确认后删除")
            ok = False
            continue
        fp = ROOT / rel
        cur = fp.read_bytes() if fp.exists() else b""
        if cur == pris:
            sc.unlink(missing_ok=True)          # 上次已还原，只是没来得及删记录
        elif cur == mut:
            print(f"⚠ 自愈：发现上一轮（pid {d.get('pid')} @ {d.get('started')}）"
                  f"被强杀留下的改坏文件 {rel}，回滚")
            if not _restore_one(rel, pris):
                ok = False
        else:
            print(f"!! {rel} 既非原始形态也非改坏形态——期间有第三方改动，"
                  f"拒绝自动回滚，请人工处置（备份留在 {sc}）")
            ok = False
    return ok


def dirty_targets(rels: list[str]) -> list[str]:
    """git 视角下这些目标文件里已带未提交改动的部分。"""
    p = subprocess.run(["git", "status", "--porcelain", "--", *rels],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(f"!! git status 失败，无法确认工作区干净，拒绝运行：{p.stderr.strip()[:200]}")
        return ["<git 不可用>"]
    out = []
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:]
        out.append(path.split(" -> ")[-1] if " -> " in path else path)
    return out

# (id, 不变量, 文件, 原文, 改坏后, 期望被抓到的原因)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    ("M1", "grant 按路径段前缀命中（E10）", "app/spaces.py",
     'if gp == key or key.startswith(gp + "/"):', 'if gp == key:',
     "子目录授权失效——只授 shared/photos 却访问不到其下文件"),
    ("M2", "比较键统一正斜杠（P1 2026-09-03）", "app/spaces.py",
     'return os.path.normcase(s).replace("\\\\", "/")', 'return os.path.normcase(s)',
     "Windows normcase 混入反斜杠后 startswith 永不命中，前缀授权形同虚设"),
    ("M3", "虚拟路径段级穿越清洗（E1）", "app/spaces.py",
     'if any(p == ".." or p.startswith(".") for p in parts):',
     'if any(p.startswith(".") for p in parts):',
     "等价变异：`\"..\"` 本身以 `.` 开头，点前缀支已覆盖，删它零行为变化"),
    ("M4", "grant 到期即失效", "app/spaces.py",
     'if r["expires_at"] and r["expires_at"] < now:', 'if False:',
     "过期授权继续放行"),
    ("M5", "聚合视图按条判可见性（§6 教训）", "app/spaces.py",
     '        return _grant_hit(user["user_id"], vkey, "read")\n    return True',
     '        return True\n    return True',
     "搜索/最近/照片把 guest 无权条目也列出来"),
    ("M6", "guest 对 shared 无默认读权（E4）", "app/spaces.py",
     'if role == GUEST and not _grant_hit(user["user_id"], vkey, "read"):', 'if False:',
     "guest 直接读到任意 shared 文件"),
    ("M7", "秒传必须样例可读（Dedup Oracle 封堵）", "app/spaces.py",
     'if _normcase_eq(parts[0], "private"):\n'
     '        return len(parts) >= 2 and _normcase_eq(parts[1], username)',
     'if _normcase_eq(parts[0], "private"):\n        return True',
     "借他人内容指纹秒传读出他人 private 内容（锚点须含 private 分支，"
     "否则与 incoming 分支同形、命中两次）"),
    ("M8", "安装包目录写权 admin-only（P2-2）", "app/spaces.py",
     'and need == "write" and role != ADMIN:', 'and False:',
     "持写 grant 的成员可投放恶意 APK = RCE 面"),
    ("M9", "成员不得跨私人/共享搬运（E9）", "app/spaces.py",
     "if src_priv != dest_priv:", "if False:",
     "成员可把共享文件藏进私人空间"),
    ("M10", "改名后 grants 缓存失效（R2-P3-8）", "app/spaces.py",
     "    invalidate_grants_cache(None)", "    pass",
     "改名后 guest 最长 30s 403（功能受损类）"),
    ("M11", "safe_path 物理根前缀兜底", "app/filetools.py",
     "if tgt != root and not tgt.startswith(root + os.sep):", "if False:",
     "构造上不可达：拆段后 joinpath 已把绝对路径中性化进根内，判据摸不到"),
    ("M12", "DAV 读根必经 resolve()（本轮 D1 修复）", "app/api/webdav.py",
     "    target = resolve(user, path, need)",
     '    target = resolve(user, path, need) if path else (\n'
     '        SHARE_ROOT if need == "read" else resolve(user, "", "write"))',
     "证明 D1 修复必要性：回退后 guest 根列举用例应变红"),
    ("M13", "截断后再剥结尾点/空格（本轮 D2 修复）", "app/filetools.py",
     "    return n[:200].rstrip('. ') or 'unnamed'",
     "    return n[:200] or 'unnamed'",
     "证明 D2 修复必要性：回退后 200 字符边界名字会重新落出结尾点"),
    ("M14", "文件名控制符过滤", "app/filetools.py",
     r"""    n = re.sub(r'[/\\:*?"<>|\x00-\x1f]', '_', name or 'unnamed')""",
     r"""    n = re.sub(r'[/\\:*?"<>|]', '_', name or 'unnamed')""",
     "控制符漏洗 → 落盘 OSError 500（手机端 Linux FS 可传来）"),
    ("M15", "Windows 保留名守卫", "app/filetools.py",
     "    if RESERVED_NAME.match(n):", "    if False and RESERVED_NAME.match(n):",
     "实测（2026-09-20）：open('con.txt','wb')+write 不报错、字节进控制台设备，"
     "os.replace 抛 FileExistsError → 失效后果是\"上传成功但文件不存在\"的静默丢数据"),
]


# 复核后判定"存活但不是缺口"的变异（理由见模块 docstring：等价变异 / 构造上不可达）。
# 新增存活项必须先复核再进这张表——否则这张表会变成掩盖真实缺口的垃圾桶。
EXPECTED_SURVIVORS = {"M3", "M11"}


def _variants(s: str) -> list[bytes]:
    """锚点的字节形态：本仓**混合行尾**（AGENTS.md §6），首轮就踩过——用
    read_text/write_text 往返会把 LF 行统一成 CRLF，还原"成功"却留下 555 行
    纯行尾 diff。所以全程走字节，锚点按 LF/CRLF 两种形态各试一次。"""
    b = s.encode("utf-8")
    return [b, b.replace(b"\n", b"\r\n")]


def _patch(fp: Path, rel: str, old: str, new: str) -> bytes | None:
    """把 old 换成 new，返回**原始字节**（供还原）；锚点不唯一/缺失则返回 None。
    替换用与命中锚点同一行尾形态，免得往混合行尾文件里插进另一种行尾。
    写盘之前先 _arm()——顺序反了就没有崩溃窗口可言。"""
    src = fp.read_bytes()
    for old_b, new_b in zip(_variants(old), _variants(new), strict=True):
        n = src.count(old_b)
        if n == 0:
            continue
        if n != 1:
            print(f"     ✗ 锚点命中 {n} 次（需恰好 1 次）")
            return None
        mutated = src.replace(old_b, new_b, 1)
        _arm(rel, src, mutated)
        fp.write_bytes(mutated)
        return src
    print("     ✗ 锚点在文件中不存在")
    return None


# 单轮上限：全套正常 ~400s。M15 那轮曾卡 >20 分钟，最后只能 SIGKILL 止损
# —— 有上限就不必再拿"杀掉自己"当止损手段（那是把还原责任交给拦不住的信号）。
ROUND_TIMEOUT = int(os.environ.get("ABULATE_TIMEOUT", "900"))

# --tests 指定的子集；默认全套 tests/
SUITE: list[str] = ["tests/"]


def _run_suite(timeout: int = ROUND_TIMEOUT) -> tuple[int | None, float, str]:
    """返回 (rc, 耗时, 首个失败标识)。rc=None 表示本轮超时。

    只有 rc==1（"有用例失败"）才算"被抓到"。pytest 的 2/3/4/5 分别是
    中断/内部错/用法错/未收集到用例——把它们当抓到，等于一个打错的 --tests
    路径就能给每条变异都盖上"已验证"的假章（本仓 2026-09-20 刚为同类
    "测试因错误原因通过"踩过一次）。
    """
    t0 = time.time()
    cmd = [sys.executable, "-m", "pytest", *SUITE, "-q", "-x",
           "-p", "no:cacheprovider"]
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace")
    try:
        out, err = p.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        timed_out = True
    if timed_out:
        return None, time.time() - t0, f"本轮超 {timeout}s 已中止（无结论）"
    full = out + err
    m = re.search(r"^(?:FAILED|ERROR) (\S+)", full, re.M)
    tail = full.strip().splitlines()[-1][:120] if full.strip() else "<无输出>"
    return p.returncode, time.time() - t0, (m.group(1) if m else tail)


def main() -> int:
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    if "--tests" in sys.argv:
        SUITE[:] = sys.argv[sys.argv.index("--tests") + 1].split(",")

    if not recover_stale_backups():
        print("\n✗ 自愈未能把线上树带回干净状态，拒绝在其上做消融。")
        return 3
    dirty = dirty_targets(sorted({m[2] for m in MUTATIONS}))
    if dirty:
        print("✗ 拒绝运行：目标文件在工作区已带未提交改动\n  " + "\n  ".join(dirty)
              + "\n  脏树会让消融结论张冠李戴（分不清是变异导致还是别人的改动导致）。"
                "\n  先提交或 git checkout 这些改动再跑。")
        return 3

    if SUITE != ["tests/"]:
        print(f"⚠ 子集模式（{' '.join(SUITE)}）：结论只代表这一子集，别当成全套结果读。")
    print("基线自检：先确认当前实现全套绿……", flush=True)
    rc, dur, first = _run_suite()
    if rc is None:
        print(f"✗ 基线轮超时（{first}），环境状态未知，消融结论无意义。")
        return 2
    if rc != 0:
        print(f"✗ 基线不绿（{first}），消融结论无意义，先修基线。")
        return 2
    print(f"  基线绿（{dur:.0f}s）\n", flush=True)

    survivors, killed, unexpected, aborted, skipped = [], [], [], [], []
    for mid, inv, rel, old, new, why in MUTATIONS:
        if only and mid not in only:
            continue
        fp = ROOT / rel
        pristine = _patch(fp, rel, old, new)
        if pristine is None:
            skipped.append(mid)
            print(f"{mid}  SKIP（锚点问题）: {rel}\n", flush=True)
            continue
        _active[rel] = pristine        # 被打断时 atexit/signal 处理器据此还原
        try:
            rc, dur, first = _run_suite()
        finally:
            _restore_one(rel, pristine)
        if rc is None or rc >= 2:      # 超时 / 未收集到用例 / 用法错：都不得算数
            print(f"{mid:4} {'无结论':14} {dur:5.0f}s  {inv}\n     ↳ {first}\n", flush=True)
            aborted.append((mid, inv, why))
            continue
        verdict = "被抓到" if rc == 1 else "存活(覆盖缺口)"
        print(f"{mid:4} {verdict:14} {dur:5.0f}s  {inv}\n     ↳ {first}\n", flush=True)
        if rc == 1:
            killed.append((mid, inv, why))
        else:
            survivors.append((mid, inv, why))
            if mid not in EXPECTED_SURVIVORS:
                unexpected.append((mid, inv, why))

    print("=" * 72)
    print(f"被测试抓到：{len(killed)} / 存活：{len(survivors)}"
          f"（其中已判定等价或不可达：{len(survivors) - len(unexpected)}）"
          f" / 无结论：{len(aborted)}")
    for mid, inv, why in survivors:
        flag = "ℹ 已判定非缺口" if mid in EXPECTED_SURVIVORS else "⚠ 覆盖缺口"
        print(f"  {flag} {mid} {inv} —— {why}")
    for mid, inv, _why in aborted:
        print(f"  ⚠ 无结论 {mid} {inv} —— 检查 --tests 路径 / 提高 ABULATE_TIMEOUT 重跑")
    return 1 if (unexpected or aborted or skipped) else 0


if __name__ == "__main__":
    sys.exit(main())
