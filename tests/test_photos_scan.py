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

import app.api.system as system
from app.config import SHARE_ROOT


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
        out += system._db().execute(
            "SELECT path FROM photos WHERE path LIKE ?", (f"%{like}%",)
        ).fetchall()
    return out


def _cleanup(*likes):
    with system._db_lock:
        for like in likes:
            system._db().execute("DELETE FROM photos WHERE path LIKE ?",
                                 (f"%{like}%",))
        system._db().commit()
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
    system._last_photo_scan = 0.0
    system._last_force_scan = 0.0
    yield
    _cleanup("照片/随手拍", "照片/节流验证", "照片/admin/2026-旅行",
             "照片/家庭合影", "随手新建")


def test_force_scan_not_starved_by_plain_hits(client, photos_env):
    """普通扫描刚跑过（60s 内）不得吞掉紧随的 refresh=1 强扫——
    这是 App 备份完成/脉搏轮询强刷的确定性保证（0.7.2 前共用时间戳，
    网页正刷照片墙时 App 强刷常被顶掉）。"""
    p = _mk("照片/随手拍/a.jpg")
    system._last_photo_scan = time.time()  # 模拟普通请求刚扫过
    system._last_force_scan = 0.0
    system._scan_photos(force=True)
    assert _rows("随手拍"), "force 被普通扫描时间戳顶掉 = App 强刷扫了个寂寞"
    assert p.exists()


def test_plain_scan_respects_60s_floor(client, photos_env):
    """普通请求仍守 60s 节流：高频浏览不会反复全盘 rglob。"""
    _mk("照片/节流验证/b.jpg")
    system._last_photo_scan = time.time()
    system._scan_photos()
    assert not _rows("节流验证"), "60s 节流失效 = 每个普通请求都在全盘扫描"
    system._last_photo_scan = 0.0
    system._scan_photos()
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
