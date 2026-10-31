"""`/local/qr` 的测试。

这个端点免登录，安全性全靠「只允许本机」这一条判断。
所以测试重点不是二维码本身，而是**这条判断能不能被绕过**。

三个攻击面：
    1. 局域网机器直接打 API 端口 8766
    2. 局域网机器经 Caddy 反代打 8765 —— 此时 client.host 是 127.0.0.1
    3. 公网机器伪造 X-Real-IP: 127.0.0.1 直连 8766

注意：FastAPI 的 TestClient 默认 client.host 是 "testclient"，
造不出「来自 127.0.0.1」的请求，所以「放行」路径直接单测
_effective_client_ip，用假 Request 精确控制每个字段。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.qr import _effective_client_ip, router


def _fake_request(direct_ip: str, headers: dict | None = None) -> SimpleNamespace:
    """构造一个只带 client 和 headers 的假 Request。"""
    return SimpleNamespace(
        client=SimpleNamespace(host=direct_ip) if direct_ip else None,
        headers=headers or {},
    )


class TestEffectiveClientIp:
    """判定逻辑本身 —— 这是安全的核心。"""

    def test_direct_loopback_no_headers(self):
        assert _effective_client_ip(_fake_request("127.0.0.1")) == "127.0.0.1"

    def test_direct_ipv6_loopback(self):
        assert _effective_client_ip(_fake_request("::1")) == "::1"

    def test_proxy_from_localhost_trusts_real_ip(self):
        """Caddy 在本机，client.host=127.0.0.1，真实来源在 X-Real-IP。"""
        req = _fake_request("127.0.0.1", {"x-real-ip": "192.168.1.99"})
        assert _effective_client_ip(req) == "192.168.1.99"

    def test_proxy_falls_back_to_forwarded_for(self):
        req = _fake_request("127.0.0.1", {"x-forwarded-for": "192.168.1.99, 10.0.0.1"})
        assert _effective_client_ip(req) == "192.168.1.99"

    def test_direct_external_ip_ignores_spoofed_header(self):
        """**最关键的一条**：非回环直连时，X-Real-IP 一律不信。

        否则公网攻击者只要带一个 `X-Real-IP: 127.0.0.1` 就能白拿免登录接口。
        """
        req = _fake_request("8.8.8.8", {"x-real-ip": "127.0.0.1"})
        assert _effective_client_ip(req) == "8.8.8.8"

    def test_direct_lan_ip_ignores_spoofed_header(self):
        req = _fake_request("192.168.1.99", {"x-real-ip": "127.0.0.1"})
        assert _effective_client_ip(req) == "192.168.1.99"

    def test_direct_external_ignores_spoofed_forwarded_for(self):
        req = _fake_request("8.8.8.8", {"x-forwarded-for": "127.0.0.1"})
        assert _effective_client_ip(req) == "8.8.8.8"

    def test_no_client_info_returns_empty(self):
        assert _effective_client_ip(_fake_request("")) == ""


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestEndpointRejectsRemote:
    """TestClient 的 host 是 "testclient"，相当于「从外部直连 API」。"""

    def test_external_direct_blocked(self, client):
        assert client.get("/local/qr", params={"text": "http://x"}).status_code == 403

    def test_spoofed_real_ip_blocked(self, client):
        resp = client.get(
            "/local/qr",
            params={"text": "http://x"},
            headers={"X-Real-IP": "127.0.0.1"},
        )
        assert resp.status_code == 403, "非回环直连时伪造 X-Real-IP 必须无效"

    def test_spoofed_forwarded_for_blocked(self, client):
        resp = client.get(
            "/local/qr",
            params={"text": "http://x"},
            headers={"X-Forwarded-For": "127.0.0.1"},
        )
        assert resp.status_code == 403

    def test_lan_ip_via_proxy_blocked(self, client):
        resp = client.get(
            "/local/qr",
            params={"text": "http://x"},
            headers={"X-Real-IP": "192.168.1.99"},
        )
        assert resp.status_code == 403


class TestEndpointValidation:
    def test_missing_text_rejected(self, client):
        assert client.get("/local/qr").status_code == 422

    def test_oversized_text_rejected(self, client):
        assert client.get("/local/qr", params={"text": "x" * 513}).status_code == 422


class TestAuthenticatedQrStillProtected:
    def test_api_qr_requires_login(self, client):
        """/api/qr 必须仍然要登录 —— 这次改动不能顺手放松它。"""
        resp = client.get("/api/qr", params={"text": "hello"})
        assert resp.status_code in (401, 403)


class TestQrSvgOutput:
    def test_svg_shape(self):
        """直接调渲染函数验证输出格式（绕过 IP 判断）。"""
        from app.api.qr import qr_svg_response

        resp = qr_svg_response("http://192.168.1.12:8765")
        assert b"<svg" in resp.body
        assert resp.media_type == "image/svg+xml"
