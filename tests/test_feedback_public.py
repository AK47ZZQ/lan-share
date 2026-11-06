"""反馈公开通道回归（v4.0.9 P3+P4）。

背景：分发给别人的安装包里**不能带** GitHub 写权限的 token —— 一旦带上，
任何人拆包就能往仓库灌垃圾。但普通用户又没有 GitHub 账号、也不该被要求去
注册一个（Docket 那篇对比文章的原话：「account is the whole problem」）。

解法是把「公开」这个动作从客户端挪到中继端：用户只勾一个复选框，客户端发
`public: true`，由**接收端**用自己的凭据代发 issue。

覆盖：
  * 勾了 public 且接收端有 token → 真的调用 GitHub API 建 issue
  * 勾了 public 但接收端没 token → 静默跳过，反馈照收（不能因为代发失败就丢反馈）
  * issue 链接写回 frontmatter，前端能显示
  * 没勾 public → 一个字节都不往 GitHub 发
"""
import json
import os

import pytest

from app import settings
from app.api import feedback_inbox


def _rand() -> str:
    return os.urandom(3).hex()


def _inbox_token(v: str) -> None:
    settings.set_setting(settings.FEEDBACK_INBOX_TOKEN, v)


# ───────────────────────── 代发 issue ─────────────────────────

class _FakeResp:
    def __init__(self, status_code=201, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class _FakeClient:
    """拦 httpx.AsyncClient，记录发了哪些请求。"""

    calls: list = []
    reply = _FakeResp()

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None, **kw):
        _FakeClient.calls.append({"url": url, "json": json, "headers": headers or {}})
        return _FakeClient.reply


@pytest.fixture
def fake_gh(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.reply = _FakeResp(201, {"html_url": "https://github.com/o/r/issues/7"})
    monkeypatch.setattr(feedback_inbox.httpx, "AsyncClient", _FakeClient)
    settings.set_setting(settings.FEEDBACK_GH_REPO, "owner/repo")
    settings.set_setting(settings.FEEDBACK_GH_TOKEN, "ghp_fake")
    yield _FakeClient
    settings.set_setting(settings.FEEDBACK_GH_REPO, "")
    settings.set_setting(settings.FEEDBACK_GH_TOKEN, "")


def _post(client, tok, **extra):
    body = {"id": "20260101" + _rand(), "title": "公开一条", "source": "src" + _rand(), **extra}
    return client.post(
        "/api/feedback/inbox", json=body, headers={"Authorization": f"Bearer {tok}"}
    )


def test_public_triggers_issue_creation(client, fake_gh):
    tok = "t-" + _rand()
    _inbox_token(tok)
    r = _post(client, tok, public=True, body="正文")
    assert r.status_code == 200
    assert len(fake_gh.calls) == 1
    call = fake_gh.calls[0]
    assert call["url"].endswith("/repos/owner/repo/issues")
    assert "Bearer ghp_fake" in call["headers"]["Authorization"]
    assert r.json()["issue_url"] == "https://github.com/o/r/issues/7"


def test_no_public_flag_never_calls_github(client, fake_gh):
    """没勾公开就一个字节都不该发出去。"""
    tok = "t-" + _rand()
    _inbox_token(tok)
    r = _post(client, tok, body="只给开发端看")
    assert r.status_code == 200
    assert fake_gh.calls == []
    assert r.json()["issue_url"] == ""


def test_public_without_receiver_token_still_accepts(client):
    """接收端没配 GitHub 凭据时，反馈照收 —— 收件比代发重要。"""
    settings.set_setting(settings.FEEDBACK_GH_REPO, "")
    settings.set_setting(settings.FEEDBACK_GH_TOKEN, "")
    tok = "t-" + _rand()
    _inbox_token(tok)
    r = _post(client, tok, public=True)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["issue_url"] == ""


def test_issue_failure_does_not_drop_feedback(client, fake_gh):
    """GitHub 500 了也不能丢反馈。"""
    fake_gh.reply = _FakeResp(500, {"message": "boom"})
    tok = "t-" + _rand()
    _inbox_token(tok)
    r = _post(client, tok, public=True)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["issue_url"] == ""


def test_issue_url_written_to_frontmatter(client, fake_gh):
    tok = "t-" + _rand()
    _inbox_token(tok)
    fid = "20260101" + _rand()
    client.post(
        "/api/feedback/inbox",
        json={"id": fid, "title": "带链接", "source": "s" + _rand(), "public": True},
        headers={"Authorization": f"Bearer {tok}"},
    )
    from app.config import SHARE_ROOT

    hit = list((SHARE_ROOT / "反馈" / "收件箱").rglob(f"{fid}.md"))
    assert hit, "反馈文件应当落盘"
    text = hit[0].read_text(encoding="utf-8")
    assert "公开: 是" in text
    assert "https://github.com/o/r/issues/7" in text


# ───────────────────────── 配置端点 ─────────────────────────

def test_feedback_config_masks_tokens(auth):
    settings.set_setting(settings.FEEDBACK_RELAY_TOKEN, "super-secret-value")
    r = auth.get("/api/admin/feedback-config")
    assert r.status_code == 200
    data = r.json()
    assert "super-secret-value" not in json.dumps(data)
    assert data["token"].startswith("已设置")


def test_feedback_config_rejects_remote_plain_http(auth):
    """对外的接收端必须 https —— token 会跟着请求发出去。"""
    r = auth.post("/api/admin/feedback-config", json={"url": "http://example.com/inbox"})
    assert r.status_code == 422
    assert "https" in r.json()["detail"]


def test_feedback_config_allows_loopback_http(auth):
    r = auth.post(
        "/api/admin/feedback-config",
        json={"url": "http://127.0.0.1:8766/api/feedback/inbox"},
    )
    assert r.status_code == 200


def test_feedback_config_partial_update_keeps_token(auth):
    """只改地址不能把 token 清空 —— 界面显示的是脱敏文案，用户看不出来。"""
    settings.set_setting(settings.FEEDBACK_RELAY_TOKEN, "keep-me")
    r = auth.post(
        "/api/admin/feedback-config",
        json={"url": "https://relay.example.com/inbox", "enabled": True},
    )
    assert r.status_code == 200
    assert settings.get_setting(settings.FEEDBACK_RELAY_TOKEN) == "keep-me"
