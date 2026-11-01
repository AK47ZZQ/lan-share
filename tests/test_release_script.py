"""release.py 的版本号读写 —— 这俩函数写坏了会直接毁构建，必须测。

测试不碰真文件：把模块级的 ISS / PUBSPEC 指到 tmp_path 上的副本。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    """按路径加载 scripts/release.py（scripts 不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location(
        "release_script", ROOT / "scripts" / "release.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_script"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def rel(tmp_path, monkeypatch):
    mod = _load_module()

    iss = tmp_path / "lanshare.iss"
    iss.write_text(
        '#define AppName "LAN Share"\n#define AppVersion "3.77"\n#define AppId "x"\n',
        encoding="utf-8",
    )
    pub = tmp_path / "pubspec.yaml"
    pub.write_text(
        "name: lanshare_app\nversion: 1.1.20+127\nenvironment:\n  sdk: '>=3.0.0'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ISS", iss)
    monkeypatch.setattr(mod, "PUBSPEC", pub)
    return mod


class TestServerVersion:
    def test_reads_current(self, rel):
        assert rel.read_server_version() == "3.77"

    def test_write_then_read_back(self, rel):
        rel.write_server_version("3.78")
        assert rel.read_server_version() == "3.78"

    def test_write_preserves_neighbours(self, rel):
        rel.write_server_version("3.80")
        text = rel.ISS.read_text(encoding="utf-8")
        assert '#define AppName "LAN Share"' in text
        assert '#define AppId "x"' in text

    def test_missing_version_raises(self, rel):
        rel.ISS.write_text("#define AppName \"x\"\n", encoding="utf-8")
        with pytest.raises(rel.Abort):
            rel.read_server_version()


class TestAppVersion:
    def test_reads_version_and_build(self, rel):
        assert rel.read_app_version() == ("1.1.20", 127)

    def test_write_then_read_back(self, rel):
        rel.write_app_version("1.1.21", 128)
        assert rel.read_app_version() == ("1.1.21", 128)

    def test_write_preserves_other_keys(self, rel):
        rel.write_app_version("1.1.22", 129)
        text = rel.PUBSPEC.read_text(encoding="utf-8")
        assert "name: lanshare_app" in text
        assert "sdk: '>=3.0.0'" in text

    def test_does_not_touch_similar_lines(self, rel):
        """`version:` 只改第一处，别把别的行也改了。"""
        rel.PUBSPEC.write_text(
            "name: x\nversion: 1.0.0+1\ndependencies:\n"
            "  foo:\n    version: 9.9.9\n",
            encoding="utf-8",
        )
        rel.write_app_version("1.0.1", 2)
        text = rel.PUBSPEC.read_text(encoding="utf-8")
        assert "version: 1.0.1+2" in text
        assert "version: 9.9.9" in text

    def test_missing_version_raises(self, rel):
        rel.PUBSPEC.write_text("name: x\n", encoding="utf-8")
        with pytest.raises(rel.Abort):
            rel.read_app_version()


class TestChangelogGate:
    def test_finds_version_section(self, rel, tmp_path, monkeypatch):
        cl = tmp_path / "CHANGELOG.md"
        cl.write_text(
            "# 变更\n\n## 2026-11-01 — 3.77：修更新检查\n\n- 没查成要说出来\n\n"
            "## 2026-10-31 — 3.76：别的\n\n- 内容\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(rel, "CHANGELOG", cl)
        body = rel.check_changelog_has("3.77")
        assert "没查成要说出来" in body
        assert "3.76" not in body

    def test_missing_version_aborts(self, rel, tmp_path, monkeypatch):
        cl = tmp_path / "CHANGELOG.md"
        cl.write_text("# 变更\n\n## 2026-10-31 — 3.76：别的\n", encoding="utf-8")
        monkeypatch.setattr(rel, "CHANGELOG", cl)
        with pytest.raises(rel.Abort):
            rel.check_changelog_has("9.99")

    def test_does_not_match_substring_of_other_version(self, rel, tmp_path, monkeypatch):
        """查 3.7 不能匹配到 3.77 —— 否则会拿错版本的说明。"""
        cl = tmp_path / "CHANGELOG.md"
        cl.write_text("## 2026-11-01 — 3.77：新\n\n- 内容\n", encoding="utf-8")
        monkeypatch.setattr(rel, "CHANGELOG", cl)
        with pytest.raises(rel.Abort):
            rel.check_changelog_has("3.7")
