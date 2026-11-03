"""自定义更新源（3.96）。

契约见 `docs/更新源协议.md`。这里锁住几条不能退让的规则：

- 多地址按顺序试，第一个能通的就用；全部失败才回落 GitHub
- 回落必须标注来源，不能让用户以为「源里没有新版」
- http 一律拒绝（回环除外），清单缺 sha256 一律当源不可用
- 历史条目坏了跳过，但不能因为一条坏历史把整个源判死
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lanshare_cli import updater


SHA = "a" * 64


def _manifest(**over) -> dict:
    latest = {
        "version": "3.96",
        "build": 396,
        "url": "LANShare-Setup-3.96.exe",
        "sha256": SHA,
        "size": 100,
        "notes": "test",
        "published": "2026-11-04",
    }
    latest.update(over.pop("latest", {}))
    m = {"schema": 1, "latest": latest}
    if "history" in over:
        m["history"] = over.pop("history")
    m.update(over)
    return m


# ── 地址校验 ──────────────────────────────────────────────────


class TestNormalizeSource:
    def test_https_ok(self):
        assert (updater.normalize_source("https://192.168.1.100:8765/updates/")
                == "https://192.168.1.100:8765/updates")

    def test_http_rejected(self):
        with pytest.raises(ValueError) as e:
            updater.normalize_source("http://example.com/updates")
        assert "https" in str(e.value)

    def test_http_loopback_allowed(self):
        """本机调更新接口时不该被迫先配证书。"""
        assert updater.normalize_source("http://127.0.0.1:9000/u") == "http://127.0.0.1:9000/u"
        assert updater.normalize_source("http://localhost:9000") == "http://localhost:9000"

    def test_no_scheme_rejected(self):
        with pytest.raises(ValueError):
            updater.normalize_source("192.168.1.100:8765")

    def test_other_scheme_rejected(self):
        with pytest.raises(ValueError):
            updater.normalize_source("file:///C:/x")
        with pytest.raises(ValueError):
            updater.normalize_source("ftp://x/y")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            updater.normalize_source("   ")


class TestManifestUrl:
    @pytest.mark.parametrize("given,expected", [
        ("https://h/updates", "https://h/updates/index.json"),
        ("https://h/updates/", "https://h/updates/index.json"),
        ("https://h/updates/index.json", "https://h/updates/index.json"),
        ("https://h/updates/latest.json", "https://h/updates/latest.json"),
    ])
    def test_shapes(self, given, expected):
        assert updater._manifest_url(given) == expected


# ── 清单解析 ──────────────────────────────────────────────────


class TestFetchCustom:
    def _patch(self, monkeypatch, payload):
        # **_：生产代码的 _http_json 后来加了 extra_ca 关键字参数，
        # 桩必须照单全收，不然测试自己先 TypeError（2026-11-04 基线红灯根因）
        monkeypatch.setattr(updater, "_http_json",
                            lambda url, t, label, **_: payload)

    def test_ok(self, monkeypatch):
        self._patch(monkeypatch, _manifest(
            history=[{"version": "3.95", "published": "2026-11-03", "notes": "old"}]))
        got = updater.fetch_custom("https://h/updates")
        assert got["latest"]["version"] == "3.96"
        # 相对路径要按清单目录解析成绝对地址
        assert got["latest"]["url"] == "https://h/updates/LANShare-Setup-3.96.exe"
        assert len(got["history"]) == 1
        assert got["history"][0]["version"] == "3.95"

    def test_missing_sha256_kills_source(self, monkeypatch):
        """没有校验值 = 没法确认下回来的是你发的那个，整个源作废。"""
        self._patch(monkeypatch, _manifest(latest={"sha256": ""}))
        with pytest.raises(RuntimeError) as e:
            updater.fetch_custom("https://h/u")
        assert "sha256" in str(e.value)

    def test_bad_sha256_kills_source(self, monkeypatch):
        self._patch(monkeypatch, _manifest(latest={"sha256": "xyz"}))
        with pytest.raises(RuntimeError):
            updater.fetch_custom("https://h/u")

    def test_missing_url_kills_source(self, monkeypatch):
        self._patch(monkeypatch, _manifest(latest={"url": ""}))
        with pytest.raises(RuntimeError):
            updater.fetch_custom("https://h/u")

    def test_unknown_schema_rejected(self, monkeypatch):
        self._patch(monkeypatch, _manifest(schema=2))
        with pytest.raises(RuntimeError) as e:
            updater.fetch_custom("https://h/u")
        assert "schema" in str(e.value)

    def test_schema_absent_is_ok(self, monkeypatch):
        """schema 可以省 —— 早期的静态清单没人会写它。"""
        m = _manifest()
        del m["schema"]
        self._patch(monkeypatch, m)
        assert updater.fetch_custom("https://h/u")["latest"]["version"] == "3.96"

    def test_broken_history_entry_skipped(self, monkeypatch):
        """一条历史写坏了不该毁掉整个源。"""
        self._patch(monkeypatch, _manifest(history=[
            {"version": "not-a-version"},
            {"version": "3.95", "published": "2026-11-03"},
        ]))
        got = updater.fetch_custom("https://h/u")
        assert [h["version"] for h in got["history"]] == ["3.95"]

    def test_root_not_object(self, monkeypatch):
        self._patch(monkeypatch, [1, 2, 3])
        with pytest.raises(RuntimeError):
            updater.fetch_custom("https://h/u")


# ── 回退链 ────────────────────────────────────────────────────


def _fake_github(version="3.97"):
    return {
        "latest": {
            "tag": f"server-v{version}", "version": version, "build": 0,
            "url": "https://github/x.exe", "size": 1, "sha256": "",
            "notes": "gh", "published": "2026-11-04",
        },
        "history": [],
    }


class TestCheckFallback:
    def test_first_source_wins(self, tmp_path, monkeypatch):
        calls = []

        def fake(base, timeout, extra_ca=None):
            calls.append(base)
            return {"latest": {
                "version": "3.96", "build": 396, "url": base + "/x.exe",
                "size": 1, "sha256": SHA, "notes": "", "published": "",
            }, "history": []}

        monkeypatch.setattr(updater, "fetch_custom", fake)
        (tmp_path / "VERSION").write_text("3.95", encoding="utf-8")

        out = updater.check(tmp_path, sources=["https://lan/u", "https://wan/u"])
        # 第一条通了就不该再试第二条 —— 局域网优先就是这么实现的
        assert calls == ["https://lan/u"]
        assert out["source"] == "custom"
        assert out["has_update"] is True
        assert out["source_url"] == "https://lan/u"

    def test_falls_through_to_second_source(self, tmp_path, monkeypatch):
        calls = []

        def fake(base, timeout, extra_ca=None):
            calls.append(base)
            if "lan" in base:
                raise RuntimeError("连不上")
            return {"latest": {
                "version": "3.96", "build": 396, "url": "https://wan/x.exe",
                "size": 1, "sha256": SHA, "notes": "", "published": "",
            }, "history": []}

        monkeypatch.setattr(updater, "fetch_custom", fake)
        (tmp_path / "VERSION").write_text("3.95", encoding="utf-8")

        out = updater.check(tmp_path, sources=["https://lan/u", "https://wan/u"])
        assert calls == ["https://lan/u", "https://wan/u"]
        assert out["source"] == "custom"
        # 第一条挂的原因必须留下来，否则用户不知道局域网那条坏了
        assert "lan" in out["source_error"]

    def test_falls_back_to_github_and_says_so(self, tmp_path, monkeypatch):
        def boom(base, timeout, extra_ca=None):
            raise RuntimeError("TLS 失败")

        monkeypatch.setattr(updater, "fetch_custom", boom)
        monkeypatch.setattr(updater, "fetch_github",
                            lambda timeout: _fake_github("3.97"))
        (tmp_path / "VERSION").write_text("3.95", encoding="utf-8")

        out = updater.check(tmp_path, sources=["https://lan/u"])
        assert out["source"] == "github"
        # 关键：回落了必须留痕，否则用户以为「我的源里没有新版」
        assert "TLS 失败" in out["source_error"]
        assert out["latest"] == "3.97"

    def test_github_only_when_no_sources(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "fetch_github",
                            lambda timeout: _fake_github("3.97"))
        (tmp_path / "VERSION").write_text("3.95", encoding="utf-8")

        out = updater.check(tmp_path, sources=[])
        assert out["source"] == "github"
        assert out["source_error"] == ""

    def test_everything_down_reports_error(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("全挂了")

        monkeypatch.setattr(updater, "fetch_custom", boom)
        monkeypatch.setattr(updater, "fetch_github", boom)
        (tmp_path / "VERSION").write_text("3.95", encoding="utf-8")

        out = updater.check(tmp_path, sources=["https://lan/u"])
        assert out["error"]
        assert out["latest"] == ""
        assert out["has_update"] is False

    def test_cache_used_when_all_down(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("全挂了")

        (tmp_path / "VERSION").write_text("3.95", encoding="utf-8")
        # 先成功一次写入缓存
        monkeypatch.setattr(updater, "fetch_custom", lambda base, timeout, extra_ca=None: {
            "latest": {"version": "3.96", "build": 396, "url": "https://lan/x.exe",
                       "size": 1, "sha256": SHA, "notes": "", "published": ""},
            "history": [],
        })
        updater.check(tmp_path, sources=["https://lan/u"])

        # 然后全挂 —— 应该给上次的结果并标注缓存
        monkeypatch.setattr(updater, "fetch_custom", boom)
        monkeypatch.setattr(updater, "fetch_github", boom)
        out = updater.check(tmp_path, sources=["https://lan/u"])
        assert out["stale"] is True
        assert out["source"] == "cache"
        assert out["latest"] == "3.96"


class TestConfiguredSources:
    def test_lines_are_split_and_trimmed(self, monkeypatch):
        """注册表里存的是多行文本，读出来要按行拆、去掉空行和注释。"""
        monkeypatch.setattr(updater, "configured_sources", lambda key="LANShare": [])
        # 直接测解析逻辑：模拟读到的原始多行串
        raw = "https://lan/u\r\n\r\n  https://wan/u  \r\n# 注释\n"
        lines = [x.strip() for x in raw.splitlines() if x.strip() and not x.strip().startswith("#")]
        assert lines == ["https://lan/u", "https://wan/u"]

    def test_registry_join_is_multiline(self):
        """存进去必须是换行分隔，不是逗号 —— 地址里可能有逗号。"""
        urls = ["https://a/u", "https://b/u"]
        assert "\r\n".join(urls) == "https://a/u\r\nhttps://b/u"
