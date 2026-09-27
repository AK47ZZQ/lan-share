"""大 body 白名单的 CL 预检 + feedback 附件流式落盘（v3.67.4，移植自分叉批 P1）。

大体积白名单（/api/upload*、/api/feedback）此前无上限预检：FastAPI 先收完
body 才做依赖鉴权，匿名请求可把任意体积灌进 TEMP（实测匿名 POST
/api/feedback 挂起 = 服务器在等完整 body）。
消融锚：
- 摘掉 asgi 的 big_body 预检块 → 预检钉红（匿名巨型 chunk 返回 401 而非 413）；
- feedback 退回 `await up.read()` → 附件钉红依据行为差异（超限 422 与
  无残留仍成立，但全量进内存回归不可行为化观测）——本钉主要守护
  "超限 422 + 半截文件不残留 + 正常小附件仍成功"。
"""
import io

from conftest import ADMIN_PASS, ADMIN_USER


def test_anonymous_chunk_upload_huge_body_413(client, monkeypatch):
    import app.asgi as asgi_mod
    monkeypatch.setattr(asgi_mod, "MAX_CHUNK_SIZE", 8)   # cap ≈ 8MB+8
    r = client.post("/api/upload/chunk/fakesid?index=0",
                    content=b"x" * (9 * 1024 * 1024),
                    headers={"Host": "lan.local:8765"})
    assert r.status_code == 413, "匿名巨型 chunk 应在中间件 413，而非缓冲后再 401"


def test_chunked_chunk_upload_rejected(client):
    """chunked（无 CL）在白名单路径同样拒——堵"缺 CL 绕过上限预检"。"""
    def gen():
        yield b"x" * (1024 * 1024 + 100)

    r = client.post("/api/upload/chunk/fakesid?index=0", content=gen(),
                    headers={"Host": "lan.local:8765"})
    assert r.status_code == 413


def test_feedback_attachment_over_cap_422_and_no_partial(auth, monkeypatch):
    import app.api.feedback as fb
    monkeypatch.setattr(fb, "FEEDBACK_MAX_ATTACHMENT_MB", 1)
    before = sorted(p.name for p in fb._img_dir().iterdir())
    files = {"files": ("big.jpg", io.BytesIO(b"0" * (2 * 1024 * 1024)), "image/jpeg")}
    r = auth.post("/api/feedback",
                  data={"title": "rv-pin-cl-precheck", "type": "bug"},
                    files=files,
                    headers={"Host": "lan.local:8765"})
    assert r.status_code == 422, "超限附件应 422"
    after = sorted(p.name for p in fb._img_dir().iterdir())
    assert after == before, "半截附件文件残留"


def test_feedback_small_attachment_still_works(auth):
    """正常小附件（<25MB 上限、2 件）不受流式改造影响。"""
    files = [
        ("files", ("a.jpg", io.BytesIO(b"a" * 1024), "image/jpeg")),
        ("files", ("b.txt", io.BytesIO("笔记".encode("utf-8")), "text/plain")),
    ]
    r = auth.post("/api/feedback",
                  data={"title": "rv-pin-small-attach", "type": "suggest"},
                    files=files,
                    headers={"Host": "lan.local:8765"})
    assert r.status_code == 200, r.text


def test_ensure_disk_room_507(monkeypatch):
    """写前余量闸：余量 < MIN_FREE_BYTES → 507（数据盘与 PG 同盘）。
    消融锚：删掉 dav_put / upload_chunk 里的 ensure_disk_room 调用 →
    本钉仍绿（单元级），但闸由接线代码位置守护（两处各一行，代码审查面）。"""
    import collections
    import shutil

    import pytest
    from fastapi import HTTPException

    import app.filetools as ft
    usage = collections.namedtuple("usage", "total used free")(10**9, 0, 10**6)
    monkeypatch.setattr(ft.shutil, "disk_usage", lambda p: usage)
    with pytest.raises(HTTPException) as ei:
        ft.ensure_disk_room()
    assert ei.value.status_code == 507
    usage_ok = collections.namedtuple("usage", "total used free")(10**12, 0, 10**11)
    monkeypatch.setattr(ft.shutil, "disk_usage", lambda p: usage_ok)
    ft.ensure_disk_room(1024)   # 不抛
