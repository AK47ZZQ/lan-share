"""导出 OpenAPI 契约快照（07 审查批次 5：三端契约的防漂移手段）。

用法（仓库根）：
    python scripts/export-openapi.py

产物 contracts/openapi.json 提交进 git：服务端任何路径/参数/响应模型变更
都会让 tests/test_contract.py 的快照比对红灯——把"改 A 忘改 B"从三端
人肉对齐变成 git diff 可见的显式契约变更。web/mobile 的类型与端点路径
以本快照为对齐基准。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# app 导入链会读 SHARE_ROOT 并建表：导出脚本永远跑在一次性临时根上，
# 不碰生产数据
tmp = tempfile.mkdtemp(prefix="lanshare-openapi-")
os.environ["SHARE_ROOT"] = tmp

sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app  # noqa: E402


def main() -> None:
    from app.contract import normalize_openapi
    spec = normalize_openapi(app.openapi())
    out = PROJECT_ROOT / "contracts" / "openapi.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    routes = sum(len(paths) for paths in [spec.get("paths", {})])
    print(f"openapi.json written: {routes} paths -> {out}")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
