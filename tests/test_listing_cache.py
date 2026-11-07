"""_listing() 缓存的回归钉：single-flight 重建 + rel_parts 形状契约。

背景（2026-11-08 值守批实测）：
9934 文件树上，TTL 到期瞬间 10 线程 × 8 请求打 search/recent，
旧实现（查锁→释放→各自 rglob）缓存击穿：p50 6.4s / 80 请求 41s。
修复 = 重建持锁（single-flight），同场景 p50 362ms / 3.1s。
这个测试把「并发只有一个线程真正重建」钉住 —— 回退旧实现时必须红。
"""

from __future__ import annotations

import sys
import threading
import time
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import files as files_mod


def _mk_tree(tmp_path: Path) -> Path:
    root = tmp_path / "share"
    for i in range(12):
        d = root / f"d{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"f{i}.txt").write_bytes(b"x")
    return root


class TestListingCache:
    def test_rel_parts_shape(self, tmp_path):
        """清单行的形状契约：search/recent 按 3 元组解包，漂移即静默错。"""
        files_mod.SHARE_ROOT = _mk_tree(tmp_path)
        files_mod.invalidate_listing()
        rows = files_mod._listing()
        assert rows, "种子树上不应为空"
        for f, st, rel_parts in rows:
            assert isinstance(rel_parts, tuple)
            assert all(isinstance(p, str) for p in rel_parts)
            assert Path(*rel_parts) == f.relative_to(files_mod.SHARE_ROOT)

    def test_concurrent_listing_single_flight(self, tmp_path):
        """TTL 过期瞬间 5 线程并发：只有 1 个线程真正重建（single-flight）。

        拉长重建窗口（rglob 注入 sleep）让并发真的撞上；旧实现
        「查完锁就释放去扫盘」会让 5 个线程各扫一遍 → calls == 5。
        """
        files_mod.SHARE_ROOT = _mk_tree(tmp_path)
        real_rglob = Path.rglob
        calls = {"n": 0, "concurrent_peak": 0, "cur": 0}

        def slow_rglob(self, pattern):
            calls["n"] += 1
            calls["cur"] += 1
            calls["concurrent_peak"] = max(calls["concurrent_peak"], calls["cur"])
            try:
                time.sleep(0.3)
                return real_rglob(self, pattern)
            finally:
                calls["cur"] -= 1

        with mock.patch.object(Path, "rglob", slow_rglob):
            files_mod.invalidate_listing()
            results = []
            errors = []

            def worker():
                try:
                    results.append(len(files_mod._listing()))
                except Exception as e:  # pragma: no cover - 失败即红
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, errors
        assert len(results) == 5 and all(n == 12 for n in results)
        assert calls["n"] == 1, (
            f"并发重建应有且仅有一轮 rglob（single-flight），实际 {calls['n']} 轮 —— "
            "缓存击穿，10 线程实测 p50 6.4s 的根因"
        )
        assert calls["concurrent_peak"] == 1

    def test_invalidate_forces_rebuild(self, tmp_path):
        """写后失效：invalidate_listing() 必须让下一次读真的扫盘。"""
        files_mod.SHARE_ROOT = _mk_tree(tmp_path)
        files_mod.invalidate_listing()
        rows1 = files_mod._listing()
        n1 = len(rows1)
        # 新增一个文件,不失效就看不见（5s TTL 内）
        new_file = files_mod.SHARE_ROOT / "d0" / "new.txt"
        new_file.write_bytes(b"new")
        files_mod.invalidate_listing()
        rows2 = files_mod._listing()
        assert len(rows2) == n1 + 1
        assert any(f.name == "new.txt" for f, _st, _rel in rows2)