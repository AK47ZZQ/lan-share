"""App 更新域从 GitHub Release 拉版本（2026-11-01）。

为什么值得单独测：这条链路的失败**极其安静**。GitHub 拿不到就静默回退本地，
本地也没有就 404；中间任何一步解析错（比如 build 号提取失败）都表现为
「用户点检查更新永远是已是最新」——不报错、不崩、日志里也没异常。

真发生过两次，都是这个形状：
  1. 用了 `/releases/latest`，而那个接口返回的是**发布时间最晚**的 Release，
     跟标签前缀无关 —— 服务端发得晚，它就返回 server-v3.75（没有 APK）。
  2. 早期 Release 的资产叫 `LANShare-App-1.1.18.apk`，没有 `+N`，
     原来的正则取不到 build 就 `return null`，整个源被丢掉。

所以这里把「选哪个 Release」「怎么取 build」「怎么排序」三件事钉住。

注意：conftest 的 `_no_github_releases` 默认把源打成不可达，
本文件用 `monkeypatch` 显式造假的 Release 列表来测 GitHub 分支。
"""

from __future__ import annotations

import pytest

from app.api import appver

# conftest 的 `_no_github_releases` 是 autouse，会在**每条测试前**把
# `_github_app_releases` 换成返回空列表的 lambda。本文件要测 GitHub 分支，
# 必须先把真实函数捞出来 —— 在 import 期拿到的这个引用就是原装的那份。
_ORIG_GITHUB_RELEASES = appver._github_app_releases


def _rel(tag: str, asset: str, digest: str = "sha256:" + "a" * 64, size: int = 100):
    """造一个 GitHub Release 对象（只带本模块用到的字段）。"""
    return {
        "tag_name": tag,
        "body": f"{tag} 的更新说明",
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "published_at": "2026-11-01T10:00:00Z",
        "assets": [{
            "name": asset,
            "size": size,
            "digest": digest,
            "browser_download_url": f"https://github.com/x/y/releases/download/{tag}/{asset}",
        }],
    }


@pytest.fixture()
def fake_github(monkeypatch):
    """把 GitHub API 的网络层换成「返回给定 JSON」。

    **只 mock 网络，不 mock `_github_app_releases` 本身** ——
    过滤 `app-` 前缀、解析 build、排序这些恰恰是最容易错的地方，
    替换整个函数等于把它们全跳过，测试就变成自说自话了。
    """

    def install(releases: list):
        # 先把被 conftest 换掉的真实函数放回去，再只替换它的网络层
        monkeypatch.setattr(appver, "_github_app_releases", _ORIG_GITHUB_RELEASES)
        monkeypatch.setattr(appver, "_gh_cache", {"ts": 0.0, "items": []})
        monkeypatch.setattr(appver.urllib.request, "urlopen", _fake_urlopen(releases))

    return install


# ──────────────────────────────────────────────────────────────
# 选哪个 Release
# ──────────────────────────────────────────────────────────────

class TestPickRelease:
    def test_server_release_ignored(self, fake_github):
        """服务端 Release 混在列表里、而且时间更晚，也不能被选中。

        这是最初那个 bug 的核心：/releases/latest 会返回 server-v3.75。
        """
        fake_github([
            _rel("server-v3.76", "LANShare-Setup-3.76.exe"),
            _rel("app-v1.1.18", "LANShare-App-1.1.18.apk"),
        ])
        data = appver._load_version()
        assert data["_source"] == "github"
        # 选中的必须是 app 那个，且它带 APK 下载地址
        assert data["version"] == "1.1.18"
        assert data["download_url"].endswith(".apk")

    def test_prefers_newest_version_not_latest_publish(self, fake_github):
        """列表顺序不等于版本顺序 —— 按 version 取最高，不按数组下标。"""
        fake_github([
            _rel("app-v1.1.17", "lanshare-v1.1.17+124.apk"),
            _rel("app-v1.1.18", "LANShare-App-1.1.18.apk"),
        ])
        assert appver._load_version()["version"] == "1.1.18"


# ──────────────────────────────────────────────────────────────
# build 号提取（三级兜底）
# ──────────────────────────────────────────────────────────────

class TestParseVersion:
    @pytest.mark.parametrize("text,expect", [
        # 1. 文件名里的 +N —— 最准
        ("lanshare-v1.1.17+124.apk", ("1.1.17", 124)),
        # 早期用下划线
        ("lanshare-v1.1.15_122.apk", ("1.1.15", 122)),
        # 2. 没有 +N → semver 推算（1*10000 + 1*100 + 18）
        ("LANShare-App-1.1.18.apk", ("1.1.18", 10118)),
        # 3. 完全没有版本号 → None
        ("lanshare.apk", None),
        ("", None),
    ])
    def test_three_level_fallback(self, text, expect):
        assert appver._parse_version(text) == expect

    def test_semver_fallback_is_monotonic(self):
        """推算值必须单调：否则「新版比旧版小」会导致永远推不出更新。"""
        older, _ = appver._parse_version("app-1.1.17")
        newer, _ = appver._parse_version("app-1.1.18")
        assert newer > older

    def test_build_from_filename_wins_over_semver(self):
        """有真实 build 号时必须用它，不能被推算值顶掉。"""
        _, build = appver._parse_version("lanshare-v1.1.17+124.apk")
        assert build == 124        # 不是 10117


