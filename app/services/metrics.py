"""指标采集与水位告警（B5）：进程内轻量计数 + 磁盘水位 webhook 告警。

## 指标

- asgi 安全中间件尾部逐请求 `record(status, dur_ms)`（只记真正到达路由的
  请求——中间件早退的安全拒绝不计，延迟分布语义更干净）；
- 环形缓冲 deque(maxlen=2048) + 锁，snapshot() 给 by_status 计数与
  p50/p95/avg 延迟；单 worker 部署（NSSM）下进程内即全量。

## 水位告警

- 磁盘已用 ≥ DISK_WATERMARK_PCT 触发 webhook（复用 services/webhook 的
  SSRF 防护下发），冷却期 ALERT_COOLDOWN 内不重复（防轰炸）；
- 恢复复用同一冷却：水位回落后下一次超阈值仍要等冷却过——告警是"提醒
  上门"不是"持续状态上报"，状态本身看 /api/metrics。

## 边界

- 采集零落盘、零阻塞：record 只是内存 append；watchdog 注册进 cleanup
  sweep（每小时一轮），磁盘 du 的一次 syscall 在线程池里执行。
"""
import threading
import time
from collections import deque

from app.config import ALERT_COOLDOWN, DISK_WATERMARK_PCT, SHARE_ROOT
from app.settings import get_setting, set_setting

_window: deque = deque(maxlen=2048)   # (ts, status, dur_ms)
_mu = threading.Lock()

ALERT_TS_KEY = "disk_alert_last_ts"


def record(status: int, dur_ms: float) -> None:
    with _mu:
        _window.append((time.time(), status, dur_ms))


def snapshot(window_seconds: float = 3600) -> dict:
    """近窗口统计：by_status 计数 + 延迟分位（p50/p95/avg，毫秒）。"""
    cutoff = time.time() - window_seconds
    with _mu:
        rows = [(s, d) for (ts, s, d) in _window if ts >= cutoff]
    durs = sorted(d for _, d in rows)
    by_status: dict[str, int] = {}
    for s, _ in rows:
        k = f"{s // 100}xx"
        by_status[k] = by_status.get(k, 0) + 1
    def _pct(p: float) -> float:
        return round(durs[min(len(durs) - 1, int(len(durs) * p))], 1) if durs else 0.0
    return {
        "window_seconds": window_seconds,
        "total": len(rows),
        "by_status": by_status,
        "latency_ms": {"p50": _pct(0.5), "p95": _pct(0.95),
                       "avg": round(sum(durs) / len(durs), 1) if durs else 0.0},
    }


# ── 磁盘水位 watchdog（cleanup sweep 每小时一轮）─────────────


def disk_used_pct() -> float:
    import shutil
    du = shutil.disk_usage(str(SHARE_ROOT))
    return round(du.used / du.total * 100, 1)


def disk_watchdog(push=None) -> bool:
    """超阈值 → webhook 告警（冷却期内静默）。返回本轮是否真的发出告警。

    push 参数为测试注入点（默认取 services/webhook.push_webhook，函数内延迟
    import 避免环）。
    """
    try:
        pct = disk_used_pct()
    except OSError:
        return False
    if pct < DISK_WATERMARK_PCT:
        return False
    last = float(get_setting(ALERT_TS_KEY, "0") or 0)
    now = time.time()
    if now - last < ALERT_COOLDOWN:
        return False
    set_setting(ALERT_TS_KEY, str(now))
    if push is None:
        from app.services import webhook
        push = webhook.push_webhook
    push("磁盘水位告警", f"已用 {pct}%（阈值 {DISK_WATERMARK_PCT}%），请清理或扩容")
    return True
