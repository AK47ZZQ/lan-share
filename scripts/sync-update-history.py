#!/usr/bin/env python3
"""按 CHANGELOG 重建更新源清单 —— 不发版、不打包、不联网。

## 为什么单独有这个脚本

`updates/index.json`（服务端线）和 `updates/app/index.json`（App 线）是托盘
与 App「自定义更新地址」的数据源。2026-11-08 排查发现**仓库里从来没有生成
它们的东西** —— 一直是手工维护，`history` 只有零星几条，和 CHANGELOG 完全
脱节。发版脚本已经接管了「以后每次发版顺手重建」，但**已经发过的版本**
需要一个不回炉重发的补数据入口：就是本脚本。

## 它做什么

对每条版本线：从注册表找安装目录 → 解析 CHANGELOG 得到全量历史 →
在 `updates/` 里找该版本的实际产物 → 重建清单（旧文件留 `.bak`）。

## 它不做什么

- 不构建、不发布、不碰 GitHub。
- **找不到产物就不写**：`latest` 缺 `url`/`sha256` 会让整个自定义源被判死
  （`updater._norm_item(need_file=True)` 直接抛错），托盘会静默回落到
  GitHub。宁可跳过并告警，也不要写一份「能读到但没包可下」的清单。

用法：

    python scripts/sync-update-history.py              # 两条线都重建
    python scripts/sync-update-history.py --line server
    python scripts/sync-update-history.py --dry-run     # 只打印，不落盘
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

sys.path.insert(0, str(ROOT / "scripts"))
import release  # noqa: E402  —— 复用同一个解析器，避免两套规则漂移


def artifact_for(dest: Path, version: str, suffix: str) -> Path | None:
    """找某个版本对应的产物文件。

    先看清单目录本身（发版脚本会把它复制进去），找不到再去 `build/out`
    —— 那里是构建产物落点，`updates/` 里没有说明清单和文件脱节了，
    这正是本脚本要修的场景（2026-11-08：清单指向 4.0.12，目录里只有 4.0.11）。

    版本号前后不能紧邻数字，也不能再跟 `.数字`（否则 `4.0.1` 会误配
    `4.0.11`）。但 `.exe` 前面那个点是正常的 —— 所以不能用
    `(?![0-9.])`，那样连 `LANShare-Setup-4.0.11.exe` 都会被判成不匹配。
    """
    pat = re.compile(rf"(?<![0-9.]){re.escape(version)}(?!\d)(?!\.\d)")
    for d in (dest, ROOT / "build" / "out"):
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() == suffix and pat.search(f.name):
                return f
    return None


def current_version(line: str) -> str:
    if line == "server":
        return release.read_server_version()
    return release.read_app_version()[0]


def run(line: str, dry_run: bool) -> int:
    inst = release.find_install_dir()
    if inst is None:
        print("本机没装服务端（注册表无 LANShare），无从重建。")
        return 2

    dest = inst / "updates" if line == "server" else inst / "updates" / "app"
    suffix = ".exe" if line == "server" else ".apk"
    try:
        ver = str(current_version(line))
    except Exception as exc:                       # 读版本号失败不该让整条链路崩
        print(f"[{line}] 读不到当前版本号：{exc}")
        return 2

    hist = release.parse_update_history(CHANGELOG.read_text(encoding="utf-8"), line)
    art = artifact_for(dest, ver, suffix)

    print(f"[{line}] 目标 {dest / 'index.json'}")
    print(f"[{line}] CHANGELOG 解析出 {len(hist)} 个版本"
          + (f"，最早的 v{hist[-1]['version']}" if hist else ""))
    print(f"[{line}] 最新版 v{ver} 的产物：" + (art.name if art else "**目录里没找到**"))

    if art is None:
        print(f"[{line}] 跳过 —— latest 没有 url/sha256 会让整个自定义源被判死。"
              f"先把 v{ver} 的安装包放进 {dest} 再来。")
        return 1
    if dry_run:
        print(f"[{line}] --dry-run：不写盘。")
        return 0

    release.sync_update_manifest(line, ver, art)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="按 CHANGELOG 重建更新源清单")
    ap.add_argument("--line", choices=("server", "app", "both"), default="both")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lines = ("server", "app") if args.line == "both" else (args.line,)
    rc = 0
    for ln in lines:
        print("=" * 56)
        rc |= run(ln, args.dry_run)
    return rc


if __name__ == "__main__":
    sys.exit(main())
