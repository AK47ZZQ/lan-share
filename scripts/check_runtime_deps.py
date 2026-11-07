"""运行时依赖对账:v4.0.10 httpx 事故的结构性预防。

根因回顾:requirements-runtime.txt(打包装什么)、build.ps1 冒烟列表(打包后
验什么)、app/ 源码(真正 import 什么)是**三份手工维护的清单**——必然漂移。
httpx 被当"开发门禁"从 runtime 清单砍掉的那天起,生产反馈上报每次 500,
测试全绿(测试跑在开发 venv,那里什么都有)。

本脚本把**源码定为唯一真值**,双向对账:

  1. AST 扫描 app/**/*.py + server.py 的全部 import 语句(含函数内),
     过滤标准库与本仓模块,剩下的就是第三方顶层模块集合 —— 改动源码
     不用改这里,新 import 自动进入对账。
  2. 默认模式(开发 venv 跑):每个第三方模块必须 ① 能映射到已装发行版
     (importlib.metadata.packages_distributions)② 发行版出现在
     installer/requirements-runtime.txt。requirements 漏包,构建前就红。
  3. --import 模式:在本解释器里把第三方模块真 import 一遍。build.ps1
     第 5 步用它(打包 runtime 的 python)替代手工冒烟列表 —— runtime
     缺包当场炸,不会等到用户机器上。

用法:
  python scripts/check_runtime_deps.py             # 开发环境对账
  python scripts/check_runtime_deps.py --import    # 目标解释器冒烟
"""
from __future__ import annotations

import re
import sys
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "installer" / "requirements-runtime.txt"

# 顶层模块名 -> requirements 行的发行版名不一致时的手工映射。
# 绝大多数包名与模块名一致(连 -/_ 都由 packages_distributions 归一),
# 出现新映射不一致的包时在这里补一行即可。
_DIST_OVERRIDES: dict[str, str] = {}

# 源码里有 try/except ImportError 保护的**可选**依赖(app/filetools.py 的
# pillow_heif 有 fallback 逻辑):对账模式不强制要求,--import 冒烟仍会验。
_OPTIONAL: set[str] = set()


def third_party_modules() -> set[str]:
    """app/ + server.py 的全部第三方顶层模块名(源码真值)。"""
    mods: set[str] = set()
    sources = sorted((ROOT / "app").rglob("*.py")) + [ROOT / "server.py"]
    for src in sources:
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        except SyntaxError as exc:
            print(f"FAIL: syntax error in {src}: {exc}")
            sys.exit(1)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mods.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    mods.add(node.module.split(".")[0])
    stdlib = set(sys.stdlib_module_names)
    ours = {"app", "server"}
    return {m for m in mods if m not in stdlib and m not in ours and m not in _OPTIONAL}


def _listed_dists() -> set[str]:
    """requirements-runtime.txt 里列的发行版名(小写,-/_ 归一)。"""
    out: set[str] = set()
    for line in REQ.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        t = t.split(";")[0]              # 去 env markers
        t = t.split("[")[0]              # 去 extras(psycopg[binary])
        t = re.split(r"[=<>!~ \t]", t)[0]  # 去版本约束
        if t:
            out.add(t.lower().replace("_", "-"))
    return out


def main() -> int:
    mods = third_party_modules()
    if not mods:
        print("FAIL: no third-party modules found - scan broken?")
        return 1

    if "--import" in sys.argv:
        import importlib
        bad: list[str] = []
        for m in sorted(mods):
            try:
                importlib.import_module(m)
            except Exception as exc:  # 冒烟要抓一切 import 失败
                bad.append(f"{m}: {type(exc).__name__}: {exc}")
        if bad:
            print("FAILED: " + "; ".join(bad))
            return 1
        print(f"OK import-smoke: {len(mods)} third-party modules")
        return 0

    # ── 对账模式 ──
    import importlib.metadata as md
    mapping = md.packages_distributions()  # module -> [dist names]

    listed = _listed_dists()
    problems: list[str] = []
    for m in sorted(mods):
        dists = mapping.get(m) or []
        override = _DIST_OVERRIDES.get(m)
        if override:
            dists = [override]
        if not dists:
            problems.append(f"{m}: imported in app/ but not installed in this env")
            continue
        for d in dists:
            if d.lower().replace("_", "-") not in listed:
                problems.append(f"{m} (dist {d}): not in installer/requirements-runtime.txt")
    if problems:
        print("FAILED: runtime dependency mismatch:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"OK {len(mods)} third-party modules, all covered by requirements-runtime.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
