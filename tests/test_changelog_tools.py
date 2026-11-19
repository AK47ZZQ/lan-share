"""CHANGELOG 归档 / 索引脚本的单测。

为什么值得单独测：这两个脚本是「静默出错就丢历史」的类型 —— 归档少搬
一条、索引漏一条，界面上什么都不报，等发现时已经过了几个版本，而
`CHANGELOG.md` 是 git 历史之外唯一的版本叙事（`AGENTS.md §4.2.1`）。

所以核心断言是**内容守恒**：搬走前和搬走后的 `(标题, 正文)` 多重集必须
完全相同 —— 不是「条数对得上」，是每一块的每一段正文都不许变。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str):
    """按路径加载 scripts/<rel>（scripts 不是包，不能直接 import）。"""
    path = ROOT / "scripts" / rel
    spec = importlib.util.spec_from_file_location(f"lan_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def idx():
    return _load("changelog_index.py")


@pytest.fixture(scope="module")
def arc():
    return _load("changelog_archive.py")


@pytest.fixture(scope="module")
def rel():
    return _load("release.py")


# ── 语料：覆盖真实 CHANGELOG 里出现过的全部标题形态 ──────────────
SAMPLE = """\
## 2026-11-19 — App v4.1.10+150：收起 WebDAV 入口

- 只做减法

## 2026-11-18 — 服务端 v4.0.17：公网域名栏目（Cloudflare Tunnel）

- 细节一

## 2026-11-18 — 4.1.9+149：分片下载（大文件走 CF 隧道快 11 倍）

- 细节二

## 2026-11-02（夜）— 服务端 v3.92 + App：**别拿公用网络拦人**

- 细节三

## 2026-10-01 — 服务端 v3.80：老条目，后面跟着一个假 ## 子标题

- 细节四

## 教训

这是老条目内部误用 `##` 当子标题，没有日期，不该被当成一条

## 2026-09-01 — App v0.4.1+5：紫渐变 + 白 L

