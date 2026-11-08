"""lanshare_cli 的单元测试。

设计原则：
    所有测试都**不碰真实服务、不用管理员权限、不写系统目录**。
    nssm 调用一律 mock，路径指向 tmp_path。

这层能单测，正是"把服务编排从安装脚本搬进 Python"的核心收益 ——
安装脚本里那些引号/转义/编码问题，在安装时才暴露；这里的逻辑在 CI 里就能验证。
"""

from __future__ import annotations

import os

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
        # 站点块最后一个地址是隧道端口，所以不能断言「行尾是 :9912 {」
        assert "https://localhost:9912" in text
        assert "reverse_proxy 127.0.0.1:9911" in text

    def test_listens_on_tunnel_port_too(self):
        """隧道端口也必须在站点地址里。

        内网穿透把公网流量转到本机的哪个端口，是用户在穿透面板里填的
        （历史文档里写过 8767）。Caddy 不在那个端口监听，frpc 就会
        「连接被拒绝」，用户从公网看到的是打不开 —— 2026-11-04 实机。
        """
        text = caddyfile.render(api_port=8766, web_port=8765)
        site_line = [ln for ln in text.splitlines() if ln.startswith("https://")][0]
        assert f":{caddyfile.TUNNEL_PORT} " in site_line
        assert f"https://localhost:{caddyfile.TUNNEL_PORT}" in site_line
        # 同一个站点块：TLS 配置、反向代理都共用，不是开了第二个入口
        assert site_line.count("{") == 1

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

    def test_https_with_internal_ca(self):
        """默认走 HTTPS + Caddy 内部 CA。

        2026-10-31 修正：之前判定「tls internal 对无主机名站点握手失败」
        并退回纯 HTTP，其实是开关用错了 —— auto_https off 会把证书签发
        一起关掉。改 disable_redirects 后 tls internal 正常，且签出的证书
        含 IP SAN（实测 IP Address:192.168.1.100）。

        为什么必须 HTTPS：手机端扫码只收 https（http 二维码 = 明文传密码），
        生成 HTTP 站点的话二维码是 http://...，App 会静默忽略。
        """
        text = caddyfile.render(api_port=8000, web_port=8001)
        assert "issuer internal" in text
        # 站点证书不能再用 Caddy 的 12 小时默认值：服务被停过、重装过就可能
        # 处在「已过期但没人续」的状态，客户端只看到「连不上」。
        # 注意也不能写成 `tls internal { issuer internal { ... } }` ——
        # Caddy 会报 cannot mix issuer subdirective。
        assert "lifetime 720h" in text
        assert "auto_https disable_redirects" in text
        # 生效的指令不能是 off（模板注释里提到它不算）
        assert "\tauto_https off\n" not in text

    def test_site_block_has_scheme_and_host(self):
        """站点地址必须带 https:// 和主机名。

        裸 `:8765` 会被 Caddy 当成 HTTP 站点，tls internal 就不生效。
        """
        text = caddyfile.render(
            api_port=8000, web_port=8001, hosts=["localhost", "192.168.1.100"]
        )
        assert "https://localhost:8001" in text
        assert "https://192.168.1.100:8001" in text
        # 不能出现裸端口站点块
        assert "\n:8001 {" not in text

    def test_forwarded_proto_is_https(self):
        """后端要知道这是 HTTPS，Cookie 才会加 Secure 位。"""
        text = caddyfile.render(api_port=8000, web_port=8001)
        assert "header_up X-Forwarded-Proto https" in text

    def test_loopback_bind_still_works(self):
        """LAN 关闭时 bind 127.0.0.1 仍然生效（主机名和 bind 可以共存）。"""
        text = caddyfile.render(
            api_port=8000, web_port=8001, bind_host=caddyfile.BIND_LOOPBACK
        )
        assert "bind 127.0.0.1" in text
        assert "issuer internal" in text

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
        # 列表传参时路径是一整个元素，不会被空格切开。
        # 期望值必须用 os.path.join 拼 —— 硬写反斜杠在 Linux CI 上必挂
        # （路径分隔符不同，endswith 永远 False）。
        assert install_cmd[3].endswith(
            os.path.join("Program Files", "LAN Share", "python.exe"))

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
        """空闲端口要判成空闲。

        ⚠️ 不能用固定端口（原来写死 59999）：Windows 的动态端口保留段
        （Hyper-V / WSL 会占掉成片区间）里 bind 会直接 EACCES，被误判成
        「占用」，测试在那种机器上必然假红 —— 2026-11-03 本机正是如此
        （59998/59999 都在保留段里，bind 报 errno 13）。
        改成让系统临时分配一个再释放：它一定不在保留段。
        """
        import socket

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        finally:
            probe.close()
        assert cli.port_in_use(port) is False

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
        # python / nssm / caddy / server.py / initdb / pg_ctl
        # 后两个是 PostgreSQL 二进制。没有 psql —— 随包带的是 zonky 精简版，
        # 只含 postgres/initdb/pg_ctl，建库走 psycopg。
        assert len(layout.required_files()) == 6
        # app / web/dist / static
        assert len(layout.required_dirs()) == 3

