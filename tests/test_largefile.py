"""大文件上传优化（v3.6）：自适应分片 + 分片路径 64GB 上限 + WebDAV 20GB 上限。

只测接口行为与配置函数，不真正传输大数据。
"""

from conftest import ADMIN_USER  # noqa: F401
import pytest

from app.config import CHUNK_SIZE, MAX_CHUNKED_SIZE, chunk_size_for

GB = 1024 * 1024 * 1024


# ── 自适应分片：纯函数边界 ────────────────────────────────

def test_chunk_size_boundaries():
    assert chunk_size_for(1) == CHUNK_SIZE                 # 5MB
    assert chunk_size_for(2 * GB) == CHUNK_SIZE            # ≤2GB → 5MB
    assert chunk_size_for(2 * GB + 1) == 16 * 1024 * 1024  # >2GB → 16MB
    assert chunk_size_for(5 * GB) == 16 * 1024 * 1024
    assert chunk_size_for(5 * GB + 1) == 32 * 1024 * 1024  # >5GB → 32MB
    assert chunk_size_for(10 * GB) == 32 * 1024 * 1024
    assert chunk_size_for(10 * GB + 1) == 64 * 1024 * 1024 # >10GB → 64MB
    assert chunk_size_for(64 * GB) == 64 * 1024 * 1024


def test_chunk_size_deterministic():
    # 断点续传依赖：同一 total 多次调用结果一致
    assert chunk_size_for(3 * GB) == chunk_size_for(3 * GB)


# ── 分片上传接口 ──────────────────────────────────────────

def test_init_accepts_over_2gb_with_adaptive_chunk(auth, tmp_path):
    # 3GB：旧单请求上限 2GB 不适用于分片路径；分片应自适应为 16MB
    r = auth.post("/api/upload/init", json={
        "dest_dir": "", "filename": "big-3gb.bin", "size": 3 * GB})
    assert r.status_code == 200
    d = r.json()
    assert d["instant"] is False
    assert d["chunk_size"] == 16 * 1024 * 1024
    # 清理会话，避免留下过期数据
    from app.db import _db, _db_lock
    with _db_lock:
        _db().execute("DELETE FROM upload_sessions WHERE id=?", (d["session"],))
        _db().commit()


def test_init_rejects_over_hard_cap(auth):
    r = auth.post("/api/upload/init", json={
        "dest_dir": "", "filename": "too-big.bin", "size": MAX_CHUNKED_SIZE + 1})
    assert r.status_code == 413


def test_chunk_index_bound_uses_adaptive_size(auth):
    # 3GB @16MB = 192 块；index=192 应越界（若仍按 5MB 算会误通过）
    r = auth.post("/api/upload/init", json={
        "dest_dir": "", "filename": "big-3gb-b.bin", "size": 3 * GB})
    sid = r.json()["session"]
    try:
        assert auth.post(f"/api/upload/chunk/{sid}?index=192").status_code == 400
        assert auth.post(f"/api/upload/chunk/{sid}?index=191").status_code == 200
    finally:
        from app.db import _db, _db_lock
        import shutil
        from app.config import SHARE_ROOT
        from app.api.files import _chunk_dir
        with _db_lock:
            _db().execute("DELETE FROM upload_sessions WHERE id=?", (sid,))
            _db().commit()
        shutil.rmtree(_chunk_dir(sid), ignore_errors=True)


def test_state_reports_adaptive_chunk_size(auth):
    r = auth.post("/api/upload/init", json={
        "dest_dir": "", "filename": "big-6gb.bin", "size": 6 * GB})
    sid = r.json()["session"]
    try:
        st = auth.get(f"/api/upload/state/{sid}").json()
        assert st["chunk_size"] == 32 * 1024 * 1024
    finally:
        from app.db import _db, _db_lock
        from app.api.files import _chunk_dir
        import shutil
        with _db_lock:
            _db().execute("DELETE FROM upload_sessions WHERE id=?", (sid,))
            _db().commit()
        shutil.rmtree(_chunk_dir(sid), ignore_errors=True)


# ── 单请求路径维持 2GB（安全网不放松）────────────────────

def test_simple_upload_still_capped(auth):
    # 伪造成 2GB+ 的 Content-Length 不可行；这里验证配置本身未放松
    from app.config import MAX_UPLOAD_SIZE, MAX_WEBDAV_SIZE
    assert MAX_UPLOAD_SIZE == 2 * GB
    assert MAX_WEBDAV_SIZE == 20 * GB
