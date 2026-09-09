"""大文件上传优化（v3.6）：自适应分片 + 分片路径 64GB 上限 + WebDAV 20GB 上限。

只测接口行为与配置函数，不真正传输大数据。
"""

from conftest import ADMIN_USER
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


def test_chunk_rejects_wrong_body_size(auth):
    # 暂停/断网会产生半截请求体：非末块必须整块接收，不足即拒且不落 part（原子写保护续传）
    import time as _time
    r = auth.post("/api/upload/init", json={
        "dest_dir": "", "filename": f"trunc-{_time.time()}.bin", "size": 12 * 1024 * 1024})
    sid = r.json()["session"]
    try:
        # index=0 应收满 5MB；只发 1MB → 400，且不产生 part-000000
        resp = auth.post(f"/api/upload/chunk/{sid}?index=0", content=b"x" * (1024 * 1024))
        assert resp.status_code == 400
        from app.api.files import _chunk_dir
        assert not (_chunk_dir(sid) / "part-000000").exists()
        st = auth.get(f"/api/upload/state/{sid}").json()
        assert 0 not in st["received"]
        # 补发整块 → 成功落 part 且无 _tmp_ 残留（原子写）
        ok = auth.post(f"/api/upload/chunk/{sid}?index=0", content=b"x" * (5 * 1024 * 1024))
        assert ok.status_code == 200
        from app.api.files import _chunk_dir
        assert (_chunk_dir(sid) / "part-000000").exists()
        assert not (_chunk_dir(sid) / "_tmp_000000").exists()
    finally:
        from app.db import _db, _db_lock
        from app.api.files import _chunk_dir
        import shutil
        with _db_lock:
            _db().execute("DELETE FROM upload_sessions WHERE id=?", (sid,))
            _db().commit()
        shutil.rmtree(_chunk_dir(sid), ignore_errors=True)


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
        # 末块(index=191)应收 3GB - 191*16MB = 18789120 字节（尺寸守卫要求整块）
        last = 3 * GB - 191 * (16 * 1024 * 1024)
        assert auth.post(f"/api/upload/chunk/{sid}?index=191",
                         content=b"\0" * last).status_code == 200
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


# ── 覆盖上传：旧版本进回收站（分片/秒传/单请求三条路径）──

def _trash_names(client):
    from app.db import _db
    return {r["name"] for r in _db().execute("SELECT name FROM trash").fetchall()}


def test_chunked_upload_overwrite_trashes_old(auth):
    data1 = b"version-one-content"
    data2 = b"version-two-content-different"
    name = "overwrite-test.txt"
    for data in (data1, data2):
        r = auth.post("/api/upload/init", json={
            "dest_dir": "", "filename": name, "size": len(data),
            "sha256": __import__("hashlib").sha256(data).hexdigest()})
        d = r.json()
        if d.get("instant"):
            continue
        sid = d["session"]
        assert auth.post(f"/api/upload/chunk/{sid}?index=0", content=data).status_code == 200
        assert auth.post(f"/api/upload/finish/{sid}").status_code == 200
    assert name in _trash_names(auth)  # 第一次上传的旧版本被回收站接住


def test_simple_upload_overwrite_trashes_old(auth):
    name = "overwrite-simple.txt"
    assert auth.post("/api/upload/?path=", files={"file": (name, b"first")}).status_code == 200
    assert auth.post("/api/upload/?path=", files={"file": (name, b"second")}).status_code == 200
    assert name in _trash_names(auth)


def test_upload_sanitizes_windows_illegal_chars(auth):
    # 手机端（Linux FS）可传来 Windows 非法字符 ? * < > | ——须清洗而非 500
    name = 'bad?name|with*chars<x>.txt'
    r = auth.post("/api/upload/?path=", files={"file": (name, b"content")})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == 'bad_name_with_chars_x_.txt'
    auth.delete("/api/files/" + r.json()["path"])


# ── 分片契约（07 架构审查 P0 回归钉）─────────────────────
# 历史 bug：服务端把 >5GB 文件的分片阶梯升到 32/64MB，移动端两引擎的防御
# clamp 写死 16MB 未同步 → App 上传 >5GB 必败。本组测试把"协议上限"钉为
# 单一事实源：MAX_CHUNK_SIZE；init/state 响应必须下发 max_chunk_size 供
# 客户端 clamp，任何一侧擅自越界即红灯。

def test_chunk_size_output_within_protocol_cap():
    from app.config import MAX_CHUNK_SIZE, chunk_size_for
    sizes = [0, 1, CHUNK_SIZE, 2 * GB, 2 * GB + 1, 5 * GB, 5 * GB + 1,
             10 * GB, 10 * GB + 1, MAX_CHUNKED_SIZE, MAX_CHUNKED_SIZE + 1]
    for size in sizes:
        cs = chunk_size_for(size)
        assert 0 < cs <= MAX_CHUNK_SIZE, f"size={size} -> {cs}"


def test_max_chunk_size_is_the_64mb_protocol_value():
    # 客户端（mobile/lib/data/api/protocol.dart kMaxChunkSize）与 Caddyfile
    # 注释都按 64MB 对齐；此常量一旦改动必须三端同步
    from app.config import MAX_CHUNK_SIZE
    assert MAX_CHUNK_SIZE == 64 * 1024 * 1024


def test_upload_init_and_state_declare_max_chunk_size(auth):
    import hashlib
    import time as _time
    r = auth.post("/api/upload/init", json={
        "dest_dir": "", "filename": f"contract-{_time.time()}.bin",
        "size": 6 * GB, "sha256": hashlib.sha256(b"contract").hexdigest()})
    d = r.json()
    assert d.get("instant") is not True
    sid = d["session"]
    # 会话不显式清理（与既有测试一致）：conftest 每轮跑在临时 SHARE_ROOT，
    # 会话行随临时库丢弃，TTL 3 天兜底
    assert d["max_chunk_size"] == 64 * 1024 * 1024
    assert d["chunk_size"] == 32 * 1024 * 1024  # >5GB → 32MB 阶梯（曾致 App 必败的那一档）
    st = auth.get(f"/api/upload/state/{sid}")
    assert st.status_code == 200, st.text
    assert st.json()["max_chunk_size"] == 64 * 1024 * 1024