# ──────────────────────────────────────────────────────────────
# 证书信任
# ──────────────────────────────────────────────────────────────

class TestTrustLocalCa:
    """trust_local_ca 把本地 CA 装进 Windows 信任链。

    为什么值得单测：这一步失败的表现极其隐蔽 —— 安装全部 "ok"、
    服务全部 Running、healthz 200，唯独用户点「打开网盘」看到
    ERR_CERT_AUTHORITY_INVALID 红色警告页，以为整个产品坏了。
    """

    def test_prefers_caddy_pki_root(self, tmp_path, monkeypatch):
        """tls internal 模式优先装 Caddy 自己的根证书。"""
        root_crt = (tmp_path / "certs" / "caddy" / "pki"
                    / "authorities" / "local" / "root.crt")
        root_crt.parent.mkdir(parents=True)
        root_crt.write_text("dummy", encoding="utf-8")

        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        layout = cli.Layout(tmp_path, tmp_path / "data", "X")

        assert cli.trust_local_ca(layout) is True
        assert calls and str(root_crt) in calls[0]

    def test_falls_back_to_manual_cert(self, tmp_path, monkeypatch):
        """不做 tls internal 时（用户自备证书），自签证书自己就是 CA。"""
        manual = tmp_path / "certs" / "cert.pem"
        manual.parent.mkdir(parents=True)
        manual.write_text("dummy", encoding="utf-8")

        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        layout = cli.Layout(tmp_path, tmp_path / "data", "X")

        assert cli.trust_local_ca(layout) is True
        assert str(manual) in calls[0]

    def test_returns_false_when_nothing_found(self, tmp_path, monkeypatch):
        """一个证书文件都没有时不能崩，只警告。"""
        monkeypatch.setattr(
            cli.subprocess, "run",
            lambda *a, **k: pytest.fail("不该调用 certutil"),
        )
        layout = cli.Layout(tmp_path, tmp_path / "data", "X")
        assert cli.trust_local_ca(layout) is False

    def test_certutil_failure_is_not_fatal(self, tmp_path, monkeypatch):
        """certutil 返回非 0（比如权限不够）不能把安装搞中断。"""
        manual = tmp_path / "certs" / "cert.pem"
        manual.parent.mkdir(parents=True)
        manual.write_text("dummy", encoding="utf-8")

        monkeypatch.setattr(
            cli.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "拒绝访问"),
        )
        layout = cli.Layout(tmp_path, tmp_path / "data", "X")
        assert cli.trust_local_ca(layout) is False

    def test_certutil_missing_binary_is_not_fatal(self, tmp_path, monkeypatch):
        """certutil 不存在（极端精简系统）也不能崩。"""
        manual = tmp_path / "certs" / "cert.pem"
        manual.parent.mkdir(parents=True)
        manual.write_text("dummy", encoding="utf-8")

        def boom(*a, **k):
            raise OSError("certutil not found")

        monkeypatch.setattr(cli.subprocess, "run", boom)
        layout = cli.Layout(tmp_path, tmp_path / "data", "X")
        assert cli.trust_local_ca(layout) is False

