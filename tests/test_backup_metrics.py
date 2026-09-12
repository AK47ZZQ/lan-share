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
    # 日期钉死：backup 编排经 backup_svc._today() 取日期（值守批 C3 收口）——
    # 直呼 strftime 时"写幂等位"与断言读取可能分踞午夜两侧，偶发错位失败
    monkeypatch.setattr(backup_svc, "_today", lambda: "2026-09-12")
    # PG 引擎下 dump 依赖 pg_dump 在 PATH（CI/本机不一定有）——本组测试的
    # 被测对象是**编排逻辑**（状态/幂等/保留/告警），引擎桩化解耦；
    # 引擎不可用的告警路径另有专项用例（_NO_DUMP 桩）
    if backup_svc.USE_PG:
        def _fake_engine(tmp):
            tmp.write_bytes(b"PGDMP")
            return tmp
        monkeypatch.setattr(backup_svc, "_dump_engine", _fake_engine)
        # 桩 dump（b"PGDMP"）过不了真 pg_restore -l——本组测试的被测对象是
        # 编排逻辑，验证一并用桩钉死；验证逻辑本身的真实用例见下方
        # test_backup_verify_*（统一强制 SQLite 引擎，双引擎跑法下行为一致）
        monkeypatch.setattr(backup_svc, "_verify_dump", lambda p: (True, "stub"))


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
    assert get_setting(backup_svc.LAST_DATE_KEY) == "2026-09-12"
    assert alerts == []                       # 成功不告警


def test_backup_scheduled_tick_runs_once_per_day(tmp_path, monkeypatch):
    _fresh_env(tmp_path, monkeypatch)
    set_setting(backup_svc.LAST_DATE_KEY, "")   # 清当日幂等位
    set_setting(backup_svc.STATUS_KEY, "")      # 清前一测试留下的状态（用例隔离）
    # 未到 BACKUP_HOUR：不触发
    import app.services.backup as b
    monkeypatch.setattr(time, "localtime",
                        lambda: time.struct_time((2026, 9, 12, b.BACKUP_HOUR - 1, 0, 0, 5, 255, 0)))
    backup_svc._tick()
    assert backup_svc.last_status() is None
    # 到点：触发并写幂等位
    monkeypatch.setattr(time, "localtime",
                        lambda: time.struct_time((2026, 9, 12, b.BACKUP_HOUR + 1, 0, 0, 5, 255, 0)))
    backup_svc._tick()
    assert backup_svc.last_status() and backup_svc.last_status()["ok"] is True
    assert get_setting(backup_svc.LAST_DATE_KEY) == "2026-09-12"
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


# ── restore drill 内联验证（2026-09-12 功能批：B5 dump 从未验证的缺口）──
#
# 统一强制 SQLite 引擎（monkeypatch backup_svc.USE_PG）：验证语义与引擎
# 无关的部分（编排串接/失败告警/幂等位）在这里钉死，SQLite 路径可真实
# 走 integrity_check，双引擎跑法下行为一致。消融证据：删掉 run_backup_once
# 里的验证调用，test_backup_corrupt_dump_* 立即转红（坏 dump 会记 ok=True）。


def test_backup_verify_passes_on_real_sqlite_dump(tmp_path, monkeypatch):
    if backup_svc.USE_PG:
        # PG 跑法的测试环境没有 SQLite 实体库（真引擎 dump 无从谈起）；
        # _verify_dump 真实逻辑由下方 corrupt/empty 用例在该跑法下照样覆盖
        import pytest
        pytest.skip("PG 跑法无 SQLite 实体库")
    _fresh_env(tmp_path, monkeypatch)
    monkeypatch.setattr(backup_svc, "USE_PG", False)
    monkeypatch.setattr(backup_svc, "_verify_dump", _REAL_VERIFY)   # 脱桩（PG 跑法下 _fresh_env 会桩化）
    monkeypatch.setattr(backup_svc, "_dump_engine", _REAL_DUMP_ENGINE)  # 真 SQLite 引擎产真 dump
    st = backup_svc.run_backup_once(push=lambda *a: None)
    assert st["ok"] is True
    assert st["verified"] is True
    assert "integrity ok" in st["verify_detail"]
    assert st["verify_detail"].endswith("）")  # settings 条数也在 detail 里


def test_backup_verify_catches_corrupt_dump(tmp_path, monkeypatch):
    """截断/非库文件 → 验证失败：ok=False、webhook 上门、幂等位不写（次日重试）。"""
    _fresh_env(tmp_path, monkeypatch)
    monkeypatch.setattr(backup_svc, "USE_PG", False)
    monkeypatch.setattr(backup_svc, "_verify_dump", _REAL_VERIFY)   # 脱桩（同上）

    def _corrupt_engine(tmp):
        tmp.write_bytes(b"SQLite format 3\x00truncated garbage")
        return tmp
    monkeypatch.setattr(backup_svc, "_dump_engine", _corrupt_engine)

    set_setting(backup_svc.LAST_DATE_KEY, "")
    alerts: list[str] = []
    st = backup_svc.run_backup_once(push=lambda t, b: alerts.append(t))
    assert st["ok"] is False
    assert "备份验证失败" in st["detail"]
    assert alerts == ["备份失败"]
    assert get_setting(backup_svc.LAST_DATE_KEY) != "2026-09-12"   # 明天会重试
    # 坏产物不留"备份"之名（2026-09-13 审查修复）：验证失败即删，
    # 不占 keep 组、不占盘、不被误当真
    assert list(backup_svc.BACKUP_DIR.glob("meta-*.db")) == []


