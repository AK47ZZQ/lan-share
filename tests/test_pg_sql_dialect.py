# PG 方言适配回归（R1-P2-10 生产 500 根因）：psycopg 把裸 '%' 当占位符
# 前缀，SQL 字面量里的 LIKE 模式 'private/%' 必须被 _pg_sql 翻倍成 '%%'。
# pytest 只跑 SQLite 发现不了——本测试直接钉住适配层的输出文本。
#
# 本文件是 `_pg_sql` 方言契约的**唯一归口**（11 号复审：新增用例一律加在这里，
# 不再另建文件）。跨引擎的 schema 漂移守卫在 test_pg_schema_parity.py。
import pytest

from app.db import _pg_sql


def test_pg_sql_doubles_literal_percent():
    sql = ("(path NOT LIKE 'private/%' OR path LIKE ? ESCAPE '\\')"
           " AND path NOT LIKE '.%' AND path NOT LIKE '%/.%'")
    out = _pg_sql(sql)
    assert out.count("%s") == 1
    assert "'private/%%'" in out
    assert "'.%%'" in out
    assert "'%%/.%%'" in out
    # 翻倍结果交回 psycopg 渲染后等价于原文
    assert out.replace("%%", "%").replace("%s", "?") == sql


def test_pg_sql_placeholder_outside_string():
    assert _pg_sql("SELECT * FROM t WHERE a = ? AND b LIKE 'x%'") == \
        "SELECT * FROM t WHERE a = %s AND b LIKE 'x%%'"


def test_pg_sql_question_mark_in_literal_untouched():
    # 历史约定：字符串字面量内的 '?' 不替换
    sql = "INSERT INTO t VALUES ('a?b', ?)"
    out = _pg_sql(sql)
    assert "'a?b'" in out and out.count("%s") == 1


# ─── 11 号复审批次 0 追加：把"只写在注释里的约定"变成可执行断言 ───

def test_pg_sql_multiple_placeholders():
    assert _pg_sql("SELECT * FROM t WHERE a=? AND b=?") == \
        "SELECT * FROM t WHERE a=%s AND b=%s"


def test_pg_sql_doubled_quote_keeps_string_state():
    # PG 标准 conforming strings：'' 是转义单引号、不结束字符串。
    # 追踪：' 开 → ' 闭 → ' 再开 → ' 再闭；末尾 '?' 落在串外 → 必须替换。
    assert _pg_sql("SELECT 'it''s', ? FROM t") == "SELECT 'it''s', %s FROM t"


def test_pg_sql_percent_inside_literal_still_doubled():
    # psycopg 的 %-格式化作用于**整条 SQL**，字面量里的 % 也必须翻倍
    assert _pg_sql("SELECT '100%'") == "SELECT '100%%'"


def test_pg_sql_empty_and_noop():
    assert _pg_sql("") == ""
    assert _pg_sql("SELECT 1") == "SELECT 1"


def test_pg_sql_rejects_dollar_quoting_loudly():
    """`_pg_sql` 不解析 `$$...$$`；美元引号里的 '?' 会被**静默错改**。
    改为响亮失败（11 号复审 P1-1 建议），把隐患从"注释里的警告"变成"运行时就炸"。

    注：**不能**用 `E'...'` 做守卫——合法 SQL `code='E'` 里就含 `E'` 子串，会误报。
    """
    with pytest.raises(ValueError):
        _pg_sql("SELECT $$a?b$$ FROM t")


def test_pg_sql_preescaped_percent_doubles_again():
    """契约固化：调用方**不得**预先转义 `%`（`%%` 会被再翻倍）。"""
    assert _pg_sql("SELECT 'a%%b'") == "SELECT 'a%%%%b'"
