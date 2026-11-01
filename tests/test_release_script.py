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

    def test_write_same_version_is_noop(self, rel):
        """重跑发版：目标版本 == 当前版本时不该报错。

        第一次跑 3.78 就卡在这 —— 上一次已经写成 3.78，这次再写同样内容，
        文本没变化，`if s2 == s` 就误判成「那行没匹配上」。
        """
        rel.write_server_version("3.77")          # 文件里本来就是 3.77
        assert rel.read_server_version() == "3.77"


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

    def test_write_same_version_is_noop(self, rel):
        """同 write_server_version —— 重跑不该炸。"""
        rel.write_app_version("1.1.20", 127)
        assert rel.read_app_version() == ("1.1.20", 127)

    def test_write_same_version_differs_only_in_build(self, rel):
        """只改 build 号也要真写进去。"""
        rel.write_app_version("1.1.20", 128)
        assert rel.read_app_version() == ("1.1.20", 128)


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

class TestSummarize:
    """Release 说明的压缩 —— 用户看到的更新历史就是它生成的。

    这一块最容易出的错不是崩溃，是「压出一堆断句」：
    正文是折行写的，按行取首句会把一句话切成四条，读起来全是半截。
    """

    def test_merges_soft_wrapped_lines(self, rel):
        """折行的长句要被拼回一句，不能切成好几条。"""
        md = """## 2026-11-01 — 3.78：修掉检查更新

一天发了两版，每版十来步全靠手走，每一步都错过：
版本号散在四处改漏一处、APK 资产名少个 +125 客户端就放弃整个源。
"""
        out = rel.summarize(md)
        # 拼回来应该是一整句，而不是两条半截
        assert out.count("- ") == 1
        assert "版本号散在四处" in out

    def test_skips_fenced_code_block(self, rel):
        md = """## 3.78：发版固化

正文一句话。

```
python scripts/release.py check
python scripts/release.py server 3.78
```
"""
        out = rel.summarize(md)
        assert "release.py check" not in out
        assert "发版固化" in out

    def test_skips_indented_code_block(self, rel):
        """CHANGELOG 里命令行用的是 4 空格缩进，不是 ```。"""
        md = """## 3.78：发版固化

正文一句话。

    python scripts/release.py check

后面还有一句有用的。
"""
        out = rel.summarize(md)
        assert "release.py" not in out

    def test_title_skips_install_heading(self, rel):
        """第一个 ## 若是「装这个」，要跳过找下一个 —— 否则主题全错。"""
        md = """## 装这个

`Setup.exe`（53 MB）。

## 手机端下载变快了

- 本地有就直发
"""
        out = rel.summarize(md)
        assert "装这个" not in out
        assert "手机端下载变快了" in out

    def test_skips_known_limits_section(self, rel):
        md = """## 3.78：真改动

- 修了更新检查

## 已知限制

- 自签证书会提示不安全
"""
        out = rel.summarize(md)
        assert "自签证书" not in out
        assert "修了更新检查" in out

    def test_caps_bullets(self, rel):
        md = "## 3.78：很多改动\n\n" + "\n".join(
            f"- 第 {i} 条改动内容" for i in range(12))
        out = rel.summarize(md, max_bullets=3)
        assert out.count("- 第") == 3
        assert "CHANGELOG" in out      # 提示还有更多

    def test_dedupes(self, rel):
        md = """## 3.78：改了什么

- 修了更新检查

### 修了更新检查

正文重复说一遍修了更新检查。
"""
        out = rel.summarize(md)
        assert out.count("修了更新检查") == 1

    def test_empty_input(self, rel):
        assert rel.summarize("") == ""

    def test_strips_bold_markers(self, rel):
        """手机上不一定渲染 markdown，留着 ** 很难看。"""
        md = "## 3.78：**加粗标题**\n\n- **加粗要点**内容\n"
        out = rel.summarize(md)
        assert "**" not in out