def test_backup_verify_catches_empty_dump(tmp_path, monkeypatch):
    """0 字节文件是 SQLite 眼里的合法空库（9-11 外部任务被杀的同构事故）——
    integrity_check 会说 ok，settings 表缺失兜住它。"""
    _fresh_env(tmp_path, monkeypatch)
    monkeypatch.setattr(backup_svc, "USE_PG", False)
    monkeypatch.setattr(backup_svc, "_verify_dump", _REAL_VERIFY)   # 脱桩（同上）
    monkeypatch.setattr(backup_svc, "_dump_engine", lambda tmp: (tmp.write_bytes(b""), tmp)[1])
    alerts: list[str] = []
    st = backup_svc.run_backup_once(push=lambda t, b: alerts.append(t))
    assert st["ok"] is False
    assert alerts == ["备份失败"]
    assert list(backup_svc.BACKUP_DIR.glob("meta-*.db")) == []   # 同上：坏产物即删


# _fresh_env 会把 _verify_dump 桩化（PG 跑法下）——工具缺失用例需要还原真实
# 实现才能覆盖真实代码路径，模块导入时存一份原件
_REAL_VERIFY = backup_svc._verify_dump
_REAL_DUMP_ENGINE = backup_svc._dump_engine


def test_backup_verify_skipped_when_tool_missing(tmp_path, monkeypatch):
    """PG 引擎下 pg_restore 不可达（PATH 与回退目录均无）：
    诚实呈现 verified=None，不假装通过也不误报。"""
    _fresh_env(tmp_path, monkeypatch)
    monkeypatch.setattr(backup_svc, "_verify_dump", _REAL_VERIFY)
    monkeypatch.setattr(backup_svc, "PG_BIN_FALLBACK", str(tmp_path / "no-such-bin"))

    def _no_restore(name):
        return None if name == "pg_restore" else "/fake/bin/pg_dump"
    monkeypatch.setattr(backup_svc, "USE_PG", True)
    monkeypatch.setattr("shutil.which", _no_restore)
    monkeypatch.setattr(backup_svc, "_dump_engine", lambda tmp: (tmp.write_bytes(b"PGDMP-fake"), tmp)[1])

    alerts: list[str] = []
    st = backup_svc.run_backup_once(push=lambda t, b: alerts.append(t))
    assert st["ok"] is True                   # 备份本身成功
    assert st["verified"] is None             # 但验证状态诚实标注
    assert "pg_restore" in st["verify_detail"]
    assert alerts == []
    assert get_setting(backup_svc.LAST_DATE_KEY) == "2026-09-12"


# ── PG 工具解析回退（2026-09-12：服务 PATH 无 pgsql bin 实录后的根修）──


def test_resolve_tool_falls_back_to_deploy_dir(tmp_path, monkeypatch):
    """PATH 找不到 → 回退 PG_BIN_FALLBACK 目录里的 <name>.exe。"""
    bin_dir = tmp_path / "pgbin"
    bin_dir.mkdir()
    (bin_dir / "pg_dump.exe").write_bytes(b"fake")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(backup_svc, "PG_BIN_FALLBACK", str(bin_dir))
    got = backup_svc._resolve_tool("pg_dump")
    assert got is not None and got.endswith("pg_dump.exe")


