"""settings KV 表统一读写入口 + 全部 key 常量（07 审查 §5.1 事实源①）。

此前 get/set helper 有 8 份重复实现（admin/appver/healthreport/security/notify
各写各的），key 字符串散落 6 文件 23 处——改一个 settings key 要跨文件搜全仓，
新增设置项靠人肉对齐。此后读写一律走本模块，key 一律引用这里导出的常量。

**位置（11 号复审批次 2，A2）**：原住 `app/store/settings.py`，导致安全核心
`app/security.py` 反向 import 服务层（分层倒置）。settings 本质是"可运行时修改的
配置"，不是业务仓储，故与 `config.py`/`db.py` 同层放在 `app/settings.py`；
`app/store/` 留给真正的表仓储（users/shares/sessions）。

例外：需要"锁内读-改-写"复合操作（如精选专辑 _mutate_albums）可保留自管
SQL，但 key 仍须引用本模块常量。"""
from app.db import _db, _db_lock

# ── key 常量（唯一事实源，新 key 必须加在这里）──────────
PUBLIC_BASE_URL = "public_base_url"
PUBLIC_BASE_URL_ALT = "public_base_url_alt"
NOTIFY_WEBHOOK_URL = "notify_webhook_url"
NOTIFY_PREFS_PREFIX = "notify_prefs_user_"   # + uid（每用户一个 key）
HEALTH_REPORT_ENABLED = "health_report_enabled"
HEALTH_REPORT_TIME = "health_report_time"
ACTIVITY_CLEARED_AT = "activity_cleared_at"
FEATURED_ALBUMS = "featured_albums"


def get_setting(key: str, default: str = "") -> str:
    """缺失或空值一律返回 default（"空串=未配置"语义）。"""
    row = _db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] else default


def set_setting(key: str, value: str) -> None:
    """Upsert 单 key。多 key 原子写入场景请自持 _db_lock 并保持单事务。"""
    with _db_lock:
        _db().execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        _db().commit()
