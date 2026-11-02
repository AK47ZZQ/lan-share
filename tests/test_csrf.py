"""CSRF 中间件（Origin vs Host 同源校验）。

这是 SameSite=Lax 之外的第二道防线，之前**一条测试都没有**。
2026-11-02 用户装完 3.88 注册时 403，日志里只有 "CSRF check failed" 四个字，
根本看不出 origin 和 host 分别是什么 —— 补测试的同时也把报错改成带实际值。

注意这些用例必须走真实中间件（TestClient 会经过 security_middleware），
不要直接调内部函数，否则测不到挂载顺序的问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def client():
    from app.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def _post(client, origin=None, host=None, path="/api/setup"):
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    # body 不重要 —— CSRF 在鉴权和业务之前就拦掉了
    return client.post(path, json={"username": "x", "password": "y"}, headers=headers)


class TestCsrfAccepts:
    def test_no_origin_is_allowed(self, client):
        """非浏览器客户端（curl/App）不带 Origin，不能拦。"""
        r = _post(client)
        assert r.status_code != 403

    def test_matching_origin_and_host(self, client):
        r = _post(client, origin="https://localhost:8765", host="localhost:8765")
        assert r.status_code != 403

    def test_matching_ip_origin(self, client):
        r = _post(client, origin="https://192.0.2.50:8765", host="192.0.2.50:8765")
        assert r.status_code != 403

    def test_default_port_normalised(self, client):
        """Origin 省略 443、Host 显式带 443 —— 同一个源，不该拦。"""
        r = _post(client, origin="https://example.com", host="example.com:443")
        assert r.status_code != 403

    def test_case_insensitive_host(self, client):
        r = _post(client, origin="https://LocalHost:8765", host="localhost:8765")
        assert r.status_code != 403


class TestCsrfRejects:
    def test_different_host(self, client):
        r = _post(client, origin="https://evil.example", host="localhost:8765")
        assert r.status_code == 403
        assert r.json()["detail"] == "CSRF check failed"

    def test_different_port(self, client):
        r = _post(client, origin="https://localhost:9999", host="localhost:8765")
        assert r.status_code == 403

    def test_origin_null_is_rejected(self, client):
        """`Origin: null` 来自沙箱/异常环境，判断不了来源，按拒绝处理。"""
        r = _post(client, origin="null", host="localhost:8765")
        assert r.status_code == 403

    def test_error_carries_actual_values(self, client):
        """报错必须带真实的 origin/host —— 这正是上一轮缺失的信息。"""
        r = _post(client, origin="https://evil.example", host="localhost:8765")
        body = r.json()
        assert body["origin"] == "https://evil.example"
        assert body["host"] == "localhost:8765"
        assert "hint" in body

    def test_long_values_are_truncated(self, client):
        """别让超长头把响应撑爆。"""
        long_origin = "https://" + "a" * 5000 + ".example"
        r = _post(client, origin=long_origin, host="localhost:8765")
        assert r.status_code == 403
        assert len(r.json()["origin"]) <= 200
