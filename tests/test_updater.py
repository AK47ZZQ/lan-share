"""服务端检查更新（lanshare_cli/updater.py）。

这一块判断错了用户就收不到更新提示，或者被反复骚扰说「有新版本」，
所以每个分支都要测。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lanshare_cli import updater


@pytest.fixture(autouse=True)
def _no_proxy_in_tests(monkeypatch):
    """测试里禁用代理分支。

    本机开着系统代理时，`_opener()` 会返回一个真 opener，于是请求走
    `_opener().open(...)` 而不是 `urllib.request.urlopen(...)` ——
    现有那些 mock 了 urlopen 的用例就全部失效，还会真的发网络请求。
    """
    monkeypatch.setattr(updater, "_opener", lambda: None)


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

    def _open(req, timeout=None, context=None):
        # context 是 2026-11-01 加的（给 certifi 的 CA bundle 用），
        # 假函数要跟着签名走，否则 TypeError 被当成真错误。
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

class TestSslContext:
    """embedded Python 不带 CA bundle 的补丁。

    真机事故：干净机器装 3.80，点「检查更新」报
      [SSL: CERTIFICATE_VERIFY_FAILED] unable to get issuer certificate
    因为嵌入式 Python 的 ssl.get_default_verify_paths().cafile 是 None，
    只能靠 OpenSSL 读 Windows 证书存储，那条路在精简系统上不总成立。
    """

    def test_uses_certifi_when_available(self, monkeypatch):
        import ssl as _ssl

        import lanshare_cli.updater as up

        captured = {}

        def fake_create(cafile=None):
            captured["cafile"] = cafile
            return "CTX"

        monkeypatch.setattr(_ssl, "create_default_context", fake_create)
        assert up._ssl_context() == "CTX"
        assert captured["cafile"]  # 必须带上 cafile，不能是裸的默认上下文

    def test_returns_none_when_certifi_missing(self, monkeypatch):
        """certifi 没打进包时不能崩 —— 退回系统证书那条路。"""
        import builtins

        import lanshare_cli.updater as up

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "certifi":
                raise ImportError("no certifi")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert up._ssl_context() is None

    def test_cert_verify_error_gives_chinese_hint(self, monkeypatch):
        """证书校验失败要说人话，不能把 SSLError 原样抛给用户。"""
        import ssl as _ssl
        import urllib.error

        import lanshare_cli.updater as up

        def boom(req, timeout=None, context=None):
            raise urllib.error.URLError(
                _ssl.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get issuer")
            )

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(RuntimeError) as exc:
            up.fetch_latest()
        msg = str(exc.value)
        assert "证书" in msg
        assert "VPN" in msg


class TestCache:
    """检查结果的本地缓存。

    GitHub 未登录 API 每小时 60 次，用户看到「检查失败」会反复点，
    几次就烧光额度然后进入「一直失败」的循环。缓存是唯一的出路。
    但缓存用错了比没有更糟 —— 报一个一年前的「最新版」给用户。
    """

    def _write_install(self, tmp_path, version="3.88"):
        (tmp_path / "VERSION").write_text(version, encoding="utf-8")

    def test_round_trip(self, tmp_path):
        info = {"version": "3.99", "url": "https://x/y.exe",
                "notes": "n", "published": "2026-11-02"}
        updater._write_cache(tmp_path, info)
        got = updater._read_cache(tmp_path)
        assert got is not None
        assert got["version"] == "3.99"
        assert got["_cached_at"] > 0

    def test_missing_file_returns_none(self, tmp_path):
        assert updater._read_cache(tmp_path) is None

    def test_corrupt_file_returns_none(self, tmp_path):
        f = tmp_path / "cache" / "update-check.json"
        f.parent.mkdir(parents=True)
        f.write_text("{not json", encoding="utf-8")
        assert updater._read_cache(tmp_path) is None

    def test_stale_cache_expires(self, tmp_path, monkeypatch):
        """超过 _CACHE_MAX_AGE 的缓存不再采用 —— 老数据比没数据更误导。"""
        updater._write_cache(tmp_path, {"version": "1.0", "url": ""})
        f = tmp_path / "cache" / "update-check.json"
        d = json.loads(f.read_text(encoding="utf-8"))
        d["_cached_at"] = int(__import__("time").time()) - updater._CACHE_MAX_AGE - 60
        f.write_text(json.dumps(d), encoding="utf-8")
        assert updater._read_cache(tmp_path) is None

    def test_check_falls_back_to_cache_on_error(self, tmp_path, monkeypatch):
        self._write_install(tmp_path, "3.88")
        updater._write_cache(tmp_path, {
            "version": "3.99", "url": "https://x/y.exe",
            "notes": "", "published": "2026-11-02"})

        def boom(timeout=8.0):
            raise RuntimeError("GitHub 接口限流")

        monkeypatch.setattr(updater, "fetch_latest", boom)
        out = updater.check(tmp_path)
        assert out["error"] == ""          # 用上了缓存就不算「没查成」
        assert out["latest"] == "3.99"
        assert out["has_update"] is True
        assert out["stale"] is True

    def test_check_reports_error_when_no_cache(self, tmp_path, monkeypatch):
        self._write_install(tmp_path, "3.88")

        def boom(timeout=8.0):
            raise RuntimeError("GitHub 接口限流")

        monkeypatch.setattr(updater, "fetch_latest", boom)
        out = updater.check(tmp_path)
        assert out["error"]
        assert out["latest"] == ""
        assert out["stale"] is False

    def test_successful_check_writes_cache(self, tmp_path, monkeypatch):
        self._write_install(tmp_path, "3.88")
        monkeypatch.setattr(updater, "fetch_latest", lambda timeout=8.0: {
            "version": "3.99", "url": "https://x/y.exe",
            "notes": "", "published": "2026-11-02"})
        out = updater.check(tmp_path)
        assert out["has_update"] is True
        assert updater._read_cache(tmp_path)["version"] == "3.99"


class TestSystemProxy:
    """读 Windows 系统代理。

    urllib 只认 HTTP_PROXY/HTTPS_PROXY 环境变量，**完全不读「Internet 选项」**。
    而 Clash/v2rayN 这类工具只改注册表不设环境变量，于是「检查更新」直连
    GitHub 一直报「连不上」，报错还完全指不到代理身上。
    2026-11-02 实机诊断到 ProxyEnable=1 / ProxyServer=127.0.0.1:7897，
    加上这段之后检查更新立刻从「连不上」变成「能连、只是限流」。
    """

    def test_returns_none_off_windows(self, monkeypatch):
        monkeypatch.setattr(updater.sys, "platform", "linux")
        assert updater._system_proxy() is None

    def test_reads_host_port(self, monkeypatch):
        import sys as _sys
        fake_winreg = type("W", (), {
            "HKEY_CURRENT_USER": 0,
            "OpenKey": staticmethod(lambda *a, **k: _Ctx()),
            "QueryValueEx": staticmethod(_fake_query(1, "127.0.0.1:7897")),
        })
        monkeypatch.setitem(_sys.modules, "winreg", fake_winreg)
        monkeypatch.setattr(updater.sys, "platform", "win32")
        assert updater._system_proxy() == "http://127.0.0.1:7897"

    def test_none_when_disabled(self, monkeypatch):
        import sys as _sys
        fake_winreg = type("W", (), {
            "HKEY_CURRENT_USER": 0,
            "OpenKey": staticmethod(lambda *a, **k: _Ctx()),
            "QueryValueEx": staticmethod(_fake_query(0, "127.0.0.1:7897")),
        })
        monkeypatch.setitem(_sys.modules, "winreg", fake_winreg)
        monkeypatch.setattr(updater.sys, "platform", "win32")
        assert updater._system_proxy() is None

    def test_handles_per_protocol_form(self, monkeypatch):
        """ProxyServer 可能是 'http=h:p;https=h:p' 这种分协议写法。"""
        import sys as _sys
        fake_winreg = type("W", (), {
            "HKEY_CURRENT_USER": 0,
            "OpenKey": staticmethod(lambda *a, **k: _Ctx()),
            "QueryValueEx": staticmethod(
                _fake_query(1, "http=1.2.3.4:8080;https=1.2.3.4:8443")),
        })
        monkeypatch.setitem(_sys.modules, "winreg", fake_winreg)
        monkeypatch.setattr(updater.sys, "platform", "win32")
        assert updater._system_proxy() == "http://1.2.3.4:8443"


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_query(enable, server):
    def q(key, name):
        if name == "ProxyEnable":
            return enable, 4
        return server, 1
    return q
