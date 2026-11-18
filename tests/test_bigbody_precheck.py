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


def test_chunked_chunk_upload_allowed_for_cloudflare(client):
    """分片端点**放行** chunked（2026-11-18 改，实测驱动）。

    为什么改：Cloudflare 会把请求体重新编码成 `Transfer-Encoding: chunked`。
    原来这道闸一律拒 chunked，结果走 CF 隧道时分片上传 100% 失败 ——
    实测 1MB 就打回 413（同一份客户端打本机 40MB/s、打 FRP 5.79MB/s，
    只有 CF 拒）。而 App 在公网只走 CF，等于大文件上传在门外彻底不可用。

    为什么放行是安全的：这个端点在 uploads.py 里逐块读流时按 expected 硬卡
    （`if size > expected: overflow = True; break`），超发立即停止接收 ——
    不依赖 Content-Length 也能拦住灌包，强度**高于**原来的 CL 预检
    （CL 是客户端自报值可以撒谎，累计字节数不会）。

    断言：不再 413，而是放行到端点（无凭据 → 401）。
    """
    def gen():
        yield b"x" * (1024 * 1024 + 100)

    r = client.post("/api/upload/chunk/fakesid?index=0", content=gen(),
                    headers={"Host": "lan.local:8765"})
    assert r.status_code != 413, "分片端点应放行 chunked（CF 会把 body 转 chunked）"
    assert r.status_code in (401, 404), f"应放行到端点，实际 {r.status_code}"


def test_chunked_still_rejected_on_other_big_body_endpoints(client):
    """放行只针对分片端点 —— feedback 等其它大 body 端点仍一律拒 chunked。

    这些端点的体积上限依赖 CL 预判，没有流式逐块防线，放行等于开灌包口子。
    """
    def gen():
        yield b"x" * 1024

    r = client.post("/api/feedback", content=gen(),
                    headers={"Host": "lan.local:8765",
                             "Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413, "feedback 这类端点必须继续拒 chunked"


def test_chunked_dot_segment_cannot_smuggle_into_upload_fallback(client):
    """`..` 不能让 chunked 放行扩散到 upload_file 兜底路由（2026-11-18 review）。

    洞的形状：
      POST /api/upload/chunk/../feedback   （或 %2e%2e 编码）
      中间件按 startswith("/api/upload/chunk/") 放行 chunked
      路由层不做 dot-segment 归一化 → 落到 POST /api/upload/{path:path} → upload_file
      而 upload_file 有 `file: UploadFile = File(...)`，FastAPI **先解析 multipart
      再跑 require_user** → 匿名、无体积上限、单请求可灌 Caddy 的 25GB 进 TEMP。

    实测复现过的旁证：POST /api/upload/chunk/../../healthz 返回 401
    （真 /healthz 是 200），证明 `..` 原样穿过了 Caddy。

    修法后这些形状必须**在中间件就被拒**（413），绝不能出现「等 body」。
    """
    def gen():
        yield b"x" * 1024

    for bad in ("/api/upload/chunk/../feedback",
                "/api/upload/chunk/x/y",        # 多段：不是合法分片路由
                "/api/upload/chunk/",           # 空 sid
                "/api/upload/chunk/%2e%2e/feedback"):
        r = client.post(bad, content=gen(),
                        headers={"Host": "lan.local:8765",
                                 "Content-Type": "multipart/form-data; boundary=x"})
        assert r.status_code == 413, (
            f"{bad} 必须在中间件被 413 拦掉，实际 {r.status_code}")


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
