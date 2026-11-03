"""pgdata 从「用户文件区」搬出来（3.95）。

背景：data_dir 是**用户文件根**，网盘会把里面的东西原样列给用户看。
3.94 及以前 pgdata 就躺在 <data_dir>\\pgdata 里，于是用户在自己手机网盘
里看到一个叫 pgdata 的目录，里面全是 PostgreSQL 的二进制文件。

契约：
- 新位置 = data_dir 的同级 pgdata
- 老装机升级时自动搬，搬完校验 PG_VERSION
- 两边都有库 → 响亮失败，绝不猜；猜错的代价是丢用户数据
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanshare_cli.pg import Postgres, PostgresError


def _make(tmp_path: Path, monkeypatch) -> Postgres:
    pg = Postgres(root=tmp_path / "root", data_dir=tmp_path / "data")
    # 没有真的 PG 二进制，别让权限修复卡住
    monkeypatch.setattr(pg, "_grant_service_access", lambda: None)
    return pg


class TestPgdataLocation:
    def test_pgdata_is_sibling_of_data_dir(self, tmp_path):
        pg = _make(tmp_path, pytest.MonkeyPatch())
        assert pg.pgdata == tmp_path / "pgdata"
        # 关键：绝不能在 data_dir 里面
        assert pg.pgdata.parent != pg.data_dir

    def test_pgdata_not_inside_share_root(self, tmp_path):
        """用户文件根里不能出现 pgdata —— 这是这次改动的全部意义。"""
        pg = _make(tmp_path, pytest.MonkeyPatch())
        assert pg.data_dir not in pg.pgdata.parents


class TestMigrateLegacyPgdata:
    def test_moves_old_location(self, tmp_path, monkeypatch):
        pg = _make(tmp_path, monkeypatch)
        old = pg.data_dir / "pgdata"
        old.mkdir(parents=True)
        (old / "PG_VERSION").write_text("16\n", encoding="utf-8")

        assert pg.migrate_legacy_pgdata() is True
        assert not old.exists()
        assert (pg.pgdata / "PG_VERSION").is_file()

    def test_noop_when_already_migrated(self, tmp_path, monkeypatch):
        pg = _make(tmp_path, monkeypatch)
        pg.pgdata.mkdir(parents=True)
        (pg.pgdata / "PG_VERSION").write_text("16\n", encoding="utf-8")

        assert pg.migrate_legacy_pgdata() is False
        assert pg.pgdata.is_dir()

    def test_noop_on_fresh_install(self, tmp_path, monkeypatch):
        """全新安装：老位置没有库，什么都不该发生。"""
        pg = _make(tmp_path, monkeypatch)
        assert pg.migrate_legacy_pgdata() is False

    def test_refuses_when_both_exist(self, tmp_path, monkeypatch):
        """两套库 → 必须响亮失败。

        自动挑一套看起来「聪明」，实际是在替用户丢数据。宁可让他自己确认。
        """
        pg = _make(tmp_path, monkeypatch)
        # 两边都得是「真库」（有 PG_VERSION）—— 老位置没有 PG_VERSION 的
        # 情况走的是另一条「可能不是库」的响亮失败，见上面的专用测试
        old = pg.data_dir / "pgdata"
        old.mkdir(parents=True)
        (old / "PG_VERSION").write_text("16\n", encoding="utf-8")
        pg.pgdata.mkdir(parents=True)
        (pg.pgdata / "PG_VERSION").write_text("16\n", encoding="utf-8")

        with pytest.raises(PostgresError) as exc:
            pg.migrate_legacy_pgdata()
        assert "两套" in str(exc.value)
        # 两边都不能被动过
        assert (pg.data_dir / "pgdata").is_dir()
        assert pg.pgdata.is_dir()

    def test_fails_loudly_when_version_file_missing(self, tmp_path, monkeypatch):
        """搬过去却没有 PG_VERSION → 说明搬得不完整，不能当成功。"""
        pg = _make(tmp_path, monkeypatch)
        old = pg.data_dir / "pgdata"
        old.mkdir(parents=True)          # 故意不写 PG_VERSION

        with pytest.raises(PostgresError) as exc:
            pg.migrate_legacy_pgdata()
        assert "PG_VERSION" in str(exc.value)

    def test_user_folder_named_pgdata_is_not_moved(self, tmp_path, monkeypatch):
        """老位置没有 PG_VERSION → 它可能不是库。

        老布局下 pgdata 就在用户文件区里、对用户可见 —— 用户完全可能往里
        放过自己的文件。这种目录必须在搬**之前**响亮失败，而不是先搬走
        （用户文件从网盘列表里消失）然后才报错。
        """
        pg = _make(tmp_path, monkeypatch)
        old = pg.data_dir / "pgdata"
        old.mkdir(parents=True)
        (old / "家人照片.txt").write_text("x", encoding="utf-8")

        with pytest.raises(PostgresError) as exc:
            pg.migrate_legacy_pgdata()
        assert "PG_VERSION" in str(exc.value)
        # 用户文件必须原地不动，目标位置什么都不该出现
        assert (old / "家人照片.txt").is_file()
        assert not pg.pgdata.exists()

    def test_failed_move_keeps_old_and_cleans_partial_copy(self, tmp_path, monkeypatch):
        """跨盘 move 中断（复制阶段挂了）→ 老位置原样、残缺副本清掉。

        否则重试必落进「两套库」响亮失败：用户被卡死在一条
        自己没做错任何事的路径上。
        """
        from lanshare_cli import pg as pg_module

        pg = _make(tmp_path, monkeypatch)
        old = pg.data_dir / "pgdata"
        old.mkdir(parents=True)
        (old / "PG_VERSION").write_text("16\n", encoding="utf-8")

        def broken_move(src, dst):
            # 模拟跨盘复制中途断电：目标有残缺内容、还没有 PG_VERSION
            Path(dst).mkdir(parents=True, exist_ok=True)
            (Path(dst) / "base.tar.tmp").write_text("x", encoding="utf-8")
            raise OSError("模拟复制中断")

        monkeypatch.setattr(pg_module.shutil, "move", broken_move)

        with pytest.raises(PostgresError) as exc:
            pg.migrate_legacy_pgdata()
        assert "搬迁失败" in str(exc.value)
        # 老位置原样保留，可以安全重试
        assert (old / "PG_VERSION").is_file()
        # 残缺副本（本次调用创建、无 PG_VERSION）必须清掉
        assert not pg.pgdata.exists()
