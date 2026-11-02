"""诊断包的正确性。

这份东西是用户在**出故障时**点出来的。它自己崩了、或者报个假的病因，
比没有更糟——用户会照着错误结论去删文件。所以这里重点锁三件事：

1. 任何一项检查失败都不许让整份报告崩
2. 口令必须被隐藏（这个文件是要发给别人的）
3. PG 锁文件/端口的判定不能误报（正常运行 vs 真的坏了）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanshare_cli import diagnose as diag


class _FakeLayout:
    """最小可用的 Layout 替身，避免依赖真实安装。"""

    def __init__(self, root: Path, data: Path, prefix: str = "lanshare"):
        self.root = root
        self.data_dir = data
        self.service_prefix = prefix
        self.api_port = 8766
        self.web_port = 8765
        self.api_service = f"{prefix}-api"
        self.caddy_service = f"{prefix}-caddy"
        self.pg_service = f"{prefix}-postgresql"

    def required_files(self):
        return [(self.root / "server.py", "server.py")]

    def required_dirs(self):
        return [(self.root / "app", "后端代码 app/")]


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "root"
    data = tmp_path / "data"
    root.mkdir()
    data.mkdir()
    return _FakeLayout(root, data)


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """别在测试里真跑 PowerShell/netstat —— 慢且平台相关。"""
    monkeypatch.setattr(diag, "_run", lambda cmd, timeout=12.0: "(stub)")
    monkeypatch.setattr(diag, "_ps", lambda script, timeout=15.0: "(stub)")


class TestRedaction:
    def test_hides_password(self):
        assert diag._redact("password=hunter2") == "password=<已隐藏>"

    def test_hides_token_and_secret(self):
        assert "<已隐藏>" in diag._redact("api_token=abc123")
        assert "<已隐藏>" in diag._redact("client_secret: s3cr3t")

    def test_keeps_ordinary_values(self):
        out = diag._redact("host=localhost port=8765")
        assert "host=localhost" in out
        assert "8765" in out

    def test_case_insensitive(self):
        assert "<已隐藏>" in diag._redact("PASSWORD=xyz")
        assert "<已隐藏>" in diag._redact("MySecret=xyz")


class TestNeverCrashes:
    def test_report_on_missing_paths(self, tmp_path):
        """root/data 都不存在也不能崩 —— 这正是用户最需要它的时候。"""
        layout = _FakeLayout(tmp_path / "nope", tmp_path / "nope2")
        text = diag.build_report(layout)
        assert "# LAN Share 诊断报告" in text

    def test_section_failure_does_not_kill_report(self, layout, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("故意炸")

        monkeypatch.setattr(diag, "_sec_pg", boom)
        text = diag.build_report(layout)
        assert "这一节整体失败" in text
        # 后面几节仍然要在
        assert "## 10. 日志尾部" in text

    def test_export_writes_file(self, layout, tmp_path):
        out = tmp_path / "diag.txt"
        path = diag.export(layout, out)
        assert path.is_file()
        assert "诊断报告" in path.read_text(encoding="utf-8")


class TestVerdict:
    def _report(self, sections: dict) -> str:
        return "\n".join(diag._sec_verdict(sections))

    def test_stale_lock_is_reported(self):
        txt = self._report({"pg": ["  判定：STALE_LOCK"]})
        assert "postmaster.pid" in txt

    def test_normal_running_is_not_reported(self):
        """PG 正常跑着时 postmaster.pid 本来就在 —— 报它就是误报。"""
        txt = self._report({"pg": ["  判定：NORMAL_RUNNING",
                                   "  postmaster.pid 存在，且 PID 1 正在运行"]})
        assert "没有发现明显问题" in txt

    def test_port_taken_while_service_running_is_not_a_problem(self):
        txt = self._report({
            "services": ["  lanshare-api  State=Running StartType=Automatic"],
            "ports": ["  API  8766 已被占用（PID 123 python）"],
        })
        assert "没有发现明显问题" in txt

    def test_port_taken_while_service_stopped_is_a_problem(self):
        txt = self._report({
            "services": ["  lanshare-api  State=Stopped StartType=Automatic"],
            "ports": ["  API  8766 已被占用（PID 123 other.exe）"],
        })
        assert "端口被占用但服务没在跑" in txt

    def test_not_installed_is_reported(self):
        txt = self._report({"services": ["  lanshare-api  NOT INSTALLED"]})
        assert "没装上" in txt

    def test_setup_dir_warnings_surface(self):
        txt = self._report({"layout": ["  ⚠ 程序目录含空格（容易切错 token）"]})
        assert "含空格" in txt


class TestSections:
    def test_pg_marks_stale_when_pid_dead(self, layout, monkeypatch):
        pgdata = layout.data_dir / "pgdata"
        pgdata.mkdir()
        (pgdata / "postmaster.pid").write_text("99999\n/data\n1700000000\n5433\n", encoding="utf-8")
        monkeypatch.setattr(diag, "_ps", lambda s, timeout=15.0: "False")
        text = "\n".join(diag._sec_pg(layout))
        assert "STALE_LOCK" in text

    def test_pg_marks_normal_when_pid_alive(self, layout, monkeypatch):
        pgdata = layout.data_dir / "pgdata"
        pgdata.mkdir()
        (pgdata / "postmaster.pid").write_text("99999\n/data\n1700000000\n5433\n", encoding="utf-8")
        monkeypatch.setattr(diag, "_ps", lambda s, timeout=15.0: "True")
        text = "\n".join(diag._sec_pg(layout))
        assert "NORMAL_RUNNING" in text

    def test_pg_without_pgdata(self, layout):
        text = "\n".join(diag._sec_pg(layout))
        assert "未找到" in text

    def test_layout_flags_space_in_path(self, tmp_path):
        root = tmp_path / "LAN Share"
        root.mkdir()
        layout = _FakeLayout(root, tmp_path / "d")
        text = "\n".join(diag._sec_layout(layout))
        assert "含空格" in text


class TestRedactionUrlUserinfo:
    """URL 里的 userinfo 口令。

    `DATABASE_URL` 这个 key 本身不含 password/token 之类字样，走不到按 key
    判断的规则，可它的值里明明白白带着数据库密码。诊断包是要发出去的，
    这种漏法最要命 —— 2026-11-02 审查时才发现。
    """

    def test_postgres_url_masks_password(self):
        out = diag._redact("DATABASE_URL=postgres://lanshare:s3cret@127.0.0.1:5433/db")
        assert "s3cret" not in out
        assert "lanshare:***@" in out

    def test_empty_username_masked(self):
        out = diag._redact("url=redis://:onlypass@host:6379/0")
        assert "onlypass" not in out
        assert ":***@" in out

    def test_plain_url_untouched(self):
        out = diag._redact("https://example.com/path")
        assert out == "https://example.com/path"


class TestRedactionQuoted:
    def test_json_style(self):
        out = diag._redact('"password": "s3cret"')
        assert "s3cret" not in out

    def test_single_quoted_with_space(self):
        out = diag._redact("secret='two words'")
        assert "two words" not in out

    def test_ordinary_key_keeps_value(self):
        out = diag._redact('"host": "localhost"')
        assert "localhost" in out


class TestPgdataResolution:
    """pgdata 必须以服务定义里的 -D 为准。

    2026-11-02 用户实机：诊断报「找不到 pgdata」，而 pgdata 就在
    D:/LAN Share/data/pgdata —— 服务定义指着它，注册表却写的是
    C:/ProgramData/LANShare/data。第一版只会在 data_dir 下面猜，
    于是既找不到、也发现不了「服务和注册表不一致」这个真问题。
    """

    def _qc(self, path):
        # 只保留 -D 相关的部分 —— 测试要验的就是从这里解析路径
        return 'BINARY_PATH_NAME : "pg_ctl.exe" runservice -N "x" -D "' + path + '" -w'

    def test_uses_service_dash_d(self, layout, monkeypatch):
        real = layout.root / "realdb"
        real.mkdir()
        (real / "postmaster.pid").write_text("1\n/x\n0\n5433\n", encoding="utf-8")
        monkeypatch.setattr(diag, "_run", lambda cmd, timeout=12.0: self._qc(str(real)))
        monkeypatch.setattr(diag, "_ps", lambda s, timeout=15.0: "False")
        text = "\n".join(diag._sec_pg(layout))
        assert str(real) in text

    def test_reports_mismatch_when_both_exist(self, layout, monkeypatch):
        """两份 pgdata 都存在 → 服务定义和注册表打架，这是真故障。"""
        svc = layout.root / "svcdb"
        svc.mkdir()
        reg = layout.data_dir / "pgdata"
        reg.mkdir()
        monkeypatch.setattr(diag, "_run", lambda cmd, timeout=12.0: self._qc(str(svc)))
        monkeypatch.setattr(diag, "_ps", lambda s, timeout=15.0: "False")
        text = "\n".join(diag._sec_pg(layout))
        assert "PGDATA_MISMATCH" in text

    def test_no_mismatch_when_only_service_side_exists(self, layout, monkeypatch):
        """注册表指向的位置没有 pgdata → 只是开发布局，不该报成故障。"""
        svc = layout.root / "svcdb"
        svc.mkdir()
        monkeypatch.setattr(diag, "_run", lambda cmd, timeout=12.0: self._qc(str(svc)))
        monkeypatch.setattr(diag, "_ps", lambda s, timeout=15.0: "False")
        text = "\n".join(diag._sec_pg(layout))
        assert "PGDATA_MISMATCH" not in text


class TestBackupSection:
    def test_reports_missing_dir(self, layout):
        text = "\n".join(diag._sec_backup(layout))
        assert "不存在" in text or "从未备份" in text

    def test_lists_files(self, layout):
        bk = layout.data_dir / ".backups"
        bk.mkdir()
        (bk / "meta-20260101-040000.db").write_text("x", encoding="utf-8")
        text = "\n".join(diag._sec_backup(layout))
        assert "meta-20260101-040000.db" in text


class TestVerdictNewSignals:
    def _report(self, sections):
        return "\n".join(diag._sec_verdict(sections))

    def test_pgdata_mismatch_surfaces(self):
        txt = self._report({"pg": ["  判定：PGDATA_MISMATCH"]})
        assert "不是同一个地方" in txt

    def test_no_pg_tool_surfaces(self):
        txt = self._report({"backup": ["  判定：NO_PG_TOOL"]})
        assert "pg_dump" in txt

    def test_empty_backup_surfaces(self):
        txt = self._report({"backup": ["  判定：EMPTY"]})
        assert "备份" in txt


class TestCertMultiRoot:
    """信任库里有多个 Caddy 根证书时的判定。

    2026-11-02 用户实机信任库里有三个 Caddy 根（11:03 / 10:26 / 14:02），
    当前用的是 14:02 那个。单看「根证书存在」会以为一切正常，
    但「到底信任的是哪个」其实完全说不清 —— 换过根的设备连新证书会报错。
    """

    THUMB_A = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    THUMB_B = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

    def _mock(self, monkeypatch, active_thumb, trusted):
        def fake_ps(script, timeout=15.0):
            if "X509Certificate2" in script:
                return active_thumb
            if "cert:" in script.lower() or "Get-ChildItem Cert" in script:
                return "\n".join(trusted)
            return ""
        monkeypatch.setattr(diag, "_ps", fake_ps)

    def _cert_file(self, layout):
        d = layout.root / "certs" / "caddy" / "pki" / "authorities" / "local"
        d.mkdir(parents=True, exist_ok=True)
        (d / "root.crt").write_text("x", encoding="utf-8")

    def test_single_matching_root_is_clean(self, layout, monkeypatch):
        self._cert_file(layout)
        self._mock(monkeypatch, self.THUMB_A,
                   [f"{self.THUMB_A}|2036-09-10 14:02|CN=Caddy Local Authority"])
        text = "\n".join(diag._sec_cert(layout))
        assert "MULTI_ROOT" not in text
        assert "ACTIVE_NOT_TRUSTED" not in text
        assert "← 当前使用" in text

    def test_multiple_roots_flagged(self, layout, monkeypatch):
        self._cert_file(layout)
        self._mock(monkeypatch, self.THUMB_B, [
            f"{self.THUMB_A}|2036-09-10 11:03|CN=Caddy Local Authority",
            f"{self.THUMB_B}|2036-09-10 14:02|CN=Caddy Local Authority",
        ])
        text = "\n".join(diag._sec_cert(layout))
        assert "判定：MULTI_ROOT" in text
        assert "2 个" in text

    def test_active_not_in_trust_store(self, layout, monkeypatch):
        self._cert_file(layout)
        self._mock(monkeypatch, self.THUMB_B, [
            f"{self.THUMB_A}|2036-09-10 11:03|CN=Caddy Local Authority",
        ])
        text = "\n".join(diag._sec_cert(layout))
        assert "ACTIVE_NOT_TRUSTED" in text

    def test_no_trusted_root(self, layout, monkeypatch):
        self._cert_file(layout)
        self._mock(monkeypatch, self.THUMB_A, [])
        text = "\n".join(diag._sec_cert(layout))
        assert "NOT_TRUSTED" in text


class TestPgStatusCrossCheck:
    """SCM 说 Running、PG 自己说没跑 —— 最坑人的「假 Running」。"""

    def _report(self, sections):
        return "\n".join(diag._sec_verdict(sections))

    def test_fake_running_detected(self):
        txt = self._report({
            "services": ["  lanshare-postgresql  State=Running StartType=Auto"],
            "pg": ["  判定：PG_NOT_RUNNING"],
        })
        assert "假运行" in txt

    def test_bad_cluster_detected(self):
        txt = self._report({"pg": ["  判定：PG_BAD_CLUSTER"]})
        assert "不是有效的 PG 数据目录" in txt


class TestRuntimeSignals:
    def _report(self, sections):
        return "\n".join(diag._sec_verdict(sections))

    def test_proxy_flagged(self):
        txt = self._report({"runtime": ["  判定：PROXY_SET"]})
        assert "代理" in txt

    def test_stale_process_flagged(self):
        txt = self._report({"runtime": ["  判定：STALE_PROCESS"]})
        assert "残留进程" in txt


class TestRegistrySection:
    def test_missing_key(self, layout, monkeypatch):
        monkeypatch.setattr(diag, "_ps", lambda s, timeout=15.0: "KEY_NOT_FOUND")
        text = "\n".join(diag._sec_registry(layout))
        assert "NO_REGKEY" in text

    def test_redacts_password(self, layout, monkeypatch):
        monkeypatch.setattr(
            diag, "_ps",
            lambda s, timeout=15.0: "InstallFolder=D:\LAN Share\nPgPassword=hunter2")
        text = "\n".join(diag._sec_registry(layout))
        assert "hunter2" not in text
        assert "LAN Share" in text
