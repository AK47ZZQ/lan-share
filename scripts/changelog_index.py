#!/usr/bin/env python3
"""CHANGELOG.md（+ 归档分片）→ 「更新历史索引」。

为什么要有这个东西（2026-11-19 建）：
    CHANGELOG.md 已经 440KB / 6756 行 / 187 条版本条目。任何人（尤其 AI 会话）
    想回答「这个功能是哪一版改的」，最自然的动作是把整个文件读进上下文 ——
    一次就是十几万 token，99% 的内容跟问题无关，一次就把上下文废掉。

    索引一行一条：日期 + 版本 + 标题 + 在哪个文件。十几 KB，grep 一次就定位，
    要细节再去「位置」列那个文件里搜具体版本号。

被谁用：
    - AI 会话查历史（`AGENTS.md §4.2` 红线：禁止全文读 CHANGELOG）
    - `scripts/release.py` 收尾时自动重建（见 `regenerate_index()`）
    - 人工 `--check` 检查索引有没有脱节

用法：
    python scripts/changelog_index.py             生成 / 更新索引
    python scripts/changelog_index.py --check     只检查是否过期（过期退出码 1）
    python scripts/changelog_index.py --stdout    打到标准输出，不落盘
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
ARCHIVE_DIR = ROOT / "docs" / "changelog"
INDEX = ROOT / "docs" / "CHANGELOG-INDEX.md"

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# 版本号两段和三段都有：服务端是 `v3.92`，App 是 `v4.1.10+150`。
# 只写三段会把 `v3.92` 整个漏掉，索引里服务端条目的版本列全变成「—」。
VER_RE = re.compile(r"v?(\d+\.\d+(?:\.\d+)?(?:\+\d+)?)")

# 标题太长的截断长度（防止索引自己长成第二个 CHANGELOG）
DESC_LIMIT = 46


def _clean_desc(title: str) -> str:
    """从 `## 2026-11-19 — App v4.1.10+150：收起 WebDAV 入口` 里取「收起 WebDAV 入口」。

    规则保守：先削日期前缀，再取第一个冒号之后的部分；削不出来就整条留着。
    宁可多几个字，也不要切坏原意 —— 索引是给人扫的，不是给机器解析的。
    """
    t = title.strip()
    # 日期前缀，含 `2026-11-02（夜）` 这种带括号的
    t = re.sub(r"^\d{4}-\d{2}-\d{2}\s*(?:[（(][^）)]*[）)])?\s*[—–\-]+\s*", "", t)
    # 版本前缀之后的描述（冒号前不含冒号才算，避免切到正文里的冒号）
    m = re.match(r"^[^：:]*[：:]\s*(.+)$", t)
    if m:
        t = m.group(1)
    t = t.strip().strip("*_` ").strip()
    if len(t) > DESC_LIMIT:
        t = t[: DESC_LIMIT - 1] + "…"
    return t or title.strip()


def _version_line(title: str, ver: str | None) -> str:
    """判断条目属于哪条版本线。

    CHANGELOG 里 App 和服务端是混着写的，还有 11 条是同一批两边都动。
    判不出来就写「—」，不硬猜 —— 猜错比留空更误导。
    """
    has_app = "App" in title
    has_srv = "服务端" in title
    if has_app and has_srv:
        return "混合"
    if has_app:
        return "App"
    if has_srv:
        return "服务端"
    # `4.1.9+149` 这种带 build 号的一定是 App（服务端没 build 号）
    if ver and "+" in ver:
        return "App"
    return "服务端"


def parse_entries(text: str, source: str, order_base: int) -> list[dict]:
    """按 `## ` 切块解析。只收带日期的版本条目。

    `## 教训` / `## 测试` 这类非版本小节直接跳过 —— 它们在 CHANGELOG 里是
    事故复盘，不是版本记录，混进索引会让「查某个版本」变难。
    """
    out: list[dict] = []
    sections = re.split(r"^## ", text, flags=re.M)[1:]
    for i, sec in enumerate(sections):
        lines = sec.splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        dm = DATE_RE.search(title)
        if not dm:
            continue
        vm = VER_RE.search(title)
        ver = vm.group(1) if vm else None
        out.append({
            "date": dm.group(1),
            "line": _version_line(title, ver),
            "version": ver or "—",
            "desc": _clean_desc(title),
            "source": source,
            "order": order_base + i,
        })
    return out


def collect() -> list[dict]:
    """CHANGELOG.md + 所有归档分片 → 合并后的条目列表（新到旧）。"""
    entries: list[dict] = []
    order = 0

    if CHANGELOG.is_file():
        entries += parse_entries(
            CHANGELOG.read_text(encoding="utf-8"), "CHANGELOG.md", order)
        order += 10_000

    if ARCHIVE_DIR.is_dir():
        # 文件名带年份，升序即「老的归档在前」
        for path in sorted(ARCHIVE_DIR.glob("CHANGELOG-archive-*.md")):
            rel = path.relative_to(ROOT).as_posix()
            entries += parse_entries(path.read_text(encoding="utf-8"), rel, order)
            order += 10_000

    # 日期倒序；同日期按来源顺序（CHANGELOG 在归档之前）
    entries.sort(key=lambda e: (e["date"], -e["order"]), reverse=True)
    return entries


def _cell(s: str) -> str:
    """表格单元格转义 —— 标题里出现 `|` 会把 markdown 表格切坏。"""
    return s.replace("|", "\\|")


def render(entries: list[dict]) -> str:
    if not entries:
        return "# 更新历史索引\n\n（空 —— 没解析出任何版本条目）\n"

    stat: dict[str, int] = {}
    for e in entries:
        stat[e["line"]] = stat.get(e["line"], 0) + 1

    source_files = sorted({e["source"] for e in entries})
    total_bytes = sum(
        (ROOT / f).stat().st_size for f in source_files if (ROOT / f).is_file())

    newest, oldest = entries[0]["date"], entries[-1]["date"]
    line_stat = " / ".join(
        f"{k} {v}" for k, v in sorted(stat.items(), key=lambda kv: -kv[1]))

    lines = [
        "# 更新历史索引",
        "",
        "> 由 `scripts/changelog_index.py` 自动生成，**别手改**。"
        "改 CHANGELOG 之后重跑一次即可（`release.py` 收尾会自动调）。",
        ">",
        "> **要细节就去「位置」列那个文件里搜版本号**"
        "（`grep -n -A 30 \"4.1.10+150\" CHANGELOG.md`）。",
        "> 这些文件合计 "
        f"{total_bytes / 1024:.0f}KB / {len(entries)} 条 —— "
        "整份读进来就吃掉上下文，别干这事。",
        "",
        f"**{len(entries)} 条**（{line_stat}）· "
        f"最新 {newest} · 最早 {oldest} · 来源 {len(source_files)} 个文件",
        "",
        "| 日期 | 线 | 版本 | 标题 | 位置 |",
        "|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(
            f"| {e['date']} | {e['line']} | {_cell(e['version'])} "
            f"| {_cell(e['desc'])} | `{e['source']}` |")
    lines.append("")
    return "\n".join(lines)


def regenerate() -> Path:
    """生成并落盘（幂等：内容没变就不写，避免 git 里出现纯时间戳噪声）。"""
    text = render(collect())
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    old = INDEX.read_text(encoding="utf-8") if INDEX.is_file() else None
    if old != text:
        INDEX.write_text(text, encoding="utf-8", newline="\n")
    return INDEX


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成更新历史索引")
    ap.add_argument("--check", action="store_true",
                    help="只检查索引是否过期（过期退出码 1）")
    ap.add_argument("--stdout", action="store_true", help="打到标准输出，不落盘")
    args = ap.parse_args(argv)

    text = render(collect())

    if args.stdout:
        sys.stdout.write(text)
        return 0

    if args.check:
        old = INDEX.read_text(encoding="utf-8") if INDEX.is_file() else None
        if old is None:
            print(f"索引不存在：{INDEX.relative_to(ROOT)}", file=sys.stderr)
            return 1
        if old != text:
            print("索引已过期，跑 `python scripts/changelog_index.py` 更新",
                  file=sys.stderr)
            return 1
        print("索引是最新的")
        return 0

    path = regenerate()
    print(f"已生成 {path.relative_to(ROOT)}（{len(collect())} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