class TestSecureDataDir:
    """initdb 要求数据目录 owner 是当前用户、无继承 ACL。

    C:\Program Files 下两条都不满足 → initdb Permission denied →
    安装停在 [7/10]，文件都就位但服务一个没起。
    这个 bug 只在干净环境暴露（开发目录恰好 ACL 干净），沙箱一测就现原形。
    """

    def _pg(self, tmp_path):
        from lanshare_cli.pg import Postgres
        return Postgres(root=tmp_path, data_dir=tmp_path / "pgdata",
                        port=5433, password="x")

    def test_noop_on_non_windows(self, tmp_path, monkeypatch):
        import sys as _sys
        # 只改 sys.platform：改 os.name 会把 pathlib 也带坏
        monkeypatch.setattr(_sys, "platform", "linux")
        called = []
        pg = self._pg(tmp_path)
        monkeypatch.setattr(pg, "_run", lambda *a, **k: called.append(a))
        pg._secure_data_dir()
        assert not called, "非 Windows 不该调 icacls"

    def test_noop_without_username(self, tmp_path, monkeypatch):
        import sys as _sys
        monkeypatch.setattr(_sys, "platform", "win32")
        monkeypatch.delenv("USERNAME", raising=False)
        called = []
        pg = self._pg(tmp_path)
        monkeypatch.setattr(pg, "_run", lambda *a, **k: called.append(a))
        pg._secure_data_dir()
        assert not called

    def test_runs_icacls_twice_on_windows(self, tmp_path, monkeypatch):
        import sys as _sys
        monkeypatch.setattr(_sys, "platform", "win32")
        monkeypatch.setenv("USERNAME", "tester")
        calls = []
        pg = self._pg(tmp_path)
        monkeypatch.setattr(pg, "_run",
                            lambda cmd, **k: calls.append(cmd) or
                            type("R", (), {"returncode": 0})())
        pg._secure_data_dir()
        assert len(calls) == 2
        # 第一条必须同时包含断继承和授权
        assert "/inheritance:r" in calls[0]
        assert "/grant:r" in calls[0]
        # 第二条改 owner
        assert "/setowner" in calls[1]

    def test_failure_is_swallowed(self, tmp_path, monkeypatch):
        """icacls 不可用时不能把安装搞崩 —— 让 initdb 去报它自己的错。"""
        import sys as _sys
        monkeypatch.setattr(_sys, "platform", "win32")
        monkeypatch.setenv("USERNAME", "tester")
        pg = self._pg(tmp_path)

        def boom(*a, **k):
            raise OSError("icacls not found")

        monkeypatch.setattr(pg, "_run", boom)
        pg._secure_data_dir()   # 不该抛

    def test_grant_service_access_adds_system(self, tmp_path, monkeypatch):
        """initdb 之后要把权限给 LocalSystem，否则服务起不来。

        两难的顺序：
          收紧 → initdb 能跑 → 服务 STOPPED
          不收 → initdb Permission denied
        所以是两步，不是一步。
        """
        import sys as _sys
        monkeypatch.setattr(_sys, "platform", "win32")
        calls = []
        pg = self._pg(tmp_path)
        monkeypatch.setattr(pg, "_run",
                            lambda cmd, **k: calls.append(cmd) or
                            type("R", (), {"returncode": 0})())
        pg._grant_service_access()
        assert len(calls) == 1
        joined = " ".join(calls[0])
        assert "SYSTEM" in joined
        assert "/grant" in joined

    def test_grant_noop_on_non_windows(self, tmp_path, monkeypatch):
        import sys as _sys
        monkeypatch.setattr(_sys, "platform", "linux")
        calls = []
        pg = self._pg(tmp_path)
        monkeypatch.setattr(pg, "_run", lambda *a, **k: calls.append(a))
        pg._grant_service_access()
        assert not calls

    def test_grant_failure_swallowed(self, tmp_path, monkeypatch):
        import sys as _sys
        monkeypatch.setattr(_sys, "platform", "win32")
        pg = self._pg(tmp_path)

        def boom(*a, **k):
            raise OSError("icacls missing")

        monkeypatch.setattr(pg, "_run", boom)
        pg._grant_service_access()


class TestSayNeverCrashes:
    """say() 崩掉会把真正的错误盖掉。

    实测：initdb 报 Permission denied 时，输出的字节解不出来成了 U+FFFD，
    再 print 就抛 UnicodeEncodeError —— 用户看到的是编码错误，
    完全不知道是权限问题，白白多排查一轮。
    """

    def test_survives_unencodable_char(self, capsys, monkeypatch):
        from lanshare_cli import cli

        # 模拟 GBK 编码失败：把 print 换成会抛的
        real_print = print

        def fake_print(*args, **kwargs):
            raise UnicodeEncodeError("gbk", "\ufffd", 0, 1, "illegal")

        monkeypatch.setattr("builtins.print", fake_print)
        cli.say("initdb 失败：\ufffd\ufffd Permission denied")   # 不该抛
        monkeypatch.setattr("builtins.print", real_print)

    def test_normal_output_still_works(self, capsys):
        from lanshare_cli import cli
        cli.say("普通输出")
        assert "普通输出" in capsys.readouterr().out

