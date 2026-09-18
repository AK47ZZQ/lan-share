"""迁移脚本表清单守卫（v3.57）：漏一张表 = 迁移时静默丢数据。

`scripts/migrate_sqlite_to_pg.py` 旧版手写 `(表名, 列清单)`，与 `store/schema.py`
形成第三轨漂移：todos/todo_lists/device_tokens/note_search/admin_broadcasts/
announcements **整表不在清单里**，users 少 nickname/avatar_path/totp_rc，
photos 少 added_at……而脚本对缺表只打一行 SKIP 就继续，全绿完成迁移。

本文件把"清单 ⊇ schema 全部表"变成断言：以后加表忘了同步迁移脚本，直接红。
纯静态（解析 `_PG_SCHEMA` 文本 + 导入脚本常量），两引擎下都跑。
"""
import importlib.util
import re
from pathlib import Path

from app.store.schema import _PG_SCHEMA

_CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", re.I)
_TABLE_BODY = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\)\s*;", re.S | re.I)


def _script():
    p = (Path(__file__).resolve().parent.parent
         / "scripts" / "migrate_sqlite_to_pg.py")
    spec = importlib.util.spec_from_file_location("migrate_sqlite_to_pg", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _schema_tables() -> set[str]:
    return {t.lower() for t in _CREATE_TABLE.findall(_PG_SCHEMA)}


def test_migration_covers_every_pg_table():
    mod = _script()
    planned = {t.lower() for t in mod.TABLE_ORDER}
    missing = sorted(_schema_tables() - planned - {t.lower() for t in mod.EXCLUDE})
    assert not missing, (
        f"这些表不在迁移清单里，SQLite→PG 会被静默丢弃: {missing}"
        f"（确属有意不迁则加进 EXCLUDE 并写明理由）")


def test_migration_lists_no_phantom_tables():
    """清单里的表必须真实存在——改名/删表后忘同步，迁移会整轮抛错。"""
    mod = _script()
    unknown = sorted({t.lower() for t in mod.TABLE_ORDER} - _schema_tables())
    assert not unknown, f"迁移清单里有 schema 不存在的表: {unknown}"


def test_migration_does_not_handwrite_columns():
    """列必须由 `_common_columns` 运行时反射，不得退回手写清单。"""
    mod = _script()
    assert all(isinstance(t, str) for t in mod.TABLE_ORDER), (
        "TABLE_ORDER 又变成 (表名, 列清单) 了？手写列清单正是本次要消灭的第三轨漂移")
    assert callable(mod._common_columns)


def test_columns_that_old_script_dropped_exist_in_schema():
    """回归本批发现的具象后果：昵称/头像/两步验证恢复码/入库时间必须真实存在。

    这些列由 `_common_columns` 运行时反射自动带上（不再手写），所以断言的对象
    是 schema 本身。**故意不查库**：`PRAGMA` 是 SQLite 独有语法，本文件要能在
    PG 跑法下一起执行（第一版就是在这儿踩了自己的方言守卫）。
    """
    bodies = {n.lower(): b for n, b in _TABLE_BODY.findall(_PG_SCHEMA)}
    assert "users" in bodies and "photos" in bodies, "解析没拿到表体，本守卫在空转"
    for col in ("nickname", "avatar_path", "totp_rc", "totp_last_step",
                "webdav_password_hash"):
        assert re.search(rf"\b{col}\b", bodies["users"]), f"users 缺列 {col}"
    for col in ("added_at", "duration", "codec", "width", "height"):
        assert re.search(rf"\b{col}\b", bodies["photos"]), f"photos 缺列 {col}"
    assert re.search(r"\bbroadcast_id\b", bodies["notifications"]), "notifications 缺 broadcast_id"
