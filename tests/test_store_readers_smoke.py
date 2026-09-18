"""**存储层读函数双引擎冒烟矩阵**（v3.56，#3 门禁的"真跑"那一半）。

## 为什么要有这个文件

rowid 事故（v3.54 引入、生产 PG 每 30s 抛 UndefinedColumn、静默停摆数日）的
根因不是有人写错 SQL，而是**那条代码路径在门禁里从来没被执行过**：
`test_thumb_warmup.py` 只测纯函数 `pick_missing`，PG 那轮整轮测试里没有任何
用例调用过 `photos_store.list_newest_rows`。所以"PG 门禁全绿"给的是假安心。

静态 lint（`scripts/check-sqlite-isms.py`）只能抓字面量里的已知关键字；动态
拼装的 SQL、列名改动、函数级 mis-use 都得靠**执行**。本文件就是执行：种一份
最小数据，把 10 个表主模块的**每一个公开读函数**在当前引擎上调一遍。

跑法与整轮测试相同——不设 `LANSHARE_TEST_PG_URL` 走 SQLite，设了就走真 PG
（conftest 在库内临时 schema 建表，绝不碰生产 `public`）。同一份文件、两次执行。

## 断言哲学

这里**不**校验业务语义（那是各域测试的职责），只校验两件事：
1. 调用不抛异常（方言/列名/索引类问题的探测器）；
2. 返回值形态可用（list/dict/int，且能按列名取值——PG RealDictRow 与 SQLite
   Row 的公共子集）。

新增 store 读函数时**必须**加进对应 test_* 一行，否则本矩阵失去意义。
"""
import os
import time

import pytest

from app.db import USE_PG, _db, _db_lock
from app.store import devices as devices_store
from app.store import note_search as note_store
from app.store import notifications as notif_store
from app.store import photos as photos_store
from app.store import shares as shares_store
from app.store import todos as todos_store
from app.store import trash as trash_store
from app.store import ttl_state as ttl_store
from app.store import users as users_store

# 口令哈希列只存不自证：这里给一个形状合法的假值（不参与任何校验路径）
_FAKE_HASH = "scrypt$32768$8$1$00$00"


@pytest.fixture(scope="module")
def seeded():
    """一份覆盖 9 张表的最小数据；唯一前缀定位，teardown 自建自清。

    红线遵循：全部行都带 `smk<tag>` 前缀，清理只按前缀删，绝不按"最后一行"。
    """
    tag = "smk" + os.urandom(4).hex()
    now = time.time()
    ctx = {"tag": tag, "now": now}

    ctx["uid"] = users_store.create(f"{tag}-u", _FAKE_HASH, "member", now)
    ctx["sess_hash"] = f"{tag}-sess"
    with _db_lock:                       # sessions 无表主模块（历史遗留，见分析）
        _db().execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, ip, ua) "
            "VALUES(?,?,?,?,?,?)",
            (ctx["sess_hash"], ctx["uid"], now, now + 3600, "127.0.0.1", "pytest"))
        if not USE_PG:
            _db().commit()

    ctx["dev_id"] = devices_store.insert(ctx["uid"], f"{tag}-dev", f"{tag}-tok", now, 3600)
    ctx["share_token"] = f"{tag}shar"
    shares_store.create(ctx["share_token"], f"shared/{tag}/a.jpg", "a.jpg", 10,
                        now, now + 3600, None, None, ctx["uid"], None)
    ctx["photo"] = f"shared/{tag}/2020-01-01 120000.jpg"
    photos_store.apply_scan([(ctx["photo"], "2020-01-01", 111, 222, 0.0, now)], [])
    ctx["note"] = f"shared/{tag}/n.md"
    note_store.apply_scan([(ctx["note"], f"正文 {tag} 可搜索", 111, 33)], [])
    with _db_lock:                       # trash 的 INSERT 在 filetools，不经 store
        _db().execute(
            "INSERT INTO trash(orig_path, trash_path, name, size, deleted_at) "
            "VALUES(?,?,?,?,?)",
            (f"private:{tag}/x.txt", f".trash/{tag}_x.txt", "x.txt", 5, now))
        if not USE_PG:
            _db().commit()
    with _db_lock:
        ctx["trash_id"] = _db().execute(
            "SELECT id FROM trash WHERE orig_path=?", (f"private:{tag}/x.txt",)
        ).fetchone()["id"]

    notif_store.insert(ctx["uid"], "system", f"{tag} 标题", "body", f"/f/{tag}")
    ctx["bid"] = f"{tag}bid"
    notif_store.create_broadcast(ctx["bid"], f"{tag} 广播", "b", f"[{ctx['uid']}]",
                                 "smoke-admin", "scheduled", now, now, now - 1)
    notif_store.create_announcement(f"{tag} 公告", "body", "smoke-admin")

    ctx["lid"] = todos_store.create_list(ctx["uid"], f"{tag}-list", "personal", now)
    ctx["tid"] = todos_store.create_todo(ctx["lid"], f"{tag}-todo", "n",
                                         now - 10, 1, ctx["uid"], now)

    yield ctx

    # ── 自建自清（前缀/主键定位，绝不按"最后一行"）──
    with _db_lock:
        _db().execute("DELETE FROM sessions WHERE token_hash=?", (ctx["sess_hash"],))
        _db().execute("DELETE FROM shares WHERE token=?", (ctx["share_token"],))
        _db().execute("DELETE FROM photos WHERE path=?", (ctx["photo"],))
        _db().execute("DELETE FROM note_search WHERE path=?", (ctx["note"],))
        _db().execute("DELETE FROM trash WHERE id=?", (ctx["trash_id"],))
        _db().execute("DELETE FROM admin_broadcasts WHERE id=?", (ctx["bid"],))
        _db().execute("DELETE FROM announcements WHERE title=?", (f"{tag} 公告",))
        _db().execute("DELETE FROM todo_lists WHERE id=?", (ctx["lid"],))
        _db().execute("DELETE FROM device_tokens WHERE id=?", (ctx["dev_id"],))
        _db().execute("DELETE FROM notifications WHERE user_id=?", (ctx["uid"],))
        _db().execute("DELETE FROM users WHERE id=?", (ctx["uid"],))
        if not USE_PG:
            _db().commit()


