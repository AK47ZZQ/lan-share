"""服务端检查更新（lanshare_cli/updater.py）。

这一块判断错了用户就收不到更新提示，或者被反复骚扰说「有新版本」，
所以每个分支都要测。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lanshare_cli import updater


class TestCurrentVersion:
    def test_reads_plain_version(self, tmp_path):
        (tmp_path / "VERSION").write_text("3.78\n2026-11-02 10:00:00\n",
                                          encoding="utf-8")
        assert updater.current_version(tmp_path) == "3.78"

    def test_tolerates_bom(self, tmp_path):
        """PS 5.1 写文件默认带 BOM，读回来会变成 \\ufeff3.78。"""
        (tmp_path / "VERSION").write_text("\ufeff3.79\n", encoding="utf-8")
        assert updater.current_version(tmp_path) == "3.79"

    def test_missing_file_returns_empty(self, tmp_path):
        """老安装没有 VERSION —— 返回空串，不编一个数字。"""
        assert updater.current_version(tmp_path) == ""

    def test_garbage_returns_empty(self, tmp_path):
        (tmp_path / "VERSION").write_text("unknown\n", encoding="utf-8")
        assert updater.current_version(tmp_path) == ""

    def test_only_first_line(self, tmp_path):
        (tmp_path / "VERSION").write_text("3.80\n3.81\n", encoding="utf-8")
        assert updater.current_version(tmp_path) == "3.80"


class TestIsNewer:
    def test_basic_upgrade(self):
        assert updater.is_newer("3.78", "3.77")

    def test_same_version(self):
        assert not updater.is_newer("3.77", "3.77")

    def test_older_is_not_newer(self):
        assert not updater.is_newer("3.76", "3.77")

    def test_multi_digit_segment(self):
        """3.9 < 3.10 —— 按字符串比会颠倒，必须按段转整数。"""
        assert updater.is_newer("3.10", "3.9")
        assert not updater.is_newer("3.9", "3.10")

    def test_patch_level(self):
        assert updater.is_newer("3.78.1", "3.78")

    def test_unknown_current_is_not_newer(self):
        """不知道现在是什么版本时，不冒充「有新版本」——
        否则老安装会永远提示更新。"""
        assert not updater.is_newer("3.78", "")

    def test_empty_latest(self):
        assert not updater.is_newer("", "3.77")


def _fake_urlopen(payload, *, raises=None):
    """造一个替代 urllib.request.urlopen 的假函数。"""

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def read(self):
            return json.dumps(self._data).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        if raises is not None:
            raise raises
        return FakeResp(payload)

    return _open


def _rel(tag, assets=None, body="更新说明", date="2026-11-02T10:00:00Z"):
    return {
        "tag_name": tag,
        "body": body,
        "published_at": date,
        "assets": assets if assets is not None else [
            {"name": f"LANShare-Setup-{tag.split('-v')[-1]}.exe",
             "size": 56005272,
             "browser_download_url": f"https://github.com/x/{tag}.exe"}
        ],
    }


class TestFetchLatest:
    def test_picks_server_release(self, monkeypatch):
        payload = [_rel("server-v3.78"), _rel("server-v3.77"), _rel("app-v1.1.21")]
        monkeypatch.setattr(updater.urllib.request, "urlopen",
                            _fake_urlopen(payload))
        info = updater.fetch_latest()
        assert info["tag"] == "server-v3.78"
        assert info["version"] == "3.78"
        assert info["size"] == 56005272

    def test_skips_release_without_exe(self, monkeypatch):
        """服务端 Release 没挂 exe 说明发布断了，跳过继续找下一条。"""
        payload = [
            _rel("server-v3.79", assets=[]),          # 发布失败的那条
            _rel("server-v3.78"),
        ]
        monkeypatch.setattr(updater.urllib.request, "urlopen",
                            _fake_urlopen(payload))
        assert updater.fetch_latest()["version"] == "3.78"

    def test_ignores_app_releases(self, monkeypatch):
        payload = [_rel("app-v9.9.9"), _rel("server-v3.78")]
        monkeypatch.setattr(updater.urllib.request, "urlopen",
                            _fake_urlopen(payload))
        assert updater.fetch_latest()["version"] == "3.78"

    def test_no_server_release_raises(self, monkeypatch):
        monkeypatch.setattr(updater.urllib.request, "urlopen",
                            _fake_urlopen([_rel("app-v1.1.21")]))
        with pytest.raises(RuntimeError, match="没找到"):
            updater.fetch_latest()

    def test_network_error_is_human_readable(self, monkeypatch):
        import urllib.error

        monkeypatch.setattr(
            updater.urllib.request, "urlopen",
            _fake_urlopen(None, raises=urllib.error.URLError("timed out")))
        with pytest.raises(RuntimeError, match="连不上 GitHub"):
            updater.fetch_latest()

    def test_rate_limit_message(self, monkeypatch):
        import urllib.error

        exc = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        monkeypatch.setattr(updater.urllib.request, "urlopen",
                            _fake_urlopen(None, raises=exc))
        with pytest.raises(RuntimeError, match="限流"):
            updater.fetch_latest()


class TestCheck:
    """check() 的三态：有新版本 / 已是最新 / 没查成。

    最后一种最容易写错 —— 把「查失败」当成「已是最新」，
    用户完全无从判断，2026-11-01 在 App 端就是这么栽的。
    """

    def test_has_update(self, tmp_path, monkeypatch):
        (tmp_path / "VERSION").write_text("3.77\n", encoding="utf-8")
        monkeypatch.setattr(updater.urllib.request, "urlopen",
                            _fake_urlopen([_rel("server-v3.78")]))
        r = updater.check(tmp_path)
        assert r["current"] == "3.77"
        assert r["latest"] == "3.78"
        assert r["has_update"] is True
        assert r["error"] == ""
        assert r["url"].endswith(".exe")

    def test_already_latest(self, tmp_path, monkeypatch):
        (tmp_path / "VERSION").write_text("3.78\n", encoding="utf-8")
        monkeypatch.setattr(updater.urllib.request, "urlopen",
                            _fake_urlopen([_rel("server-v3.78")]))
        r = updater.check(tmp_path)
        assert r["has_update"] is False
        assert r["error"] == ""

    def test_cannot_check_is_distinct_from_latest(self, tmp_path, monkeypatch):
        """没查成不能长得像「已是最新」。"""
        import urllib.error

        (tmp_path / "VERSION").write_text("3.78\n", encoding="utf-8")
        monkeypatch.setattr(
            updater.urllib.request, "urlopen",
            _fake_urlopen(None, raises=urllib.error.URLError("no route")))
        r = updater.check(tmp_path)
        assert r["error"] != ""          # 明说了没查成
        assert r["has_update"] is False
        assert r["latest"] == ""         # 不能编一个 latest 出来

    def test_check_never_raises(self, tmp_path, monkeypatch):
        """调用方是 UI，check 不许抛异常。"""
        import urllib.error

        monkeypatch.setattr(
            updater.urllib.request, "urlopen",
            _fake_urlopen(None, raises=ValueError("怪错")))
        r = updater.check(tmp_path)      # 不该炸
        assert r["error"] != ""

    def test_unknown_install_does_not_claim_update(self, tmp_path, monkeypatch):
        """没 VERSION 的老安装：查到最新版也不说「有新版本」。"""
        monkeypatch.setattr(updater.urllib.request, "urlopen",
                            _fake_urlopen([_rel("server-v3.78")]))
        r = updater.check(tmp_path)
        assert r["current"] == ""
        assert r["has_update"] is False
        assert r["latest"] == "3.78"     # 还是告诉用户 GitHub 上是几版
