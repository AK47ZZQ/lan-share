"""PG 服务注册的 -D 校验。

2026-11-02 用户实机事故：
    服务定义  -D = D:\\LAN Share\\data\\pgdata
    注册表    DataFolder = C:\\ProgramData\\LANShare\\data

重装时数据目录换了，但 `install_service()` 里只有一句「服务已存在就 return」，
服务定义没跟着更新。结果 PG 一直 Stopped，而注册表信誓旦旦说数据目录是新的
—— 光看任何一处都看不出问题。

这条测试锁住「服务定义必须指向当前 pgdata」这个契约。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanshare_cli.pg import Postgres, PostgresError


class FakeRunner:
    """伪造 subprocess 调用，记录被调过什么命令。"""

    def __init__(self, *, exists=True, qc_path="", delete_takes=1):
        self.calls: list[list[str]] = []
        self.exists = exists
        self.qc_path = qc_path
        self.delete_takes = delete_takes   # sc delete 之后还要多少轮才消失
        self._deleted_rounds = 0

    def __call__(self, cmd, timeout=60):
        self.calls.append(list(cmd))
        joined = " ".join(str(c) for c in cmd)

        class R:
            def __init__(self, rc, out="", err=""):
                self.returncode, self.stdout, self.stderr = rc, out, err

        if "query" in joined:
            if self.exists:
                return R(0, "SERVICE_NAME: x")
            return R(1060, "", "服务没有安装")
        if "qc" in joined:
            return R(0, f'BINARY_PATH_NAME : pg_ctl.exe runservice -D "{self.qc_path}" -w')
        if "delete" in joined:
            self._pending_delete = True
            self._deleted_rounds = 0
            return R(0, "已删除")
        if "register" in joined:
            self.exists = True
            return R(0, "")
        return R(0, "")


def _make(tmp_path, monkeypatch, runner):
    pg = Postgres(root=tmp_path / "root", data_dir=tmp_path / "data")
    # 测试里没有真的 PG 二进制，别让它卡在「找不到 pg_ctl.exe」
    monkeypatch.setattr(pg, "exe", lambda name: tmp_path / f"{name}.exe")
    monkeypatch.setattr(pg, "_run", runner)
    # sc delete 是异步的：让 query 在 delete 之后返回「不存在」
    def run(cmd, timeout=60):
        r = runner(cmd, timeout)
        if "query" in " ".join(str(c) for c in cmd) and getattr(runner, "_pending_delete", False):
            runner._deleted_rounds += 1
            if runner._deleted_rounds >= runner.delete_takes:
                runner.exists = False
        return r
    monkeypatch.setattr(pg, "_run", run)
    return pg


class TestInstallServiceDashD:
    def test_registers_when_absent(self, tmp_path, monkeypatch):
        runner = FakeRunner(exists=False)
        pg = _make(tmp_path, monkeypatch, runner)
        pg.install_service()
        assert any("register" in " ".join(c) for c in runner.calls)

    def test_keeps_service_when_dash_d_matches(self, tmp_path, monkeypatch):
        """服务指向的就是当前 pgdata → 保持不动，不要删重建。"""
        # Postgres(data_dir=tmp/data) 的 pgdata = tmp/pgdata（数据目录的同级）
        runner = FakeRunner(exists=True, qc_path=str(tmp_path / "pgdata"))
        pg = _make(tmp_path, monkeypatch, runner)
        pg.install_service()
        assert not any("delete" in " ".join(c) for c in runner.calls)
        assert not any("register" in " ".join(c) for c in runner.calls)

    def test_rebuilds_service_when_dash_d_differs(self, tmp_path, monkeypatch):
        """服务指向别的目录 → 必须删掉重建（这正是 2026-11-02 的故障）。"""
        runner = FakeRunner(exists=True, qc_path=r"D:\LAN Share\data\pgdata")
        pg = _make(tmp_path, monkeypatch, runner)
        pg.install_service()
        joined = [" ".join(c) for c in runner.calls]
        assert any("delete" in j for j in joined), "应该删掉旧服务定义"
        assert any("register" in j for j in joined), "应该按新路径重新注册"

    def test_raises_when_old_service_wont_die(self, tmp_path, monkeypatch):
        """删不掉就响亮失败，不要带着过期定义继续往下走。"""
        runner = FakeRunner(exists=True, qc_path=r"D:\old\pgdata", delete_takes=9999)
        pg = _make(tmp_path, monkeypatch, runner)
        with pytest.raises(PostgresError):
            pg.install_service()
