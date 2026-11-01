"""反馈上报与收件箱回归（2026-10-31）。

背景：反馈原来只落在本机 data/反馈/，别人装了 LAN Share 开发端看不到。
新增两条上报通道（HTTP 收件箱 / GitHub Issue）后补的测试。

覆盖：
  * 收件箱没配 token 时一律 403（不能变成开放接口）
  * token 不匹配 403
  * 正常上报落盘，且文件名/目录名经过清洗（防路径穿越）
  * 重复编号不重复写
  * 上报体只带约定字段，不带路径/附件之类
"""
import json
import os

from app import settings
from app.config import SHARE_ROOT
from app.services import feedback_relay


def _rand() -> str:
    return os.urandom(3).hex()


def _token(v: str) -> None:
    settings.set_setting(settings.FEEDBACK_INBOX_TOKEN, v)


def test_inbox_closed_when_no_token(client):
    """没配 token 就不开门。宁可收不到，也不能当公网垃圾桶。"""
    _token("")
    r = client.post("/api/feedback/inbox", json={"id": "x1", "title": "hi"})
    assert r.status_code == 403
    assert "未启用" in r.json()["detail"]


def test_inbox_rejects_wrong_token(client):
    _token("right-token")
    r = client.post(
        "/api/feedback/inbox",
        json={"id": "x2", "title": "hi"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 403
    assert "token" in r.json()["detail"]


def test_inbox_accepts_correct_token_and_writes_md(client):
    tok = "t-" + _rand()
    _token(tok)
    src = "home" + _rand()
    fid = "2026010100" + _rand()
    r = client.post(
        "/api/feedback/inbox",
        json={
            "id": fid,
            "title": "上传很慢",
            "type": "bug",
            "body": "大文件传一半断了",
            "app_ver": "1.2.3",
            "device": "Pixel 7",
            "user": "zhang",
            "created": "2026-01-01 10:00",
            "source": src,
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    path = SHARE_ROOT / "反馈" / "收件箱" / src / f"{fid}.md"
    assert path.is_file(), f"没写出来: {path}"
    text = path.read_text(encoding="utf-8")
    assert "上传很慢" in text
    assert "来源: " + src in text
    # 原始 JSON 也留一份，方便排查上报体本身的问题
    raw = SHARE_ROOT / "反馈" / "收件箱" / src / f"{fid}.json"
    assert raw.is_file()


def test_inbox_sanitises_path_traversal(client):
    """来源和编号都不能带 ../ —— 不然能把文件写到收件箱外面去。"""
    tok = "t-" + _rand()
    _token(tok)
    r = client.post(
        "/api/feedback/inbox",
        json={"id": "../../evil", "title": "x", "source": "../../../etc"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    inbox = SHARE_ROOT / "反馈" / "收件箱"
    # 不该在收件箱之外出现 evil.md
    outside = SHARE_ROOT / "evil.md"
    assert not outside.exists()
    # 落点必须在收件箱内
    for p in inbox.rglob("*.md"):
        assert inbox in p.parents or p.parent == inbox


def test_inbox_dedupes_same_id(client):
    tok = "t-" + _rand()
    _token(tok)
    src = "dup" + _rand()
    fid = "2026010101" + _rand()
    body = {"id": fid, "title": "重复提交", "source": src}
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/feedback/inbox", json=body, headers=h).status_code == 200
    second = client.post("/api/feedback/inbox", json=body, headers=h)
    assert second.status_code == 200
    assert second.json().get("dup") is True


def test_relay_payload_excludes_sensitive_fields():
    """上报体只该有这九个字段。任何路径/附件/Token 都不许混进去。"""
    item = {
        "id": "20260101-abc",
        "title": "标题",
        "type": "idea",
        "body": "正文",
        "app_ver": "1.2.3",
        "device": "Pixel",
        "user": "zhang",
        "created": "2026-01-01",
        # 下面这些就算传进来也不许出现在上报体里
        "path": "C:/Users/secret/photo.jpg",
        "files": ["a.png"],
        "token": "should-not-leak",
    }
    payload = feedback_relay.build_payload(item)
    assert set(payload) == {
        "id", "title", "type", "body", "app_ver", "device", "user", "created",
        "source",
    }
    blob = json.dumps(payload)
    assert "secret" not in blob
    assert "should-not-leak" not in blob


def test_relay_targets_empty_when_nothing_configured():
    """什么都没配时不该尝试发请求，targets 返回空。"""
    for k in (
        settings.FEEDBACK_RELAY_ENABLED,
        settings.FEEDBACK_RELAY_URL,
        settings.FEEDBACK_GH_ENABLED,
        settings.FEEDBACK_GH_REPO,
    ):
        settings.set_setting(k, "")
    assert feedback_relay.targets() == []


# ──────────────────────────────────────────────────────────────
# 上报脱敏（2026-11-01）
# ──────────────────────────────────────────────────────────────

class TestRelayScrub:
    """反馈正文和身份字段发出去之前必须洗一遍。

    用户提交反馈时想不到要删服务器地址、内网 IP、同事真名 —— 这些东西
    一旦进开发者的收件箱就等于出了用户的机器。
    """

    def test_scrubs_urls_and_ips(self):
        from app.services.feedback_relay import _scrub

        out = _scrub("我的服务器是 https://192.0.2.10:8765 打不开", 2000)
        assert "192.0.2.10" not in out
        assert "<地址>" in out

    def test_scrubs_bare_ip(self):
        from app.services.feedback_relay import _scrub

        out = _scrub("连 10.0.0.5 这台不行", 2000)
        assert "10.0.0.5" not in out
        assert "<IP>" in out

    def test_scrubs_windows_path(self):
        from app.services.feedback_relay import _scrub

        out = _scrub(r"文件在 D:\work\a.zip", 2000)
        assert "zhangsan" not in out
        assert "<路径>" in out

    def test_scrubs_email(self):
        from app.services.feedback_relay import _scrub

        out = _scrub("联系我 user@example.com", 2000)
        assert "user@example.com" not in out
        assert "<邮箱>" in out

    def test_keeps_normal_text(self):
        """别把正常内容也洗没了。"""
        from app.services.feedback_relay import _scrub

        out = _scrub("上传大文件时会卡住，重新登录后恢复", 2000)
        assert out == "上传大文件时会卡住，重新登录后恢复"

    def test_pseudonym_is_stable_and_irreversible(self):
        from app.services.feedback_relay import _pseudonym

        a = _pseudonym("admin", "salt1")
        b = _pseudonym("admin", "salt1")
        c = _pseudonym("admin", "salt2")
        assert a == b            # 同一人同一部署 → 能串起来看
        assert a != c            # 不同部署的盐不同 → 不能跨部署关联
        assert "admin" not in a
        assert len(a) == 12

    def test_payload_has_no_plaintext_identity(self):
        from app.services.feedback_relay import build_payload

        payload = build_payload({
            "id": "f1", "title": "上传失败", "type": "bug",
            "body": "在 https://192.0.2.10:8765 上，用户 zhangsan 传不上去",
            "app_ver": "1.1.20", "device": "ZZQ的台式机",
            "user": "zhangsan", "created": "2026-11-01",
        })
        blob = str(payload)
        assert "192.0.2.10" not in blob
        assert "zhangsan" not in blob
        assert "ZZQ的台式机" not in blob
        # 该留的还在
        assert payload["app_ver"] == "1.1.20"
        assert payload["type"] == "bug"
