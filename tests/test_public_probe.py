"""公网域名检测（probe）与隧道启停的测试。

覆盖三块：
1. 格式校验矩阵 —— 这是防呆的第一道门，错一种就会让用户配出一个不能用的入口
2. 同源校验 —— 这是安全底线，错一次用户就把 App 指到别人的服务器上
3. 环境检测与隧道控制 —— 关键是「只提示不阻塞」和「非 Windows 不崩」

**不联网**：所有 HTTP 打桩。**不动真服务**：隧道控制相关用例全部 mock `_run`。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import settings
from app.services import public_probe, tunnel_ctl


@pytest.fixture()
def local_client():
    """模拟托盘面板：来源 IP 与 Host 都是回环，能通过 _require_local。

    默认的 client fixture 用 base_url=https://testserver，Host 不是回环，
    会被 /local/* 的第三道门拦成 403 —— 所以这里必须自建。
    """
    from app.main import app as fastapi_app

    return TestClient(fastapi_app, client=("127.0.0.1", 51000),
                      base_url="http://127.0.0.1:8766")


# ── 1. 格式校验矩阵 ───────────────────────────────────────


@pytest.mark.parametrize("url,expect_ok,fragment", [
    ("https://lanshareapi.ak47zzq.com", True, "格式正确"),
    ("https://ok.example.com/", True, "格式正确"),
    ("http://ok.example.com", False, "协议"),
    ("https://ok.example.com:8443", False, "端口"),
    ("https://ok.example.com/path", False, "路径"),
    ("https://ok.example.com?x=1", False, "路径"),
    # 用 TEST-NET-1（RFC 5737 文档保留段），不要写真实私网地址 ——
    # 仓库是开源的，隐私检查会拦。判据只看「是不是 IP 字面量」，与具体网段无关。
    ("https://203.0.113.1", False, "IP"),
    ("https://localhost", False, "域名"),
    ("https://foo.pages.dev", False, "Cloudflare 自己的域名"),
    ("https://x.cfargotunnel.com", False, "Cloudflare 自己的域名"),
    ("https://ok", False, "域名"),
    ("", False, "为空"),
])
def test_check_format_matrix(url, expect_ok, fragment):
    ok, detail, hint, host = public_probe.check_format(url)
    assert ok is expect_ok, f"{url}: {detail} / {hint}"
    assert fragment in (detail + hint), f"{url}: {detail} / {hint}"
    if ok:
        assert host == "lanshareapi.ak47zzq.com" or host.endswith("example.com")


def test_check_format_strips_trailing_dot():
    """FQDN 形式的尾点要吃掉，否则解析会失败。"""
    ok, _, _, host = public_probe.check_format("https://ok.example.com.")
    assert ok and host == "ok.example.com"


# ── 2. Cloudflare 错误码映射 ─────────────────────────────


@pytest.mark.parametrize("status,body,fragment", [
    (502, b"error code: 1033", "1033"),
    (530, b"error code: 1034", "1034"),
    (403, b"error code: 1010", "1010"),
    (403, b"error code: 1016", "1016"),
    (526, b"error code: 526", "526"),
    (502, b"error code: 502", "502"),
    (502, b"", "502"),
])
def test_cf_error_hint_maps_known_codes(status, body, fragment):
    detail, hint = public_probe._cf_error_hint(status, body)
    assert fragment in detail, detail
    assert hint, "每个已知错误码都应该给出一句能照做的建议"


# ── 3. 同源校验 ─────────────────────────────────────────


def test_same_instance_ok(monkeypatch):
    local_id = settings.ensure_instance_id()
    monkeypatch.setattr(public_probe, "_http_get",
                        lambda *a, **k: (200, json.dumps(
                            {"instance_id": local_id}).encode(), ""))
    ok, detail, hint = public_probe.check_same_instance("https://x.example.com")
    assert ok, detail
    assert local_id[:8] in detail
    assert hint == ""


def test_same_instance_mismatch(monkeypatch):
    """最关键的一条：域名指向别人家时必须拒绝。"""
    monkeypatch.setattr(public_probe, "_http_get",
                        lambda *a, **k: (200, json.dumps(
                            {"instance_id": "00000000-0000-0000-0000-000000000000"}
                        ).encode(), ""))
    ok, detail, hint = public_probe.check_same_instance("https://x.example.com")
    assert not ok
    assert "不一致" in detail
    assert "不是这台机器" in hint


def test_same_instance_404_means_old_version(monkeypatch):
    monkeypatch.setattr(public_probe, "_http_get", lambda *a, **k: (404, b"", ""))
    ok, detail, hint = public_probe.check_same_instance("https://x.example.com")
    assert not ok
    assert "没有 /api/instance-id" in detail
    assert "版本" in hint


# ── 4. probe 整体行为 ───────────────────────────────────


def test_probe_stops_at_format_error(monkeypatch):
    """格式错时不该再发任何网络请求。"""
    called = []
    monkeypatch.setattr(public_probe, "_http_get",
                        lambda *a, **k: called.append(a) or (200, b"{}", ""))
    result = public_probe.probe("http://bad.example.com")
    assert result["ok"] is False
    assert called == [], "格式检查失败后仍然发起了网络请求"
    assert result["hint"]


def test_probe_ok_end_to_end(monkeypatch):
    local_id = settings.ensure_instance_id()

    def fake_get(url, timeout=public_probe.PROBE_TIMEOUT):
        if url.endswith("/api/auth/state"):
            return 200, b'{"logged_in":false}', ""
        if url.endswith("/api/instance-id"):
            return 200, json.dumps({"instance_id": local_id}).encode(), ""
        return 404, b"", ""

    monkeypatch.setattr(public_probe, "_http_get", fake_get)
    monkeypatch.setattr(public_probe, "check_dns",
                        lambda h: (True, "解析到 172.67.1.1（Cloudflare 边缘）", ""))

    result = public_probe.probe("https://lanshareapi.ak47zzq.com")
    assert result["ok"] is True, result
    keys = [s["key"] for s in result["steps"]]
    # 四步都要出现，顺序固定
    assert keys[:4] == ["format", "dns", "https", "same"]
    assert result["hint"] == ""


def test_env_check_never_blocks(monkeypatch):
    """环境检测的任何结果都不能让 probe.ok 变 false —— 这是硬约束。"""
    local_id = settings.ensure_instance_id()
    monkeypatch.setattr(public_probe, "check_dns", lambda h: (True, "ok", ""))
    monkeypatch.setattr(
        public_probe, "_http_get",
        lambda url, timeout=15: (200, json.dumps(
            {"instance_id": local_id}).encode(), "") if "instance-id" in url
        else (200, b"{}", ""))
    # 强制环境检测全部报坏
    monkeypatch.setattr(public_probe, "_find_cloudflared", lambda: "")
    monkeypatch.setattr(public_probe, "_windows_task_running", lambda img: True)

    result = public_probe.probe("https://lanshareapi.ak47zzq.com")
    assert result["ok"] is True
    env_items = [s for s in result["steps"] if s["key"].startswith("env_")]
    assert env_items, "环境检测项应该出现在结果里"
    assert all(item["blocking"] is False for item in env_items)
    assert any(item["level"] in ("warn", "danger") for item in env_items)


# ── 5. /api/instance-id 端点 ───────────────────────────


def test_instance_id_endpoint(local_client):
    r = local_client.get("/api/instance-id")
    assert r.status_code == 200
    body = r.json()
    # 只有一个字段 —— 防止以后有人"顺手"加 version
    assert list(body) == ["instance_id"]
    assert len(body["instance_id"]) == 36
    assert r.headers.get("cache-control") == "no-store"
    assert r.headers.get("x-robots-tag") == "noindex"


def test_instance_id_is_stable(local_client):
    first = local_client.get("/api/instance-id").json()["instance_id"]
    second = local_client.get("/api/instance-id").json()["instance_id"]
    assert first == second


# ── 6. 保存 / 读取（mode 语义）──────────────────────────


def test_public_url_roundtrip_with_mode(local_client):
    r = local_client.post("/local/public-url",
                          json={"url": "https://lanshareapi.ak47zzq.com",
                                "mode": "cloudflare"})
    assert r.status_code == 200, r.text

    got = local_client.get("/local/public-url").json()
    assert got["url"] == "https://lanshareapi.ak47zzq.com"
    assert got["mode"] == "cloudflare"

    # 清理，免得污染其他用例
    local_client.post("/local/public-url", json={"url": "", "mode": "cloudflare"})
    assert local_client.get("/local/public-url").json()["mode"] == ""


def test_public_url_rejects_bad_cloudflare_url(local_client):
    """后端也要拦 —— 托盘不是唯一调用方。"""
    r = local_client.post("/local/public-url",
                          json={"url": "https://x.example.com:8443",
                                "mode": "cloudflare"})
    assert r.status_code == 422
    assert "端口" in r.json()["detail"]


def test_public_url_rejects_unknown_mode(local_client):
    r = local_client.post("/local/public-url",
                          json={"url": "https://x.example.com", "mode": "nope"})
    assert r.status_code == 422


def test_mode_defaults_to_frp_for_legacy_clients(local_client):
    """老托盘只传 url，不传 mode —— 行为必须和改造前一致。"""
    r = local_client.post("/local/public-url",
                          json={"url": "https://cn-bj.frp.one:35123"})
    assert r.status_code == 200
    assert local_client.get("/local/public-url").json()["mode"] == "frp"
    local_client.post("/local/public-url", json={"url": ""})


def test_mode_inferred_for_legacy_rows(local_client):
    """存量数据：库里 mode 为空但 url 有值 → 推断成 frp。"""
    from app import settings as s

    s.set_setting(s.PUBLIC_BASE_URL, "https://old.example.com")
    s.set_setting(s.PUBLIC_ACCESS_MODE, "")
    got = local_client.get("/local/public-url").json()
    assert got["mode"] == "frp"
    s.set_setting(s.PUBLIC_BASE_URL, "")


# ── 7. 隧道启停（全部打桩，不碰真服务）──────────────────


def test_tunnel_status_shape(monkeypatch):
    monkeypatch.setattr(tunnel_ctl, "_find_service", lambda: ("LANShareTunnel", "running"))
    monkeypatch.setattr(tunnel_ctl, "_process_running", lambda: (True, 1234))
    st = tunnel_ctl.tunnel_status()
    if st["supported"]:
        assert st["service"] == "LANShareTunnel"
        assert st["controllable"] is True
        assert "LANShareTunnel" in st["note"]


def test_tunnel_stop_prefers_service(monkeypatch):
    calls = []

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(tunnel_ctl, "_find_service", lambda: ("LANShareTunnel", "running"))
    monkeypatch.setattr(tunnel_ctl, "_service_state", lambda n: "stopped")
    monkeypatch.setattr(tunnel_ctl, "_process_running", lambda: (True, 999))
    monkeypatch.setattr(tunnel_ctl, "_run",
                        lambda args, timeout=20: calls.append(args) or FakeProc())
    res = tunnel_ctl.tunnel_stop()
    if res["method"]:
        assert res["method"] == "service", "有服务时必须走服务，不能直接杀进程"
        assert any("stop" in c for c in calls[0])


def test_tunnel_stop_no_tunnel(monkeypatch):
    monkeypatch.setattr(tunnel_ctl, "_find_service", lambda: ("", ""))
    monkeypatch.setattr(tunnel_ctl, "_process_running", lambda: (False, 0))
    res = tunnel_ctl.tunnel_stop()
    assert res["ok"] is False
    assert "没找到" in res["detail"] or "不支持" in res["detail"]


def test_tunnel_start_without_service_is_honest(monkeypatch):
    """没有服务形态时必须说实话，不能假装启动成功。"""
    monkeypatch.setattr(tunnel_ctl, "_find_service", lambda: ("", ""))
    monkeypatch.setattr(tunnel_ctl, "_process_running", lambda: (False, 0))
    res = tunnel_ctl.tunnel_start()
    if tunnel_ctl.sys.platform == "win32":
        assert res["ok"] is False
        assert "没有安装" in res["detail"] or "无法启动" in res["detail"]


def test_local_tunnel_endpoints_require_loopback(local_client):
    """隧道端点同样受 /local 守卫保护。"""
    assert local_client.get("/local/tunnel").status_code == 200


def test_local_endpoints_reject_public_host(monkeypatch):
    """/local/* 的 Host 守卫：Host 不是回环时必须 403（即使 IP 是回环）。"""
    from app.main import app as fastapi_app

    # IP 回环、Host 却是公网域名 —— 这模拟的是经隧道进来的公网请求
    c = TestClient(fastapi_app, client=("127.0.0.1", 51000),
                   base_url="http://lanshareapi.ak47zzq.com")
    assert c.get("/local/tunnel").status_code == 403