class TestReadDataDirFromRegistry:
    """卸载时不能靠默认路径猜数据在哪。

    真机事故链条：用户在向导里把安装目录改到 D 盘 → 数据目录页还留着
    自动填的 C 盘值 → 注册表 DataFolder 记的是 D 盘、但 [Dirs] 段照旧在
    C:\ProgramData 建目录 → 卸载时 CLI 用 default_data_dir() 去删，
    真数据原封不动。「卸载只删了一部分」有一半是这个。
    """

    def test_reads_value_from_registry(self, monkeypatch):
        import lanshare_cli.cli as cli

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake_winreg = type("W", (), {
            "HKEY_LOCAL_MACHINE": "HKLM",
            "OpenKey": staticmethod(lambda root, path: FakeKey()),
            "QueryValueEx": staticmethod(lambda k, name: (r"D:\LANShare\data", 1)),
        })
        monkeypatch.setitem(__import__("sys").modules, "winreg", fake_winreg)
        got = cli._read_data_dir_from_registry()
        assert got is not None
        assert "LANShare" in str(got)

    def test_missing_key_returns_none(self, monkeypatch):
        import lanshare_cli.cli as cli

        def boom(*a, **k):
            raise OSError("no such key")

        fake_winreg = type("W", (), {
            "HKEY_LOCAL_MACHINE": "HKLM",
            "OpenKey": staticmethod(boom),
        })
        monkeypatch.setitem(__import__("sys").modules, "winreg", fake_winreg)
        assert cli._read_data_dir_from_registry() is None


class TestKillByName:
    def test_only_targets_named_process(self, monkeypatch):
        """只按具体进程名杀，不做通配清场 —— 通配会把无关程序一起干掉。"""
        import lanshare_cli.cli as cli

        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        monkeypatch.setattr(cli.time, "sleep", lambda s: None)
        cli._kill_by_name("caddy")

        assert seen, "应该调用一次 powershell"
        joined = " ".join(seen[0])
        assert "-Name 'caddy'" in joined
        assert "Stop-Process" in joined
        # 绝不能出现通配符
        assert "*" not in joined

    def test_failure_is_swallowed(self, monkeypatch):
        import lanshare_cli.cli as cli

        def boom(*a, **k):
            raise OSError("powershell missing")

        monkeypatch.setattr(cli.subprocess, "run", boom)
        cli._kill_by_name("caddy")   # 不该抛


class TestServicePrefixValidation:
    """服务名前缀会拼进 Windows 服务名与注册表 PowerShell 脚本 ——
    必须白名单校验；注册表值写入要对 PowerShell 单引号转义。"""

    def test_rejects_quote_in_prefix(self):
        with pytest.raises(ValueError) as e:
            cli.resolve_prefix("Wei's Server")
        assert "前缀" in str(e.value)

    def test_rejects_shell_metachars(self):
        for bad in ("a;b", "a b", "$(x)", "a\nb", "a;b'", 'a"b'):
            with pytest.raises(ValueError):
                cli.resolve_prefix(bad)

    def test_accepts_normal_prefixes(self):
        assert cli.resolve_prefix("lanshare") == "lanshare"
        assert cli.resolve_prefix("lan-share") == "lan-share"
        assert cli.resolve_prefix(None) == cli.DEFAULT_SERVICE_PREFIX

    def test_registry_value_escapes_single_quotes(self, monkeypatch):
        """值里的单引号必须双写转义 —— 否则 Set-ItemProperty 的字符串
        断裂，写进去的是半截（或成为注入面）。"""
        captured = {}

        def fake_run(cmd, **kw):
            captured["script"] = cmd[3] if len(cmd) > 3 else ""
            class _R:
                returncode = 0
            return _R()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        cli.write_registry_value("ServicePrefix", "Wei's Server", "LANShare")
        assert "'Wei''s Server'" in captured["script"], captured["script"]


# ── PG 口令文件 ACL（2026-11-09 值守批 · P1 实证钉）────────────────


