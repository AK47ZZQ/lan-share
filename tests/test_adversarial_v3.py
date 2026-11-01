"""对抗测试第三轮（2026-11-01）。

针对本轮改动（更新镜像 / 反馈脱敏 / 发版脚本）新增的攻击面。每个用例
docstring 里写清楚「这里失守会怎样」—— 以后改代码时才知道哪些是危险区。

## 覆盖范围

1. **收件箱鉴权**：前缀、后缀、缺 Bearer、Basic 冒充、大小写、空白
2. **版本信息数据源**：本地文件被改坏 / 被改成路径穿越值时会发生什么
3. **更新历史**：畸形 JSON、超大输入、版本号污染

## 不覆盖（别以为这里没测就安全）

- 登录鉴权（`require_user`）—— 见 test_auth*.py
- 文件上传路径穿越 —— 见 test_adversarial_filename.py
- 安装器注入 —— 见 test_lanshare_cli.py
"""

from __future__ import annotations

import json
import os

import pytest

from app import settings
from app.config import SHARE_ROOT

DIST = SHARE_ROOT / "App安装包"


def _rand() -> str:
    return os.urandom(4).hex()


def _set_token(v: str) -> None:
    settings.set_setting(settings.FEEDBACK_INBOX_TOKEN, v)


def _inbox(client, auth: str | None):
    headers = {} if auth is None else {"Authorization": auth}
    return client.post("/api/feedback/inbox", headers=headers,
                       json={"id": _rand(), "title": "t", "body": "b"})


# ──────────────────────────────────────────────────────────────
# 1. 收件箱鉴权变体
#
# 这里失守会怎样：收件箱是公网可达的（要收别人的反馈），
# 鉴权破了就是一个公开的写文件接口 —— 攻击者能往你的磁盘上灌垃圾，
# 也能靠它探测你的服务端版本。
# ──────────────────────────────────────────────────────────────

class TestInboxAuthBypass:
    @pytest.fixture(autouse=True)
    def _token(self):
        _set_token("correct-horse-battery-staple-1234567890")
        yield

    def test_prefix_rejected(self, client):
        """真实 token 的前缀必须被拒 —— 否则能逐字符爆破。"""
        assert _inbox(client, "Bearer correct-horse-battery").status_code == 403

    def test_suffix_rejected(self, client):
        assert _inbox(
            client, "Bearer correct-horse-battery-staple-1234567890EXTRA"
        ).status_code == 403

    def test_one_char_diff_rejected(self, client):
        assert _inbox(
            client, "Bearer correct-horse-battery-staple-1234567891"
        ).status_code == 403

    def test_missing_bearer_prefix_rejected(self, client):
        """裸 token 不带 Bearer 不算。"""
        assert _inbox(client, "correct-horse-battery-staple-1234567890").status_code == 403

    def test_basic_auth_rejected(self, client):
        """换成 Basic 塞同一个 token 不该过。"""
        assert _inbox(
            client, "Basic correct-horse-battery-staple-1234567890"
        ).status_code == 403

    def test_empty_bearer_rejected(self, client):
        assert _inbox(client, "Bearer ").status_code == 403
        assert _inbox(client, "Bearer").status_code == 403

    def test_no_header_rejected(self, client):
        assert _inbox(client, None).status_code == 403

    def test_whitespace_padded_token(self, client):
        """前后空格：实现做了 strip 就放行（这算宽容），
        但绝不能因为空格就整段跳过校验。"""
        r = _inbox(client, "Bearer  correct-horse-battery-staple-1234567890 ")
        # 200 或 403 都可接受，唯独不能 500
        assert r.status_code in (200, 403)

    def test_constant_time_compare_used(self):
        """锁住实现细节：必须是 compare_digest，不能退回 ==。

        这是防回归的守卫 —— 有人把 hmac.compare_digest 改成 == 会在这里红。
        """
        import inspect

        from app.api import feedback_inbox
        src = inspect.getsource(feedback_inbox._token_ok)
        assert "compare_digest" in src


# ──────────────────────────────────────────────────────────────
# 2. 版本信息：本地文件被改坏
#
# 这里失守会怎样：本地优先之后，`App安装包/version.json` 成了第一信任源。
# 谁能写这个目录（同一台机器的其他账户、同步工具、误操作），谁就能控制
# 「App 检测到的新版本」—— 指向一个恶意 APK。所以 filename 必须严格校验。
# ──────────────────────────────────────────────────────────────

