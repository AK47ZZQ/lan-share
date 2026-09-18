"""后台循环健康登记（v3.56）：把"定时任务静默猝死"变成看得见的状态。

## 起因

缩略图预热在**生产 PG** 上每 30s 抛一次 `UndefinedColumn: rowid`（SQLite 独有
伪列，单测引擎察觉不到）。循环必须 per-round 吞异常，否则一次 OSError 就杀死
`while True`——于是"吞异常保活"与"吞异常即失踪"是同一件事的两面：进程活着、
日志在刷、功能零产出，数日无人知晓。本模块补上后一半：**每轮成败进内存计数，
健康日报据此告警**。

## 用法与不变式

`watched` / `watched_sync` 是唯一推荐入口——它同时负责"吞异常保活"和"登记"，
所以调用点在结构上无法做到"只吞不记"（这正是 rowid 事件的漏口）。

- 纯进程内，与 `services/metrics` 同前提（单 worker NSSM 部署），**不持久化**：
  重启即清零，重启后第一轮就会重新记到；
- 只记最近一次错误文本，不做历史堆栈（日志里有完整 traceback）；
- `failing()` 是告警口径：**连续**失败次数达阈值才算坏，避免单次抖动刷屏。
"""
import logging
import threading
import time
from contextlib import asynccontextmanager, contextmanager

# 连续失败到第 N 轮才告警：预热/清理都可能因竞态偶发抛一次，不该为它发日报
CONSECUTIVE_ALERT = 3

_state: dict[str, dict] = {}
_mu = threading.Lock()


def _entry(name: str) -> dict:
    e = _state.get(name)
    if e is None:
        e = {"runs": 0, "errors": 0, "consecutive": 0,
             "last_ok_at": None, "last_error_at": None, "last_error": ""}
        _state[name] = e
    return e


def note_ok(name: str) -> None:
    with _mu:
        e = _entry(name)
        e["runs"] += 1
        e["consecutive"] = 0
        e["last_ok_at"] = time.time()


def note_error(name: str, exc: BaseException | None = None) -> None:
    with _mu:
        e = _entry(name)
        e["runs"] += 1
        e["errors"] += 1
        e["consecutive"] += 1
        e["last_error_at"] = time.time()
        e["last_error"] = f"{type(exc).__name__}: {exc}" if exc else "unknown"


@asynccontextmanager
async def watched(name: str):
    """异步循环体包装：吞异常保活 + 登记成败（异常逸出会杀死宿主 while 循环）。"""
    try:
        yield
    except Exception as exc:      # 吞掉是设计意图：逸出即事故（保活是调用方的需求）
        note_error(name, exc)
        logging.exception("%s round failed", name)
    else:
        note_ok(name)


@contextmanager
def watched_sync(name: str):
    """同步版（线程池内执行的分项，如 cleanup 的单个 sweep）。"""
    try:
        yield
    except Exception as exc:      # 同上
        note_error(name, exc)
        logging.exception("%s round failed", name)
    else:
        note_ok(name)


def snapshot() -> dict:
    """{循环名: 计数}，浅拷贝供 API/日报输出。"""
    with _mu:
        return {k: dict(v) for k, v in _state.items()}


def failing(threshold: int = CONSECUTIVE_ALERT) -> list[dict]:
    """连续失败已达阈值的循环（按名排序，供日报判定"有没有坏"）。"""
    with _mu:
        rows = [{"name": k, **v} for k, v in _state.items()
                if v["consecutive"] >= threshold]
    rows.sort(key=lambda r: r["name"])
    return rows


def reset_loops() -> None:
    """测试夹具用：清空登记态（与 security.reset_auth_gates 同型）。"""
    with _mu:
        _state.clear()
