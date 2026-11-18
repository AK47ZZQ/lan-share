"""公网域名功能的三轮对抗性测试。

A1 格式校验绕过  —— 用户输入是最不可信的入口，绕过一次就是 SSRF 或误放行
A2 同源校验伪造  —— 这是安全底线，绕过它 = App 连到别人的服务器
A3 隧道控制滥用  —— 会真的执行 sc / taskkill，误判就是误杀别人的服务

三轮都只打桩，不联网、不动真服务。
"""

import json

import pytest

from app import settings
from app.services import public_probe, tunnel_ctl


# ══════════════════════════════════════════════════════════
# A1：格式校验绕过
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("raw,why", [
    ("HTTPS://ok.example.com", "大写 scheme —— urlsplit 会保留原样，比较必须小写"),
    ("https://ok.example.com.", "FQDN 尾点，要规范化"),
    ("  https://ok.example.com  ", "前后空白"),
    ("https://ok.example.com\t", "尾部制表符"),
    ("https://ok.example.com\n", "尾部换行"),
])
def test_format_normalizes_benign_variants(raw, why):
    """这些是「看起来像绕过但其实是正常输入」，必须被规范化后接受。"""
    ok, detail, hint, host = public_probe.check_format(raw)
    assert ok, f"{why}: {detail} / {hint}"
    assert host == "ok.example.com"


@pytest.mark.parametrize("raw,why", [
    ("https://x.com:0443", "前导零端口 —— int('0443') 就是 443，语义上等价于不写端口"),
    ("https://x.com:443", "显式 443 是允许的"),
])
def test_format_accepts_equivalent_443(raw, why):
    """这些是「看着像绕过、其实等价于 443」的输入，该放行。"""
    ok, detail, hint, _host = public_probe.check_format(raw)
    assert ok, f"{why}: {detail} / {hint}"


@pytest.mark.parametrize("raw,why", [
    ("https://x.com:4430", "端口前缀相似"),
    ("https://x.com:44", "短端口"),
    ("https://user:pass@ok.example.com", "带 userinfo —— 凭据会被发出去，也是钓鱼形状"),
    ("https://ok.example.com#frag", "fragment"),
    ("https://ok.example.com/../evil", "路径穿越"),
    ("https://ok.example.com%2Fevil", "编码斜杠 —— host 里混进 %2f"),
    ("https://ok.example.com\\@evil.com", "反斜杠伪装（Win 风格）→ host 变成 evil.com"),
    ("https://" + "a" * 300 + ".com", "超长 label（DNS 上限 63）"),
    ("https://ok.example.com:99999", "越界端口 —— urlsplit 会抛 ValueError"),
])
def test_format_rejects_bypass_attempts(raw, why):
    """每条都必须拒绝。放过去一条，probe 就会向意外的地方发请求。"""
    ok, detail, hint, host = public_probe.check_format(raw)
    assert not ok, f"应拒绝但放行：{why} → host={host}"


def test_format_rejects_idn_without_crash():
    """中文域名不该让程序崩，只该被拒（暂不支持 IDN）。"""
    ok, detail, hint, _host = public_probe.check_format("https://例子.中国")
    assert isinstance(ok, bool)
    if not ok:
        assert detail or hint


def test_format_empty_and_none():
    for raw in ("", "   ", None):
        ok, detail, hint, host = public_probe.check_format(raw)
        assert not ok and hint and host == ""


# ══════════════════════════════════════════════════════════
# A2：同源校验伪造
# ══════════════════════════════════════════════════════════


def test_same_instance_rejects_malformed_body(monkeypatch):
    """返回体不是 JSON —— 对面不是 LANShare，必须拒绝且不抛异常。"""
    monkeypatch.setattr(public_probe, "_http_get",
                        lambda *a, **k: (200, b"<html>not json</html>", ""))
    ok, detail, hint = public_probe.check_same_instance("https://x.example.com")
    assert not ok
    assert "JSON" in detail or "LANShare" in hint


def test_same_instance_rejects_missing_field(monkeypatch):
    monkeypatch.setattr(public_probe, "_http_get",
                        lambda *a, **k: (200, json.dumps({"other": "x"}).encode(), ""))
    ok, detail, _hint = public_probe.check_same_instance("https://x.example.com")
    assert not ok
    assert "instance_id" in detail


