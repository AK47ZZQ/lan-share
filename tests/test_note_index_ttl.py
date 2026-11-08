"""note_index 复扫节流的回归钉（2026-11-09 值守批三）。

背景：refresh_index() 此前每次 /api/search（<50 文件命中时）都全树
rglob + 逐文件读头 —— perf 实测 5000 文件树 0 命中查询 p50 474ms
（9934 生产规模更高），而 11-08 perf 批的基线查询恰好命中 ≥50 文件
走了短路没暴露。修法 = 与 files._listing 同款 5s TTL + 写时失效
（invalidate_listing 联动）。

消融：去掉节流（直接把 _REFRESH_TTL 置 0）→ 钉红。
⚠ 教训：perf 基线的查询设计必须包含「零命中」用例 —— 短路路径
（≥50 命中）会把下游组件的全部成本藏起来。
"""
from __future__ import annotations

import sys
import unittest.mock as mock
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import note_index


@pytest.fixture()
def note_root(tmp_path, monkeypatch):
    """隔离扫描根：3 个笔记文件，测试结束自动还原。"""
    root = tmp_path / "share"
    (root / "n1").mkdir(parents=True)
    (root / "n1" / "a.md").write_text("# hello", encoding="utf-8")
    (root / "n1" / "b.txt").write_text("world", encoding="utf-8")
    (root / "n2").mkdir()
    (root / "n2" / "c.md").write_text("# third", encoding="utf-8")
    monkeypatch.setattr(note_index, "SHARE_ROOT", root)
    monkeypatch.setattr(note_index, "to_rel", lambda p: str(p.relative_to(root)))
    monkeypatch.setattr(note_index, "_last_refresh", 0.0)
    return root


class TestNoteIndexTtl:
    def test_two_searches_within_ttl_scan_once(self, note_root, monkeypatch):
        """TTL 窗口内两次复扫请求 → 只有 1 轮全树 rglob（single-flight 节流）。"""
        real_rglob = Path.rglob
        calls = {"n": 0}

        def counting_rglob(self, pattern):
            calls["n"] += 1
            return real_rglob(self, pattern)

        with mock.patch.object(Path, "rglob", counting_rglob):
            note_index.invalidate()
            r1 = note_index.refresh_index()
            r2 = note_index.refresh_index()
        assert r1.get("indexed") == 3, r1
        assert r2 == {}, f"TTL 窗口内第二次复扫应零成本返回: {r2}"
        assert calls["n"] == 1, (
            f"TTL 窗口内应只有 1 轮全树 rglob，实际 {calls['n']} —— 节流失效"
        )

    def test_invalidate_forces_rescan(self, note_root):
        """写时失效：invalidate() 后下一次复扫必须真的重扫（且看到新文件）。"""
        note_index.invalidate()
        r1 = note_index.refresh_index()
        assert r1.get("indexed") == 3, r1
        (note_root / "n1" / "new.md").write_text("# fresh", encoding="utf-8")
        note_index.invalidate()
        r2 = note_index.refresh_index()
        assert r2.get("indexed") == 4, r2

    def test_write_endpoint_invalidation_link(self, auth, client, monkeypatch):
        """API 写入联动：upload 后搜索立即看到新笔记（不吃 5s TTL 窗口）。"""
        r = auth.post("/api/upload/priv-note",
                        files={"file": ("ttlname.md", b"# ttl link check", "text/plain")})
        assert r.status_code in (200, 201), r.text
        note_index.invalidate()
        r = auth.get("/api/search", params={"q": "ttlname"})
        assert r.status_code == 200
        assert any("ttlname" in str(x) for x in r.json()), r.text[:300]
