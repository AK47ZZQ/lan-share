"""后台循环健康登记（v3.56）——把"吞异常保活"变成"看得见"。

起因：预热循环在生产 PG 上每 30s 抛错、连续数日无人发现。本文件锁两件事：
1. 登记语义（连续失败计数、成功即归零、阈值告警）；
2. **一个每轮都失败的循环必须能被日报指出来**——这才是真正的防线，
   因为"不抛异常"从来不是问题，"抛了但没人知道"才是。
"""
import asyncio

import pytest

from app.api.healthreport import format_report, gather_health
from app.services import cleanup, loop_health

# 每条清理/调度项都必须注册（注册靠模块 import 副作用，最容易悄悄丢）。
# v3.57：share_tickets 项已并入 cleanup._cleanup_db 的 ttl_state 统一过期回收。
EXPECTED_SWEEPS = {"upload_sessions", "zip_tickets",
                   "log_archives", "todo_due", "daily_backup", "disk_watermark"}


@pytest.fixture(autouse=True)
def _clean():
    loop_health.reset_loops()
    yield
    loop_health.reset_loops()


def _health_dict(**over) -> dict:
    """format_report 的手搭基线（健康全绿），再按需覆盖单字段。"""
    d = {"disk_used_pct": 40.0, "disk_free_gb": 100.0,
         "backup": {"present": True, "dump_name": "x.dump", "dump_age_hours": 3.0,
                    "orchestrator": {"ok": True, "verified": True,
                                     "verify_detail": "sha ok"}},
         "cert_days_left": 90, "public_ok": True, "counts": {},
         "loops_failing": []}
    d.update(over)
    return d


# ─── 登记语义 ─────────────────────────────────────────────


def test_note_ok_resets_consecutive_but_keeps_history():
    for _ in range(3):
        loop_health.note_error("loopA", ValueError("boom"))
    e = loop_health.snapshot()["loopA"]
    assert (e["runs"], e["errors"], e["consecutive"]) == (3, 3, 3)
    loop_health.note_ok("loopA")
    e = loop_health.snapshot()["loopA"]
    assert (e["runs"], e["errors"], e["consecutive"]) == (4, 3, 0), \
        "成功必须立刻归零连续计数（否则恢复后仍长期告警）"
    assert e["errors"] == 3, "历史错误数不该被抹掉"


def test_failing_uses_consecutive_threshold():
    loop_health.note_error("loopB", OSError("x"))
    loop_health.note_error("loopB", OSError("x"))
    assert loop_health.failing() == [], "未达阈值不告警（单次抖动不该刷屏日报）"
    loop_health.note_error("loopB", OSError("x"))
    bad = loop_health.failing()
    assert [r["name"] for r in bad] == ["loopB"]
    assert bad[0]["consecutive"] == 3
    assert "OSError" in bad[0]["last_error"]


def test_watched_sync_swallows_and_records():
    """watched_sync 既要吞异常（保活），又必须登记——两者是同一个设计。"""
    with loop_health.watched_sync("loopC"):
        raise ValueError("inner")
    assert loop_health.snapshot()["loopC"]["consecutive"] == 1


def test_watched_async_records_success_and_failure():
    async def boom():
        async with loop_health.watched("loopD"):
            raise RuntimeError("db down")

    async def fine():
        async with loop_health.watched("loopD"):
            return 1

    asyncio.run(boom())
    asyncio.run(fine())
    e = loop_health.snapshot()["loopD"]
    assert (e["runs"], e["errors"], e["consecutive"]) == (2, 1, 0)


def test_watched_does_not_swallow_cancellation():
    """CancelledError 是 BaseException：保活不该吞它，否则关停/超时失效。"""
    async def cancelled():
        async with loop_health.watched("loopE"):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled())
    assert "loopE" not in loop_health.snapshot()


# ─── 日报真的能看见 ───────────────────────────────────────


def test_health_report_flags_a_dead_loop():
    """rowid 事故的探测口径：每轮都失败的循环 → 日报里一行指名道姓 + warn。"""
    for _ in range(loop_health.CONSECUTIVE_ALERT):
        loop_health.note_error("thumb warmup", Exception(
            'column "rowid" does not exist'))
    d = gather_health()
    assert [r["name"] for r in d["loops_failing"]] == ["thumb warmup"]
    body, warn = format_report(d)
    assert warn is True
    assert "后台任务停摆" in body and "thumb warmup" in body
    assert 'rowid' in body, "最近错误要带进日报，否则站长还得翻日志"


def test_health_report_quiet_when_loops_healthy():
    loop_health.note_ok("thumb warmup")
    body, warn = format_report(_health_dict(loops_failing=loop_health.failing()))
    assert warn is False and "停摆" not in body


def test_format_report_tolerates_missing_key():
    """老调用方/手搭 dict 不带 loops_failing 时不得 KeyError（日报停发=失去唯一哨兵）。"""
    d = _health_dict()
    del d["loops_failing"]
    _, warn = format_report(d)
    assert warn is False


# ─── cleanup：一个坏 sweep 不再毒掉整轮 ────────────────────


def test_run_registered_isolates_failing_sweep():
    """分项隔离（v3.56）：原先一个 sweep 抛错 → 后续 sweep 与 _cleanup_db 全跳过。"""
    seen: list[str] = []
    before = len(cleanup._sweeps)

    def bad():
        seen.append("bad")
        raise OSError("竞态：文件已被并发删掉")

    def good():
        seen.append("good")

    cleanup.register_sweep("smoke-bad", bad)
    cleanup.register_sweep("smoke-good", good)
    try:
        cleanup.run_registered()          # 不得抛出
    finally:
        del cleanup._sweeps[before:]
    assert seen == ["bad", "good"], "失败项之后的 sweep 必须仍然执行"
    snap = loop_health.snapshot()
    assert snap["sweep:smoke-bad"]["consecutive"] == 1
    assert snap["sweep:smoke-good"]["consecutive"] == 0


def test_every_expected_sweep_is_registered():
    """sweep 注册表的"漏注册"守卫（03-踩坑清单：disk_watchdog 曾实现+测试+发布
    俱全，唯独没注册，告警从此再没触发过）。注册靠 import 副作用，因此这条
    断言必须跑在"app.main 已被 conftest 导入"之后——正是测试会话的形态。

    期望集**只增不减**：新增 sweep 时把它一并写进来，漏写即红。
    """
    names = {n for n, _ in cleanup._sweeps}
    assert names, "注册表为空 = app.main 未被导入，本守卫在空转（必须显式失败）"
    missing = EXPECTED_SWEEPS - names
    assert not missing, f"这些清理项没注册，永远不会被调度: {sorted(missing)}"