- 细节五
"""


def _blocks(text: str) -> Counter:
    """(标题, 去空白后的正文) 多重集 —— 内容守恒的唯一判据。"""
    out: Counter = Counter()
    for sec in re.split(r"^## ", text, flags=re.M)[1:]:
        i = sec.find("\n")
        title = (sec if i == -1 else sec[:i]).strip()
        body = "" if i == -1 else sec[i + 1:]
        out[(title, re.sub(r"\s+", "", body))] += 1
    return out


# ════════════════════════════════════════════════════════════
# 索引：解析规则
# ════════════════════════════════════════════════════════════


def test_索引只收带日期的条目(idx):
    entries = idx.parse_entries(SAMPLE, "CHANGELOG.md", 0)
    # 6 个版本块；`## 教训` 那种没有日期，必须跳过
    assert len(entries) == 6
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"]) for e in entries)


def test_版本线分类(idx):
    lines = [e["line"] for e in idx.parse_entries(SAMPLE, "CHANGELOG.md", 0)]
    # `App v…` → App；`服务端 v…` → 服务端；两边都有 → 混合
    assert lines[0] == "App"
    assert lines[1] == "服务端"
    assert "混合" in lines


def test_带build号但没写App字样_归到App(idx):
    """`4.1.9+149` 这种只有 App 才有 build 号，不能误判成服务端。"""
    entries = idx.parse_entries(SAMPLE, "CHANGELOG.md", 0)
    e = next(e for e in entries if e["version"] == "4.1.9+149")
    assert e["line"] == "App"


def test_标题提取取冒号后的描述(idx):
    entries = idx.parse_entries(SAMPLE, "CHANGELOG.md", 0)
    assert entries[0]["desc"] == "收起 WebDAV 入口"
    # 带全角括号的日期前缀也要削掉
    mixed = next(e for e in entries if e["version"] == "3.92")
    assert mixed["desc"] == "别拿公用网络拦人"


def test_超长标题被截断(idx):
    entries = idx.parse_entries(
        "## 2026-11-19 — App v9.9.9：" + "很长的描述" * 30 + "\n", "x.md", 0)
    assert len(entries[0]["desc"]) <= idx.DESC_LIMIT


def test_渲染出表格且竖线被转义(idx):
    entries = idx.parse_entries(
        "## 2026-11-19 — App v9.9.9：a | b\n\n- x\n", "x.md", 0)
    text = idx.render(entries)
    assert "| 日期 | 线 | 版本 | 标题 | 位置 |" in text
    assert "a \\| b" in text, "标题里的竖线没转义会把 markdown 表格切坏"


def test_渲染空列表不炸(idx):
    assert "空" in idx.render([])


# ════════════════════════════════════════════════════════════
# 归档：切分与守恒
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def sandbox(tmp_path, arc, monkeypatch):
    """把归档脚本的模块级路径指到 tmp 下 —— 绝不碰真仓库的 CHANGELOG。"""
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(SAMPLE, encoding="utf-8")
    ad = tmp_path / "docs" / "changelog"
    monkeypatch.setattr(arc, "ROOT", tmp_path)
    monkeypatch.setattr(arc, "CHANGELOG", cl)
    monkeypatch.setattr(arc, "ARCHIVE_DIR", ad)
    return cl, ad


def test_切分点落在版本块边界(sandbox, arc):
    cl, _ = sandbox
    head, tail = arc.split(arc.strip_marker(cl.read_text(encoding="utf-8")), 3)
    assert head.count("\n## ") + 1 == 3, "保留区应当正好 3 个版本块"
    assert tail.lstrip().startswith("## 2026-11-02"), \
        "切分点必须落在第 4 个版本块的开头"


def test_归档后内容零丢失(sandbox, arc):
    """最关键的一条：主文件 + 全部分片 的块多重集必须等于归档前。"""
    cl, ad = sandbox
    before = _blocks(cl.read_text(encoding="utf-8"))
    assert arc.main(["--keep", "3"]) == 0

    after = _blocks(cl.read_text(encoding="utf-8"))
    for f in sorted(ad.glob("CHANGELOG-archive-*.md")):
        after.update(_blocks(f.read_text(encoding="utf-8")))

    assert after == before, f"内容对不上：丢失 {before - after}，多出 {after - before}"


def test_归档保留指定条数(sandbox, arc):
    cl, _ = sandbox
    arc.main(["--keep", "3"])
    text = cl.read_text(encoding="utf-8")
    assert len(arc._version_block_starts(text)) == 3


def test_分片索引在文件顶部且不进任何条目正文(sandbox, arc):
    """marker 放末尾会被算进最后一个条目的正文（实测污染 100 字）。"""
    cl, _ = sandbox
    arc.main(["--keep", "3"])
    text = cl.read_text(encoding="utf-8")
    assert text.startswith("<!-- ARCHIVES:"), "分片索引必须在第一行"

    first_block = re.split(r"^## ", text, flags=re.M)[1]
    assert "ARCHIVES" not in first_block, "注释混进了首条正文"


def test_分片索引按新到旧排列(sandbox, arc):
    cl, ad = sandbox
    arc.main(["--keep", "3"])
    text = cl.read_text(encoding="utf-8")
    rels = re.search(r"<!--\s*ARCHIVES:(.*?)-->", text).group(1).split()
    assert rels == sorted(rels, reverse=True)


def test_归档幂等(sandbox, arc):
    cl, _ = sandbox
    arc.main(["--keep", "3"])
    first = cl.read_text(encoding="utf-8")
    arc.main(["--keep", "3"])
    assert cl.read_text(encoding="utf-8") == first


def test_条数不够时不归档(sandbox, arc):
    cl, ad = sandbox
    assert arc.main(["--keep", "99"]) == 0
    assert cl.read_text(encoding="utf-8") == SAMPLE
    assert not ad.exists()


def test_无日期的块跟着它上面那条走(sandbox, arc):
    """`## 教训` 属于前一条 2026-10-01，该进 Q4 而不是落单。"""
    _, ad = sandbox
    arc.main(["--keep", "3"])
    q4 = (ad / "CHANGELOG-archive-2026Q4.md").read_text(encoding="utf-8")
    assert "## 教训" in q4


# ════════════════════════════════════════════════════════════
# 回归防线：release.py 必须从「全量」生成更新历史
# ════════════════════════════════════════════════════════════


def test_发版脚本从全量生成历史_不是只读主文件(rel):
    """归档之后，`release.py` 只读 CHANGELOG.md 会静默腰斩更新历史。

    实测（2026-11-19 归档当天）：全量 104 个 App 版本，只读主文件只有 **8 个**。
    而 R2 清单和 `versions.json` 都从这条路径生成 —— 一旦腰斩，用户看到的
    「更新历史」会凭空少掉 90 多个版本，且没有任何报错。
    """
    full = rel.read_changelog_full()
    from_full = rel.parse_changelog_history(full)
    from_main = rel.parse_changelog_history(
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))

    assert len(from_full) >= 100, f"全量只解析出 {len(from_full)} 个 App 版本"
    assert len(from_full) > len(from_main), "全量必须比只读主文件多"
    # 版本号不许重复（同一版本拆成多小节时要只留一条）
    versions = [i["version"] for i in from_full]
    assert len(versions) == len(set(versions))


def test_全量拼接不会把分片说明混进条目正文(rel):
    """分片开头的 `# CHANGELOG 归档` + 说明段必须被削掉。"""
    from_full = rel.parse_changelog_history(rel.read_changelog_full())
    for item in from_full:
        assert "CHANGELOG 归档" not in item["notes"], \
            f"{item['version']} 的正文混进了分片说明"


def test_全量拼接按新到旧(rel):
    from_full = rel.parse_changelog_history(rel.read_changelog_full())
    dates = [i["date"] for i in from_full if i["date"]]
    assert dates == sorted(dates, reverse=True), "拼接后的顺序不是新到旧"


def test_真仓库的索引是最新的(idx):
    """改了 CHANGELOG 却忘了重建索引 → 这里红（CI 也跑这条）。

    索引是 AI 会话查历史的唯一入口（`AGENTS.md §4.2.1` 禁止全文读
    CHANGELOG），脱节了等于没有。`release.py` 收尾会自动重建；手动改完
    CHANGELOG 记得跑一次 `python scripts/changelog_index.py`。

    ⚠️ 索引必须放在**公开目录** `docs/CHANGELOG-INDEX.md`：放
    `docs/_internal/` 的话那目录是 gitignore 的，CI checkout 里根本没有这个
    文件，这条测试就永远是「索引不存在」——2026-11-19 实测踩过一次。
    """
    fresh = idx.render(idx.collect())
    index_path = ROOT / "docs" / "CHANGELOG-INDEX.md"
    assert index_path.is_file(), f"索引不存在：{index_path}"
    assert index_path.read_text(encoding="utf-8") == fresh, \
        "索引已过期，跑 python scripts/changelog_index.py"