# ──────────────────────────────────────────────────────────────
# 排序
# ──────────────────────────────────────────────────────────────

class TestSorting:
    def test_sorted_by_version_tuple_not_build(self, monkeypatch):
        """混合两种编号体系时，必须按 version 排。

        1.1.18 的 build 是推算值 10118，1.1.17 是真实 124 —— 一按数字排
        就会得出「1.1.15 比 1.1.17 新」这种鬼结果。
        """
        raw = [
            _rel("app-v1.1.15", "lanshare-v1.1.15_122.apk"),
            _rel("app-v1.1.18", "LANShare-App-1.1.18.apk"),   # build 10118
            _rel("app-v1.1.17", "lanshare-v1.1.17+124.apk"),
            _rel("app-v1.1.16", "lanshare-v1.1.16+123.apk"),
        ]

        monkeypatch.setattr(appver, "_github_app_releases", _ORIG_GITHUB_RELEASES)
        monkeypatch.setattr(appver, "_gh_cache", {"ts": 0.0, "items": []})
        monkeypatch.setattr(appver.urllib.request, "urlopen", _fake_urlopen(raw))

        items = appver._github_app_releases(force=True)
        assert [i["version"] for i in items] == ["1.1.18", "1.1.17", "1.1.16", "1.1.15"]


def _fake_urlopen(raw: list):
    """把 urllib 换成直接返回给定 JSON 的假实现。"""
    import io
    import json

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        return _Resp(json.dumps(raw).encode())

    return opener


# ──────────────────────────────────────────────────────────────
# 兜底
# ──────────────────────────────────────────────────────────────

class TestFallback:
    def test_falls_back_to_local_when_github_empty(self, fake_github, tmp_path, monkeypatch):
        """GitHub 拿不到 → 走本地 version.json，不能直接报错。"""
        fake_github([])

        dist = tmp_path / "App安装包"
        dist.mkdir()
        (dist / "version.json").write_text(
            '{"version": "1.1.17", "build": 124, "filename": "lanshare.apk"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)

        data = appver._load_version()
        assert data["_source"] == "local"
        assert data["version"] == "1.1.17"

    def test_history_uses_github_notes(self, fake_github):
        """更新日志直接取 Release body —— 这就是「日志也从 GitHub 拉」。"""
        fake_github([_rel("app-v1.1.18", "LANShare-App-1.1.18.apk")])
        items = appver._load_history()
        assert len(items) == 1
        assert "更新说明" in items[0]["notes"]
        assert items[0]["html_url"].endswith("app-v1.1.18")


# ──────────────────────────────────────────────────────────────
# 下载：本地优先（2026-11-01「下载太慢」）
# ──────────────────────────────────────────────────────────────

class TestDownloadLocalFirst:
    """局域网里已经有那份 APK 时，别再 302 去 GitHub 绕一圈。

    真机反馈：点更新后下载几分钟不动。局域网用户明明和服务端在同一台
    机器旁，却要出网跑一趟 GitHub CDN 再回来。
    """

    def test_serves_local_file_when_size_matches(self, tmp_path, monkeypatch):
        from fastapi.responses import FileResponse

        dist = tmp_path / "App安装包"
        dist.mkdir()
        apk = dist / "LANShare-App-1.1.20+127.apk"
        apk.write_bytes(b"x" * 1024)

        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)
        monkeypatch.setattr(
            appver, "_load_version",
            lambda: {"version": "1.1.20", "build": 127,
                     "filename": apk.name, "size": 1024,
                     "download_url": "https://github.com/x/y/releases/download/a/b.apk",
                     "_source": "github"},
        )
        monkeypatch.setattr(appver, "audit", lambda *a, **k: None)
        monkeypatch.setattr(appver, "client_ip", lambda r: "127.0.0.1")

        resp = appver.app_download(request=None, _user=None)
        assert isinstance(resp, FileResponse)

    def test_falls_back_to_302_when_size_differs(self, tmp_path, monkeypatch):
        """本地文件大小对不上 = 不是同一版，必须回落到 GitHub。"""
        from fastapi.responses import RedirectResponse

        dist = tmp_path / "App安装包"
        dist.mkdir()
        (dist / "lanshare.apk").write_bytes(b"old" * 100)   # 300 字节，旧的

        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)
        monkeypatch.setattr(
            appver, "_load_version",
            lambda: {"version": "1.1.20", "build": 127,
                     "filename": "LANShare-App-1.1.20+127.apk", "size": 1024,
                     "download_url": "https://github.com/x/y/releases/download/a/b.apk",
                     "_source": "github"},
        )
        monkeypatch.setattr(appver, "audit", lambda *a, **k: None)
        monkeypatch.setattr(appver, "client_ip", lambda r: "127.0.0.1")

        resp = appver.app_download(request=None, _user=None)
        assert isinstance(resp, RedirectResponse)
        assert resp.headers["location"].startswith("https://github.com/")


