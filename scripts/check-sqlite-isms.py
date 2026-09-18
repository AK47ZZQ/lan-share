#!/usr/bin/env python3
"""SQLite 专有语法守卫（v3.56）：业务层 SQL 字面量里不许出现 SQLite-only 关键字。

## 为什么是静态扫描 + 为什么它不够

生产跑 PostgreSQL、单测默认跑 SQLite（`LANSHARE_TEST_PG_URL` 才切）。于是
"这句 SQL 在 PG 上根本跑不通"这类问题**天然对单测隐形**。v3.54 的 `rowid` 事故
就是：`ORDER BY added_at DESC, rowid DESC` 在 SQLite 合法、在 PG 抛
UndefinedColumn，而那条路径没有任何测试执行过 → 门禁全绿上线 → 预热停摆数日。

本脚本用 AST 扫 `app/` 下的字符串常量，命中已知 SQLite-only 关键字即红。
它抓的是"**已知类别**"；动态拼装、未知方言差异要靠
`tests/test_store_readers_smoke.py`（把每个 store 读函数在真引擎上跑一遍）。
两道互补，缺一都会再漏。

## 例外怎么给

`ALLOWLIST` 按**模块前缀**放行，且必须写理由。`app/db.py` / `app/store/schema.py`
是方言适配层本身，`app/services/backup.py` 的 PRAGMA 在 SQLite 专属校验分支里。
新增例外前先想清楚：业务模块出现 SQLite-only 语法，绝大多数情况是 bug 不是例外。

用法：`python scripts/check-sqlite-isms.py`（退出码 0=干净，1=有命中）
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIR = ROOT / "app"

# 关键字 → 为什么在生产引擎上是错的（PG 侧行为）
SQLITE_ONLY = {
    "rowid": "PG 无 rowid 伪列 → UndefinedColumn",
    "pragma": "PG 无 PRAGMA 语句",
    "autoincrement": "PG 用 BIGSERIAL，无 AUTOINCREMENT 关键字",
    "insert or replace": "PG 用 INSERT ... ON CONFLICT DO UPDATE",
    "insert or ignore": "PG 用 INSERT ... ON CONFLICT DO NOTHING",
    "without rowid": "PG 无此建表选项",
    "sqlite_master": "PG 用 information_schema / pg_catalog",
    "sqlite_sequence": "PG 用 pg_sequences / setval",
    "last_insert_rowid": "PG 用 RETURNING / lastval()",
    "changes()": "PG 用 UPDATE 返回的 rowcount",
}

# 方言适配层与 SQLite 专属分支：模块前缀 → 放行理由
ALLOWLIST = {
    "app/db.py": "双引擎适配层本体（_pg_sql / SQLite 连接与 PRAGMA）",
    "app/store/schema.py": "DDL 双轨定义层，SQLite 建表脚本合法使用 PRAGMA/AUTOINCREMENT",
    "app/services/backup.py": "备份完整性校验按引擎分支，PRAGMA 只在 SQLite 分支",
}

_SQL_STARTS = ("select", "insert", "update", "delete", "pragma", "create",
               "drop", "alter", "with", "replace")


def _is_sql(text: str) -> bool:
    t = text.strip().lower()
    if t.startswith(_SQL_STARTS):
        return True
    # 片段式 SQL（拼进更大语句的 where/order 段）与多行模板
    return " from " in f" {t} " or " set " in f" {t} "


def _allowed(rel: str) -> str | None:
    for prefix, reason in ALLOWLIST.items():
        if rel == prefix or rel.startswith(prefix.rstrip("/") + "/"):
            return reason
    return None


def scan() -> list[str]:
    hits: list[str] = []
    for py in sorted(SCAN_DIR.rglob("*.py")):
        rel = py.relative_to(ROOT).as_posix()
        if _allowed(rel):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            text = node.value
            if not _is_sql(text):
                continue
            low = text.lower()
            for kw, why in SQLITE_ONLY.items():
                if kw in low:
                    hits.append(f"{rel}:{node.lineno}: SQLite-only 关键字 "
                                f"`{kw}` —— {why}\n    {text.strip()[:120]}")
    return hits


def main() -> int:
    if not SCAN_DIR.is_dir():
        print(f"FATAL: 待扫描目录不存在 {SCAN_DIR}（守卫不许空转）")
        return 1
    n_files = len(list(SCAN_DIR.rglob("*.py")))
    hits = scan()
    if hits:
        print(f"发现 {len(hits)} 处 SQLite 专有语法（扫 {n_files} 文件）：")
        for h in hits:
            print("  " + h)
        print("\n生产引擎是 PostgreSQL：这些语句上线即 500（且单测可能全绿）。")
        print("改成语句两引擎都成立的写法；确属方言层则进 ALLOWLIST 并写理由。")
        return 1
    print(f"SQLite 专有语法守卫通过（扫 {n_files} 文件，例外模块 "
          f"{len(ALLOWLIST)} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
