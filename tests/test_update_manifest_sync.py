"""更新源清单（`updates/index.json` / `updates/app/index.json`）的生成规则。

2026-11-08 的起因：这两份清单**从来没有生成者** —— 一直是手工维护，`history`
只有零星几条，和 CHANGELOG 早就脱节。用户在托盘点「更新记录」看到的是残缺
数据，而 App 那条线（`release.py` 自动生成的 `versions.json`）反而是全的。

现在 `release.py` 接管：历史**永远从 CHANGELOG 全量重建**。本文件钉住四件事：

1. server / app 两条线**不能串**（CHANGELOG 里它们是混着写的）
2. 未发布的节不能进历史
3. 说明取标题里那句人写的一行摘要，不是正文里的 `###` 分类名
   （早年取分类名会得到 `- 安全`、`- 体验` 这种零信息量的条目）
4. 写出来的清单必须能被**真正的消费端**（`updater._norm_item`）解析通过
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_release():
    spec = importlib.util.spec_from_file_location(
        "release_script_manifest", ROOT / "scripts" / "release.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_script_manifest"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def rel():
    return _load_release()


SAMPLE = """\
# 变更记录

## 未发布（v9.9.9 候选 · 2026-11-08 值守批）

### 修复

- 还没发的东西

## 2026-11-07 — v4.0.11：自主值守批（8 个修复 + 质量基建）

### 安全

- 反馈文件可被伪造

### 体验

- txt/md 附件能传不能读

## 2026-11-04 — 服务端 v3.96 + App v1.1.25：自定义更新地址（可加多条，局域网优先）

正文。

## 2026-11-01 — App v1.1.20（一）：真机反馈两连修

正文。

## 2026-11-07 — 反馈通道加固 + 依赖清单源码对账（服务端修复批，未发版）

