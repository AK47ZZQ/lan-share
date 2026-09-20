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
每条实验打印结果；退出码非 0 表示存在"存活"的变异（即覆盖缺口）。

安全：只改工作区文件并从内存副本还原，不碰 git、不碰 data/ 真实数据。
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
     "private:../../etc 之类可达物理穿越"),
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
     "符号链接/绝对路径逃出 SHARE_ROOT"),
    ("M12", "DAV 读根必经 resolve()（本轮 D1 修复）", "app/api/webdav.py",
     "    target = resolve(user, path, need)",
     '    target = resolve(user, path, need) if path else (\n'
     '        SHARE_ROOT if need == "read" else resolve(user, "", "write"))',
     "证明 D1 修复必要性：回退后 guest 根列举用例应变红"),
]


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
    print(f"基线自检：先确认当前实现全套绿……", flush=True)
    rc, dur, first = _run_suite()
    if rc != 0:
        print(f"✗ 基线不绿（{first}），消融结论无意义，先修基线。")
        return 2
    print(f"  基线绿（{dur:.0f}s）\n", flush=True)

    survivors, killed = [], []
    cache: dict[str, str] = {}
    for mid, inv, rel, old, new, why in MUTATIONS:
        if only and mid not in only:
            continue
        fp = ROOT / rel
        if rel not in cache:
            cache[rel] = fp.read_text(encoding="utf-8")
        src = cache[rel]
        n = src.count(old)
        if n != 1:
            print(f"{mid}  SKIP 锚点命中 {n} 次（需恰好 1 次）: {rel}")
            continue
        fp.write_text(src.replace(old, new, 1), encoding="utf-8")
        try:
            rc, dur, first = _run_suite()
        finally:
            fp.write_text(src, encoding="utf-8")
        verdict = "被抓到" if rc != 0 else "存活(覆盖缺口)"
        print(f"{mid:4} {verdict:14} {dur:5.0f}s  {inv}\n     ↳ {first}\n", flush=True)
        (killed if rc != 0 else survivors).append((mid, inv, why))

    print("=" * 72)
    print(f"被测试抓到：{len(killed)} / 存活：{len(survivors)}")
    for mid, inv, why in survivors:
        print(f"  ⚠ {mid} {inv} —— 破坏它测试全绿；风险：{why}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
