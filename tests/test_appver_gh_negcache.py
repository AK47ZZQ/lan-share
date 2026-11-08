"""GitHub 远端版本源的**负缓存**：失败本身也要缓存。

缺口（2026-11-08 值守批挂账的那条）：

`_github_app_releases` 的 except 分支只 `return 旧缓存`，不记时间戳。于是
「GitHub 不可达 + 全新部署没有本地 version.json」这条路上，**每个请求都
重新去撞一次 4s 超时**（`_GH_TIMEOUT`），而它注定每次都失败 —— 请求越多
越慢，且这是免登录也能打到的端点。

修法：失败记 `fail_ts`，`_GH_NEG_TTL`（60s）内直接返回缓存；成功即清零。
60s 是个保守值：更新检查本来就是低频操作，退避一分钟对用户无感，却能
把「N 个请求 × 4s」压成「每 60s 一次 4s」。
"""
import time

import pytest

import app.api.appver as _appver

# 收集期抓一份真函数：conftest 的 autouse 桩会把模块属性换成「永远不可用」的
# lambda，这里必须留一份原始引用，否则测的是桩不是实现。
_REAL_GH = _appver._github_app_releases


@pytest.fixture(autouse=True)
def _reset(_no_github_releases, monkeypatch):
    """依赖 `_no_github_releases` 强制排在它之后，再把真函数装回去。"""
    import app.api.appver as m
    monkeypatch.setattr(m, "_github_app_releases", _REAL_GH)
    m._gh_cache.update({"ts": 0.0, "items": [], "fail_ts": 0.0})
    yield
    m._gh_cache.update({"ts": 0.0, "items": [], "fail_ts": 0.0})


class _Counter:
    def __init__(self):
        self.n = 0

    def __call__(self, *a, **k):
        self.n += 1
        raise OSError("github unreachable (test)")


class TestNegativeCache:
    def test_failure_is_cached_for_window(self, monkeypatch):
        """连续两次调用只应真的去撞一次 GitHub。"""
        import app.api.appver as m
        c = _Counter()
        monkeypatch.setattr(m.urllib.request, "urlopen", c)
        assert m._github_app_releases() == []
        assert c.n == 1
        assert m._github_app_releases() == []
        assert c.n == 1, "退避窗口内不该再发请求"
        assert m._gh_cache["fail_ts"] > 0

    def test_window_expiry_retries(self, monkeypatch):
        """窗口过去之后必须重试（否则 GitHub 恢复了也永远拿不到）。"""
        import app.api.appver as m
        c = _Counter()
        monkeypatch.setattr(m.urllib.request, "urlopen", c)
        m._github_app_releases()
        assert c.n == 1
        m._gh_cache["fail_ts"] = time.monotonic() - (m._GH_NEG_TTL + 1)
        m._github_app_releases()
        assert c.n == 2

    def test_force_bypasses_backoff(self, monkeypatch):
        """force=True 是显式刷新，必须穿透退避。"""
        import app.api.appver as m
        c = _Counter()
        monkeypatch.setattr(m.urllib.request, "urlopen", c)
        m._github_app_releases()
        m._github_app_releases(force=True)
        assert c.n == 2

    def test_success_clears_backoff(self, monkeypatch):
        """成功一次就把 fail_ts 清零，不能带着失败的阴影继续退避。"""
        import app.api.appver as m
        c = _Counter()
        monkeypatch.setattr(m.urllib.request, "urlopen", c)
        m._github_app_releases()
        assert m._gh_cache["fail_ts"] > 0

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                # 一条没有任何 app- 资产的 release：解析成功但 items 为空，
                # 正好覆盖「请求通了但没东西」这一支。
                return b'[{"tag_name": "server-v1.0.0", "assets": []}]'

        monkeypatch.setattr(m.urllib.request, "urlopen", lambda *a, **k: _Resp())
        m._github_app_releases(force=True)
        assert m._gh_cache["fail_ts"] == 0.0

    def test_stale_items_still_served_during_backoff(self, monkeypatch):
        """退避期靠旧数据撑着：有缓存就还回去，不能因为 GitHub 挂了就清空。"""
        import app.api.appver as m
        good = [{"version": "4.0.2", "build": 134, "filename": "app.apk"}]
        m._gh_cache["items"] = list(good)
        m._gh_cache["fail_ts"] = time.monotonic()   # 刚失败过
        c = _Counter()
        monkeypatch.setattr(m.urllib.request, "urlopen", c)
        assert m._github_app_releases() == good
        assert c.n == 0, "退避期内连请求都不该发"
