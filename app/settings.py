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
# 公网入口的接入方式："frp" | "cloudflare"；空串 = 未配置。
# 只描述"当前对外公布的那条路是哪来的"，不控制任何隧道进程。
# 读取时兼容存量数据：mode 为空且 url 非空 → 推导为 "frp"（改造前只有 FRP）。
PUBLIC_ACCESS_MODE = "public_access_mode"
# 本套部署的唯一标识（UUID4），首次需要时生成，此后固定。
# 用途：公网入口的同源校验 —— 从外面访问 <域名>/api/instance-id，
# 返回值和本机一致，才说明那个域名指向的就是这台机器。
INSTANCE_ID = "instance_id"
# 遗留字段：备用公网入口已于 2026-11-04 删除，没有任何读取方。
# 保留常量只为一件事 —— 保存主地址时把它写成空串，清掉历史残留值
# （那条值曾被填成另一条隧道的地址，App 会把它当成能连的备用入口）。
PUBLIC_BASE_URL_ALT = "public_base_url_alt"
NOTIFY_WEBHOOK_URL = "notify_webhook_url"
NOTIFY_PREFS_PREFIX = "notify_prefs_user_"   # + uid（每用户一个 key）
HEALTH_REPORT_ENABLED = "health_report_enabled"
HEALTH_REPORT_TIME = "health_report_time"
ACTIVITY_CLEARED_AT = "activity_cleared_at"
FEATURED_ALBUMS = "featured_albums"
PHOTO_WALL_EXCLUDES = "photo_wall_excludes"   # 照片墙全局隐藏目录（JSON 数组，相对路径前缀）

# ── 反馈上报（2026-10-31）────────────────────────────────
# 反馈默认只落在本机 data/反馈/，别人装了看不到。这两个通道把反馈
# 送到开发端：A 走 HTTP 上报，B 直接开 GitHub Issue。
FEEDBACK_RELAY_ENABLED = "feedback_relay_enabled"   # "1" 开启
FEEDBACK_RELAY_URL = "feedback_relay_url"           # 开发端 /api/feedback/inbox
FEEDBACK_RELAY_TOKEN = "feedback_relay_token"       # Bearer
FEEDBACK_GH_ENABLED = "feedback_gh_enabled"         # "1" 开启
FEEDBACK_GH_REPO = "feedback_gh_repo"               # owner/repo
FEEDBACK_GH_TOKEN = "feedback_gh_token"             # fine-grained PAT，只给 issues:write
FEEDBACK_SOURCE = "feedback_source"                 # 本机标识，多台时区分来源
FEEDBACK_INBOX_TOKEN = "feedback_inbox_token"       # 收件端鉴权 token（开发端配）


def get_setting(key: str, default: str = "") -> str:
    """缺失或空值一律返回 default（"空串=未配置"语义）。"""
    row = _db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] else default


def set_setting(key: str, value: str) -> None:
    """Upsert 单 key。多 key 原子写入场景请用 set_settings。"""
    with _db_lock:
        _db().execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        _db().commit()


def set_settings(pairs: dict) -> None:
    """多 key 原子写：一次事务、一次 commit。

    为什么不能循环调 set_setting：那个每键都 commit，两次调用之间崩掉会留下
    「URL 已改成公网域名、MODE 还是 frp」这种自相矛盾的状态，而 MODE 会被
    托盘和排查逻辑当作事实读取。
    """
    with _db_lock:
        conn = _db()
        for key, value in pairs.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()


_INSTANCE_ID_CACHE: list = []


def ensure_instance_id() -> str:
    """取实例 UUID，没有就生成并落库（幂等，进程内缓存）。

    这是「公网入口同源校验」的锚点：从公网访问 <域名>/api/instance-id，
    返回值与本机一致才说明那个域名指向的是这套部署。所以：
    - 一旦生成就不能变（不要加"每次启动重新生成"之类的逻辑）
    - 并发首次调用必须只生成一个（下面整个读-生成-写都在 _db_lock 内）
    """
    if _INSTANCE_ID_CACHE:
        return _INSTANCE_ID_CACHE[0]
    import uuid as _uuid
    with _db_lock:
        if _INSTANCE_ID_CACHE:            # 双检：等锁期间别人可能已生成
            return _INSTANCE_ID_CACHE[0]
        current = get_setting(INSTANCE_ID)
        if not current:
            current = str(_uuid.uuid4())
            _db().execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (INSTANCE_ID, current))
            _db().commit()
        _INSTANCE_ID_CACHE.append(current)
        return current
