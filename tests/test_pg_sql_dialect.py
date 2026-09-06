# PG 方言适配回归（R1-P2-10 生产 500 根因）：psycopg 把裸 '%' 当占位符
# 前缀，SQL 字面量里的 LIKE 模式 'private/%' 必须被 _pg_sql 翻倍成 '%%'。
# pytest 只跑 SQLite 发现不了——本测试直接钉住适配层的输出文本。
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
