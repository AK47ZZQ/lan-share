"""运行时依赖对账回归(v4.0.10 httpx 事故的结构性预防)。

scripts/check_runtime_deps.py 的常驻门禁包装:app/ 的第三方 import 与
installer/requirements-runtime.txt 必须双向覆盖 —— requirements 漏包,
构建前就红,不再依赖任何人记得手工同步清单。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_runtime_deps_match_requirements():
    """app/ 第三方 import ⊆ requirements-runtime.txt(双向对账,rc==0)。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_runtime_deps.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120)
    assert r.returncode == 0, f"依赖对账失败:\n{r.stdout}\n{r.stderr}"
    assert "OK" in r.stdout
