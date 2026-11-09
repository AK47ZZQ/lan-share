"""app/db.py 的连接串文件回退钉（2026-11-09 值守批 P1 配套）。

为什么用子进程：DATABASE_URL/USE_PG 是模块级常量（进程启动时读 env），
在本进程 importlib.reload 会换掉 _db_lock/_db 对象身份 —— 全套里
`from app.db import _db` 的既有引用全部悬空，属跨测试状态事故
（test_listing_cache 文件头的教训）。子进程完全隔离。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_CODE = """
import os
import app.db as db
print(db.DATABASE_URL)
print(db.USE_PG)
"""


def test_db_url_file_fallback(tmp_path, monkeypatch):
    """LANSHARE_PG_URL_FILE：DATABASE_URL 未设时从收紧的文件读连接串。"""
    f = tmp_path / ".lanshare-dburl"
    f.write_text("postgresql://u:p@127.0.0.1:5999/lanshare", encoding="utf-8")
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env["LANSHARE_PG_URL_FILE"] = str(f)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-c", _CODE], capture_output=True,
                       text=True, env=env, timeout=60, cwd=Path(__file__).parents[1])
    lines = r.stdout.strip().splitlines()
    assert r.returncode == 0, r.stderr[-300:]
    assert lines[0] == "postgresql://u:p@127.0.0.1:5999/lanshare", lines
    assert lines[1] == "True", lines


def test_db_url_env_still_wins_and_absent_file_means_sqlite(tmp_path, monkeypatch):
    """优先级回归：显式 DATABASE_URL 优先；env 指向的文件读不到则 fail-fast。

    契约在 2026-11-09 发布前复核时改过：原来是「读不到就静默回退 SQLite」，
    那会让 API 带一个空库起来、被用户当成「文件全没了」。现在宁可服务起不来。
    """
    f = tmp_path / ".lanshare-dburl"
    f.write_text("postgresql://u:p@127.0.0.1:5999/lanshare", encoding="utf-8")
    env = dict(os.environ)
    env["DATABASE_URL"] = "postgresql://explicit@127.0.0.1:5998/x"
    env["LANSHARE_PG_URL_FILE"] = str(f)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-c", _CODE], capture_output=True,
                       text=True, env=env, timeout=60, cwd=Path(__file__).parents[1])
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "postgresql://explicit@127.0.0.1:5998/x", lines

    env2 = dict(os.environ)
    env2.pop("DATABASE_URL", None)
    env2["LANSHARE_PG_URL_FILE"] = str(tmp_path / "missing-file")
    env2["PYTHONIOENCODING"] = "utf-8"
    r2 = subprocess.run([sys.executable, "-c", _CODE], capture_output=True,
                        text=True, env=env2, timeout=60, cwd=Path(__file__).parents[1])
    # fail-fast：env 明确给了路径却读不到，绝不静默退 SQLite（空库比崩溃更难诊断）
    assert r2.returncode != 0, f"读不到连接串文件必须失败，实测 rc=0 stdout={r2.stdout[:200]!r}"
    assert "拒绝退回 SQLite" in (r2.stderr or ""), (r2.stderr or "")[-500:]
