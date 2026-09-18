"""照片时间线扫描节流与范围（0.7.2，App v0.7.2 同批）。

背景：手机 App 报"照片页实时更新挂了/新建文件夹里的照片不显示"。排查结论：
- 服务端扫描范围本就是 SHARE_ROOT 全盘 rglob，新建文件夹天然覆盖——但需要
  回归测试钉死，防止将来有人加目录白名单；
- 真缺口一是 force（refresh=1）与普通请求共用 60s 时间戳：网页正刷照片墙时
  App 的强刷被 5s 内的普通命中顶掉，扫了个寂寞；
- 真缺口二在 App 触发面（只认本机备份引擎），App 侧 0.7.2 加脉搏轮询解决。
本文件钉服务端行为：force 独立节流 + 新文件夹必进时间线。
"""

import os
import time
from pathlib import Path

import pytest

import app.api.photos as photos
from app.config import SHARE_ROOT
from app.db import _db, _db_lock
from app.store import photos as photos_store


def _root() -> Path:
    return Path(SHARE_ROOT)


def _mk(rel: str) -> Path:
    p = _root() / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8fakejpg")  # 扩展名参与判定，内容不必是真 JPEG
    return p


def _rows(*likes):
    out = []
    for like in likes:
        out += _db().execute(
            "SELECT path FROM photos WHERE path LIKE ?", (f"%{like}%",)
        ).fetchall()
    return out


def _cleanup(*likes):
    with _db_lock:
        for like in likes:
            _db().execute("DELETE FROM photos WHERE path LIKE ?",
                                 (f"%{like}%",))
        _db().commit()
    for like in likes:
        d = _root() / like.split("/")[0]
        if d.exists():
            for child in sorted(d.rglob("*"), reverse=True):
                child.rmdir() if child.is_dir() else child.unlink(missing_ok=True)
            try:
                d.rmdir()
            except OSError:
                pass


@pytest.fixture()
def photos_env():
    """扫描时间戳归零 + 结束后清掉本批测试文件与库行（测试库会话级共享）。"""
    photos._last_photo_scan = 0.0
    photos._last_force_scan = 0.0
    yield
    _cleanup("照片/随手拍", "照片/节流验证", "照片/admin/2026-旅行",
             "照片/家庭合影", "随手新建", ".scan-dot-e2e-x7",
             "照片/scan-prune-e2e-x7")
    (_root() / ".scan-dot-e2e-x7.jpg").unlink(missing_ok=True)  # 单文件，_cleanup 只清目录


def test_force_scan_not_starved_by_plain_hits(client, photos_env):
    """普通扫描刚跑过（60s 内）不得吞掉紧随的 refresh=1 强扫——
    这是 App 备份完成/脉搏轮询强刷的确定性保证（0.7.2 前共用时间戳，
    网页正刷照片墙时 App 强刷常被顶掉）。"""
    p = _mk("照片/随手拍/a.jpg")
    photos._last_photo_scan = time.time()  # 模拟普通请求刚扫过
    photos._last_force_scan = 0.0
    photos._scan_photos(force=True)
    assert _rows("随手拍"), "force 被普通扫描时间戳顶掉 = App 强刷扫了个寂寞"
    assert p.exists()


def test_plain_scan_respects_60s_floor(client, photos_env):
    """普通请求仍守 60s 节流：高频浏览不会反复全盘 rglob。"""
    _mk("照片/节流验证/b.jpg")
    photos._last_photo_scan = time.time()
    photos._scan_photos()
    assert not _rows("节流验证"), "60s 节流失效 = 每个普通请求都在全盘扫描"
    photos._last_photo_scan = 0.0
    photos._scan_photos()
    assert len(_rows("节流验证")) == 1


def test_new_folder_photos_enter_timeline(auth, photos_env):
    """用户点名的场景：以后新建的文件夹里上传照片，必须出现在时间线——
    扫描范围是全盘 rglob，无固定目录清单；归属按路径推导，非用户名
    二段的手工整理目录归"共享空间"但对成员可见。"""
    _mk("照片/admin/2026-旅行/新文件夹/c.jpg")   # 用户相册三级目录
    _mk("照片/家庭合影/集体照.png")              # 非用户名手工目录
    _mk(os.path.join("随手新建", "llq录入.mov"))  # 任意顶层新文件夹（视频）
    r = auth.get("/api/photos", params={"refresh": True})
    assert r.status_code == 200
    items = {i["path"]: i for i in r.json()["items"]}
    assert "照片/admin/2026-旅行/新文件夹/c.jpg" in items
    assert items["照片/admin/2026-旅行/新文件夹/c.jpg"]["uploader"] == "admin"
    assert "照片/家庭合影/集体照.png" in items
    assert items["照片/家庭合影/集体照.png"]["uploader"] == "共享空间"
    assert items["随手新建/llq录入.mov"]["media_type"] == "video"


def test_dot_prefixed_paths_never_scanned(client, photos_env):
    """点前缀目录/文件永不入库（反馈 05a0 批）：扫描遍历从 rglob 逐条 dot
    判断改为 scandir 目录级剪枝后，语义必须逐条对齐——.thumbs 这类缓存
    目录整个不下探，隐藏媒体文件也不能借符号链接/非常规名字混上墙。"""
    _mk(".scan-dot-e2e-x7/a.jpg")     # 点前缀目录里的媒体
    _mk(".scan-dot-e2e-x7.jpg")       # 点前缀媒体文件
    _mk("照片/scan-prune-e2e-x7/ok.jpg")  # 正常目录对照组
    photos._last_photo_scan = 0.0
    photos._scan_photos()
    assert not _rows("scan-dot-e2e-x7"), "点前缀路径混入照片墙 = 剪枝语义回退"
    assert len(_rows("scan-prune-e2e-x7")) == 1, "正常目录照片被剪枝误伤"


def test_probe_candidates_exclude_probed_videos(client, photos_env):
    """补扫候选集口径（反馈 05a0 批修正）：探成功的视频行（width 恒 NULL 是
    设计）不得重回候选集——否则每轮扫描都把它们重新 ffprobe 一遍且永不收敛
    （生产实测 37 行 × 20/轮 ≈ 1.4s/轮）。尺寸待补的图片行（duration=0 且
    width NULL）必须仍在候选集。"""
    ins = ("INSERT INTO photos(path, taken_at, mtime_ns, size, duration, "
           "codec, width, height, added_at) VALUES(?,?,?,?,?,?,?,?,?)")
    with _db_lock:
        _db().execute(ins, ("scan-probe-x7/probed.mp4", "2026-01-01", 1, 1,
                            12.5, "h264", None, None, 1.0))   # 探成功的视频
        _db().execute(ins, ("scan-probe-x7/pending.mp4", "2026-01-01", 2, 2,
                            None, None, None, None, 1.0))     # 待补视频
        _db().execute(ins, ("scan-probe-x7/nosize.jpg", "2026-01-01", 3, 3,
                            0, None, None, None, 1.0))        # 尺寸待补图片
        _db().execute(ins, ("scan-probe-x7/done.jpg", "2026-01-01", 4, 4,
                            0, None, 1920, 1080, 1.0))        # 已齐备图片
        _db().commit()
    try:
        hit = {r["path"] for r in photos_store.list_probe_missing()
               if r["path"].startswith("scan-probe-x7/")}
        assert hit == {"scan-probe-x7/pending.mp4", "scan-probe-x7/nosize.jpg"}, \
            f"候选集口径漂移：{hit}"
    finally:
        with _db_lock:
            _db().execute("DELETE FROM photos WHERE path LIKE 'scan-probe-x7/%'")
            _db().commit()