class TestVersionFileTampering:
    @pytest.fixture()
    def dist(self, tmp_path, monkeypatch):
        """把 SHARE_ROOT 指到 tmp，避免碰真实数据目录。"""
        from app.api import appver
        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)
        d = tmp_path / "App安装包"
        d.mkdir()
        return d

    def _write(self, d, **kw):
        data = {"version": "9.9.9", "build": 999,
                "filename": "LANShare-App-9.9.9+999.apk", "size": 100}
        data.update(kw)
        (d / "version.json").write_text(json.dumps(data), encoding="utf-8")

    def test_malformed_json_falls_back(self, dist, monkeypatch):
        """坏 JSON 不能让 /api/app/latest 500，要回落到下个数据源。"""
        from fastapi import HTTPException

        from app.api import appver
        (dist / "version.json").write_text("{ not json", encoding="utf-8")

        called = {"github": False}

        def fake_gh():
            called["github"] = True
            return []

        monkeypatch.setattr(appver, "_github_app_releases", fake_gh)
        with pytest.raises(HTTPException):
            appver._load_version()
        assert called["github"], "本地解析失败后必须去试 GitHub"

    def test_traversal_filename_rejected_by_apk_path(self, dist):
        """filename 被改成 ../../ 时，_apk_path 必须拒绝。

        失守后果：/api/app/download 会变成任意文件读取器。
        """
        from fastapi import HTTPException

        from app.api import appver
        for evil in ("../../etc/passwd", r"..\..\Windows\win.ini",
                     "C:/Windows/system32/config/sam", "//server/share/x.apk"):
            with pytest.raises(HTTPException):
                appver._apk_path({"filename": evil})

    def test_missing_build_field_falls_back(self, dist, monkeypatch):
        """缺 build 字段 → 本地不可用 → 回落 GitHub，不崩。"""
        from fastapi import HTTPException

        from app.api import appver
        (dist / "version.json").write_text(
            json.dumps({"version": "9.9.9", "filename": "x.apk"}), encoding="utf-8")
        monkeypatch.setattr(appver, "_github_app_releases", lambda: [])
        with pytest.raises(HTTPException):
            appver._load_version()


# ──────────────────────────────────────────────────────────────
# 3. 更新历史：畸形输入
#
# 这里失守会怎样：versions.json 是 release.py 生成的，但用户可能手改、
# 同步工具可能截断。解析失败不能把整个 /api/app/history 打成 500 ——
# 「关于」页会整块空白，用户以为 App 坏了。
# ──────────────────────────────────────────────────────────────

class TestHistoryTampering:
    @pytest.fixture()
    def dist(self, tmp_path, monkeypatch):
        from app.api import appver
        monkeypatch.setattr(appver, "SHARE_ROOT", tmp_path)
        d = tmp_path / "App安装包"
        d.mkdir()
        return d

    def test_malformed_json_returns_empty_not_500(self, dist, monkeypatch):
        from app.api import appver
        (dist / "versions.json").write_text("[[[", encoding="utf-8")
        monkeypatch.setattr(appver, "_github_app_releases", lambda: [])
        assert appver._load_history() == []

    def test_entries_without_version_skipped(self, dist, monkeypatch):
        from app.api import appver
        (dist / "versions.json").write_text(json.dumps({"items": [
            {"build": 1, "notes": "没有 version"},
            {"version": "1.0.0", "build": "abc"},
            {"version": "1.0.1", "build": 2, "notes": "正常"},
        ]}), encoding="utf-8")
        monkeypatch.setattr(appver, "_github_app_releases", lambda: [])
        items = appver._load_history()
        assert len(items) == 1
        assert items[0]["version"] == "1.0.1"

    def test_negative_and_huge_build_accepted_as_int(self, dist, monkeypatch):
        """build 是负数或极大值时不该崩 —— 反正只是显示用。"""
        from app.api import appver
        (dist / "versions.json").write_text(json.dumps({"items": [
            {"version": "1.0.0", "build": -1},
            {"version": "1.0.1", "build": 10**18},
        ]}), encoding="utf-8")
        monkeypatch.setattr(appver, "_github_app_releases", lambda: [])
        assert len(appver._load_history()) == 2

    def test_huge_history_does_not_hang(self, dist, monkeypatch):
        """5000 条历史要能秒回，不能 O(n²)。"""
        import time

        from app.api import appver
        items = [{"version": f"1.{i}.0", "build": i, "notes": "x" * 100}
                 for i in range(5000)]
        (dist / "versions.json").write_text(json.dumps({"items": items}),
                                            encoding="utf-8")
        monkeypatch.setattr(appver, "_github_app_releases", lambda: [])
        t0 = time.time()
        got = appver._load_history()
        assert len(got) == 5000
        assert time.time() - t0 < 3.0, "解析 5000 条不该超过 3 秒"
