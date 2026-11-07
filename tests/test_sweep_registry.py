"""后台循环/扫尾注册守卫(disk_watchdog 首轮漏注册的教训门禁化)。

历史缺陷:新增 sweep 忘了 register_sweep → 告警永不触发,只有手工
`python -c` 打印注册表才能发现(AGENTS §6「门禁外的隐蔽要求」)。本守卫
把这条纪律变成测试:

  1. 静态:app/ 下含 `while True` 的模块必须在「接了 loop_health」或
     「请求域白名单」二选一 —— 新写的裸后台循环立刻红;白名单新增也要
     显式过目(请求域循环登记健康没有意义);
  2. 扫尾注册表钉死当前 9 个注册名 —— 新增/删除 sweep 不改这里就红
     (强迫开发者显式过目注册表,而不是靠记性);
  3. 名唯一;
  4. run_registered() 真跑一轮,每个注册名都有 sweep:{name} 健康登记
     (登记即"看得见",吞异常保活必须配看得见)。
"""
from pathlib import Path

from app.services import cleanup, loop_health

ROOT = Path(__file__).resolve().parent.parent

# 历史教训钉名单:disk_watchdog 的载体(metrics._tick = disk_watermark)、
# 日常备份、上传会话、zip 票据、待办到期、番茄结算、日志归档、限速落盘、
# 回收站孤儿审计
_EXPECTED_SWEEPS = {
    "disk_watermark", "daily_backup", "upload_sessions", "zip_tickets",
    "todo_due", "pomodoro_settle", "log_archives", "metrics_persist",
    "trash_orphan_audit",
}

# 请求域循环白名单:这些 `while True` 是单请求作用域(连接/流迭代),
# 不是后台长命循环,不该进 loop_health(按连接登记健康无意义)。
# 新模块出现裸循环时:后台循环 → 接 loop_health;请求域 → 加进这里并写明理由。
_REQUEST_SCOPED = {
    "app/api/appver.py",   # 更新源轮询重试(单请求内)
    "app/api/events.py",   # SSE 每连接事件流
    "app/api/trash.py",    # 回收站批量迭代(单请求内)
    "app/api/webdav.py",   # DAV 请求体流读取
}


def test_while_true_modules_use_loop_health():
    """`while True` 模块必须引用 loop_health,或在请求域白名单(文本级扫描)。"""
    offenders = []
    for src in (ROOT / "app").rglob("*.py"):
        rel = src.relative_to(ROOT).as_posix()
        if rel == "app/services/loop_health.py":
            continue  # 本体:docstring 里有字面 "while True" 示例
        if rel in _REQUEST_SCOPED or rel.replace("/", "\\") in _REQUEST_SCOPED:
            continue
        text = src.read_text(encoding="utf-8")
        if "while True" in text and "loop_health" not in text:
            offenders.append(rel)
    assert not offenders, f"这些模块有裸循环但未接 loop_health: {offenders}"


def test_sweep_registry_pinned(client):
    """注册表钉死:当前 9 个注册名一一对应,新增/删除必须显式改本用例。"""
    names = [n for n, _ in cleanup._sweeps]
    assert set(names) == _EXPECTED_SWEEPS, (
        f"扫尾注册表漂移: 多={set(names) - _EXPECTED_SWEEPS} "
        f"少={_EXPECTED_SWEEPS - set(names)}")
    assert len(names) == len(set(names)), "注册表重名"


def test_run_registered_reports_health(client):
    """真跑一轮:每个注册名都有 sweep:{name} 登记条目(吞异常必须配看得见)。"""
    loop_health.reset_loops()
    cleanup.run_registered()
    snap = loop_health.snapshot()
    missing = [n for n, _ in cleanup._sweeps if f"sweep:{n}" not in snap]
    assert not missing, f"跑了但没有健康登记: {missing}"
