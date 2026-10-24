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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _count_py(d: Path) -> int:
    return len([p for p in d.glob("*.py") if p.name != "__init__.py"])


def main() -> int:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
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
    elif not Path(m.group(1).strip().strip('"')).is_dir():
        problems.append(f"LANSHARE_FLUTTER_BIN 指向不存在的目录：{m.group(1)}")

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
