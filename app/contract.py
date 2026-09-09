"""OpenAPI 契约归一化（07 审查批次 5）。

webdav 的多方法 api_route 让 FastAPI 按 methods 集合的迭代序给重复
operationId 追加去重后缀，跨进程不稳定；契约比对（导出脚本与
tests/test_contract.py）先经这里把 dav 系 operationId 归一为
path+method 的确定形态。
"""


def normalize_openapi(spec: dict) -> dict:
    for path, ops in spec.get("paths", {}).items():
        for method, op in list(ops.items()):
            if isinstance(op, dict) and str(op.get("operationId", "")).startswith("dav_handler"):
                op["operationId"] = f"dav_{path}_{method}".replace("/", "_")
    return spec
