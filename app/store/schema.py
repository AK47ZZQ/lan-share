"""元数据库 schema 与迁移的**唯一主人**（B1 存储层拆分，自 db.py 迁入）。

## 边界

- 本模块只管"表长什么样、怎么从旧版本长到现在"：PG 全量建表（`_PG_SCHEMA`）、
  SQLite 幂等增量（`init_db` + `_migrate_v3`）、迁移脚本的序列拨正。
- 连接/方言适配（占位符翻译、行访问、事务、锁）**仍在 `app/db.py`**——本模块
  通过 `from app.db import ...` 使用它们；`app/db.py` 末尾反向 re-export 本模块的
  公共符号（`init_db`/`pg_setval_sequences`）保既有 `from app.db import init_db`
  调用方零改动。这个环是有意为之且单向初始化安全：db.py 加载完自身符号后才
  import 本模块，本模块再取 db 的名字时它们已存在。
- 各表数据的语义读写归 `app/store/` 下的表主模块（users/notifications/…）。

## 引擎差异备忘

- PG：`CREATE TABLE IF NOT EXISTS` 全量 + 逐列 `ADD COLUMN IF NOT EXISTS` 增量
  （IF NOT EXISTS 不会给存量表补列）。
- SQLite：基础表建完后走 `_migrate_v3` 逐列判断补齐（PRAGMA table_info 探测）。
"""
from app.config import SHARE_ROOT
from app.db import _db, _db_lock, USE_PG
from app.db import _PgConn  # 仅类型标注用

# 索引**单源**定义（v3.56）：PG 与 SQLite 的 CREATE INDEX 语法在本库用到的
# 形态下完全一致（IF NOT EXISTS / DESC / 部分索引 WHERE 两边都支持），所以一份
# 文本同时拼进两条建库路径（_PG_SCHEMA 尾、_migrate_v3 的 executescript 尾）。
# 此前索引散在两处各写一遍——正是"改一处忘一处"的双轨盲区，加一条就要同步两处。
_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, id);
    CREATE INDEX IF NOT EXISTS idx_device_tokens_user ON device_tokens(user_id, id);
    CREATE INDEX IF NOT EXISTS idx_todos_list ON todos(list_id, status);
    -- v3.56 补齐：每条都对应一处核实过的真实查询，非预防性堆砌（多余索引在白烧
    -- 写入成本）。预热每 30s 一轮 ORDER BY added_at DESC LIMIT 600，无索引即
    -- 全表扫+排序，是全库频率最高的一条排序查询
    CREATE INDEX IF NOT EXISTS idx_photos_added ON photos(added_at DESC, path);
    -- 登出/改密/停用账号：sessions 按 user_id 等值删
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    -- 每小时 shares.purge_expired 按 expires_at 收口
    CREATE INDEX IF NOT EXISTS idx_shares_expires ON shares(expires_at);
    -- 每小时 trash.delete_before 按 deleted_at 收口
    CREATE INDEX IF NOT EXISTS idx_trash_deleted ON trash(deleted_at);
    -- 广播撤回/统计按 broadcast_id 过滤（notifications 是全库增长最快的表）
    CREATE INDEX IF NOT EXISTS idx_notif_broadcast ON notifications(broadcast_id);
    -- 启动期审计回填每次扫 username='' 的待补行；部分索引把常驻全扫降为空段扫
    CREATE INDEX IF NOT EXISTS idx_audit_pending ON audit_log(id) WHERE username='';