@pytest.mark.skipif(os.name != "nt", reason="icacls 是 Windows 命令")
def test_lock_pgpass_acl_removes_users_access(tmp_path):
    """lock_pgpass_acl 必须让本机 Users 组读不到 PG 口令文件。

    实证背景：生产 .lanshare-pgpass 的继承 ACL 是 Users:(M) +
    Authenticated Users:(M) —— os.chmod(0o600) 在 Windows 不生效，本机
    任意账号可读 PG 角色口令（→ 5433 全库读写）。本钉真跑 icacls 于
    tmp 文件（无需管理员：文件属主是测试用户），断言收紧后输出里
    不再有 Users / Authenticated Users，且 SYSTEM/Administrators 在。
    回退 lock_pgpass_acl 内的 icacls 调用 → 本钉红。
    """
    import subprocess

    from lanshare_cli.cli import lock_pgpass_acl

    # 先造出生产的暴露形态（tmp 默认 ACL 本就干净，收不收紧分不出红绿）：
    # 对父目录授 Users:(OI)(CI)M（*S-1-5-32-545 = Users，SID 防组名本地化），
    # 文件自然**继承**带 (I) 标记的 Users:(M) —— 与生产继承链同构。
    # ⚠ 不能对文件显式 /grant Users:M：lock 的 /inheritance:r 只清除
    # 继承 ACE，显式 ACE 会残留 → 那样造出的暴露态修不掉，钉恒红。
    subprocess.run(["icacls", str(tmp_path), "/grant", "*S-1-5-32-545:(OI)(CI)M"],
                   capture_output=True, timeout=60)
    f = tmp_path / ".lanshare-pgpass"
    f.write_text("test-password", encoding="utf-8")

    lock_pgpass_acl(f)

    raw = subprocess.run(["icacls", str(f)], capture_output=True).stdout or b""
    out = raw.decode("gbk", errors="replace")   # icacls 输出是 GBK（utf-8 解码崩）
    # "Users:(" 只出现在 ACE 行（路径行是 C:\Users\... 不带 ":("）——
    # icacls 的第一个 ACE 与文件路径同行，不能按行粗暴切片
    assert not any("Users:(" in ln for ln in out.splitlines()), \
        f"Users 仍可访问 PG 口令文件：{out}"
    assert "SYSTEM:(" in out, f"SYSTEM 授权缺失：{out}"
    assert "Administrators:(" in out, f"Administrators 授权缺失：{out}"
    # 文件内容不受影响
    assert f.read_text(encoding="utf-8") == "test-password"


@pytest.mark.skipif(os.name != "nt", reason="icacls 是 Windows 命令")
def test_api_service_env_keeps_password_out_of_registry(tmp_path):
    """API 服务环境变量不得携带带口令的连接串（2026-11-09 P1 钉）。

    NSSM 把 env 存 HKLM 服务键 Parameters\AppEnvironmentExtra，
    ACL 实测 Users: ReadKey —— 本机任意用户可读带口令的 DATABASE_URL。
    修复后 env 只带 LANSHARE_PG_URL_FILE 路径，连接串落
    .lanshare-dburl（icacls 收紧）。回退（env 重新塞回 DATABASE_URL）
    → 本钉红。"""
    from lanshare_cli import cli as cli_mod

    calls: dict = {}

    class _FakePg:
        def connection_url(self):
            return "postgresql://lanshare:secret-pw@127.0.0.1:5433/lanshare"

    layout = type("L", (), {"data_dir": tmp_path})()
    env = cli_mod._api_service_env(layout, 8766, _FakePg())
    # 1) env 不得含 DATABASE_URL（除非文件方案失败回退）
    assert "DATABASE_URL" not in env, \
        f"带口令连接串仍在服务环境变量里: {sorted(env)}"
    # 2) env 带文件路径
    assert env.get("LANSHARE_PG_URL_FILE") == str(tmp_path / ".lanshare-dburl")
    # 3) 文件存在且收紧（真 icacls 验证）
    f = tmp_path / ".lanshare-dburl"
    assert f.is_file()
    raw = subprocess.run(["icacls", str(f)], capture_output=True).stdout or b""
    out = raw.decode("gbk", errors="replace")
    assert not any("Users:(" in ln for ln in out.splitlines()), out
    # 4) 文件内容 = 完整连接串（app 端能读回）
    assert f.read_text(encoding="utf-8") == "postgresql://lanshare:secret-pw@127.0.0.1:5433/lanshare"
