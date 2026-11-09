"""PG 口令落盘契约（2026-11-09 发布前复核）。

背景：口令原来双写「注册表 + 数据目录文件」。注册表键
`HKLM\\SOFTWARE\\<key>` 的 ACL 实测 `BUILTIN\\Users: ReadKey` +
`ALL APPLICATION PACKAGES: ReadKey` —— 本机任意账号与任何 AppContainer
都能读走生产库口令。文件那一份已 icacls 收紧，注册表这半没有可收的余地
（同一个键还要给 Users 读 InstallFolder 等非敏感值），所以不再写。

这两条钉守的是「别有人把注册表双写加回来」和「读的时候别再优先信注册表」。
"""

import pathlib

from lanshare_cli import cli


def test_write_pg_password_never_touches_registry(tmp_path, monkeypatch):
    """口令只落 .lanshare-pgpass 并收紧 ACL；注册表不写、历史值删掉。"""
    written: list = []
    deleted: list = []
    monkeypatch.setattr(cli, "write_registry_value",
                        lambda *a, **k: written.append(a))
    monkeypatch.setattr(cli, "delete_registry_value",
                        lambda *a, **k: deleted.append(a))
    monkeypatch.setattr(cli, "lock_pgpass_acl", lambda f: None)

    cli.write_pg_password(tmp_path, "LANShare", "s3cret-value")

    f = tmp_path / ".lanshare-pgpass"
    assert f.is_file(), "口令文件必须落盘"
    assert f.read_text(encoding="utf-8") == "s3cret-value"
    assert written == [], "口令绝不能写进注册表（Users: ReadKey = 本机任意账号可读）"
    assert [d[0] for d in deleted] == ["PgPassword"], "必须清掉注册表里的历史遗留值"


def test_read_pg_password_prefers_file_over_registry(tmp_path, monkeypatch):
    """读的顺序：数据目录文件优先，注册表只作老装机兜底。"""
    (tmp_path / ".lanshare-pgpass").write_text("from-file", encoding="utf-8")
    monkeypatch.setattr(cli, "read_registry_value",
                        lambda *a, **k: "from-registry")
    assert cli.read_pg_password(tmp_path, "LANShare") == "from-file"


def test_read_pg_password_falls_back_to_registry_for_old_install(tmp_path, monkeypatch):
    """老装机没有口令文件时，仍能从注册表读回旧密码（升级不能把库锁死）。"""
    monkeypatch.setattr(cli, "read_registry_value",
                        lambda *a, **k: "from-registry")
    assert cli.read_pg_password(tmp_path, "LANShare") == "from-registry"


def test_delete_registry_value_rejects_injection():
    """键名白名单沿用 write_registry_value 的口径（拼 PowerShell 脚本）。"""
    import pytest

    for evil in ("", "a b", "a'; Remove-Item C:\\ ", "x/y"):
        with pytest.raises(ValueError):
            cli.delete_registry_value("PgPassword", evil)
