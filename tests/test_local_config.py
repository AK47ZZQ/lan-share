"""`/local/public-url` 的测试。

这个端点和 `/local/qr` 一样免登录，安全全靠「只允许本机」。

除了访问控制，还有几个真实场景要覆盖：
    - 用户只输了 `xxx.frp-any.com:35391`（没写协议）
    - 用户输了带尾部斜杠的地址
    - 用户清空（表示取消公网配置）
    - 保存后要立刻生效（不能被 60 秒 netloc 缓存拖住）
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.local_config import _normalize, router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestNormalize:
    """输入整理 —— 用户不会规规矩矩写 https://。"""

    def test_bare_host_gets_https(self):
        """只写 `xxx.frp-any.com:35391` 时补 https。

        公网入口几乎都是 HTTPS，而 sakuraFrp 的「自动 HTTPS」
        也是这个假设，所以默认 https 比 http 更符合实际。
        """
        value, err = _normalize("xxx.frp-any.com:35391")
        assert err == ""
        assert value == "https://xxx.frp-any.com:35391"

    def test_keeps_explicit_scheme(self):
        value, err = _normalize("http://192.168.1.12:8765")
        assert err == ""
        assert value == "http://192.168.1.12:8765"

    def test_strips_trailing_slash(self):
        """尾部斜杠要去掉：前端拼 /api/xxx 时会出现双斜杠。"""
        value, err = _normalize("https://example.com:8443/")
        assert err == ""
        assert value == "https://example.com:8443"

    def test_strips_path(self):
        """带了路径也只保留 scheme://netloc。"""
        value, err = _normalize("https://example.com:8443/some/path")
        assert err == ""
        assert value == "https://example.com:8443"

    def test_empty_is_clear(self):
        """空字符串是合法的 —— 表示清除配置。"""
        value, err = _normalize("")
        assert value == ""
        assert err == ""

    def test_whitespace_is_clear(self):
        value, err = _normalize("   ")
        assert value == ""

    def test_garbage_rejected(self):
        """没有主机名的输入要报错，不能静默存成半截。"""
        value, err = _normalize("https://")
        assert err != ""


class TestAccessControl:
    """TestClient 的 host 是 "testclient"，相当于从外部直连 API。"""

    def test_get_blocked_for_remote(self, client):
        assert client.get("/local/public-url").status_code == 403

    def test_post_blocked_for_remote(self, client):
        resp = client.post("/local/public-url", json={"url": "https://x.com"})
        assert resp.status_code == 403

    def test_spoofed_real_ip_ignored(self, client):
        """非回环直连时伪造 X-Real-IP 必须无效。"""
        resp = client.get(
            "/local/public-url", headers={"X-Real-IP": "127.0.0.1"}
        )
        assert resp.status_code == 403


class TestRoundTrip:
    """用 monkeypatch 替掉 settings，验证读写逻辑本身。"""

    @pytest.fixture
    def local_client(self, monkeypatch):
        """把 _require_local 打桩成放行，专心测业务逻辑。"""
        import app.api.local_config as mod

        monkeypatch.setattr(mod, "_require_local", lambda request: None)

        store: dict[str, str] = {}
        monkeypatch.setattr(mod.settings, "get_setting", lambda k, d="": store.get(k, d))
        monkeypatch.setattr(
            mod.settings, "set_setting", lambda k, v: store.__setitem__(k, v)
        )

        app = FastAPI()
        app.include_router(router)
        return TestClient(app), store

    def test_save_and_read(self, local_client):
        client, store = local_client

        resp = client.post(
            "/local/public-url",
            json={"url": "xxx.frp-any.com:35391", "url_alt": "yyy.frp-any.com:40000"},
        )
        assert resp.status_code == 200
        assert resp.json()["url"] == "https://xxx.frp-any.com:35391"

        got = client.get("/local/public-url")
        assert got.status_code == 200
        body = got.json()
        assert body["url"] == "https://xxx.frp-any.com:35391"
        assert body["url_alt"] == "https://yyy.frp-any.com:40000"
        assert body["available"] is True

    def test_clear(self, local_client):
        client, store = local_client
        client.post("/local/public-url", json={"url": "https://a.com:1"})
        client.post("/local/public-url", json={"url": "", "url_alt": ""})

        body = client.get("/local/public-url").json()
        assert body["url"] == ""
        assert body["url_alt"] == ""

    def test_read_failure_is_reported(self, local_client, monkeypatch):
        """读库失败要明确说出来，不能装作「没配置」。

        否则界面上会显示「未配置」，用户以为自己的设置在、
        实际是数据库读不出来 —— 排查方向完全跑偏。
        """
        import app.api.local_config as mod

        client, store = local_client

        def boom(*a, **k):
            raise RuntimeError("数据库没初始化")

        monkeypatch.setattr(mod.settings, "get_setting", boom)
        body = client.get("/local/public-url").json()
        assert body["available"] is False
        assert "数据库没初始化" in body["error"]

    def test_invalid_url_rejected(self, local_client):
        client, store = local_client
        resp = client.post("/local/public-url", json={"url": "https://"})
        assert resp.status_code == 422
