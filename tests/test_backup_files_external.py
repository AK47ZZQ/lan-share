"""backup_files_external.py 的回归钉（2026-11-08 值守批）。

该脚本是"照片第二份拷贝"解冻条件的唯一载体，此前从未真实运行过——
盘符根闸 off-by-one 曾把 docstring 自荐用法（两层子目录）误拒。
用例只断言脚本可观察行为（退出码/文件落位/manifest 字段），不断言 robocopy 内部。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "backup_files_external.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backup_files_external", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backup_files_external"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_cli(args: list[str], share: Path, dest: Path) -> subprocess.CompletedProcess:
    """端到端跑一次 CLI（真进程），SHARE_ROOT 指向隔离目录。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "SHARE_ROOT": str(share)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env,
        cwd=str(ROOT), check=False,
    )


def _make_share(tmp_path: Path) -> Path:
    """合成源树：3 个用户文件 + 应被排除的 .thumbs/.chunks。"""
    src = tmp_path / "src-share"
    (src / "2026" / "11").mkdir(parents=True)
    (src / "2026" / "11" / "a.jpg").write_bytes(b"a" * 10)
    (src / "2026" / "11" / "b.txt").write_bytes(b"b" * 20)
    (src / "反馈").mkdir()
    (src / "反馈" / "c.md").write_bytes(b"c" * 30)
    (src / ".thumbs").mkdir()
    (src / ".thumbs" / "t.bin").write_bytes(b"t" * 40)
    (src / ".chunks").mkdir()
    (src / ".chunks" / "k.bin").write_bytes(b"k" * 50)
    return src


def test_two_level_dest_accepted_not_treated_as_drive_root():
    """设计用法（盘根一层子目录，两层路径）不被盘符根闸误拒（缺陷①回归钉）。"""
    m = _load_module()
    d = Path("C:\\") / f"lansbak-test-{os.getpid()}"
    d.mkdir(exist_ok=True)
    try:
        assert m._validate_dest(d) is None, m._validate_dest(d)
    finally:
        d.rmdir()


def test_bare_drive_root_still_refused():
    """C:\\ 对默认 SHARE_ROOT 而言先命中"祖先目录"闸（防御纵深排序），
    无论哪道闸，盘符根都必须被拒。"""
    m = _load_module()
    err = m._validate_dest(Path("C:\\"))
    assert err is not None and ("盘符根" in err or "祖先目录" in err)


def test_dest_inside_share_refused():
    m = _load_module()
    src = Path(os.environ["TEMP"]) / f"lansbak-t-{os.getpid()}"
    src.mkdir(parents=True, exist_ok=True)
    (src / "f.txt").write_text("x")
    m.SHARE_ROOT = src  # 把被测函数的 SHARE_ROOT 指到隔离目录
    try:
        (src / "sub").mkdir()
        assert "数据目录内部" in (m._validate_dest(src / "sub") or "")
    finally:
        import shutil
        shutil.rmtree(src, ignore_errors=True)


def test_tally_skips_top_level_excluded_dirs():
    m = _load_module()
    src = _make_share(Path(os.environ["TEMP"]) / f"lansbak-tally-{os.getpid()}")
    files, bytes_ = m._tally(src, skip_dirs=tuple(m.EXCLUDE_DIRS))
    assert (files, bytes_) == (3, 60)
    all_files, all_bytes = m._tally(src)
    assert (all_files, all_bytes) == (5, 150)


def test_tally_skips_artifact_prefixes():
    m = _load_module()
    base = Path(os.environ["TEMP"]) / f"lansbak-tally2-{os.getpid()}"
    base.mkdir(parents=True)
    (base / "manifest-1.json").write_text("{}")
    (base / "robocopy-1.log").write_text("x")
    (base / "data.bin").write_bytes(b"d" * 7)
    files, bytes_ = m._tally(base, skip_prefixes=m.ARTIFACT_PREFIXES)
    assert (files, bytes_) == (1, 7)
    shutil_done = True
    import shutil
    shutil.rmtree(base, ignore_errors=True)


def test_e2e_incremental_run_two_manifests(tmp_path):
    """端到端：首跑全拷 + 二跑增量 rc=0，双侧对账 match=true，排除目录不落 dest。"""
    src = _make_share(tmp_path)
    dest = tmp_path / "bak-dest"
    dest.mkdir()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "SHARE_ROOT": str(src)}
    cmd = [sys.executable, str(SCRIPT), "--dest", str(dest)]

    r1 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                        env=env, cwd=str(ROOT))
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert (dest / "2026" / "11" / "a.jpg").read_bytes() == b"a" * 10
    assert not (dest / ".thumbs").exists(), "排除目录不得落目标侧"
    mans = sorted(dest.glob("manifest-*.json"))
    assert len(mans) == 1
    man1 = json.loads(mans[0].read_text(encoding="utf-8"))
    assert man1["match"] is True
    assert man1["src_files"] == 3 and man1["dest_files"] == 3
    assert man1["robocopy_log"] and (Path(man1["robocopy_log"]).exists())
    log_text = Path(man1["robocopy_log"]).read_text(encoding="gbk", errors="replace")
    assert "a.jpg" in log_text, "日志必须含 per-file 明细（/NFL /NDL 已去除的回归钉）"
    assert man1["dry_run"] is False

    r2 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                        env=env, cwd=str(ROOT))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    mans2 = sorted(dest.glob("manifest-*.json"))
    assert len(mans2) == 2, "第二次运行应再落一份清单"
    man2 = json.loads(mans2[1].read_text(encoding="utf-8"))
    assert man2["match"] is True
    assert man2["dest_files"] == 3


def test_e2e_dry_run_reports_source_side(tmp_path):
    """dry-run 报源侧预计规模（缺陷②回归钉：不再对空目标清点报 0），且不写任何文件。"""
    src = _make_share(tmp_path)
    dest = tmp_path / "dry-dest"
    dest.mkdir()
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--dest", str(dest), "--dry-run"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "SHARE_ROOT": str(src)}, cwd=str(ROOT),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "预计源侧共 3 文件" in r.stdout, r.stdout
    assert list(dest.iterdir()) == [], "dry-run 不得在目标侧写任何文件"


def test_e2e_rejects_dest_inside_share(tmp_path):
    src = _make_share(tmp_path)
    bad = src / "bak-inside"
    bad.mkdir()
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--dest", str(bad)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "SHARE_ROOT": str(src)}, cwd=str(ROOT),
    )
    assert r.returncode == 2
    assert "数据目录内部" in r.stdout
