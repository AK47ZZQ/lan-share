"""OpenAPI 契约快照比对（07 审查批次 5）。

服务端任何路径/参数/请求响应模型变更都会在这里红灯——契约变更是显式
动作：运行 `python scripts/export-openapi.py` 更新快照并同步 web/mobile。
"""
import json
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "contracts" / "openapi.json"


def test_openapi_matches_committed_snapshot(client):
    from app.main import app

    assert SNAPSHOT.is_file(), (
        "契约快照缺失：先运行 python scripts/export-openapi.py 生成")
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    from app.contract import normalize_openapi
    actual = normalize_openapi(app.openapi())
    diff_paths = set(actual.get("paths", {})) ^ set(saved.get("paths", {}))
    assert actual == saved, (
        f"API 契约漂移（新增/消失路径：{sorted(diff_paths) or '模型/参数级，详见 diff'}）。"
        "若为有意变更：python scripts/export-openapi.py 更新快照，并同步 web/mobile 两端")
