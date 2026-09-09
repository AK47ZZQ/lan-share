"""2026-09-02 App v0.6.0 大批次服务端回归：分享编辑 PATCH / 批量复制 /
App 版本分发端点 / 照片时间线纳入视频（media_type + ffprobe 时长）。

约定同 test_extras：conftest 已建唯一 admin；workdir 夹具给独立子目录。
"""
import os
import shutil
import subprocess

import pytest

from conftest import ADMIN_PASS, ADMIN_USER

from app.config import SHARE_ROOT
from app.main import app


def _rand() -> str:
    return os.urandom(3).hex()


def _bare(client):
    return client.__class__(app, base_url="https://testserver")


def _admin(client):
    c = _bare(client)
    r = c.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200
    return c


# ── ① 分享编辑 PATCH ─────────────────────────────────────────


def _mk_share(auth, workdir, name="a.txt", **form):
    path = os.path.join(SHARE_ROOT, workdir)
    with open(os.path.join(path, name), "w", encoding="utf-8") as f:
        f.write("hello share edit")
    r = auth.post("/api/share", data={"path": f"{workdir}/{name}",
                                      "expires_hours": 24, **form})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_share_edit_expiry_and_max_downloads(auth, workdir):
    token = _mk_share(auth, workdir)
    r = auth.patch(f"/api/shares/{token}",
                   json={"expires_hours": 48, "max_downloads": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["max_downloads"] == 7
    assert body["expires_in"].endswith("h")
    assert int(body["expires_in"][:-1]) in (47, 48)  # 秒级误差


def test_share_edit_owner_guard_and_validation(auth, workdir):
    token = _mk_share(auth, workdir)
    # 未登录 401
    assert _bare(auth).patch(f"/api/shares/{token}", json={"expires_hours": 5}
                             ).status_code == 401
    # 空补丁 400
    assert auth.patch(f"/api/shares/{token}", json={}).status_code == 400
    # 非法值 400
    assert auth.patch(f"/api/shares/{token}",
                      json={"expires_hours": 0}).status_code == 400
    assert auth.patch(f"/api/shares/{token}",
                      json={"max_downloads": -1}).status_code == 400
    # 不存在的分享 404
    assert auth.patch("/api/shares/no-such-token",
                      json={"expires_hours": 5}).status_code == 404


# ── ② 批量复制 ───────────────────────────────────────────────


def test_copy_file_and_conflict_rename(auth, workdir):
    src = os.path.join(SHARE_ROOT, workdir, "c.txt")
    sub = os.path.join(SHARE_ROOT, workdir, "sub")
    os.makedirs(sub, exist_ok=True)
    with open(src, "w", encoding="utf-8") as f:
        f.write("copy me")
    r = auth.post("/api/copy", json={"sources": [f"{workdir}/c.txt"],
                                     "dest_dir": f"{workdir}/sub"})
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["ok"] is True
    assert os.path.exists(os.path.join(sub, "c.txt"))
    assert open(src, encoding="utf-8").read() == "copy me"  # 源还在

    # 同名再复制 → "c (1).txt"
    r = auth.post("/api/copy", json={"sources": [f"{workdir}/c.txt"],
                                     "dest_dir": f"{workdir}/sub"})
    assert r.json()["results"][0]["ok"] is True
    assert os.path.exists(os.path.join(sub, "c (1).txt"))


def test_copy_directory(auth, workdir):
    d1 = os.path.join(SHARE_ROOT, workdir, "dirA")
    d2 = os.path.join(SHARE_ROOT, workdir, "dirB")
    os.makedirs(os.path.join(d1, "inner"), exist_ok=True)
    os.makedirs(d2, exist_ok=True)
    with open(os.path.join(d1, "inner", "x.txt"), "w", encoding="utf-8") as f:
        f.write("deep")
    r = auth.post("/api/copy", json={"sources": [f"{workdir}/dirA"],
                                     "dest_dir": f"{workdir}/dirB"})
    assert r.status_code == 200, r.text
    assert os.path.isfile(os.path.join(d2, "dirA", "inner", "x.txt"))


def test_copy_guards(auth, workdir):
    os.makedirs(os.path.join(SHARE_ROOT, workdir, "d"), exist_ok=True)
    # 复制到自身子目录 400
    r = auth.post("/api/copy", json={"sources": [f"{workdir}/d"],
                                     "dest_dir": f"{workdir}/d"})
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is False
    # 源不存在 → 该项失败不炸整体
    r = auth.post("/api/copy", json={"sources": [f"{workdir}/ghost"],
                                     "dest_dir": workdir})
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is False
    # 空列表 400
    assert auth.post("/api/copy", json={"sources": [],
                                        "dest_dir": workdir}).status_code == 400


# ── ③ App 版本分发 ───────────────────────────────────────────


@pytest.fixture()
def app_dist():
    """在共享空间根造 App安装包/（版本+假 APK），用完即清。"""
    tag = _rand()
    dist = os.path.join(SHARE_ROOT, "App安装包")
    os.makedirs(dist, exist_ok=True)
    vj = os.path.join(dist, "version.json")
    apk = os.path.join(dist, f"test-{tag}.apk")
    backup = open(vj, "rb").read() if os.path.exists(vj) else None
    with open(vj, "w", encoding="utf-8") as f:
        f.write('{"version": "9.9.9", "build": 999, "filename": "%s", '
                '"notes": "t"}' % f"test-{tag}.apk")
    with open(apk, "wb") as f:
        f.write(b"PK\x03\x04fake-apk-bytes")
    yield {"version": vj, "apk": apk, "backup": backup, "tag": tag}
    if backup is not None:
        with open(vj, "wb") as f:
            f.write(backup)
    else:
        os.unlink(vj)
    if os.path.exists(apk):
        os.unlink(apk)


def test_app_latest_and_download(auth, client, app_dist):
    r = auth.get("/api/app/latest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == "9.9.9" and body["build"] == 999
    assert body["available"] is True and body["size"] > 0
    # 未登录不可见
    assert _bare(auth).get("/api/app/latest").status_code == 401
    # 下载
    r = auth.get("/api/app/download")
    assert r.status_code == 200
    assert r.content.startswith(b"PK\x03\x04")
    assert "android" in r.headers["content-type"]


def test_app_version_missing(auth, app_dist):
    os.unlink(app_dist["apk"])
    r = auth.get("/api/app/latest")
    assert r.status_code == 200 and r.json()["available"] is False
    assert auth.get("/api/app/download").status_code == 404


# ── ④ 照片时间线纳入视频 ─────────────────────────────────────


def _make_mp4(path: str):
    """真 mp4（1 秒彩条），ffprobe 才有真实时长。无 ffmpeg 时降级为假文件。"""
    exe = shutil.which("ffmpeg")
    if exe:
        subprocess.run([exe, "-y", "-v", "quiet", "-f", "lavfi",
                        "-i", "testsrc=duration=1:size=64x64:rate=2",
                        "-pix_fmt", "yuv420p", path],
                       check=True, timeout=60)
        return True
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42fake")
    return False


def test_photos_timeline_includes_videos(auth, workdir):
    mp4 = os.path.join(SHARE_ROOT, workdir, "v0.mp4")
    real = _make_mp4(mp4)
    r = auth.post(f"/api/upload/{workdir}",
                  files={"file": ("p0.jpg", b"not-a-real-jpg", "image/jpeg")})
    assert r.status_code == 200, r.text
    import app.api.photos as system_mod
    system_mod._last_photo_scan = 0
    r = auth.get("/api/photos?limit=500&refresh=true")
    assert r.status_code == 200, r.text
    items = {it["path"]: it for it in r.json()["items"]}
    v = items[f"{workdir}/v0.mp4"]
    assert v["media_type"] == "video"
    if real:
        assert v["duration"] is not None and v["duration"] > 0
    p = items[f"{workdir}/p0.jpg"]
    assert p["media_type"] == "photo"
    # media_type 筛选
    r = auth.get("/api/photos?limit=500&media_type=video")
    assert all(it["media_type"] == "video" for it in r.json()["items"])
    r = auth.get(f"/api/photos?limit=500&media_type=video&month={v['month']}")
    assert any(it["path"] == f"{workdir}/v0.mp4" for it in r.json()["items"])


def test_photos_video_filter_excludes_photos(auth, workdir):
    _make_mp4(os.path.join(SHARE_ROOT, workdir, "v1.mp4"))
    r = auth.get("/api/photos?limit=500&media_type=photo")
    assert all(it["media_type"] == "photo" for it in r.json()["items"])
