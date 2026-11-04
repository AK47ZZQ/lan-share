"""孤儿上传临时文件清理（_cleanup_tmp 的孤儿段）的边界。

这函数直接删用户文件树里的东西，白名单纪律与备份 _prune_old 同罪：
  · 带 pid-ns 的 .uploading-<pid>-<ns> / .dav-uploading-<pid>-<ns> 是
    程序的中间态 —— 必须回收（锚定正则）；
  · .dedup 是当前活跃的裸后缀（uploads.py 去重中间态）—— 必须回收；
  · 裸 .uploading / .dav-uploading 已无程序写手（2026-11-04 核实，
    写手全部唯一化为 pid-ns 形态）—— 保留这两档只会误删用户合法文件
    （my.uploading、video.dav-uploading 这种名字）。
"""

from __future__ import annotations

import time

import pytest

from app.services import cleanup


@pytest.fixture()
def share_root(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "SHARE_ROOT", tmp_path)
    return tmp_path


def _aged(path, days: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    old = time.time() - days * 86400
    import os
    os.utime(path, (old, old))


class TestOrphanUploadTemps:
    def test_crash_leftover_with_pid_ns_is_collected(self, share_root):
        """崩溃残留（带 pid-ns）必须被回收 —— 这是清理存在的意义。"""
        f = share_root / "照片.uploading-123-1700000000000000"
        _aged(f, 2)
        cleanup._cleanup_tmp()
        assert not f.exists()

    def test_crash_leftover_dav_is_collected(self, share_root):
        f = share_root / "照片.dav-uploading-456-1700000000000000"
        _aged(f, 2)
        cleanup._cleanup_tmp()
        assert not f.exists()

    def test_dedup_leftover_is_collected(self, share_root):
        """裸 .dedup 仍是活跃中间态（uploads.py），保留回收。"""
        f = share_root / "照片.jpg.dedup"
        _aged(f, 2)
        cleanup._cleanup_tmp()
        assert not f.exists()

    def test_user_file_named_uploading_survives(self, share_root):
        """用户自己的 `my.uploading` 绝不能删 —— 裸后缀已无程序写手，
        这两档只服务误删。"""
        f = share_root / "my.uploading"
        _aged(f, 5)
        cleanup._cleanup_tmp()
        assert f.exists(), "用户的 my.uploading 被误删了"

    def test_user_file_named_dav_uploading_survives(self, share_root):
        f = share_root / "video.dav-uploading"
        _aged(f, 5)
        cleanup._cleanup_tmp()
        assert f.exists()

    def test_normal_user_files_untouched(self, share_root):
        keep = share_root / "旅行照片.jpg"
        _aged(keep, 400)
        cleanup._cleanup_tmp()
        assert keep.exists()

    def test_fresh_tmp_files_untouched(self, share_root):
        """宽限期内（<1 天）的残留不动 —— 可能还在上传。"""
        f = share_root / "照片.uploading-789-1700000000000000"
        _aged(f, 0.1)
        cleanup._cleanup_tmp()
        assert f.exists()


class TestTrashNotTouchedByOrphanSweep:
    """孤儿清理扫全树（SHARE_ROOT.rglob），但 .trash 是回收站——
    里面的保留期由 _cleanup_trash 按 deleted_at 管理，孤儿规则
    （.dedup / .tmp- 前缀 / .uploading-* 名字）绝不能越权提前物理销毁：
    用户把一个叫 x.dedup 的文件删进回收站，第二天就没了 = 回收站承诺是假的。
    """

    def test_trash_dedup_survives_orphan_sweep(self, share_root):
        f = share_root / ".trash" / "照片.dedup"
        _aged(f, 5)
        cleanup._cleanup_tmp()
        assert f.exists(), "回收站条目被孤儿清理越权销毁"

    def test_trash_tmp_prefixed_survives_orphan_sweep(self, share_root):
        f = share_root / ".trash" / ".tmp-abc123.jpg"
        _aged(f, 5)
        cleanup._cleanup_tmp()
        assert f.exists()

    def test_trash_uploading_named_survives_orphan_sweep(self, share_root):
        f = share_root / ".trash" / "video.uploading-1-1700000000000000"
        _aged(f, 5)
        cleanup._cleanup_tmp()
        assert f.exists()