def _paths(rows) -> list[str]:
    """行集 → path 列（PG RealDictRow 与 SQLite Row 都支持列名下标）。"""
    return [r["path"] for r in rows]


# ─── users ────────────────────────────────────────────────


def test_users_readers(seeded):
    uid, tag = seeded["uid"], seeded["tag"]
    assert users_store.count() >= 1
    assert users_store.get(uid)["username"] == f"{tag}-u"
    assert users_store.get_by_username(f"{tag}-u")["id"] == uid
    assert users_store.get_active_by_username(f"{tag}-u")["id"] == uid
    assert users_store.get_active_by_username_ci(f"{tag.upper()}-U")["id"] == uid
    assert users_store.get_username(uid) == f"{tag}-u"
    av = users_store.get_avatar_path_by_username(f"{tag}-u")
    assert av["avatar_path"] == "" and av["id"] == uid
    assert "nickname" in dict(users_store.get_profile(uid))
    assert users_store.get_password_hash(uid) == _FAKE_HASH
    assert users_store.get_webdav_password_hash(uid) is None
    assert users_store.get_totp_state(uid)["totp_enabled"] == 0
    assert users_store.get_allowed_field(uid, "nickname") is not None
    assert users_store.exists_username_ci(f"{tag}-u") is True
    assert users_store.id_by_username(f"{tag}-u") == uid
    assert any(r["id"] == uid for r in users_store.list_for_admin())
    assert isinstance(users_store.list_active_admin_ids(), list)
    assert uid not in [r["id"] for r in users_store.list_active_except(uid)]
    assert isinstance(users_store.list_active_brief(), list)
    assert f"{tag}-u" in users_store.list_usernames()
    assert isinstance(users_store.list_nickname_rows(), list)
    assert isinstance(users_store.id_to_username_map(), dict)
    p = users_store.principal_by_session(seeded["sess_hash"])
    assert p is not None and p["user_id"] == uid, "sessions JOIN users 读失败"
    d = users_store.principal_by_device(f"{tag}-tok")
    assert d is not None and d["username"] == f"{tag}-u", "device_tokens JOIN users 读失败"


def test_users_get_allowed_field_rejects_unknown_column(seeded):
    """白名单列守卫必须在两引擎上都成立（拼列名的动态 SQL 最易被方言掩盖）。"""
    with pytest.raises(ValueError):
        users_store.get_allowed_field(seeded["uid"], "role")


# ─── devices / shares ─────────────────────────────────────


def test_device_readers(seeded):
    uid = seeded["uid"]
    assert devices_store.count_for_user(uid) == 1
    assert devices_store.find_id_by_name(uid, f"{seeded['tag']}-dev") == seeded["dev_id"]
    assert len(devices_store.list_for_user(uid)) == 1
    assert devices_store.get_for_user(seeded["dev_id"], uid)["name"].endswith("-dev")
    assert all("id" in dict(r) for r in devices_store.list_active(seeded["now"]))
    assert devices_store.count_active(seeded["now"]) >= 1
    assert devices_store.count() >= 1
    assert isinstance(devices_store.list_brief_by_user(uid), list)


def test_share_readers(seeded):
    t, now = seeded["share_token"], seeded["now"]
    assert shares_store.get_by_token(t)["name"] == "a.jpg"
    assert shares_store.exists(t) is True
    assert any(r["token"] == t for r in shares_store.list_all())
    assert shares_store.list_brief_by_owner(seeded["uid"])[0]["token"] == t
    assert shares_store.count() >= 1
    stats = shares_store.overview_stats(now)
    # overview_stats 是**全局**口径（管理端概览），别的用例会往 shares 里灌
    # 下载计数——这里只断形态，不断绝对值
    assert stats["total"] >= 1 and stats["active"] >= 1
    assert isinstance(stats["downloads"], int)