# ──────────────────────────────────────────────────────────────
# 后台镜像（让「别人搭的服务端」也能本地直发）
# ──────────────────────────────────────────────────────────────

class TestMirrorApk:
    """第一次下载时后台拉一份到本地，之后局域网走本地 IO。

    这条对其他用户是关键的：他们自己搭的服务端 `App安装包/` 是空的，
    没有镜像的话「本地优先」对他们等于死代码，永远 302 去 GitHub。
    """

    def test_find_local_apk_matches_by_size(self, tmp_path, monkeypatch):
        dist = tmp_path / "App安装包"
        dist.mkdir()
        (dist / "LANShare-App-1.1.20+127.apk").write_bytes(b"x" * 500)

        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)
        assert appver._find_local_apk("LANShare-App-1.1.20+127.apk", 500) is not None
        # 大小对不上 → 不是同一版
        assert appver._find_local_apk("LANShare-App-1.1.20+127.apk", 999) is None

    def test_find_local_apk_falls_back_to_generic_name(self, tmp_path, monkeypatch):
        """`lanshare.apk` 这个通用名也认（老部署习惯）。"""
        dist = tmp_path / "App安装包"
        dist.mkdir()
        (dist / "lanshare.apk").write_bytes(b"y" * 300)

        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)
        got = appver._find_local_apk("任何名字.apk", 300)
        assert got is not None and got.name == "lanshare.apk"

    def test_find_local_apk_rejects_path_traversal_name(self, tmp_path, monkeypatch):
        """GitHub 响应里的文件名带 `../` 或盘符 → 不能逃出 App安装包/。"""
        dist = tmp_path / "App安装包"
        dist.mkdir()
        escaped = tmp_path / "evil.apk"
        escaped.write_bytes(b"z" * 100)

        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)
        # 清洗后名字不存在于 dist → 返回 None；绝不指向 dist 之外
        got = appver._find_local_apk("../evil.apk", 100)
        assert got is None
        got2 = appver._find_local_apk("C:\\evil.apk", 100)
        assert got2 is None

    def test_safe_apk_name(self):
        assert appver._safe_apk_name("../../evil.apk") == "evil.apk"
        assert appver._safe_apk_name("C:\\evil.apk") == "evil.apk"
        assert appver._safe_apk_name("a/b/c.apk") == "c.apk"
        assert appver._safe_apk_name("") == ""
        assert appver._safe_apk_name(None) == ""

    def test_mirror_writes_file_and_is_atomic(self, tmp_path, monkeypatch):
        """镜像成功后本地有完整文件，且不残留 .part。"""
        import threading
        import time as _t

        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)
        payload = b"z" * 2048

        class _Resp:
            def __init__(self):
                self._buf = payload
            def read(self, n=-1):
                if not self._buf:
                    return b""
                out, self._buf = self._buf[:n], self._buf[n:]
                return out
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr(appver.urllib.request, "urlopen",
                            lambda req, timeout=None: _Resp())

        appver._mirror_apk_async("LANShare-App-9.9.9+999.apk",
                                 "https://example.com/a.apk", 2048)

        dest = tmp_path / "App安装包" / "LANShare-App-9.9.9+999.apk"
        for _ in range(100):          # 等后台线程落盘
            if dest.is_file():
                break
            _t.sleep(0.05)

        assert dest.is_file()
        assert dest.read_bytes() == payload
        # 不能留 .part 残骸
        assert not dest.with_suffix(".apk.part").exists()
        assert "LANShare-App-9.9.9+999.apk" not in appver._apk_syncing

    def test_mirror_rejects_size_mismatch(self, tmp_path, monkeypatch):
        """GitHub 报的 size 和实际拉下来的不符 → 丢弃，不留坏文件。"""
        import time as _t

        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)

        class _Resp:
            def read(self, n=-1):
                return b""
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        # 实际给 0 字节，但声明 2048
        monkeypatch.setattr(appver.urllib.request, "urlopen",
                            lambda req, timeout=None: _Resp())

        appver._mirror_apk_async("bad.apk", "https://example.com/b.apk", 2048)
        for _ in range(40):
            if "bad.apk" not in appver._apk_syncing:
                break
            _t.sleep(0.05)

        assert not (tmp_path / "App安装包" / "bad.apk").exists()

    def test_mirror_skips_when_already_present(self, tmp_path, monkeypatch):
        """本地已有同 size 的文件 → 不重复下载。"""
        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)
        dist = tmp_path / "App安装包"
        dist.mkdir()
        (dist / "dup.apk").write_bytes(b"a" * 100)

        called = []
        monkeypatch.setattr(appver.urllib.request, "urlopen",
                            lambda *a, **k: called.append(1))

        appver._mirror_apk_async("dup.apk", "https://example.com/c.apk", 100)
        import time as _t
        for _ in range(40):
            if "dup.apk" not in appver._apk_syncing:
                break
            _t.sleep(0.05)

        assert called == []      # 一次网络请求都没发