"""

# 存量 photos.added_at 回填（v3.56，双轨共用一条语句）：`ORDER BY added_at DESC`
# 在 SQLite 把 NULL 排最后、在 PG 排最前（实测差异），NULL 留在库里会让预热候选
# 集被最老的行顶满。用 mtime_ns 折算作"进库时间"代理，只碰 NULL 行故幂等收敛。
_PHOTOS_ADDED_BACKFILL = (
    "UPDATE photos SET added_at=mtime_ns/1000000000.0 WHERE added_at IS NULL")

_PG_SCHEMA = """
    CREATE TABLE IF NOT EXISTS users(
        id BIGSERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        is_active INTEGER NOT NULL DEFAULT 1,
        quota_bytes BIGINT,
        totp_secret TEXT,
        totp_enabled INTEGER NOT NULL DEFAULT 0,
        webdav_password_hash TEXT,
        totp_rc TEXT,
        totp_last_step BIGINT,
        nickname TEXT NOT NULL DEFAULT '',
        avatar_path TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS sessions(
        token_hash TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL,
        ip TEXT, ua TEXT
    );
    CREATE TABLE IF NOT EXISTS shares(
        token TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        name TEXT NOT NULL,
        size BIGINT,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL,
        password_hash TEXT,
        max_downloads INTEGER,
        downloads INTEGER NOT NULL DEFAULT 0,
        owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
        password_plain TEXT,
        target_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS audit_log(
        id BIGSERIAL PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        ip TEXT,
        action TEXT NOT NULL,
        target TEXT,
        detail TEXT,
        username TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS blobs(
        sha256 TEXT PRIMARY KEY,
        size BIGINT NOT NULL,
        sample_path TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL
    );
    CREATE TABLE IF NOT EXISTS trash(
        id BIGSERIAL PRIMARY KEY,
        orig_path TEXT NOT NULL,
        trash_path TEXT NOT NULL,
        name TEXT NOT NULL,
        size BIGINT,
        deleted_at DOUBLE PRECISION NOT NULL
    );
    CREATE TABLE IF NOT EXISTS upload_sessions(
        id TEXT PRIMARY KEY,
        dest_dir TEXT NOT NULL,
        filename TEXT NOT NULL,
        total_size BIGINT NOT NULL,
        sha256 TEXT,
        created_at DOUBLE PRECISION NOT NULL,
        user_id BIGINT
    );
    CREATE TABLE IF NOT EXISTS photos(
        path TEXT PRIMARY KEY,
        taken_at TEXT NOT NULL,
        mtime_ns BIGINT NOT NULL,
        size BIGINT NOT NULL,
        duration DOUBLE PRECISION,
        codec TEXT,
        width INTEGER,
        height INTEGER,
        added_at DOUBLE PRECISION
    );
    CREATE TABLE IF NOT EXISTS grants(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        can_read INTEGER NOT NULL DEFAULT 1,
        can_write INTEGER NOT NULL DEFAULT 0,
        created_by BIGINT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION,
        UNIQUE(user_id, path)
    );
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS invites(
        code TEXT PRIMARY KEY,
        role TEXT NOT NULL DEFAULT 'member',
        created_by BIGINT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL,
        used_by BIGINT,
        used_at DOUBLE PRECISION
    );
    CREATE TABLE IF NOT EXISTS notifications(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT,
        link TEXT,
        created_at DOUBLE PRECISION NOT NULL,
        read_at DOUBLE PRECISION,
        broadcast_id TEXT
    );
    CREATE TABLE IF NOT EXISTS admin_broadcasts(
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        body TEXT,
        targets TEXT NOT NULL DEFAULT '[]',
        created_by TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'sent',
        scheduled_at DOUBLE PRECISION,
        sent_at DOUBLE PRECISION,
        withdrawn_at DOUBLE PRECISION,
        created_at DOUBLE PRECISION NOT NULL
    );
    CREATE TABLE IF NOT EXISTS device_tokens(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at DOUBLE PRECISION NOT NULL,
        last_seen DOUBLE PRECISION,
        expires_at DOUBLE PRECISION NOT NULL
    );
    CREATE TABLE IF NOT EXISTS announcements(
        id BIGSERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        body TEXT,
        created_by TEXT NOT NULL DEFAULT '',
        created_at DOUBLE PRECISION NOT NULL
    );
    CREATE TABLE IF NOT EXISTS note_search(
        path TEXT PRIMARY KEY,
        content_lower TEXT NOT NULL,
        mtime_ns BIGINT NOT NULL,
        size BIGINT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS todo_lists(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'personal',
        created_at DOUBLE PRECISION NOT NULL,
        UNIQUE(user_id, name)
    );
    CREATE TABLE IF NOT EXISTS todos(
        id BIGSERIAL PRIMARY KEY,
        list_id BIGINT NOT NULL REFERENCES todo_lists(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        due_at DOUBLE PRECISION,
        priority INTEGER NOT NULL DEFAULT 0,
        assignee_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
        status TEXT NOT NULL DEFAULT 'todo',
        done_at DOUBLE PRECISION,
        done_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
        reminded_at DOUBLE PRECISION,
        deleted_at DOUBLE PRECISION,
        created_at DOUBLE PRECISION NOT NULL
    );
"""

# 照片评论/表情回应（photo_comments / photo_reactions）已按改进建议
# 9.6-P2-13 下线：不再建表（存量部署中的旧表与数据保留不动，只是没有
# 代码再读写）。若从 SQLite 迁移到 PG，这两张表也不会再出现在目标库。
# 注意：不再使用，勿把两表加回 _PG_SEQUENCES。

_PG_SEQUENCES = ("users", "audit_log", "trash", "grants", "notifications", "device_tokens",
                 "announcements", "todo_lists", "todos")


def pg_setval_sequences(conn: _PgConn):
    """数据迁移灌入显式 id 后，把 serial 序列拨到 MAX(id)。"""
    for table in _PG_SEQUENCES:
        conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))")


def _backfill_audit_usernames():
    """存量审计回填：username 为空且 target 以 incoming/<u>/、private/<u>/ 开头的行，
    从路径反推操作者。幂等（只补空行）；无法反推的保持空串（前端显示"链接访客/
    早期记录"）。"""
    with _db_lock:
        conn = _db()
        rows = conn.execute(
            "SELECT id, target FROM audit_log WHERE username=''").fetchall()
        updates = []
        for r in rows:
            parts = str(r["target"] or "").split("/")
            if len(parts) >= 2 and parts[0] in ("incoming", "private") and parts[1]:
                updates.append((parts[1], r["id"]))
        if updates:
            for u, rid in updates:
                conn.execute("UPDATE audit_log SET username=? WHERE id=? AND username=''", (u, rid))
            if not USE_PG:
                conn.commit()


def init_db():
    SHARE_ROOT.mkdir(parents=True, exist_ok=True)
    if USE_PG:
        _db().executescript(_PG_SCHEMA)
        # CREATE IF NOT EXISTS 不会给存量表补列：PG 生产库逐列显式增量（幂等）
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS webdav_password_hash TEXT")
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_rc TEXT")
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_last_step BIGINT")
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT NOT NULL DEFAULT ''")
        _db().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_path TEXT NOT NULL DEFAULT ''")
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS duration DOUBLE PRECISION")
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS codec TEXT")
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS width INTEGER")
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS height INTEGER")
        # v0.20：入库时间（二维码/手机上传按拍摄时间落历史月份，"没更新"
        # 的感知问题）——扫描首次见到该文件时打点，按入库排序用
        _db().execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS added_at DOUBLE PRECISION")
        # v0.21：通知管理——广播组 id（撤回/发送记录按组操作）
        _db().execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS broadcast_id TEXT")
        # 索引必须建在**全部增量 ALTER 之后**：idx_photos_added / idx_notif_broadcast
        # 落在增量列上，先建索引会在"存量表尚无该列"的库上直接报错
        _db().executescript(_INDEXES)
        # v3.56：存量 NULL 打点清零（为什么必须回填见 _PHOTOS_ADDED_BACKFILL 定义处）
        _db().execute(_PHOTOS_ADDED_BACKFILL)
        # 提取码明文停存（R1-P2-5 + R2-P2）：PG 分支同样清洗存量值（幂等）
        _db().execute("UPDATE shares SET password_plain=NULL WHERE password_plain IS NOT NULL")
        _backfill_audit_usernames()
        return
    conn = _db()
    conn.execute("PRAGMA journal_mode=WAL")
    _db().executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            ip TEXT, ua TEXT
        );
        CREATE TABLE IF NOT EXISTS shares(
            token TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            size INTEGER,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            password_hash TEXT,
            max_downloads INTEGER,
            downloads INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            ip TEXT,
            action TEXT NOT NULL,
            target TEXT,
            detail TEXT
        );
        CREATE TABLE IF NOT EXISTS blobs(
            sha256 TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            sample_path TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trash(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orig_path TEXT NOT NULL,
            trash_path TEXT NOT NULL,
            name TEXT NOT NULL,
            size INTEGER,
            deleted_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS upload_sessions(
            id TEXT PRIMARY KEY,
            dest_dir TEXT NOT NULL,
            filename TEXT NOT NULL,
            total_size INTEGER NOT NULL,
            sha256 TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS photos(
            path TEXT PRIMARY KEY,
            taken_at TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            duration REAL,
            codec TEXT,
            width INTEGER,
            height INTEGER,
            added_at REAL
        );
    """)
    _db().commit()
    _migrate_v3()


def _migrate_v3():
    """v3 多用户 schema 增量迁移（幂等；仅 SQLite——PG 全量建表无此需求）：
    角色/激活/配额 + 授权表 + 邀请表 + 通知表 + 定向分享/两步验证列。
    首个（最小 id）用户自动晋升 admin，兼容 v2 单管理员存量。"""
    with _db_lock:
        conn = _db()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "role" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
        if "is_active" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "quota_bytes" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN quota_bytes INTEGER")
        if "totp_secret" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
        if "totp_enabled" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")
        if "webdav_password_hash" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN webdav_password_hash TEXT")
        if "totp_rc" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_rc TEXT")
        if "totp_last_step" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN totp_last_step INTEGER")
        if "nickname" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
        if "avatar_path" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT NOT NULL DEFAULT ''")
        ucols = {r["name"] for r in conn.execute("PRAGMA table_info(upload_sessions)").fetchall()}
        if "user_id" not in ucols:
            conn.execute("ALTER TABLE upload_sessions ADD COLUMN user_id INTEGER")
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(shares)").fetchall()}
        if "owner_id" not in scols:
            conn.execute("ALTER TABLE shares ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        if "password_plain" not in scols:
            conn.execute("ALTER TABLE shares ADD COLUMN password_plain TEXT")
        # 提取码明文停存（R1-P2-5 + R2-P2）：一次性清洗存量值——列保留是为了
        # 兼容旧 schema 迁移路径，但值不再留存（一次 dump 全泄露的根源）
        conn.execute("UPDATE shares SET password_plain=NULL WHERE password_plain IS NOT NULL")
        if "target_user_id" not in scols:
            conn.execute("ALTER TABLE shares ADD COLUMN target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        acols = {r["name"] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
        if "username" not in acols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN username TEXT NOT NULL DEFAULT ''")
        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
        if "duration" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN duration REAL")
        if "codec" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN codec TEXT")
        if "width" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN width INTEGER")
        if "height" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN height INTEGER")
        if "added_at" not in pcols:
            conn.execute("ALTER TABLE photos ADD COLUMN added_at REAL")
        # v3.56：与 PG 分支共用同一条回填语句（双轨漂移本身就是本批要治的东西）
        conn.execute(_PHOTOS_ADDED_BACKFILL)
        # v0.21：通知广播组 id（改进建议 9.6-P2-14，撤回/发送记录按组操作）
        ncols = {r["name"] for r in conn.execute("PRAGMA table_info(notifications)").fetchall()}
        if ncols and "broadcast_id" not in ncols:
            conn.execute("ALTER TABLE notifications ADD COLUMN broadcast_id TEXT")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS grants(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                can_read INTEGER NOT NULL DEFAULT 1,
                can_write INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL,
                UNIQUE(user_id, path)
            );
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS invites(
                code TEXT PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'member',
                created_by INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used_by INTEGER,
                used_at REAL
            );
            CREATE TABLE IF NOT EXISTS notifications(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                link TEXT,
                created_at REAL NOT NULL,
                read_at REAL,
                broadcast_id TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_broadcasts(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT,
                targets TEXT NOT NULL DEFAULT '[]',
                created_by TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                scheduled_at REAL,
                sent_at REAL,
                withdrawn_at REAL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS device_tokens(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                last_seen REAL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS announcements(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT,
                created_by TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS note_search(
                path TEXT PRIMARY KEY,
                content_lower TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS todo_lists(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'personal',
                created_at REAL NOT NULL,
                UNIQUE(user_id, name)
            );
            CREATE TABLE IF NOT EXISTS todos(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER NOT NULL REFERENCES todo_lists(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                due_at REAL,
                priority INTEGER NOT NULL DEFAULT 0,
                assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'todo',
                done_at REAL,
                done_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                reminded_at REAL,
                deleted_at REAL,
                created_at REAL NOT NULL
            );
            -- 照片评论/表情回应两表（photo_comments/photo_reactions）已按
            -- 改进建议 9.6-P2-13 下线，不再建表；存量库中的旧表保留不删
            -- 索引不在这里写：统一来自 _INDEXES（单源，两引擎共用一份文本）
        """ + _INDEXES)
        # 仅当库内已无 admin 时才晋升（R1-P2-1）：此前每次启动无条件把最小 id
        # 用户提为 admin，管理员被降级/删号后重启会"复活"成 admin
        conn.execute("UPDATE users SET role='admin' WHERE id=(SELECT MIN(id) FROM users) "
                     "AND NOT EXISTS(SELECT 1 FROM users WHERE role='admin')")
        conn.commit()
