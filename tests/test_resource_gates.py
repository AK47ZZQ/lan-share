"""v3.68 资源闸与可见性收口回归钉。

- .chunks 全局在途总量闸（分叉批 P1-⑧ 收尾）：单用户 64GB 上限之外，
  全家并发叠加仍有 CHUNKS_TOTAL_CAP（128GB）硬顶；
- 打包字节预留制（分叉批 P2-5）：/api/zip 与 admin_export 的"检查 free
  → 开写"竞态由进程内预留台账收口；
- 渲染专用执行器（分叉批 P1 收尾，审查 P2-1 修订）：_render_job 双检，并发上限 = 专用池线程数（等待者不占请求线程池、不与后台循环共享默认池）；
- data/tmp 运维目录收口（resolve 403）。

消融锚：
- 摘掉 upload_init 的全局闸 → test_chunks_global_cap_507 红；
- 撤销 reserve_bytes 的 free 判定 → test_reserve_bytes_507 红；
- 摘掉 resolve 的 tmp 分支 → test_tmp_denied 红；
- _render_job 撤双检 → test_render_job_double_check 红。
"""
import pytest
from fastapi import HTTPException

import app.api.uploads as uploads_mod
from app import spaces
from app.services import archive


# ── .chunks 全局在途总量闸 ─────────────────────────────────

def test_chunks_global_cap_507(auth, monkeypatch):
    """在途总量逼近 CHUNKS_TOTAL_CAP 时新会话必须 507。"""
    monkeypatch.setattr(uploads_mod, "CHUNKS_TOTAL_CAP", 1000)
    r = auth.post("/api/upload/init",
                  json={"dest_dir": "private:", "filename": "cap.bin", "size": 2000},
                  headers={"Host": "lan.local:8765"})
    assert r.status_code == 507, r.text


def test_chunks_normal_init_still_works(auth):
    """正常小会话不受全局闸影响。"""
    r = auth.post("/api/upload/init",
                  json={"dest_dir": "private:", "filename": "cap-ok.bin",
                        "size": 1024, "sha256": None},
                  headers={"Host": "lan.local:8765"})
    assert r.status_code == 200, r.text
    sid = r.json().get("session")
    if sid:
        uploads_mod._drop_session(sid)


# ── 打包字节预留制 ─────────────────────────────────────────

def test_reserve_bytes_507_and_release(monkeypatch):
    import collections
    import shutil as _shutil
    usage = collections.namedtuple("usage", "total used free")

    half_g = 750 * 1024**2   # 6G - 0.75G - 0.75G = 4.5G < 5G 底线 → 第二笔必拒
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda p: usage(10**12, 0, 6 * 1024**3))
    archive.reserve_bytes(half_g)                 # free 6G - 预留 0.73G - 底线 5G ≥ 0 → 过
    with pytest.raises(HTTPException) as ei:
        archive.reserve_bytes(half_g)             # 再预留 0.73G：free 6G - 1.5G - 5G < 0 → 拒
    assert ei.value.status_code == 507
    archive.release_bytes(half_g)
    archive.reserve_bytes(half_g)                 # 释放后同量请求恢复放行
    archive.release_bytes(half_g)


# ── 渲染信号量双检 ─────────────────────────────────────────

def test_render_job_double_check(monkeypatch, tmp_path):
    """缓存已存在时 _render_job 不得再触发渲染（排队期他人已完成的
    成果直接复用）。消融锚：撤掉双检 → monkeypatch 的渲染函数被调用 → 红。"""
    import app.api.previews as previews
    cached = tmp_path / "x.jpg"
    cached.write_bytes(b"cached")
    target = tmp_path / "src.jpg"
    target.write_bytes(b"src")

    def boom(*a, **k):
        raise AssertionError("缓存已存在却仍触发渲染")

    monkeypatch.setattr(previews, "generate_thumb", boom)
    previews._render_job(target, cached, ".jpg")   # 不抛即双检生效


def test_render_slot_calls_generate_when_missing(monkeypatch, tmp_path):
    import app.api.previews as previews
    target = tmp_path / "src.jpg"
    target.write_bytes(b"src")
    cached = tmp_path / "x.jpg"
    calls = []
    monkeypatch.setattr(previews, "generate_thumb",
                        lambda t, c, e: calls.append((t, c, e)))
    previews._render_job(target, cached, ".jpg")
    assert calls == [(target, cached, ".jpg")]
    assert cached.exists() is False   # monkeypatch 未真写缓存，属预期


# ── data/tmp 运维目录收口 ──────────────────────────────────

def test_tmp_denied_in_resolve():
    with pytest.raises(HTTPException) as ei:
        spaces.resolve({"user_id": 2, "username": "reviewfix", "role": "member"},
                       "tmp/anything.txt", "read")
    assert ei.value.status_code == 403


def test_reserve_no_corruption_on_507(monkeypatch):
    """审查 P1-1 反向消融钉：reserve 抛 507 后台账必须原样（不得被
    finally 的 release 冲销他人在途量）。调用方约定：只有 reserve 成功
    才记自己的 reserved，finally 只 release_bytes(reserved)。"""
    import collections
    import shutil as _shutil
    usage = collections.namedtuple("usage", "total used free")
    assert archive._reserved_bytes == 0   # 会话内无他测残留（基线自证）
    half_g = 750 * 1024**2
    monkeypatch.setattr(archive.shutil, "disk_usage",
                        lambda p: usage(10**12, 0, 6 * 1024**3))
    archive.reserve_bytes(half_g)                     # A 在途 0.73G
    with pytest.raises(HTTPException):
        archive.reserve_bytes(6 * 1024**3)            # B 超限 507
    assert archive._reserved_bytes == half_g, "507 路径冲销了 A 的在途预留"
    archive.release_bytes(half_g)                     # A 正常收尾
    assert archive._reserved_bytes == 0
