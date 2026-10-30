#!/usr/bin/env python3
"""Pre-open-source check: scan working tree AND full git history for leaks.

Read-only. Never modifies the repo.

    python scripts/pre-open-source-check.py

Exit 0 = all clear, 1 = blockers found.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (label, regex)  — things that must never appear in a public repo
SECRETS = [
    ("real e-mail",        r"[A-Za-z0-9._%+-]+@(?:qq|163|126)\.com"),
    ("tunnel domain",      r"frp-any\.com|ngrok\.io|natapp\.cn"),
    ("private LAN IP",     r"192\.168\.1\.(?!1$|99$|100$|101$)\d+"),
    ("tailscale identity", r"tailf[0-9a-f]{6,}|100\.(?:8[0-9]|9[0-9])\.[0-9]{1,3}\.[0-9]{1,3}"),
    ("credentials",        r"(?:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[=:]\s*[\"'](?!JBSWY3DP)[A-Za-z0-9_\-]{20,}"),
    ("home dir path",      r"C:\\Users\\[A-Za-z0-9]+\\"),
]

# files that must not be tracked
BANNED_PATHS = [
    ".backup-audit", "docs/_internal", "docs/项目记忆", "_archived-",
    "caddy.exe", "test-reports", "AGENTS.md", "HANDOFF.md",
    "DEBUG-ACCOUNT.md", "docs/STATUS-v3.md", ".workbuddy",
]

problems: list[str] = []
notes: list[str] = []


def sh(*args: str) -> str:
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout or ""


def section(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


# ---------------------------------------------------------------- HEAD ----
section("1. working tree (HEAD) secrets")
tracked = [p for p in sh("git", "ls-files").splitlines() if p]
print(f"  tracked files: {len(tracked)}")
for label, pat in SECRETS:
    rx = re.compile(pat)
    hits = []
    for f in tracked:
        p = ROOT / f
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".zip", ".apk"):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if rx.search(txt):
            hits.append(f)
    if hits:
        problems.append(f"HEAD 含 {label}: {hits[:5]}")
        print(f"  FAIL  {label:22s} {len(hits)} file(s)")
    else:
        print(f"  ok    {label:22s} 0")

# ------------------------------------------------------------- HISTORY ----
section("2. full git history")
revs = sh("git", "rev-list", "--all").split()
print(f"  commits: {len(revs)}")
for label, pat in SECRETS[:4]:      # the identity-specific ones only
    found = []
    for c in revs:
        out = sh("git", "grep", "-l", "-E", pat, c, "--", ".")
        if out.strip():
            found.append(c[:8])
            break
    if found:
        problems.append(f"历史含 {label}（提交 {found[0]}）")
        print(f"  FAIL  {label:22s} first seen in {found[0]}")
    else:
        print(f"  ok    {label:22s} clean across {len(revs)} commits")

# --------------------------------------------------------------- FILES ----
section("3. files that must not be tracked")
for b in BANNED_PATHS:
    hit = [f for f in tracked if f == b or f.startswith(b + "/") or f"/{b}/" in f or f.endswith("/" + b)]
    if hit:
        problems.append(f"不该入库: {b}")
        print(f"  FAIL  {b:22s} {hit[:2]}")
    else:
        print(f"  ok    {b:22s}")

# --------------------------------------------------------------- LICENSE --
section("4. licence + readme")
lic = ROOT / "LICENSE"
if not lic.exists():
    problems.append("缺 LICENSE")
    print("  FAIL  LICENSE 不存在")
elif "GNU AFFERO GENERAL PUBLIC LICENSE" in lic.read_text(encoding="utf-8", errors="ignore"):
    print("  ok    LICENSE = AGPL-3.0")
else:
    notes.append("LICENSE 存在但不是 AGPL 全文")
    print("  warn  LICENSE 不是 AGPL 全文")

readme = ROOT / "README.md"
if not readme.exists():
    problems.append("缺 README.md")
    print("  FAIL  README.md 不存在")
else:
    txt = readme.read_text(encoding="utf-8")
    bad = []
    for m in re.finditer(r'<img src="([^"]+)"|!\[[^\]]*\]\(([^)]+)\)', txt):
        p = m.group(1) or m.group(2)
        if p.startswith(("http://", "https://")):
            continue
        if not (ROOT / p).exists():
            bad.append(p)
    if bad:
        problems.append(f"README 图片缺失: {bad[:3]}")
        print(f"  FAIL  README 图片缺失 {bad[:3]}")
    else:
        print("  ok    README 图片引用全部存在")

# -------------------------------------------------------------- SCRIPT EOL -
section("5. batch file line endings")
bad_eol = []
for f in tracked:
    if f.lower().endswith((".bat", ".cmd")):
        raw = (ROOT / f).read_bytes()
        if raw.count(b"\r\n") != raw.count(b"\n"):
            bad_eol.append(f)
if bad_eol:
    problems.append(f".bat 非 CRLF: {bad_eol}")
    print(f"  FAIL  {bad_eol}")
else:
    print("  ok    所有 .bat/.cmd 均为 CRLF")

# ------------------------------------------------------------- BIG FILES --
section("6. large tracked files (>1 MB, non-image)")
big = []
for f in tracked:
    p = ROOT / f
    if not p.is_file():
        continue
    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        continue
    if p.stat().st_size > 1_048_576:
        big.append((f, p.stat().st_size))
if big:
    for f, s in sorted(big, key=lambda x: -x[1])[:5]:
        notes.append(f"大文件 {f} {s/1048576:.1f}MB")
        print(f"  warn  {f}  {s/1048576:.1f} MB")
else:
    print("  ok    无 >1MB 的非图片文件")

# ---------------------------------------------------------------- VERDICT --
section("verdict")
for n in notes:
    print(f"  note: {n}")
if problems:
    print(f"\n  BLOCKED — {len(problems)} problem(s):")
    for p in problems:
        print(f"    - {p}")
    sys.exit(1)
print("\n  ALL CLEAR — safe to go public")
sys.exit(0)