def test_resolve_tool_none_when_neither_available(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(backup_svc, "PG_BIN_FALLBACK", str(tmp_path / "no-such-bin"))
    assert backup_svc._resolve_tool("pg_dump") is None


def test_resolve_tool_prefers_path(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/pg_dump")
    assert backup_svc._resolve_tool("pg_dump") == "/usr/bin/pg_dump"


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


# ── 2026-09-13 夜间审查批：备份链加固四项 ──────────────────────
#
# 1) 引擎中途抛错（pg_dump 超时/崩溃）不留 .tmp 残留；
# 2) pg_dump 口令只进子进程环境（PGPASSWORD），不出现在命令行
#    （Windows 任意进程可读别家命令行；与 backup.bat PGPASSFILE 纪律对齐）；
# 3) SQLite 验证 URI 百分号编码（SHARE_ROOT 含 %/#/? 不再误报）；
# 4) PG 验证核对 settings 表在档（与 SQLite 侧对称，空档期归档不再蒙混过关）。


def test_dump_engine_error_cleans_tmp(tmp_path, monkeypatch):
    def _boom(tmp):
        raise RuntimeError("pg_dump timed out")
    monkeypatch.setattr(backup_svc, "_dump_engine", _boom)
    dst = tmp_path / "b" / "meta-x.db"
    dst.parent.mkdir(parents=True)
    try:
        backup_svc._dump_to(dst)
        raised = False
    except RuntimeError:
        raised = True
    assert raised
    assert list(dst.parent.iterdir()) == []     # .tmp 已随异常清掉


def test_pg_dump_cmd_splits_url_and_hides_password():
    url = ("postgresql://lanshare:p%40ss%2Fw0rd@127.0.0.1:5433/lanshare"
           "?options=-csearch_path%3Dpublic")
    argv, env = backup_svc._pg_dump_cmd(url)
    assert argv == ["-h", "127.0.0.1", "-U", "lanshare", "-p", "5433",
                    "-d", "lanshare"]
    assert env == {"PGPASSWORD": "p@ss/w0rd"}   # URL 编码口令被还原
    assert "p@ss" not in " ".join(argv)         # 口令绝不上命令行


def test_pg_dump_cmd_tolerates_partial_url():
    argv, env = backup_svc._pg_dump_cmd("postgresql://u@dbhost/mydb")
    assert argv == ["-h", "dbhost", "-U", "u", "-d", "mydb"]
    assert env == {}                            # 无口令就不设 PGPASSWORD


def test_dump_engine_pg_hides_password_from_argv(tmp_path, monkeypatch):
    import pathlib
    import subprocess as _sp
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        for i, a in enumerate(cmd):
            if a == "-f":
                captured["dst"] = cmd[i + 1]
                break
        dst_tmp = pathlib.Path(captured["dst"])
        dst_tmp.parent.mkdir(parents=True, exist_ok=True)
        dst_tmp.write_bytes(b"PGDMP-fake")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(backup_svc, "USE_PG", True)
    monkeypatch.setattr(backup_svc, "DATABASE_URL",
                        "postgresql://lanshare:SECRET@127.0.0.1:5433/lanshare")
    monkeypatch.setattr(backup_svc, "_resolve_tool", lambda name: "/fake/pg_dump")
    monkeypatch.setattr(_sp, "run", _fake_run)
    dst = backup_svc._dump_to(tmp_path / "b" / "meta-y.db")
    assert dst is not None and dst.is_file()
    assert "SECRET" not in " ".join(captured["cmd"])          # 口令不上 argv
    assert captured["kwargs"]["env"]["PGPASSWORD"] == "SECRET"  # 只进子进程环境
    assert "-Fc" in captured["cmd"] and captured["dst"].endswith(".tmp")


def test_verify_sqlite_uri_survives_url_special_chars(tmp_path, monkeypatch):
    """SHARE_ROOT 含 %/# 时验证不该被 URI 语法截断而误报失败。"""
    import sqlite3 as _sq
    d = tmp_path / "b#dir%2F"
    d.mkdir(parents=True)
    good = d / "meta-ok.db"
    con = _sq.connect(str(good))
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO settings VALUES ('k', 'v')")
    con.commit()
    con.close()
    monkeypatch.setattr(backup_svc, "USE_PG", False)
    ok, detail = backup_svc._verify_dump(good)
    assert ok is True, detail


_TOC_WITH_SETTINGS = (
    ";\n; Archive created at ...:\n;     dbname: lanshare\n;\n"
    "2147483647; 1255 16386 TABLE - public settings postgres\n"
    "2147483649; 1259 16390 TABLE DATA - public settings postgres\n"
)
_TOC_WITHOUT_SETTINGS = (
    ";\n; Archive created at ...:\n;\n"
    "2147483649; 1259 16390 TABLE DATA - public users postgres\n"
)


def test_verify_pg_rejects_toc_without_settings(tmp_path, monkeypatch):
    """PG 验证对称性：TOC 可读但缺 settings 表 → 判坏（空档期/半截归档兜底）。"""
    import subprocess as _sp
    monkeypatch.setattr(backup_svc, "USE_PG", True)
    monkeypatch.setattr(backup_svc, "_resolve_tool",
                        lambda name: "/fake/pg_restore" if name == "pg_restore" else None)
    dump = tmp_path / "meta-z.db"
    dump.write_bytes(b"PGDMP")

    def _run(cmd, **kwargs):
        return _sp.CompletedProcess(cmd, 0, stdout=_TOC_WITHOUT_SETTINGS, stderr="")

    monkeypatch.setattr(_sp, "run", _run)
    ok, detail = backup_svc._verify_dump(dump)
    assert ok is False
    assert "settings" in detail


def test_verify_pg_accepts_toc_with_settings(tmp_path, monkeypatch):
    import subprocess as _sp
    monkeypatch.setattr(backup_svc, "USE_PG", True)
    monkeypatch.setattr(backup_svc, "_resolve_tool",
                        lambda name: "/fake/pg_restore" if name == "pg_restore" else None)
    dump = tmp_path / "meta-z.db"
    dump.write_bytes(b"PGDMP")

    def _run(cmd, **kwargs):
        return _sp.CompletedProcess(cmd, 0, stdout=_TOC_WITH_SETTINGS, stderr="")

    monkeypatch.setattr(_sp, "run", _run)
    ok, detail = backup_svc._verify_dump(dump)
    assert ok is True, detail
    assert "settings" in detail