def test_same_instance_rejects_empty_id(monkeypatch):
    monkeypatch.setattr(public_probe, "_http_get",
                        lambda *a, **k: (200, json.dumps({"instance_id": ""}).encode(), ""))
    ok, _detail, hint = public_probe.check_same_instance("https://x.example.com")
    assert not ok and hint


def test_same_instance_rejects_case_mismatch(monkeypatch):
    """UUID 大小写不同不算同一套 —— 说明对面在拼凑，不是真读取。"""
    local_id = settings.ensure_instance_id()
    monkeypatch.setattr(public_probe, "_http_get",
                        lambda *a, **k: (200, json.dumps(
                            {"instance_id": local_id.upper()}).encode(), ""))
    ok, detail, _hint = public_probe.check_same_instance("https://x.example.com")
    assert not ok
    assert "不一致" in detail


def test_same_instance_handles_redirect(monkeypatch):
    """3xx 必须被当成异常（_NoRedirect 不跟随），不能顺着跳到别处拿个 UUID 回来。"""
    monkeypatch.setattr(public_probe, "_http_get", lambda *a, **k: (302, b"", ""))
    ok, _detail, hint = public_probe.check_same_instance("https://x.example.com")
    assert not ok, "3xx 不该被当作通过"
    assert hint


def test_same_instance_handles_timeout(monkeypatch):
    monkeypatch.setattr(public_probe, "_http_get", lambda *a, **k: (0, b"", "timeout"))
    ok, detail, hint = public_probe.check_same_instance("https://x.example.com")
    assert not ok and hint


def test_http_get_never_returns_none(monkeypatch):
    """_http_get 的契约是永远返回三元组 —— 返回 None 会让调用方解包崩溃。"""
    # 无网络环境下打一个不可达地址，验证异常路径也有返回值
    status, body, err = public_probe._http_get(
        "https://203.0.113.1/api/instance-id", timeout=1)
    assert isinstance(status, int)
    assert isinstance(body, (bytes, bytearray))
    assert isinstance(err, str)


# ══════════════════════════════════════════════════════════
# A3：隧道控制滥用
# ══════════════════════════════════════════════════════════


class _FakeProc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def test_stop_does_not_touch_service_when_none(monkeypatch):
    """没有服务时不能瞎发 sc stop（会停掉同名的别人的服务）。"""
    calls = []
    monkeypatch.setattr(tunnel_ctl, "_find_service", lambda: ("", ""))
    monkeypatch.setattr(tunnel_ctl, "_process_running", lambda: (False, 0))
    monkeypatch.setattr(tunnel_ctl, "_run",
                        lambda args, timeout=20: calls.append(args) or _FakeProc())
    res = tunnel_ctl.tunnel_stop()
    assert res["ok"] is False
    assert calls == [], f"没有隧道时不该执行任何命令，实际执行了 {calls}"


def test_stop_service_failure_is_reported(monkeypatch):
    """sc stop 失败要如实上报，不能当作成功。"""
    monkeypatch.setattr(tunnel_ctl, "_find_service", lambda: ("LANShareTunnel", "running"))
    monkeypatch.setattr(tunnel_ctl, "_service_state", lambda n: "running")
    monkeypatch.setattr(tunnel_ctl, "_run", lambda args, timeout=20:
                        _FakeProc(rc=5, err="拒绝访问"))
    res = tunnel_ctl.tunnel_stop()
    assert res["ok"] is False
    assert "拒绝访问" in res["detail"] or "失败" in res["detail"]


def test_stop_verifies_state_after_command(monkeypatch):
    """下发了 stop 但状态没变，不能报成功。"""
    monkeypatch.setattr(tunnel_ctl, "_find_service", lambda: ("LANShareTunnel", "running"))
    monkeypatch.setattr(tunnel_ctl, "_service_state", lambda n: "running")  # 一直 running
    monkeypatch.setattr(tunnel_ctl, "_run", lambda args, timeout=20: _FakeProc())
    res = tunnel_ctl.tunnel_stop()
    assert res["ok"] is False, "状态没变却报成功"
    assert "running" in res["detail"] or "仍是" in res["detail"]


