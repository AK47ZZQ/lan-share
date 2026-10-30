"""AGENTS.md 基线守卫（v3.70）：文档里的结构性数字必须与现实一致，否则红。

背景（2026-10-24 评审 #7）：AGENTS 四项基线数字集体过期四周（138→143 路径、
46→64 测试文件、206→240 flutter 项、Flutter 路径指向不存在的目录）——文档是
本仓"最重要的资产"，但维护它是无聊活；无聊的活要交给门禁，不交给自觉。

校验项（绑定 AGENTS.md §2/§3 文案，改文案须同步本守卫的正则）：
  1. app/api|services|store 模块数（不含 __init__.py）
  2. tests/ 的 test_*.py 数
  3. contracts/openapi.json 的路径数
  4. env.bat LANSHARE_FLUTTER_BIN 指向的目录真实存在
退出码：全一致 0；任何漂移 1（打印 AGENTS 写的值 vs 实际值）。
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _count_py(d: Path) -> int:
    return len([p for p in d.glob("*.py") if p.name != "__init__.py"])


def main() -> int:
    agents_path = ROOT / "AGENTS.md"
    if not agents_path.exists():
        print("AGENTS.md 不在本版本中（内部规范未随开源发布），跳过基线守卫")
        raise SystemExit(0)
    agents = agents_path.read_text(encoding="utf-8")
    problems: list[str] = []

    def check(pattern: str, actual: int, what: str) -> None:
        m = re.search(pattern, agents)
        if not m:
            problems.append(f"AGENTS.md 找不到模式 {pattern!r}（文案改了？请同步守卫）")
            return
        if int(m.group(1)) != actual:
            problems.append(f"{what}：AGENTS 写 {m.group(1)}，实际 {actual}")

    check(r"(\d+) 个按域 API 模块", _count_py(ROOT / "app" / "api"), "api 模块数")
    check(r"(\d+) 个业务模块", _count_py(ROOT / "app" / "services"), "services 模块数")
    check(r"(\d+) 个表主人模块", _count_py(ROOT / "app" / "store"), "store 模块数")
    check(r"(\d+) 个测试文件", len(list((ROOT / "tests").glob("test_*.py"))), "测试文件数")
    openapi = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    check(r"契约快照（(\d+) 路径", len(openapi["paths"]), "openapi 路径数")

    envbat = (ROOT / "env.bat").read_text(encoding="ascii", errors="replace")
    m = re.search(r"LANSHARE_FLUTTER_BIN=(\S+)", envbat)
    if not m:
        problems.append("env.bat 缺 LANSHARE_FLUTTER_BIN")
    else:
        # env.bat 的默认值基于 %LANSHARE_ROOT%（脚本自身位置），这样仓库可以克隆到任意
        # 路径。静态检查时按仓库根把它展开；若展开后仍残留其它 %VAR%，说明该路径依赖
        # 外部环境，此时跳过存在性检查——不能把「无法静态判定」误报成「基线漂移」。
        raw = m.group(1).strip().strip('"')
        # 用 lambda 做替换：替换串里含 C:\Users\ 这类路径，直接当 repl 会被 re 当成
        # 转义序列（bad escape \U）。
        raw = re.sub(r"%LANSHARE_ROOT%", lambda _: str(ROOT) + os.sep, raw, flags=re.IGNORECASE)
        if "%" in raw:
            print(f"跳过 Flutter 路径检查：{raw} 含未展开的环境变量")
        elif not Path(raw).is_dir():
            problems.append(f"LANSHARE_FLUTTER_BIN 指向不存在的目录：{raw}")

    if problems:
        print("基线漂移（改完代码/文档后两边都要动）：")
        for p in problems:
            print(" -", p)
        return 1
    print("基线一致：api/services/store 模块数、测试文件数、openapi 路径数、"
          "Flutter 路径 全部核对通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
