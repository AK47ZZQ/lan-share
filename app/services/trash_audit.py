"""`.trash` 无主残留对账（D5 残余风险 a）：先可见（v3.66），再救援（v3.67）。

背景（15-路线图 D5 记录）：`_move_to_trash_locked` 里"rename 成功 → INSERT 前"
崩溃会留下**躺在 .trash 但没有索引行**的文件——回收站列表点不到，且
`_cleanup_trash` 按 **mtime**（不分有无索引）清理 30 天以上文件 ⇒ 残留的
真实风险是"**前 30 天不可见、之后被静默删除**"。

两段处置（本模块）：
- **可见**：超 7 天宽限的无主文件写入 settings + 健康日报（v3.66）。
- **救援**（v3.67，点单确认的方案②）：7~29 天的无主文件**自动重新登记**
  进回收站索引——家人立即可见/可还原；deleted_at 取文件 mtime，与
  `_cleanup_trash` 的按 mtime 清理天然对齐（行与文件同寿命，不延长也不
  缩短残留的生存期）。**29 天是硬上限**：再晚登记会与小时级清理竞态，
  产出"有行无文件"的坏条目（D5 当初要消灭的形态）。

只登记、从不删除；判定口径防误报优先（7 天宽限 >> 亚秒级竞态窗口）。
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
GRACE_DAYS = 7            # 宽限：rename→INSERT 竞态窗口远小于此
RESCUE_MAX_DAYS = 29      # 救援上限：再晚会与小时级清理竞态产出坏条目
_MAX_ITEMS_IN_REPORT = 20


def _orig_name(file_name: str) -> str:
    """`.trash` 落盘名 = `{time_ns()}_{原名}`（filetools._move_to_trash_locked）。
    原名可含下划线 ⇒ 只按第一个下划线切分；不符合命名约定的残留整名保留。"""
    return file_name.split("_", 1)[1] if "_" in file_name else file_name


def audit_trash_orphans(now: float | None = None) -> dict:
    """扫 .trash：无主超期文件 → 救援登记 + 结果落 settings（健康日报读取）。"""
    now = now if now is not None else time.time()
    trash_root = Path(SHARE_ROOT) / ".trash"
    referenced = trash_store.referenced_trash_names()
    orphans: list[dict] = []
    rescued = 0
    try:
        entries = list(trash_root.iterdir())
    except OSError:
        entries = []
    for f in entries:
        try:
            if not f.is_file() or f.name in referenced:
                continue
            st = f.stat()
            age_days = (now - st.st_mtime) / 86400
            if age_days < GRACE_DAYS:
                continue
            if age_days < RESCUE_MAX_DAYS:
                # 救援窗口（7~29 天）：登记进回收站，家人可见可还原；
                # deleted_at 取 mtime ⇒ 行与文件同寿命（见模块 docstring）
                trash_store.register_rescued(
                    "（对账救援·原路径未知）", f".trash/{f.name}",
                    _orig_name(f.name), st.st_size, st.st_mtime)
                referenced.add(f.name)
                rescued += 1
                continue
            # ≥29 天：即将被/已被小时级清理按 mtime 移除，只报告不动
            orphans.append({"name": f.name, "size": st.st_size,
                            "age_days": int(age_days)})
        except OSError:
            continue   # 瞬时消失/权限：本轮跳过，下轮再看
    report = {"ts": now, "count": len(orphans), "rescued": rescued,
              "total_bytes": sum(o["size"] for o in orphans),
              "items": sorted(orphans, key=lambda o: -o["age_days"])
                       [:_MAX_ITEMS_IN_REPORT]}
    set_setting(AUDIT_KEY, json.dumps(report, ensure_ascii=False))
    if orphans or rescued:
        # 只在真有事件时留痕：日常零残留不该每小时刷日志
        logging.warning("trash orphan audit: 救援登记 %d，超期未登记 %d"
                        "（详见 %s）", rescued, report["count"], AUDIT_KEY)
    return report


def _sweep() -> None:
    audit_trash_orphans()


cleanup.register_sweep("trash_orphan_audit", _sweep)
