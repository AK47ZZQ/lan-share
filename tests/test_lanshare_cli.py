"""lanshare_cli 的单元测试。

设计原则：
    所有测试都**不碰真实服务、不用管理员权限、不写系统目录**。
    nssm 调用一律 mock，路径指向 tmp_path。

这层能单测，正是"把服务编排从安装脚本搬进 Python"的核心收益 ——
安装脚本里那些引号/转义/编码问题，在安装时才暴露；这里的逻辑在 CI 里就能验证。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lanshare_cli import caddyfile
from lanshare_cli import cli
from lanshare_cli.service import NssmService, ServiceError, _decode


# ──────────────────────────────────────────────────────────────
# Caddyfile 渲染
# ──────────────────────────────────────────────────────────────


class TestCaddyfile:
    def test_renders_both_ports(self):
        text = caddyfile.render(api_port=9911, web_port=9912)
        assert ":9912 {" in text
        assert "reverse_proxy 127.0.0.1:9911" in text

    def test_no_hardcoded_default_ports(self):
        """换端口时不能残留默认值 —— 这是之前 PowerShell 版踩过的坑。"""
        text = caddyfile.render(api_port=9911, web_port=9912)
        assert "8766" not in text
        assert "8765" not in text

    def test_caddy_placeholders_are_literal(self):
        """Caddy 的 {host} / {remote_host} 不能被模板引擎吃掉。"""
        text = caddyfile.render(api_port=8000, web_port=8001)
        assert "{host}" in text
        assert "{remote_host}" in text

    def test_http_only_no_tls(self):
        """默认不启用 TLS。tls internal 对无主机名站点会握手失败（实测）。"""
        text = caddyfile.render(api_port=8000, web_port=8001)
        assert "tls internal" not in text
        assert "auto_https off" in text

    def test_forwarded_proto_is_http(self):
        """后端要知道这是 HTTP，否则 Cookie 会加 Secure 位导致内网登不上。"""
        text = caddyfile.render(api_port=8000, web_port=8001)
        assert "header_up X-Forwarded-Proto http" in text

    @pytest.mark.parametrize("bad", [0, -1, 65536, 99999])
    def test_rejects_out_of_range(self, bad):
        with pytest.raises(ValueError, match="越界"):
            caddyfile.render(api_port=bad, web_port=9000)

    def test_rejects_same_port(self):
        with pytest.raises(ValueError, match="不能相同"):
            caddyfile.render(api_port=9000, web_port=9000)

    def test_write_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "conf" / "Caddyfile"
        caddyfile.write(target, api_port=9110, web_port=9111)
        assert target.is_file()
        assert "9111" in target.read_text(encoding="utf-8")

    def test_write_uses_lf(self, tmp_path):
        """LF 换行，避免 CRLF 在 diff 里制造噪音。"""
        target = tmp_path / "Caddyfile"
        caddyfile.write(target, api_port=9110, web_port=9111)
        raw = target.read_bytes()
        assert b"\r\n" not in raw


# ──────────────────────────────────────────────────────────────
# 输出解码
# ──────────────────────────────────────────────────────────────


class TestDecode:
    def test_utf8(self):
        assert _decode("服务已安装".encode("utf-8")) == "服务已安装"

    def test_gbk(self):
        """中文 Windows 上 nssm 的输出可能是 GBK。"""
        assert _decode("服务已安装".encode("gbk")) == "服务已安装"

    def test_utf16_strips_nulls(self):
        assert _decode("Service installed".encode("utf-16-le")) == "Service installed"

    def test_empty(self):
        assert _decode(b"") == ""


# ──────────────────────────────────────────────────────────────
# NssmService
# ──────────────────────────────────────────────────────────────


class TestNssmServiceInit:
    def test_missing_nssm_raises(self, tmp_path):
        with pytest.raises(ServiceError, match="不存在"):
            NssmService(tmp_path / "nope.exe")

    def test_valid_path(self, tmp_path):
        fake = tmp_path / "nssm.exe"
        fake.write_bytes(b"MZ")
        svc = NssmService(fake)
        assert svc.nssm.endswith("nssm.exe")


class TestNssmServiceInstall:
    """install() 的参数拼装是重点 —— 之前所有引号问题都出在这一步。"""

    @pytest.fixture
    def svc(self, tmp_path):
        fake = tmp_path / "nssm.exe"
        fake.write_bytes(b"MZ")
        return NssmService(fake)

    @pytest.fixture
    def exe(self, tmp_path):
        e = tmp_path / "python.exe"
        e.write_bytes(b"MZ")
        return e

    def test_env_vars_passed_as_separate_args(self, svc, exe, monkeypatch):
        """关键回归测试：多个环境变量必须作为独立参数传，不能拼成一个字符串。

        拼成一个字符串时 NSSM 会把整串当成单个变量的值，Caddy 永远读到默认端口。
        """
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return ""

        monkeypatch.setattr("lanshare_cli.service.run", fake_run)
        monkeypatch.setattr(svc, "exists", lambda name: False)

        svc.install(
            "TestSvc",
            exe,
            args=["server.py"],
            env={"A": "1", "B": "2", "C": "3"},
        )

        env_calls = [c for c in calls if "AppEnvironmentExtra" in c]
        assert len(env_calls) == 1
        cmd = env_calls[0]
        # ["nssm","set","TestSvc","AppEnvironmentExtra","A=1","B=2","C=3"]
        assert cmd[-3:] == ["A=1", "B=2", "C=3"]
        assert not any(" " in item and "=" in item for item in cmd[-3:]), (
            "环境变量被拼成了一个字符串，NSSM 会当成单个变量"
        )

    def test_install_always_includes_exe(self, svc, exe, monkeypatch):
        """nssm install 不带 exe 会弹出 GUI 窗口，安装器就永远等下去。"""
        calls = []
        monkeypatch.setattr("lanshare_cli.service.run", lambda cmd, **kw: calls.append(cmd) or "")
        monkeypatch.setattr(svc, "exists", lambda name: False)
        monkeypatch.setattr(svc, "_set", lambda *a, **kw: None)

        svc.install("TestSvc", exe)

        install_calls = [c for c in calls if len(c) > 1 and c[1] == "install"]
        assert len(install_calls) == 1
        assert len(install_calls[0]) >= 4, "install 命令必须带 application 路径"
        assert "python.exe" in install_calls[0][3]

    def test_path_with_spaces_survives(self, svc, tmp_path, monkeypatch):
        """带空格路径必须原样传过去 —— 这是 'open C:\\Program' 那个坑的回归测试。"""
        spaced_dir = tmp_path / "Program Files" / "LAN Share"
        spaced_dir.mkdir(parents=True)
        exe = spaced_dir / "python.exe"
        exe.write_bytes(b"MZ")

        calls = []
        monkeypatch.setattr("lanshare_cli.service.run", lambda cmd, **kw: calls.append(cmd) or "")
        monkeypatch.setattr(svc, "exists", lambda name: False)
        monkeypatch.setattr(svc, "_set", lambda *a, **kw: None)

        svc.install("TestSvc", exe, cwd=spaced_dir)

        install_cmd = [c for c in calls if len(c) > 1 and c[1] == "install"][0]
        # 列表传参时路径是一整个元素，不会被空格切开
        assert install_cmd[3].endswith("Program Files\\LAN Share\\python.exe")

    def test_app_parameters_get_quoted(self, svc, tmp_path, monkeypatch):
        """回归：AppParameters 里带空格的路径必须带引号。

        NSSM 把 AppParameters 当命令行喂给程序。如果只是 " ".join(args)，
        "C:\\Program Files\\x\\server.py" 会被切成 "C:\\Program" —— 服务启动即崩，
        报 `can't open file 'C:\\Program'`。必须用 subprocess.list2cmdline。
        """
        spaced_dir = tmp_path / "Program Files" / "LAN Share"
        spaced_dir.mkdir(parents=True)
        exe = spaced_dir / "python.exe"
        exe.write_bytes(b"MZ")
        entry = spaced_dir / "server.py"
        entry.write_text("# entry", encoding="utf-8")

        captured = {}

        def fake_set(name, param, value):
            captured[param] = value

        monkeypatch.setattr("lanshare_cli.service.run", lambda cmd, **kw: "")
        monkeypatch.setattr(svc, "exists", lambda name: False)
        monkeypatch.setattr(svc, "_set", fake_set)

        svc.install("TestSvc", exe, args=[str(entry)])

        params = captured.get("AppParameters", "")
        assert params.startswith('"'), f"路径没有加引号: {params}"
        assert params.endswith('"'), f"路径没有加引号: {params}"
        assert "Program Files" in params

    def test_refuses_existing_service_without_force(self, svc, exe, monkeypatch):
        """默认拒绝覆盖。误改生产服务的 AppDirectory 就是这么发生的。"""
        monkeypatch.setattr(svc, "exists", lambda name: True)
        with pytest.raises(ServiceError, match="已存在"):
            svc.install("TestSvc", exe)

    def test_force_removes_then_installs(self, svc, exe, monkeypatch):
        removed = []
        monkeypatch.setattr(svc, "exists", lambda name: True)
        monkeypatch.setattr(svc, "remove", lambda name, **kw: removed.append(name))
        monkeypatch.setattr("lanshare_cli.service.run", lambda cmd, **kw: "")
        monkeypatch.setattr(svc, "_set", lambda *a, **kw: None)

        svc.install("TestSvc", exe, force=True)
        assert removed == ["TestSvc"]

    def test_missing_exe_raises(self, svc, tmp_path, monkeypatch):
        monkeypatch.setattr(svc, "exists", lambda name: False)
        with pytest.raises(ServiceError, match="服务程序不存在"):
            svc.install("TestSvc", tmp_path / "ghost.exe")


class TestNssmServiceRun:
    def test_timeout_message_mentions_gui(self, monkeypatch):
        """超时错误要指向真实原因（nssm 无参弹 GUI），别让人对着 exit code 猜。"""
        def boom(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1)

        monkeypatch.setattr(subprocess, "run", boom)
        from lanshare_cli.service import run as real_run

        with pytest.raises(ServiceError, match="GUI"):
            real_run(["nssm", "install"])

    def test_missing_binary(self, monkeypatch):
        def boom(cmd, **kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr(subprocess, "run", boom)
        from lanshare_cli.service import run as real_run

        with pytest.raises(ServiceError, match="找不到可执行文件"):
            real_run([r"C:\nope\nssm.exe"])


# ──────────────────────────────────────────────────────────────
# CLI 辅助函数
# ──────────────────────────────────────────────────────────────


class TestPortInUse:
    def test_free_port(self):
        assert cli.port_in_use(59999) is False

    def test_occupied_port(self):
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            assert cli.port_in_use(port) is True


class TestDefaultDataDir:
    def test_under_programdata(self, monkeypatch):
        monkeypatch.setenv("PROGRAMDATA", r"D:\PD")
        assert cli.default_data_dir() == Path(r"D:\PD") / "LANShare" / "data"


class TestParser:
    def test_install_flags(self):
        parser = cli.build_parser()
        args = parser.parse_args(
            ["--api-port", "9911", "--web-port", "9912",
             "--service-prefix", "TestShare", "install", "--dry-run"]
        )
        assert args.command == "install"
        assert args.api_port == 9911
        assert args.web_port == 9912
        assert args.service_prefix == "TestShare"
        assert args.dry_run is True

    def test_uninstall_purge_flag(self):
        parser = cli.build_parser()
        args = parser.parse_args(["uninstall", "--purge-data"])
        assert args.purge_data is True
        assert args.command == "uninstall"


class TestLayout:
    def test_service_names_use_prefix(self, tmp_path):
        layout = cli.Layout(tmp_path, tmp_path / "data", "TestShare")
        assert layout.api_service == "TestShare-api"
        assert layout.caddy_service == "TestShare-caddy"

    def test_required_files_count(self, tmp_path):
        layout = cli.Layout(tmp_path, tmp_path / "data", "X")
        # python / nssm / caddy / server.py
        assert len(layout.required_files()) == 4
        # app / web/dist / static
        assert len(layout.required_dirs()) == 3
