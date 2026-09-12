"""系统任务：定时清理调度 + healthz 健康探针。

（07 审查批次 3B 按域拆分：照片时间线 → api/photos，精选专辑 → api/albums
（与 social 合流），QR → api/qr；通用与各域清理 → services/cleanup.py
注册式调度——system 不再 import 任何别家的私有状态。）
"""
from fastapi import APIRouter

from app.db import _db
from app.config import SHARE_ROOT
from app.services import cleanup

# 路由挂到本模块 router；app 对象在 app.asgi，由 app.main 统一组装
router = APIRouter()


async def startup():
    """首轮清理 + 周期循环（通用五件套 + 各域注册的 sweep）。

    由 app.main 的 lifespan 统一调度（router.on_event 已弃用）。"""
    await cleanup.startup()


@router.get("/healthz")
def healthz():
    """免认证健康探针（M5）：进程存活 + DB 连通 + 数据目录可写 + 磁盘水位。"""
    import shutil as _shutil
    checks = {"db": False, "data_dir": False}
    try:
        _db().execute("SELECT 1").fetchone()
        checks["db"] = True
    except Exception:
        pass
    try:
        probe = SHARE_ROOT / ".healthz-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["data_dir"] = True
    except Exception:
        pass
    du = _shutil.disk_usage(str(SHARE_ROOT))
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        **checks,
        "disk_free_gb": round(du.free / 2**30, 1),
        "disk_total_gb": round(du.total / 2**30, 1),
        "disk_used_pct": round(du.used / du.total * 100, 1),
    }
