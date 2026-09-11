"""精选专辑存储层（F4/A5 收口批自 app/api/albums.py 下沉）。

为什么搬进 services：`services/accounts.py` 改名流程需要
`rename_album_owner`，此前只能函数体内反向 import `app.api.albums`——
服务层掏 API 层是分层倒置（A5 验收项：services 不得 import app.api.*）。
存储核心（settings JSON + 锁内读-改-写）本就不是路由职责，下沉后
api/albums、api/photos、services/accounts 三个消费方同层取用。

模型 {id, title, created_by, created_at, items:[photos.path...]}，
兼容旧月份书签型（month 有值、items 空）。权限/可见性判定留在 API 层。
"""
import json

from app.db import _db, _db_lock
from app.settings import FEATURED_ALBUMS


def load_albums() -> list[dict]:
    row = _db().execute("SELECT value FROM settings WHERE key=?", (FEATURED_ALBUMS,)).fetchone()
    if not row or not row["value"]:
        return []
    try:
        data = json.loads(row["value"])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_albums(albums: list[dict]):
    with _db_lock:
        _db().execute(
            "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (FEATURED_ALBUMS, json.dumps(albums, ensure_ascii=False)))
        _db().commit()


def rename_album_owner(old: str, new: str):
    """用户名变更后同步精选专辑归属（R2-P2-3）：否则改名者丢失自己专辑的
    编辑/删除权（created_by 比对落空）。走 mutate_albums 的锁内读-改-写，
    消灭与并发编辑专辑的丢更新竞态（R2-P2-5）。"""
    changed = []

    def _do(albums: list[dict]) -> bool:
        for a in albums:
            if a.get("created_by") == old:
                a["created_by"] = new
                changed.append(True)
        return bool(changed)

    mutate_albums(_do)


def mutate_albums(mutator) -> bool:
    """精选专辑整表"读-改-写"原子化（v0.14 审查 P2）。

    load_albums()→改→save_albums() 三步之间不持锁时，两个设备并发
    勾选/删除会整份互相覆盖丢更新；专辑条目现在是用户策展数据，必须
    全程持 _db_lock。save 自己也拿这把锁（Lock 不可重入），
    所以这里绕开它直接写库。mutator(albums) 就地修改，返回 False 表示
    未命中（不落库）。"""
    with _db_lock:
        row = _db().execute(
            "SELECT value FROM settings WHERE key=?", (FEATURED_ALBUMS,)).fetchone()
        try:
            albums = json.loads(row["value"]) if row and row["value"] else []
            if not isinstance(albums, list):
                albums = []
        except Exception:
            albums = []
        if not mutator(albums):
            return False
        _db().execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (FEATURED_ALBUMS, json.dumps(albums, ensure_ascii=False)))
        _db().commit()
        return True
