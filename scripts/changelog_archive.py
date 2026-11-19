#!/usr/bin/env python3
"""CHANGELOG.md 滚动归档：主文件只留最近 N 条，更早的切进按季度分的分片。

为什么（2026-11-19）：
    CHANGELOG.md 涨到 440KB / 6756 行 / 187 条。三个后果：
      ① 任何 AI 会话想读格式或查历史，一次就是十几万 token，上下文直接废
         （对策是根目录索引 `docs/_internal/项目记忆/17-更新历史索引.md`）；
      ② GitHub 网页打开都费劲；
      ③ `api.github.com/.../contents/CHANGELOG.md` 单文件超过 **1MB** 就不返回
         content 了 —— App 的更新历史兜底路径会静默失效。

    N 条留在主文件（默认 50，约 150KB），其余按**季度**切到
    `docs/changelog/CHANGELOG-archive-YYYYQN.md`（单季度约 300KB，离 1MB 远）。

    分片必须放 `docs/changelog/` 而不是 `docs/_internal/` —— 后者在 .gitignore
    里，App 拉不到。

主文件**顶部**会写一行机器可读索引：

    <!-- ARCHIVES: docs/changelog/CHANGELOG-archive-2026Q4.md ... -->

    为什么必须在顶部：markdown 里没有「元数据」这个概念，任何写在末尾的行都会
    被 `^## ` 切块逻辑算进最后一个条目的正文（2026-11-19 实测让末条 body 多出
    100 字，App 会把注释当成本版更新说明显示出来）。放顶部则天然安全 —— 两边
    解析器（Dart 的 `parseChangelog`、Python 的 `parse_update_history`）都是
    `split(/^## /).skip(1)`，第一段不参与解析。

    App 端（`appver_api.dart::githubChangelog`）靠它知道要补拉哪些分片。
    所以**顺序是从新到旧**，拼接时直接按这个顺序接在后面。

用法：
    python scripts/changelog_archive.py --dry-run   先看会搬走什么
    python scripts/changelog_archive.py             执行
    python scripts/changelog_archive.py --keep 80   改保留条数
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
ARCHIVE_DIR = ROOT / "docs" / "changelog"
KEEP = 50

H2_RE = re.compile(r"^## ", re.M)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
MARKER_RE = re.compile(r"<!--\s*ARCHIVES:.*?-->[ \t]*\n?")

HEADER = """# CHANGELOG 归档 · {q}

> 从仓库根 `CHANGELOG.md` 滚动切下来的历史条目（**新到旧**，与主文件同序）。
> App 的更新历史兜底路径会拉这个文件（见 `mobile/lib/data/api/appver_api.dart`），
> 所以块格式必须与主文件完全一致：`## 日期 — 版本线 版本号：标题`。
> 由 `scripts/changelog_archive.py` 生成，**别手改**。

