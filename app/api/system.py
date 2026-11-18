"""系统任务：定时清理调度 + healthz 健康探针。

（07 审查批次 3B 按域拆分：照片时间线 → api/photos，精选专辑 → api/albums
（与 social 合流），QR → api/qr；通用与各域清理 → services/cleanup.py
注册式调度——system 不再 import 任何别家的私有状态。）
"""
from fastapi import APIRouter, Response

from app.db import _db
from app.config import SHARE_ROOT
from app.services import cleanup
from app import settings

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


@router.get("/api/instance-id")
def instance_id(response: Response):
    """公开的实例标识 —— 只给「公网入口同源校验」用。

    场景：用户在托盘里填 `https://自己的域名`，程序要确认「这个域名指向的就是
    这台机器」。做法是从本机和从公网各读一次本端点，比对 UUID 是否相同。
    所以它**必须免鉴权**（探针经隧道进来时没有登录态）。

    ## 为什么只回一个 UUID，一个字段都不多加

    加 `version` 是最容易犯的「顺手友好」错误：那等于给攻击者一份
    「该用哪个已知漏洞」的清单。加 `name`/`user_count` 则直接泄露部署信息。

    ## 暴露风险评估（已记入 docs/SECURITY.md）

    最坏情况：别人能知道「这两个域名属于同一套部署」。不含用户数、内容、
    版本、文件结构。UUID 本身是随机值，无法反推任何东西。可接受。

    ## 响应头

    - `Cache-Control: no-store`：别让 CDN 或浏览器缓存实例标识
    - `X-Robots-Tag: noindex`：别让搜索引擎把 UUID 收进结果页
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return {"instance_id": settings.ensure_instance_id()}
