"""`.trash` 无主残留对账（D5 残余风险 a 的第一步：先可见，再谈处置）。

背景（15-路线图 D5 记录）：`_move_to_trash_locked` 里"rename 成功 → INSERT 前"
崩溃会留下**躺在 .trash 但没有索引行**的文件——回收站列表点不到、30 天清理
也不收（清理按索引行走），属不可见的磁盘泄漏。本模块**只对账不删除**：
发现超期无主文件 → 写 settings 键 + 健康日报可见；物理处置（删除或重新
登记）留待人值守时拍板——无人值守会话不做"以对账结果为删除依据"的动作。

判定口径（防误报优先）：
- 只看 `SHARE_ROOT/.trash` 下的**文件**（子目录不属于该机制）；
- 无主 = 文件名不在 trash 索引 `trash_path` 基名集合里；
- 宽限 7 天（mtime）：rename→INSERT 窗口是亚秒级，7 天足以排除任何在途态，
  也排除备份/巡检瞬间读到的假阳性。
"""
import json
import logging
import time
from pathlib import Path

from app.config import SHARE_ROOT
from app.services import cleanup
from app.settings import set_setting
from app.store import trash as trash_store

AUDIT_KEY = "trash_orphan_audit_last"
GRACE_DAYS = 7
_MAX_ITEMS_IN_REPORT = 20


def audit_trash_orphans(now: float | None = None) -> dict:
    """扫 .trash 找无主超期文件；结果落 settings（健康日报读取）并返回。"""
    now = now if now is not None else time.time()
    trash_root = Path(SHARE_ROOT) / ".trash"
    referenced = trash_store.referenced_trash_names()
    orphans: list[dict] = []
    try:
        entries = list(trash_root.iterdir())
    except OSError:
        entries = []
    for f in entries:
        try:
            if not f.is_file() or f.name in referenced:
                continue
            age_days = (now - f.stat().st_mtime) / 86400
            if age_days < GRACE_DAYS:
                continue
            orphans.append({"name": f.name, "size": f.stat().st_size,
                            "age_days": int(age_days)})
        except OSError:
            continue   # 瞬时消失/权限：本轮跳过，下轮再看
    report = {"ts": now, "count": len(orphans),
              "total_bytes": sum(o["size"] for o in orphans),
              "items": sorted(orphans, key=lambda o: -o["age_days"])
                       [:_MAX_ITEMS_IN_REPORT]}
    set_setting(AUDIT_KEY, json.dumps(report, ensure_ascii=False))
    if orphans:
        # 只在真有残留时留痕：日常零残留不该每小时刷日志
        logging.warning("trash orphan audit: %d 个无主残留，共 %d 字节（详见 %s）",
                        report["count"], report["total_bytes"], AUDIT_KEY)
    return report


def _sweep() -> None:
    audit_trash_orphans()


cleanup.register_sweep("trash_orphan_audit", _sweep)