"""


def _quarter(date: str) -> str:
    """2026-11-19 → 2026Q4"""
    return f"{date[:4]}Q{(int(date[5:7]) - 1) // 3 + 1}"


def _version_block_starts(text: str) -> list[int]:
    """所有**带日期**的 `## ` 块的起始偏移。

    不带日期的 `## 教训` / `## 测试` 是老条目内部误用 `##` 当子标题，
    它们不算一条，否则切分点会落在条目中间把内容切碎。
    """
    starts: list[int] = []
    for m in H2_RE.finditer(text):
        eol = text.find("\n", m.start())
        title = text[m.start() + 3: eol if eol != -1 else len(text)]
        if DATE_RE.search(title):
            starts.append(m.start())
    return starts


def _blocks(text: str) -> list[tuple[str, str]]:
    """按 `## ` 切块 → [(标题, 正文)]，正文不含标题行。"""
    out: list[tuple[str, str]] = []
    parts = re.split(r"^## ", text, flags=re.M)
    for p in parts[1:]:
        i = p.find("\n")
        title = (p if i == -1 else p[:i]).strip()
        body = "" if i == -1 else p[i + 1:]
        out.append((title, body))
    return out


def strip_marker(text: str) -> str:
    return MARKER_RE.sub("", text).lstrip("\n")


def split(text: str, keep: int) -> tuple[str, str]:
    """返回 (保留区, 待归档区)。切分点一定落在版本块边界上。"""
    starts = _version_block_starts(text)
    if len(starts) <= keep:
        return text, ""
    cut = starts[keep]
    return text[:cut], text[cut:]


def _group_by_quarter(tail: str) -> dict[str, list[tuple[str, str]]]:
    """待归档区按季度分组。无日期的块跟随它前面（更新）那条的季度。

    正序扫描是对的：`## 教训` 这类子标题属于它**上面**那个条目。
    """
    blocks = _blocks(tail)
    quarters: list[str | None] = []
    last: str | None = None
    for title, _ in blocks:
        dm = DATE_RE.search(title)
        if dm:
            last = _quarter(dm.group(1))
        quarters.append(last)
    first = next((q for q in quarters if q), None)
    quarters = [q or first or "unknown" for q in quarters]  # type: ignore[list-item]

    groups: dict[str, list[tuple[str, str]]] = {}
    for q, blk in zip(quarters, blocks, strict=True):
        groups.setdefault(q, []).append(blk)
    return groups


def write_archives(groups: dict[str, list[tuple[str, str]]],
                   dry_run: bool) -> list[Path]:
    touched: list[Path] = []
    for q in sorted(groups):
        path = ARCHIVE_DIR / f"CHANGELOG-archive-{q}.md"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        # 幂等判据：块首行（`## 标题`）已经在这个分片里了，就不再写一遍
        fresh = [(t, b) for t, b in groups[q] if f"## {t}" not in existing]
        if not fresh:
            continue
        touched.append(path)
        if dry_run:
            print(f"  {path.relative_to(ROOT).as_posix()}  +{len(fresh)} 块"
                  f"（跳过重复 {len(groups[q]) - len(fresh)}）")
            continue

        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        text = existing or HEADER.format(q=q)
        chunk = "".join(f"## {t}\n{b}" for t, b in fresh)
        path.write_text(text.rstrip("\n") + "\n\n" + chunk.rstrip("\n") + "\n",
                        encoding="utf-8", newline="\n")
        print(f"  {path.relative_to(ROOT).as_posix()}  +{len(fresh)} 块"
              f"（跳过重复 {len(groups[q]) - len(fresh)}）")
    return touched


def write_marker(head: str, dry_run: bool) -> str:
    """把分片索引插到**文件最前面** —— 绝不能放末尾，见模块 docstring。"""
    archives = sorted(ARCHIVE_DIR.glob("CHANGELOG-archive-*.md"), reverse=True)
    if not archives:
        return head.lstrip("\n")
    rels = " ".join(p.relative_to(ROOT).as_posix() for p in archives)
    marker = f"<!-- ARCHIVES: {rels} -->"
    return marker + "\n\n" + head.lstrip("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CHANGELOG 滚动归档")
    ap.add_argument("--keep", type=int, default=KEEP,
                    help=f"主文件保留的版本条数（默认 {KEEP}）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = ap.parse_args(argv)

    if not CHANGELOG.is_file():
        print(f"找不到 {CHANGELOG}", file=sys.stderr)
        return 1

    raw = CHANGELOG.read_text(encoding="utf-8")
    text = strip_marker(raw)
    starts = _version_block_starts(text)
    print(f"主文件：{len(raw.encode()) / 1024:.0f}KB / {len(starts)} 条")

    head, tail = split(text, args.keep)
    if not tail:
        print(f"只有 {len(starts)} 条，不到 {args.keep}，不需要归档")
        return 0

    kept = len(_version_block_starts(head))
    moved = len(_version_block_starts(tail))
    print(f"保留 {kept} 条（{len(head.encode()) / 1024:.0f}KB），"
          f"归档 {moved} 条（{len(tail.encode()) / 1024:.0f}KB）")

    groups = _group_by_quarter(tail)
    touched = write_archives(groups, args.dry_run)

    new_text = write_marker(head, args.dry_run)
    if not args.dry_run:
        CHANGELOG.write_text(new_text, encoding="utf-8", newline="\n")
        print(f"主文件已重写：{len(new_text.encode()) / 1024:.0f}KB")
    else:
        print(f"[dry-run] 主文件将变成 {len(new_text.encode()) / 1024:.0f}KB，"
              f"涉及 {len(touched)} 个分片")

    # 内容守恒自检：搬走的块数必须等于写进分片的块数
    total_in_blocks = sum(len(v) for v in groups.values())
    if total_in_blocks != len(_blocks(tail)):
        print(f"⚠️ 块数对不上：分组 {total_in_blocks} vs 实际 {len(_blocks(tail))}",
              file=sys.stderr)
        return 2
    print("内容守恒校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