无版本号，也要排除。
"""


class TestParseLines:
    def test_server_and_app_do_not_mix(self, rel):
        """两条线各自只认自己那串版本号 —— 串了会让用户以为手机装了 4.0.11。"""
        srv = rel.parse_update_history(SAMPLE, "server")
        app = rel.parse_update_history(SAMPLE, "app")
        assert [x["version"] for x in srv] == ["4.0.11", "3.96"]
        assert [x["version"] for x in app] == ["1.1.25", "1.1.20"]

    def test_unreleased_sections_excluded(self, rel):
        """`未发布` / `未发版` 两类标题一律不进历史。"""
        for line in ("server", "app"):
            versions = [x["version"] for x in rel.parse_update_history(SAMPLE, line)]
            assert "9.9.9" not in versions

    def test_new_to_old_order(self, rel):
        srv = rel.parse_update_history(SAMPLE, "server")
        assert srv[0]["published"] >= srv[-1]["published"]

    def test_duplicate_version_kept_once(self, rel):
        md = SAMPLE + "\n## 2026-11-07 — v4.0.11：同一个版本的第二节\n"
        srv = rel.parse_update_history(md, "server")
        assert [x["version"] for x in srv].count("4.0.11") == 1

    def test_bad_line_rejected(self, rel):
        with pytest.raises(ValueError):
            rel.parse_update_history(SAMPLE, "mobile")


class TestNotes:
    def test_summary_is_title_text_not_section_names(self, rel):
        """说明取标题冒号后那句，不是正文 `###` 分类名。"""
        srv = {x["version"]: x["notes"] for x in rel.parse_update_history(SAMPLE, "server")}
        assert srv["4.0.11"] == "自主值守批（8 个修复 + 质量基建）"
        assert srv["3.96"] == "自定义更新地址（可加多条，局域网优先）"
        assert "安全" not in srv["4.0.11"]

    def test_parenthesised_ordinal_stripped(self, rel):
        """`App v1.1.20（一）：xxx` 里的 `（一）` 不是说明的一部分。"""
        app = {x["version"]: x["notes"] for x in rel.parse_update_history(SAMPLE, "app")}
        assert app["1.1.20"] == "真机反馈两连修"

    def test_markdown_decoration_removed(self, rel):
        """说明是给纯文本列表用的，`**` 和反引号必须去掉。"""
        assert rel._clean_note("**加粗** 与 `代码`") == "加粗 与 代码"

    def test_leading_build_number_removed(self, rel):
        """`App +124` 这类标题会残留 `124：`，要去掉。"""
        assert rel._clean_note("124：双更新源") == "双更新源"

    def test_no_empty_notes_on_real_changelog(self, rel):
        """真 CHANGELOG 上不许出现空说明 —— 空条目在界面上就是一行光秃秃的版本号。"""
        md = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for line in ("server", "app"):
            empty = [x["version"] for x in rel.parse_update_history(md, line)
                     if not x["notes"]]
            assert not empty, f"{line} 线这些版本没有说明：{empty[:5]}"


class TestSyncManifest:
    def _setup(self, rel, tmp_path, monkeypatch, changelog: str = SAMPLE):
        inst = tmp_path / "inst"
        (inst / "updates" / "app").mkdir(parents=True)
        cl = tmp_path / "CHANGELOG.md"
        cl.write_text(changelog, encoding="utf-8")
        monkeypatch.setattr(rel, "CHANGELOG", cl)
        monkeypatch.setattr(rel, "find_install_dir", lambda: inst)
        return inst

    def test_writes_manifest_consumable_by_updater(self, rel, tmp_path, monkeypatch):
        """写完的清单必须能被真正的消费端解析 —— 否则托盘静默回落到 GitHub。"""
        from lanshare_cli import updater as U

        inst = self._setup(rel, tmp_path, monkeypatch)
        exe = inst / "updates" / "LANShare-Setup-4.0.11.exe"
        exe.write_bytes(b"fake-server-installer")
        rel.sync_update_manifest("server", "4.0.11", exe)

        data = json.loads((inst / "updates" / "index.json").read_text(encoding="utf-8"))
        assert data["schema"] == 1
        assert data["latest"]["version"] == "4.0.11"
        assert data["latest"]["url"] == exe.name
        latest = U._norm_item(data["latest"], "http://host/updates", need_file=True)
        assert latest["sha256"] and latest["size"] == exe.stat().st_size
        for h in data["history"]:
            U._norm_item(h, "http://host/updates", need_file=False)   # 不许抛

    def test_history_rebuilt_not_appended(self, rel, tmp_path, monkeypatch):
        """重复跑结果一致（全量重建），不会越跑越长。"""
        inst = self._setup(rel, tmp_path, monkeypatch)
        exe = inst / "updates" / "LANShare-Setup-4.0.11.exe"
        exe.write_bytes(b"x")
        rel.sync_update_manifest("server", "4.0.11", exe)
        first = (inst / "updates" / "index.json").read_text(encoding="utf-8")
        rel.sync_update_manifest("server", "4.0.11", exe)
        second = (inst / "updates" / "index.json").read_text(encoding="utf-8")
        assert first == second
        assert len(json.loads(second)["history"]) == 2      # SAMPLE 里 server 就两条

    def test_old_manifest_backed_up(self, rel, tmp_path, monkeypatch):
        """生产目录里的文件，覆盖前必须留 .bak。"""
        inst = self._setup(rel, tmp_path, monkeypatch)
        old = inst / "updates" / "index.json"
        old.write_text('{"schema":1,"latest":{"version":"0.0.1"}}', encoding="utf-8")
        exe = inst / "updates" / "LANShare-Setup-4.0.11.exe"
        exe.write_bytes(b"x")
        rel.sync_update_manifest("server", "4.0.11", exe)
        assert old.with_suffix(".json.bak").read_text(encoding="utf-8").startswith(
            '{"schema":1,"latest":{"version":"0.0.1"}}')

    def test_version_match_not_confused_with_longer_version(self, rel, tmp_path, monkeypatch):
        """`4.0.1` 不能匹配到 `4.0.11.exe` —— 差一位就是给用户一个错误的包。"""
        inst = self._setup(rel, tmp_path, monkeypatch)
        (inst / "updates" / "LANShare-Setup-4.0.11.exe").write_bytes(b"newer")
        data_changelog = SAMPLE.replace("v4.0.11", "v4.0.1")
        (inst.parent / "CHANGELOG.md").write_text(data_changelog, encoding="utf-8")
        rel.sync_update_manifest("server", "4.0.1", None)
        data = json.loads((inst / "updates" / "index.json").read_text(encoding="utf-8"))
        got = {h["version"]: h.get("url") for h in data["history"]}
        assert got["4.0.1"] is None          # 目录里没有 4.0.1 的包，就该留空

    def test_app_line_goes_to_app_subdir(self, rel, tmp_path, monkeypatch):
        """App 线的清单落 `updates/app/`，两条线各有各的文件。"""
        inst = self._setup(rel, tmp_path, monkeypatch)
        apk = inst / "updates" / "app" / "lanshare-1.1.25+125.apk"
        apk.write_bytes(b"x")
        rel.sync_update_manifest("app", "1.1.25", apk)
        assert (inst / "updates" / "app" / "index.json").is_file()
        assert not (inst / "updates" / "index.json").exists()

    def test_build_written_for_latest_and_history(self, rel, tmp_path, monkeypatch):
        """build 必须写进清单。

        2026-11-08 实机：清单里没有 build → App 拿到的全是 0 →
        关于页显示「服务端分发版本 v4.1.0 (0)」，更新记录页**每一行**
        都挂上「最新」标签（因为 0 == 0）。
        """
        inst = self._setup(rel, tmp_path, monkeypatch)
        apk = inst / "updates" / "app" / "LANShare-App-1.1.25+138.apk"
        apk.write_bytes(b"x")
        monkeypatch.setattr(rel, "read_app_version", lambda: ("1.1.25", 138))
        rel.sync_update_manifest("app", "1.1.25", apk)

        data = json.loads((inst / "updates" / "app" / "index.json").read_text(encoding="utf-8"))
        assert data["latest"]["build"] == 138, "latest.build 必须是真实 versionCode"
        top = data["history"][0]
        # 当前版本的历史条目也要被覆盖成真实 versionCode，
        # 否则「当前 / 最新」两个标签对不上同一行。
        assert top["version"] == "1.1.25" and top["build"] == 138
        for h in data["history"]:
            assert isinstance(h.get("build"), int) and h["build"] > 0, h

    def test_artifact_copied_into_updates_dir(self, rel, tmp_path, monkeypatch):
        """产物不在 updates/ 里时必须复制进去。

        2026-11-08 实测踩到：发版脚本把 `build/out` 里那个 exe 的**文件名**写进
        清单的 `latest.url`，但文件从来没被搬进 `updates/` —— 托盘照样 404。
        清单只写文件名，Caddy 按 `/updates/*` 静态下发，产物必须在同一目录。
        """
        inst = self._setup(rel, tmp_path, monkeypatch)
        build_out = tmp_path / "build"
        build_out.mkdir()
        exe = build_out / "LANShare-Setup-4.0.11.exe"
        exe.write_bytes(b"installer-bytes")

        rel.sync_update_manifest("server", "4.0.11", exe)

        copied = inst / "updates" / "LANShare-Setup-4.0.11.exe"
        assert copied.is_file(), "产物没被复制进 updates 目录"
        data = json.loads((inst / "updates" / "index.json").read_text(encoding="utf-8"))
        assert data["latest"]["url"] == copied.name
        # 历史条目里凡是给了 url 的，都必须真能下到
        for h in data["history"]:
            if h.get("url"):
                assert (inst / "updates" / h["url"]).is_file(), h["url"]

    def test_no_server_installed_is_quiet(self, rel, tmp_path, monkeypatch):
        """没装服务端（注册表没有）时安静跳过，不该让发版脚本失败。"""
        monkeypatch.setattr(rel, "find_install_dir", lambda: None)
        rel.sync_update_manifest("server", "4.0.11", None)   # 不抛

    def test_empty_changelog_keeps_manifest(self, rel, tmp_path, monkeypatch):
        """解析不出任何版本时保持原样，别把已有清单清空。"""
        inst = self._setup(rel, tmp_path, monkeypatch, changelog="# 空\n")
        target = inst / "updates" / "index.json"
        target.write_text('{"schema":1,"latest":{"version":"9.9.9"}}', encoding="utf-8")
        rel.sync_update_manifest("server", "4.0.11", None)
        assert json.loads(target.read_text(encoding="utf-8"))["latest"]["version"] == "9.9.9"


class TestCustomHistoryNotTruncated:
    def test_custom_source_keeps_far_more_than_20(self, monkeypatch):
        """自定义源是自家清单，历史要「看全部」—— 20 条的上限必须已经去掉。

        （改为 500 只是防别人塞超长清单，正常量级碰不到。）
        """
        from lanshare_cli import updater as U

        manifest = {"schema": 1,
                    "latest": {"version": "9.9.9", "url": "a.exe",
                               "sha256": "0" * 64, "size": 1},
                    "history": [{"version": f"1.0.{i}", "published": "2026-01-01",
                                 "notes": "x"} for i in range(60)]}
        monkeypatch.setattr(U, "_http_json", lambda *a, **k: manifest)
        got = U.fetch_custom("https://example.com/updates")
        assert len(got["history"]) == 60

    def test_github_page_still_bounded(self):
        from lanshare_cli import updater as U

        assert U.GITHUB_HISTORY == 30
        assert U.MAX_HISTORY >= 100
