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
每条实验打印结果；退出码非 0 表示存在**未判定**的存活变异（即覆盖缺口）。

**存活 ≠ 一定有缺口**（首轮实测到的两类误报，故须复核后再入 EXPECTED）：
  · 等价变异：M3 删掉 `p == ".."` 后行为不变——`".."` 本来就以 `.` 开头，
    同一条件的点前缀支已经覆盖它。任何测试都抓不到一个不改行为的改动。
  · 构造上不可达：M11 的根前缀兜底——safe_path 先按 `/` 拆段再 joinpath，
    绝对路径被就地中性化（实测 `C:/Windows/win.ini` → `<root>/Windows/win.ini`、
    `/etc/passwd` → `<root>/etc/passwd`），永远落在根内，判据无从触发。
    它是防"未来有人改了拆段逻辑"的保险，不是当前防线。

安全：只改工作区文件并从内存字节副本还原，不碰 git、不碰 data/ 真实数据。
（必须走字节：本仓混合行尾，read_text/write_text 往返会留下数百行纯行尾 diff。）
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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


def _patch(fp: Path, old: str, new: str) -> bytes | None:
    """把 old 换成 new，返回**原始字节**（供还原）；锚点不唯一/缺失则返回 None。
    替换用与命中锚点同一行尾形态，免得往混合行尾文件里插进另一种行尾。"""
    src = fp.read_bytes()
    for old_b, new_b in zip(_variants(old), _variants(new), strict=True):
        n = src.count(old_b)
        if n == 0:
            continue
        if n != 1:
            print(f"     ✗ 锚点命中 {n} 次（需恰好 1 次）")
            return None
        fp.write_bytes(src.replace(old_b, new_b, 1))
        return src
    print("     ✗ 锚点在文件中不存在")
    return None


def _run_suite() -> tuple[int, float, str]:
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "-x",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = p.stdout + p.stderr
    m = re.search(r"^(?:FAILED|ERROR) (\S+)", out, re.M)
    return p.returncode, time.time() - t0, (m.group(1) if m else out.strip().splitlines()[-1][:120])


def main() -> int:
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    print("基线自检：先确认当前实现全套绿……", flush=True)
    rc, dur, first = _run_suite()
    if rc != 0:
        print(f"✗ 基线不绿（{first}），消融结论无意义，先修基线。")
        return 2
    print(f"  基线绿（{dur:.0f}s）\n", flush=True)

    survivors, killed, unexpected, skipped = [], [], [], []
    for mid, inv, rel, old, new, why in MUTATIONS:
        if only and mid not in only:
            continue
        fp = ROOT / rel
        pristine = _patch(fp, old, new)
        if pristine is None:
            skipped.append(mid)
            print(f"{mid}  SKIP（锚点问题）: {rel}\n", flush=True)
            continue
        try:
            rc, dur, first = _run_suite()
        finally:
            fp.write_bytes(pristine)
            if (ROOT / rel).read_bytes() != pristine:
                print(f"!! {mid} 还原失败，请手工 git checkout -- {rel}")
        verdict = "被抓到" if rc != 0 else "存活(覆盖缺口)"
        print(f"{mid:4} {verdict:14} {dur:5.0f}s  {inv}\n     ↳ {first}\n", flush=True)
        if rc != 0:
            killed.append((mid, inv, why))
        else:
            survivors.append((mid, inv, why))
            if mid not in EXPECTED_SURVIVORS:
                unexpected.append((mid, inv, why))

    print("=" * 72)
    print(f"被测试抓到：{len(killed)} / 存活：{len(survivors)}"
          f"（其中已判定等价或不可达：{len(survivors) - len(unexpected)}）")
    for mid, inv, why in survivors:
        flag = "ℹ 已判定非缺口" if mid in EXPECTED_SURVIVORS else "⚠ 覆盖缺口"
        print(f"  {flag} {mid} {inv} —— {why}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
