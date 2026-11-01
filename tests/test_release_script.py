"""release.py 的版本号读写 —— 这俩函数写坏了会直接毁构建，必须测。

测试不碰真文件：把模块级的 ISS / PUBSPEC 指到 tmp_path 上的副本。
"""

from __future__ import annotations

import importlib.util
import json
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
class TestChangelogHistory:
    """CHANGELOG → 更新历史。服务端 versions.json 和 App 端都吃这份数据。"""

    def test_only_app_versions(self, rel):
        md = """## 2026-11-01 — v3.79 + App v1.1.23：改了什么

- 一条改动

## 2026-10-31 — 服务端 v3.76：只有服务端

- 不该出现
"""
        items = rel.parse_changelog_history(md)
        assert len(items) == 1
        assert items[0]["version"] == "1.1.23"

    def test_dedupes_same_version(self, rel):
        md = """## 2026-11-01（一）— App v1.1.20：第一篇

- 甲

## 2026-11-01（二）— App v1.1.20：第二篇

- 乙
"""
        items = rel.parse_changelog_history(md)
        assert len(items) == 1

    def test_build_is_derived(self, rel):
        md = "## 2026-11-01 — App v1.2.3：x\n\n- y\n"
        items = rel.parse_changelog_history(md)
        assert items[0]["build"] == 1 * 10000 + 2 * 100 + 3

    def test_notes_are_compressed(self, rel):
        """服务端文件不该背整篇复盘 —— notes 用精简版。"""
        md = "## 2026-11-01 — App v1.1.23：标题\n\n" + ("正文很长的段落。" * 50)
        items = rel.parse_changelog_history(md)
        assert len(items[0]["notes"]) < 600

    def test_keeps_document_order(self, rel):
        md = """## 2026-11-02 — App v1.1.24：新

- a

## 2026-11-01 — App v1.1.23：旧

- b
"""
        items = rel.parse_changelog_history(md)
        assert [i["version"] for i in items] == ["1.1.24", "1.1.23"]

    def test_empty(self, rel):
        assert rel.parse_changelog_history("") == []


class TestFindLocalShareDir:
    def test_returns_none_when_no_registry(self, rel, monkeypatch):
        """没装服务端的机器上跑 release.py 不该炸。"""
        import sys
        monkeypatch.setattr(sys, "platform", "linux")   # 非 Windows 直接跳过
        assert rel.find_local_share_dir() is None
class TestSafeTag:
    """GitHub 返回的 tag 是外部数据，但它会被拼进子进程参数和 URL。

    这里失守会怎样：
      · tag = `--yes` → argv 错位，`gh release delete` 删错东西
      · tag = `--repo=别人的仓库` → 删到别人仓库去
      · tag = `../../x` → `gh api .../releases/tags/<tag>` 请求路径被改写

    仓库是公开的，任何有写权限的协作者都能造出这种 tag，而发版脚本
    是自动跑的、没人看列表 —— 所以要白名单挡掉。
    """

    @pytest.mark.parametrize("good", [
        "server-v3.79", "app-v1.1.23", "v1.0.0", "release_2026.11.01",
        "a", "x" * 128,
    ])
    def test_accepts_normal_tags(self, rel, good):
        assert rel._safe_tag(good) == good

    @pytest.mark.parametrize("evil", [
        "--yes", "-f", "--repo=other/repo", "-", "--",
        "../../etc/passwd", "tag/../x", "tag with space",
        "tag;rm -rf /", "tag$(id)", "tag`id`", "", "  ",
        "tag\nname", "x" * 129,
    ])
    def test_rejects_dangerous_tags(self, rel, evil):
        with pytest.raises(rel.Abort):
            rel._safe_tag(evil)

    def test_prune_refuses_on_evil_tag(self, rel, monkeypatch):
        """被污染的 tag 列表要整体中止，不能「跳过这条继续删下一个」。

        注意：恶意 tag 必须**带合法前缀**才能穿过第一道过滤
        （`app-` 开头），否则测不到 _safe_tag —— 第一版测试就栽在这，
        用 `--repo=evil` 当样本，结果被 startswith 挡掉，DID NOT RAISE。
        """
        fake = json.dumps([
            {"tag_name": "app-v1.1.23"},
            {"tag_name": "app-../../evil"},      # 前缀合法，内容恶意
        ])
        monkeypatch.setattr(rel, "run_checked", lambda *a, **k: fake)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr(rel, "run", fake_run)
        with pytest.raises(rel.Abort):
            rel.prune_old_releases("app", "app-v1.1.24")
        # 关键：出事之后一个删除命令都不该发出去
        assert not calls, f"非法 tag 出现后仍执行了：{calls}"
