"""健康日报（app/api/healthreport.py）测试：
采集/格式化纯函数 + 管理端配置与手动触发端点。不触网（探活打桩）。"""
import time

import pytest

import app.api.healthreport as hr


@pytest.fixture()
def fake_backup(tmp_path, monkeypatch):
    """把备份产物目录指到临时路径，造一个"2 小时前"的新鲜 dump。"""
    d = tmp_path / "backup" / "db"
    d.mkdir(parents=True)
    dump = d / "lanshare-20990101.dump"
    dump.write_bytes(b"PGDMP")
    import os
    old = os.utime
    past = time.time() - 2 * 3600
    os.utime(dump, (past, past))
    monkeypatch.setattr(hr, "BACKUP_DEST", tmp_path / "backup")
    return dump


def test_gather_no_backup(client, monkeypatch, tmp_path):
    monkeypatch.setattr(hr, "BACKUP_DEST", tmp_path / "nothing")
    d = hr.gather_health(probe_public=False)
    assert d["backup"]["present"] is False
    assert d["public_ok"] is None          # 未要求探活就不出网
    assert d["db_ok"] is True
    assert 0 <= d["disk_used_pct"] <= 100
    assert d["counts"]["users"] >= 1       # conftest setup 已建 admin


def test_gather_fresh_backup(fake_backup):
    d = hr.gather_health()
    assert d["backup"]["present"] is True
    assert d["backup"]["dump_name"] == "lanshare-20990101.dump"
    assert d["backup"]["dump_age_hours"] < 3


def test_format_fresh_vs_stale(fake_backup):
    d = hr.gather_health()
    body, warn = hr.format_report(d)
    assert "备份" in body and "成员" in body
    assert warn is False

    d["backup"]["dump_age_hours"] = 49     # 模拟隔天没跑：超 26h
    body, warn = hr.format_report(d)
    assert "⚠️" in body and warn is True


def test_format_missing_backup_warns(monkeypatch, tmp_path):
    monkeypatch.setattr(hr, "BACKUP_DEST", tmp_path / "nothing")
    body, warn = hr.format_report(hr.gather_health())
    assert "未发现备份产物" in body and warn is True


def test_config_roundtrip(auth, monkeypatch):
    monkeypatch.setattr(hr, "_probe_public", lambda hp: True)
    r = auth.get("/api/admin/healthreport")
    assert r.status_code == 200
    assert r.json()["enabled"] is True          # 默认开

    r = auth.post("/api/admin/healthreport", json={"enabled": False, "time": "8:5"})
    assert r.status_code == 200 and r.json()["time"] == "08:05"
    r = auth.get("/api/admin/healthreport")
    assert r.json()["enabled"] is False and r.json()["time"] == "08:05"

    r = auth.post("/api/admin/healthreport", json={"enabled": True, "time": "25:00"})
    assert r.status_code == 400                 # 非法时间
    r = auth.post("/api/admin/healthreport", json={"enabled": True, "time": "九点"})
    assert r.status_code == 400


def test_test_endpoint(auth, monkeypatch):
    monkeypatch.setattr(hr, "_probe_public", lambda hp: True)
    r = auth.post("/api/admin/healthreport/test")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["sent"] is None                    # 没配 Webhook：只回显不发送
    assert "数据盘" in j["report"]
    # 非管理员不可见
    anon = auth.__class__(server := __import__("server").app, base_url="https://testserver")
    anon.cookies.clear()
    assert anon.post("/api/admin/healthreport/test").status_code in (401, 403)