def test_kill_process_reports_irreversibility(monkeypatch):
    """杀掉前台进程后必须明说「程序没法帮你重新启动」。"""
    seq = {"n": 0}

    def fake_running():
        seq["n"] += 1
        return (seq["n"] == 1, 4321)   # 第一次在跑，之后没有

    monkeypatch.setattr(tunnel_ctl, "_find_service", lambda: ("", ""))
    monkeypatch.setattr(tunnel_ctl, "_process_running", fake_running)
    monkeypatch.setattr(tunnel_ctl, "_run", lambda args, timeout=15: _FakeProc())
    res = tunnel_ctl.tunnel_stop()
    if tunnel_ctl.sys.platform == "win32":
        assert res["ok"] is True
        assert res["method"] == "process"
        assert "无法" in res["detail"] and "重新启动" in res["detail"]


def test_start_refuses_when_no_service(monkeypatch):
    monkeypatch.setattr(tunnel_ctl, "_find_service", lambda: ("", ""))
    monkeypatch.setattr(tunnel_ctl, "_process_running", lambda: (False, 0))
    res = tunnel_ctl.tunnel_start()
    if tunnel_ctl.sys.platform == "win32":
        assert res["ok"] is False
        assert "没有安装" in res["detail"] or "无法启动" in res["detail"]


def test_service_state_parse_survives_localized_output(monkeypatch):
    """sc query 的输出在中文 Windows 上是本地化的 —— 只能靠 STATE 数字解析。"""
    chinese = (
        "SERVICE_NAME: LANShareTunnel\r\n"
        "        TYPE               : 10  WIN32_OWN_PROCESS\r\n"
        "        STATE              : 4  RUNNING\r\n"
        "                                (STOPPABLE, NOT_PAUSABLE)\r\n"
    )
    monkeypatch.setattr(tunnel_ctl, "_run",
                        lambda args, timeout=20: _FakeProc(rc=0, out=chinese))
    assert tunnel_ctl._service_state("LANShareTunnel") == "running"


def test_service_state_nonexistent(monkeypatch):
    """服务不存在时 sc 返回 1060，必须归到空串而不是 unknown。"""
    monkeypatch.setattr(tunnel_ctl, "_run", lambda args, timeout=20:
                        _FakeProc(rc=1060, err="[SC] OpenService 失败 1060:"))
    assert tunnel_ctl._service_state("NoSuchService") == ""


def test_status_never_crashes_on_any_platform():
    """status 是只读探测，任何平台都该返回结构化结果而不是抛异常。"""
    st = tunnel_ctl.tunnel_status()
    for key in ("supported", "service", "service_state", "process", "pid",
                "controllable", "note"):
        assert key in st, f"status 缺字段 {key}"


def test_probe_env_items_never_block(monkeypatch):
    """环境检测无论怎么坏，都不许让 probe.ok 变 false（否则别的机器的合法部署被拦）。"""
    local_id = settings.ensure_instance_id()
    # 解析本身必须是成功的（非公网地址是阻塞项，见下一条），
    # 只把「诊断版」的 check_dns 打桩成失败来模拟不走 Cloudflare。
    monkeypatch.setattr(public_probe, "_resolve_public_ips",
                        lambda h: (["203.0.113.1"], ""))
    monkeypatch.setattr(public_probe, "check_dns", lambda h: (False, "解析失败", "x"))
    monkeypatch.setattr(
        public_probe, "_http_get",
        lambda url, timeout=15: (200, json.dumps(
            {"instance_id": local_id}).encode(), "") if "instance-id" in url
        else (200, b"{}", ""))
    monkeypatch.setattr(public_probe, "_find_cloudflared", lambda: "")
    monkeypatch.setattr(public_probe, "_windows_task_running", lambda img: True)

    result = public_probe.probe("https://lanshareapi.example.com")
    assert result["ok"] is True, f"环境/诊断问题不该拦下本次启用: {result}"


def test_probe_blocks_when_domain_points_internal(monkeypatch):
    """解析到内网/回环 = SSRF，必须**阻塞**失败。

    对抗测试实测：`https://127.0.0.1.nip.io` 这类域名格式合法（不是 IP 字面量），
    但解析到 127.0.0.1 —— 放过去就等于把内网扫描能力交给调用方。
    """
    monkeypatch.setattr(public_probe, "_resolve_public_ips",
                        lambda h: ([], "解析到非公网地址：127.0.0.1"))

    result = public_probe.probe("https://127.0.0.1.nip.io")
    assert result["ok"] is False
    assert "非公网" in result["hint"]
    # 必须停在解析这一步，不能再往后发 HTTP 请求
    assert [s["key"] for s in result["steps"]] == ["format", "dns"]
