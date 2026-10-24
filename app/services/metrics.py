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
- 例外（v3.70）：限速拒绝累计与启动史**持久化**到 settings——本机服务随
  开关机每天重启，纯进程内计数等于每天清零，"限速有没有误伤家人"的观测
  仪器必须在重启间存活（每小时 sweep 落盘，崩溃最多丢最后 <1h 增量）。
"""
import json
import threading
import time
from collections import deque

from app.config import ALERT_COOLDOWN, DISK_WATERMARK_PCT, SHARE_ROOT
from app.settings import get_setting, set_setting

_window: deque = deque(maxlen=2048)   # (ts, status, dur_ms)
_mu = threading.Lock()
_started = time.time()

# 限速拒绝计数（2026-09-28，不自信点①收口）：by_status 只留 1h 窗口、
# 桶 429 又不进任何日志——阈值是否误伤家人全靠事后无从查。桶级累计计数
# 自进程启动起累计（单 worker 进程内即全量），配 uptime_seconds 解读；
# 观察一周后据此定夺 dav/media 阈值，不再依赖“公网日志恰好 0 个 429”单点证据
_rate_rejects: dict[str, int] = {}

# ── 跨重启持久化（v3.70）：基线（历史累计）落 settings，进程内增量叠加，
# snapshot 输出合并值；每小时 sweep 落盘。启动史同理（环形 200 条）——
# 机器常整段关机，此前开关机史无处可查（10 月停机 15 天靠 PG 日志考古还原）。
REJECTS_PERSIST_KEY = "metrics_rate_rejects_total"
BOOT_HISTORY_KEY = "boot_history"
_BOOT_HISTORY_CAP = 200
_rejects_baseline: dict[str, int] = {}


def _merged_rejects() -> dict[str, int]:
    out = dict(_rejects_baseline)
    for k, v in _rate_rejects.items():
        out[k] = out.get(k, 0) + v
    return out


def load_persisted_rejects() -> None:
    """进程启动时载入历史累计（幂等；坏数据按空基线处理，不炸主流程）。"""
    global _rejects_baseline
    raw = get_setting(REJECTS_PERSIST_KEY, "")
    try:
        data = json.loads(raw) if raw else {}
        _rejects_baseline = {str(k): int(v) for k, v in data.items()}
    except (ValueError, TypeError):
        _rejects_baseline = {}


def persist_rejects() -> None:
    """合并总数写回 settings（cleanup sweep 每小时一轮；测试与 admin 也可直呼）。"""
    with _mu:
        merged = dict(_merged_rejects())
    set_setting(REJECTS_PERSIST_KEY, json.dumps(merged, ensure_ascii=False))


def note_boot() -> None:
    """记录一次进程启动（环形封顶）。巡检与排障据此还原开关机史。"""
    raw = get_setting(BOOT_HISTORY_KEY, "")
    try:
        boots = json.loads(raw) if raw else []
        boots = boots if isinstance(boots, list) else []
    except (ValueError, TypeError):
        boots = []
    boots.append({"ts": round(time.time(), 3)})
    set_setting(BOOT_HISTORY_KEY, json.dumps(boots[-_BOOT_HISTORY_CAP:]))


ALERT_TS_KEY = "disk_alert_last_ts"


def record(status: int, dur_ms: float) -> None:
    with _mu:
        _window.append((time.time(), status, dur_ms))


def record_rate_reject(bucket: str) -> None:
    with _mu:
        _rate_rejects[bucket] = _rate_rejects.get(bucket, 0) + 1


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
    with _mu:
        rejects = dict(sorted(_merged_rejects().items()))
    return {
        "window_seconds": window_seconds,
        "total": len(rows),
        "by_status": by_status,
        "latency_ms": {"p50": _pct(0.5), "p95": _pct(0.95),
                       "avg": round(sum(durs) / len(durs), 1) if durs else 0.0},
        # 桶级限速拒绝累计（不随窗口滑动、跨重启存活）+ 本次进程启动时长供解读
        "rate_rejects": rejects,
        "uptime_seconds": round(time.time() - _started),
    }


# ── 磁盘水位 watchdog（cleanup sweep 每小时一轮）─────────────


def ensure_scheduled() -> None:
    """注册进 cleanup sweep（幂等；由 app/main 组装时调用）。"""
    from app.services import cleanup

    def _tick() -> None:
        disk_watchdog()   # 返回值弃用：告警成功与否见 settings 的 alert 时间戳

    cleanup.register_sweep("disk_watermark", _tick)
    cleanup.register_sweep("metrics_persist", persist_rejects)  # v3.70：限速累计每小时落盘



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