# ─── ttl_state（v3.57 外置票据；ON CONFLICT/RETURNING 双方言都要真跑）────


def test_ttl_state_readers(seeded):
    k = f"{seeded['tag']}-t"
    assert ttl_store.put_if_absent("smoke", k, "v", 600) is True
    assert ttl_store.put_if_absent("smoke", k, "v", 600) is False
    assert ttl_store.get("smoke", k) == "v"
    assert (k, "v") in ttl_store.get_all("smoke")
    assert ttl_store.count("smoke") == 1
    assert ttl_store.count() >= 1
    assert ttl_store.take("smoke", k) == "v"        # DELETE ... RETURNING
    assert ttl_store.get("smoke", k) is None
    ttl_store.put("smoke", k, "v", 600)
    assert ttl_store.delete("smoke", k) == 1
    ttl_store.put("smoke", k, "v2", 600)
    assert ttl_store.delete_scope_value("smoke", "v2") == 1
    ttl_store.put("smoke", k, "v3", -1)             # 直接置过期
    assert ttl_store.sweep_expired() >= 1
    assert ttl_store.clear_scope("smoke") >= 0


# ─── photos / note_search（rowid 事故现场）───────────────────


def test_photo_readers(seeded):
    assert isinstance(photos_store.list_probe_missing(), list)
    assert isinstance(photos_store.list_missing_sizes(10), list)
    assert seeded["photo"] in _paths(photos_store.load_index())
    # ⬅ 就是这条查询在 v3.54 带上了 SQLite 独有的 rowid 排序键上到生产 PG
    assert seeded["photo"] in _paths(photos_store.list_newest_rows(600))
    assert seeded["photo"] in _paths(photos_store.list_paths_by_taken())
    prefix = seeded["photo"].rsplit("/", 1)[0]
    assert seeded["photo"] in _paths(photos_store.list_for_gallery(
        "path LIKE ?", [f"{prefix}/%"], "COALESCE(added_at, 0) DESC, path"))
    assert photos_store.count() >= 1
    n, size = photos_store.count_and_size()
    assert n >= 1 and size >= 0


def test_note_search_readers(seeded):
    assert seeded["note"] in _paths(note_store.load_index())
    hits = note_store.search_snippets(seeded["tag"], 10)
    assert seeded["note"] in _paths(hits), "LIKE 检索应命中自己的前缀"


# ─── trash / notifications / todos ─────────────────────────


def test_trash_readers(seeded):
    assert seeded["trash_id"] in [r["id"] for r in trash_store.list_all()]
    assert trash_store.get(seeded["trash_id"])["name"] == "x.txt"
    total, size = trash_store.overview_stats()
    assert total >= 1 and size >= 5


def test_notification_readers(seeded):
    uid, bid, now = seeded["uid"], seeded["bid"], seeded["now"]
    assert notif_store.count_unread(uid) >= 1
    assert len(notif_store.list_for_user(uid)) >= 1
    assert isinstance(notif_store.stats_by_broadcast(bid, [uid]), dict)
    assert isinstance(notif_store.list_unread_users(bid, [uid]), list)
    assert len(notif_store.list_for_admin(uid, 10)) >= 1
    assert len(notif_store.list_for_admin(None, 10)) >= 1
    assert notif_store.recent_unread_by_link(uid, f"/f/{seeded['tag']}", 600) is not None
    b = notif_store.get_broadcast(bid)
    assert b["status"] == "scheduled"
    assert notif_store.get_live_broadcast(bid)["id"] == bid
    assert bid in [r["id"] for r in notif_store.list_broadcasts()]
    assert bid in [r["id"] for r in notif_store.list_due_broadcasts(now)], \
        "定时广播扫描（每 60s 一轮的循环）读路径失效"
    assert isinstance(notif_store.list_recent_announcements(), list)


def test_todo_readers(seeded):
    uid, lid, tid, now = seeded["uid"], seeded["lid"], seeded["tid"], seeded["now"]
    assert todos_store.get_list(lid)["name"].endswith("-list")
    assert lid in [r["id"] for r in todos_store.lists_visible(uid)]
    assert todos_store.count_lists_by_name(uid, f"{seeded['tag']}-list") == 1
    assert todos_store.get_todo(tid)["title"] == f"{seeded['tag']}-todo"
    assert tid in [r["id"] for r in todos_store.todos_visible(uid)]
    assert tid in [r["id"] for r in todos_store.due_unreminded(now)], \
        "到期提醒扫描（sweep 的读路径）失效"
    assert isinstance(todos_store.deleted_before(now), list)
