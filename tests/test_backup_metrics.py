"""备份编排与指标水位告警（B5）回归。

覆盖计划要求的两条核心语义：调度触发（到点补跑 + 当日幂等）、告警冷却
（阈值触发一次、冷却期内不重复）；另含 dump 落盘/保留策略/失败告警/
指标快照统计。全部走进程内备份兜底（BACKUP_DIR 重定向到 tmp，不碰生产
C:/lan-share-backup 的外部计划任务产物）。
"""
import json
import time

from conftest import MEMBER_PASS

from app.config import BACKUP_DIR
from app.services import backup as backup_svc
from app.services import metrics as metrics_svc
from app.settings import get_setting, set_setting


def _fresh_env(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_svc, "BACKUP_DIR", tmp_path / "b")


def test_backup_run_once_writes_dump_and_status(tmp_path, monkeypatch):
    _fresh_env(tmp_path, monkeypatch)
    alerts: list[str] = []
    st = backup_svc.run_backup_once(push=lambda t, b: alerts.append(t))
    assert st["ok"] is True, st
    assert len(st["files"]) == 2              # dump + settings 快照
    assert all((f.startswith(str(tmp_path / "b"))) for f in st["files"])
    # 状态入 settings（healthreport / /api/metrics 的呈现源）
    last = backup_svc.last_status()
    assert last and last["ok"] is True
    assert get_setting(backup_svc.LAST_DATE_KEY) == time.strftime("%Y-%m-%d")
    assert alerts == []                       # 成功不告警


def test_backup_scheduled_tick_runs_once_per_day(tmp_path, monkeypatch):
    _fresh_env(tmp_path, monkeypatch)
    set_setting(backup_svc.LAST_DATE_KEY, "")   # 清当日幂等位
    set_setting(backup_svc.STATUS_KEY, "")      # 清前一测试留下的状态（用例隔离）
    # 未到 BACKUP_HOUR：不触发
    import app.services.backup as b
    real_localtime = time.localtime
    monkeypatch.setattr(time, "localtime",
                        lambda: time.struct_time((2026, 9, 12, b.BACKUP_HOUR - 1, 0, 0, 5, 255, 0)))
    backup_svc._tick()
    assert backup_svc.last_status() is None
    # 到点：触发并写幂等位
    monkeypatch.setattr(time, "localtime",
                        lambda: time.struct_time((2026, 9, 12, b.BACKUP_HOUR + 1, 0, 0, 5, 255, 0)))
    backup_svc._tick()
    assert backup_svc.last_status() and backup_svc.last_status()["ok"] is True
    assert get_setting(backup_svc.LAST_DATE_KEY) == time.strftime("%Y-%m-%d", real_localtime())
    # 当日再 tick：不重复备份（状态 ts 不变）
    ts1 = backup_svc.last_status()["ts"]
    backup_svc._tick()
    assert backup_svc.last_status()["ts"] == ts1


def test_backup_failure_alerts_webhook(tmp_path, monkeypatch):
    _fresh_env(tmp_path, monkeypatch)
    alerts: list[str] = []
    monkeypatch.setattr(backup_svc, "_dump_to", lambda dst: backup_svc._NO_DUMP)
    st = backup_svc.run_backup_once(push=lambda t, b: alerts.append(t))
    assert st["ok"] is False
    assert alerts == ["备份失败"]             # 失败必须上门提醒


def test_backup_prune_keeps_pairs_balanced(tmp_path, monkeypatch):
    """终审 P1-1 守卫：淘汰必须按组成对——不允许 dump 全灭而快照独存
    （原按文件名排序时 meta-* 恒前于 settings-*，切片先删光 dump）。"""
    _fresh_env(tmp_path, monkeypatch)
    d = tmp_path / "b"
    d.mkdir(parents=True)
    for i in range(1, 11):
        stamp = f"202609{i:02d}-120000"   # 8 位日期（YYYYMMDD），与正则一致
        (d / f"meta-{stamp}.db").write_text("db")
        (d / f"settings-{stamp}.json").write_text("{}")
    backup_svc._prune_old()
    left = sorted(f.name for f in d.iterdir())
    dbs = [f for f in left if f.startswith("meta-")]
    js = [f for f in left if f.startswith("settings-")]
    assert len(dbs) == len(js) == backup_svc.BACKUP_KEEP   # 成对且足量
    assert dbs == sorted(dbs)[-backup_svc.BACKUP_KEEP:]    # 留的是最新组


def test_backup_prune_keeps_recent(tmp_path, monkeypatch):
    _fresh_env(tmp_path, monkeypatch)
    st = backup_svc.run_backup_once(push=lambda *a: None)
    # 伪造 20 份旧产物（10 组）再跑一轮：应只留最近 7 组（14 件）+ 本轮 2 件
    for i in range(10):
        (tmp_path / "b" / f"meta-2026010{i % 10}-00000{i}.db").write_text("x")
        (tmp_path / "b" / f"settings-2026010{i % 10}-00000{i}.json").write_text("{}")
    backup_svc._prune_old()
    left = list((tmp_path / "b").iterdir())
    assert len(left) <= backup_svc.BACKUP_KEEP * 2
    assert st["ok"]


def test_metrics_snapshot_counts_and_latency():
    metrics_svc._window.clear()
    for code, ms in ((200, 10), (200, 30), (404, 5), (500, 100)):
        metrics_svc.record(code, ms)
    snap = metrics_svc.snapshot(window_seconds=3600)
    assert snap["total"] == 4
    assert snap["by_status"] == {"2xx": 2, "4xx": 1, "5xx": 1}
    assert snap["latency_ms"]["p50"] == 30.0
    assert snap["latency_ms"]["p95"] == 100.0
    assert snap["latency_ms"]["avg"] == 36.2   # round(x,1) 银行家舍入 36.25→36.2


def test_disk_watchdog_cooldown(tmp_path, monkeypatch):
    """超阈值触发一次；冷却期内不重复；低于阈值静默。"""
    monkeypatch.setattr(metrics_svc, "disk_used_pct", lambda: 91.0)
    monkeypatch.setattr(metrics_svc, "ALERT_COOLDOWN", 3600)
    set_setting(metrics_svc.ALERT_TS_KEY, "0")
    fired: list[str] = []
    assert metrics_svc.disk_watchdog(push=lambda t, b: fired.append(t)) is True
    assert len(fired) == 1
    # 冷却期内再超阈值：静默
    assert metrics_svc.disk_watchdog(push=lambda t, b: fired.append(t)) is False
    assert len(fired) == 1
    # 冷却过后：再发
    set_setting(metrics_svc.ALERT_TS_KEY, str(time.time() - 7200))
    assert metrics_svc.disk_watchdog(push=lambda t, b: fired.append(t)) is True
    assert len(fired) == 2
    # 水位回落：不触发
    monkeypatch.setattr(metrics_svc, "disk_used_pct", lambda: 50.0)
    assert metrics_svc.disk_watchdog(push=lambda t, b: fired.append(t)) is False
    assert len(fired) == 2


def test_metrics_endpoint_requires_admin(client):
    """/api/metrics 只读快照：member 访问 403（admin 专属）。"""
    from test_extras import _mk_member, _member_session, _rand
    username = "mx" + _rand()
    _mk_member(client, username, MEMBER_PASS)
    m = _member_session(client, username, MEMBER_PASS)
    assert m.get("/api/metrics").status_code == 403
    # 匿名 401
    assert client.get("/api/metrics").status_code == 401
