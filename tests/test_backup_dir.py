"""备份目录可配置 + 清理安全性。

背景（2026-11-02 长跑复查）：

`_prune_old` 原来的写法是「不匹配 meta-*/settings-* 的文件一律 unlink」。
默认备份目录是 `data/.backups`，里面本来就只有我们自己的文件，看着没问题。
但一旦允许用户把备份指到别的盘（比如 D:\\备份），那个目录里可能还放着
用户的照片、文档、旧备份 —— 那段代码等于把整个目录清空。

这是「备份功能本身会毁掉用户数据」级别的缺陷，必须有测试锁住。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import backup as backup_svc


class TestSetBackupDir:
    def test_rejects_relative_path(self):
        with pytest.raises(ValueError, match="绝对路径"):
            backup_svc.set_backup_dir("relative/dir")

    def test_rejects_unwritable(self, tmp_path):
        # 指向一个不存在的盘符：Windows 上 mkdir 会失败
        with pytest.raises((ValueError, OSError)):
            backup_svc.set_backup_dir("Z:\\definitely-not-a-real-volume\\bk")

    def test_accepts_writable_dir(self, tmp_path, monkeypatch):
        captured = {}

        class FakeSettings:
            @staticmethod
            def get_setting(key, default=""):
                return captured.get(key, default)

            @staticmethod
            def set_setting(key, value):
                captured[key] = value

        import app.settings as real_settings
        monkeypatch.setattr(real_settings, "get_setting", FakeSettings.get_setting)
        monkeypatch.setattr(real_settings, "set_setting", FakeSettings.set_setting)

        target = tmp_path / "mybackup"
        backup_svc.set_backup_dir(str(target))

        assert target.is_dir()
        assert captured["backup_dir"] == str(target)
        # 探针文件不能留在用户的目录里
        assert not (target / ".lanshare-write-test").exists()

    def test_empty_restores_default(self, monkeypatch):
        captured = {}

        import app.settings as real_settings
        monkeypatch.setattr(real_settings, "get_setting",
                            lambda k, d="": captured.get(k, d))
        monkeypatch.setattr(real_settings, "set_setting",
                            lambda k, v: captured.__setitem__(k, v))

        backup_svc.set_backup_dir("")
        assert captured["backup_dir"] == ""


class TestBackupDirResolution:
    def test_falls_back_to_default_when_unset(self, monkeypatch):
        import app.settings as real_settings
        monkeypatch.setattr(real_settings, "get_setting", lambda k, d="": d)
        assert backup_svc.backup_dir() == backup_svc._DEFAULT_BACKUP_DIR

    def test_uses_configured_dir(self, monkeypatch, tmp_path):
        import app.settings as real_settings
        monkeypatch.setattr(real_settings, "get_setting",
                            lambda k, d="": str(tmp_path) if k == "backup_dir" else d)
        assert backup_svc.backup_dir() == tmp_path


class TestPruneSafety:
    """_prune_old 绝不能删不是它自己产出的文件。"""

    def test_keeps_unrelated_files(self, monkeypatch, tmp_path):
        # 用户选的目录里放着别的东西
        keep_me = [
            tmp_path / "家庭照片.jpg",
            tmp_path / "工作文档.docx",
            tmp_path / "notes.txt",
            tmp_path / "subdir",
        ]
        for f in keep_me[:3]:
            f.write_text("x", encoding="utf-8")
        keep_me[3].mkdir()

        # 我们自己产的备份
        (tmp_path / "meta-20260101-040000.db").write_text("d", encoding="utf-8")
        (tmp_path / "settings-20260101-040000.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(backup_svc, "backup_dir", lambda: tmp_path)
        backup_svc._prune_old()

        for f in keep_me:
            assert f.exists(), f"{f.name} 被误删了"

    def test_removes_temp_leftovers(self, monkeypatch, tmp_path):
        tmpf = tmp_path / "meta-20260101-040000.db.tmp"
        tmpf.write_text("half written", encoding="utf-8")

        monkeypatch.setattr(backup_svc, "backup_dir", lambda: tmp_path)
        backup_svc._prune_old()

        assert not tmpf.exists()
        assert list(tmp_path.iterdir()) == []

    def test_keeps_user_temp_files(self, monkeypatch, tmp_path):
        """用户自己的 .tmp/.part 不是我们的半成品 —— 绝不能删。

        备份目录是用户自选的，里面可能有下载一半的 .part、编辑器临时
        文件 .tmp。原实现「后缀匹配就删」会把它们静默清掉 —— 违反
        「只删自己产的」红线。自家的半成品永远带 meta-/settings- 前缀。
        """
        mine = [
            tmp_path / "旅行记录.vlog.part",
            tmp_path / "notes.tmp",
            tmp_path / "download.part",
        ]
        for f in mine:
            f.write_text("user data", encoding="utf-8")
        # 自家半成品仍要被清
        own = tmp_path / "settings-20260101-040000.tmp"
        own.write_text("half", encoding="utf-8")

        monkeypatch.setattr(backup_svc, "backup_dir", lambda: tmp_path)
        backup_svc._prune_old()

        for f in mine:
            assert f.exists(), f"{f.name} 被误删了"
        assert not own.exists()

    def test_keeps_only_recent_groups(self, monkeypatch, tmp_path):
        # 造 12 组，只有最近 BACKUP_KEEP 组该留下
        keep = backup_svc.BACKUP_KEEP
        stamps = [f"202601{i:02d}-040000" for i in range(1, keep + 6)]
        for s in stamps:
            (tmp_path / f"meta-{s}.db").write_text("d", encoding="utf-8")
            (tmp_path / f"settings-{s}.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(backup_svc, "backup_dir", lambda: tmp_path)
        backup_svc._prune_old()

        remaining = sorted(p.name for p in tmp_path.iterdir())
        # dump 和快照必须成对保留、成对删除
        assert len(remaining) == keep * 2
        for s in stamps[-keep:]:
            assert f"meta-{s}.db" in remaining
            assert f"settings-{s}.json" in remaining
        for s in stamps[:-keep]:
            assert f"meta-{s}.db" not in remaining

    def test_prunes_dump_suffix_too(self, monkeypatch, tmp_path):
        """PG 引擎的 dump 后缀是 .dump，滚动淘汰必须同样认它。

        不认的后果是「老的不删、新的不认」，备份目录越滚越大，
        而 BACKUP_KEEP 形同虚设。
        """
        keep = backup_svc.BACKUP_KEEP
        stamps = [f"202602{i:02d}-040000" for i in range(1, keep + 4)]
        for s in stamps:
            (tmp_path / f"meta-{s}.dump").write_text("PGDMP", encoding="utf-8")
            (tmp_path / f"settings-{s}.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(backup_svc, "backup_dir", lambda: tmp_path)
        backup_svc._prune_old()

        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert len(remaining) == keep * 2
        for s in stamps[-keep:]:
            assert f"meta-{s}.dump" in remaining
        for s in stamps[:-keep]:
            assert f"meta-{s}.dump" not in remaining

    def test_dump_suffix_matches_engine(self, monkeypatch):
        """后缀必须如实反映内容：PG 的 pg_dump -Fc 归档不是 sqlite 库文件。

        2026-11-06 排障：备份叫 meta-*.db，用 sqlite 打开报
        'file is not a database'，差点被判定成「备份坏了」。
        """
        monkeypatch.setattr(backup_svc, "USE_PG", True)
        assert backup_svc._dump_suffix() == ".dump"
        monkeypatch.setattr(backup_svc, "USE_PG", False)
        assert backup_svc._dump_suffix() == ".db"

    def test_missing_dir_is_noop(self, monkeypatch, tmp_path):
        monkeypatch.setattr(backup_svc, "backup_dir",
                            lambda: tmp_path / "does-not-exist")
        backup_svc._prune_old()   # 不该抛
