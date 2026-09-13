# v3 设计 · 数据库升级（DATABASE）

> SQLite(.meta.db) → PostgreSQL 16。Alembic 管理 schema；SQLite 仅保留给单元测试
> （内存库），生产/集成测试一律 PG。

## 1. 驱动与访问层决策

| 决策项 | 结论 | 理由 |
|--------|------|------|
| 驱动 | **psycopg 3**（`psycopg[binary,pool]`） | 官方现役；原生支持管道/二进制；自带连接池 |
| ORM | **不用**，手写 SQL + repository 层 | v2 全是手写 SQL，机械搬运成本最低；类型安全靠 typed dataclass + mypy 而非 ORM |
| 迁移 | **Alembic** | 事实标准；revision 链可审计 |
| 隔离级别 | 默认（READ COMMITTED） | 无长事务需求；写冲突靠重试（合并/移动热点路径加 `SELECT ... FOR UPDATE`） |
| 并发参数 | pool 10 / max_overflow 10，2 worker × 线程池足够 | 家用规模 |

v2 教训吸收：SQLite 时代"每线程连接"问题由 `psycopg_pool` 统一解决；
所有 SQL 走参数化（现有代码已如此，搬运时保持）。

## 2. 目标 schema（baseline 迁移 0002 的蓝图）

```sql
-- 账号与认证（M3 审查定稿：用户名单列 CITEXT，废弃双列方案——
-- 双列必然漂移；citext 扩展在迁移 0001 由 alembic 执行 CREATE EXTENSION，
-- 需建库账号有相应权限，安装手册写明）
users (
  id            SERIAL PRIMARY KEY,
  username      CITEXT NOT NULL UNIQUE,             -- 大小写不敏感唯一（防爆破/
                                                    -- 重名变体绕过），显示用原值
  password_hash TEXT NOT NULL,                      -- scrypt（v2 格式原样兼容）
  role          TEXT NOT NULL DEFAULT 'member'
                CHECK (role IN ('admin','member','guest')),
  quota_bytes   BIGINT,                             -- NULL = 不限
  totp_secret   BYTEA,                              -- 加密存储，NULL = 未启用
  is_active     BOOL NOT NULL DEFAULT TRUE,         -- guest 过期/禁用软删
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)

sessions (
  token_hash  TEXT PRIMARY KEY,                     -- SHA256(token)，v2 语义不变
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ip TEXT, ua TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
)

-- 授权（完整语义见 PERMISSIONS.md §3–4）
grants ( id, user_id, path, can_read, can_write, created_by,
         created_at, expires_at, UNIQUE(user_id, path) )

-- 分享（v2 shares 升级：绑定创建者；定向分享新表）
shares (
  token TEXT PRIMARY KEY,
  owner_id INT NOT NULL REFERENCES users(id),       -- v3 新增
  vpath TEXT NOT NULL,                              -- v2 存物理相对路径，迁移时换算
  name TEXT, size BIGINT,
  password_hash TEXT,
  expires_at TIMESTAMPTZ, max_downloads INT, downloads INT DEFAULT 0,
  revoked BOOL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
share_recipients (                                 -- 定向分享（M4）
  share_id INT REFERENCES shares(id) ON DELETE CASCADE,
  user_id  INT REFERENCES users(id)  ON DELETE CASCADE,
  read_at  TIMESTAMPTZ, PRIMARY KEY (share_id, user_id)
)

-- 审计（v2 audit_log 升级：user_id 结构化；request_id 贯穿）
audit_log (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_id INT,                        -- 匿名/失败登录为 NULL
  username TEXT NOT NULL DEFAULT '',  -- 冗余存名，账号删除后仍可读
  action TEXT NOT NULL, target TEXT, detail JSONB,
  ip TEXT, request_id TEXT
)
CREATE INDEX ON audit_log (ts DESC);
CREATE INDEX ON audit_log (user_id, ts DESC);

-- 上传/去重/照片（v2 语义不变，仅换方言）
upload_sessions ( id TEXT PRIMARY KEY, user_id INT, vpath TEXT,
                  total_size BIGINT, chunk_size INT,
                  received_chunks JSONB, file_hash TEXT,
                  created_at, updated_at )
blobs ( hash TEXT PRIMARY KEY, size BIGINT, refcount INT NOT NULL DEFAULT 0,
        sample_path TEXT,          -- 自愈逻辑保留（v2 结论：rename 后随登记自愈）
        created_at )
file_refs (                        -- v2 隐式硬链接计数显式化：去重触达的所有落点
  path TEXT PRIMARY KEY,           -- 物理路径，**normcase 折叠后存储**——Windows
                                   -- 大小写不敏感，原样存会产生同文件双行、
                                   -- 去重与配额重复计数（E14 同源问题）
  blob_hash TEXT REFERENCES blobs(hash),
  owner_id INT,                    -- 配额归属（PERMISSIONS.md §8）
  trashed BOOL DEFAULT FALSE, trashed_at TIMESTAMPTZ
)
photos ( path TEXT PRIMARY KEY, owner_id INT, taken_at TIMESTAMPTZ,
         width INT, height INT, scanned_at )   -- path 同样 normcase 后存储

jobs (                             -- Worker 任务表（OVERVIEW §8；M4）
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,              -- thumbnail / photo_scan / cleanup / zip
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','running','done','failed')),
  requested_by INT,                -- 用户触发的任务（缩略图/zip）记发起人
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ, error TEXT
)
CREATE INDEX ON jobs (status, created_at);

settings ( key TEXT PRIMARY KEY, value JSONB )      -- 邀请码、系统开关、
                                                    -- 页面灰度开关（FRONTEND §4）等
invites  ( code TEXT PRIMARY KEY, created_by INT, role TEXT,
           expires_at, used_by INT, used_at )

rate_limits (                       -- 限速/防爆破共享计数（2 worker 必须共享，
                                    -- v2 为进程内存桶，见 OVERVIEW §8）
  key TEXT NOT NULL,                -- 类别:标识（login:username_ci 等）
  window_start TIMESTAMPTZ NOT NULL,
  count INT NOT NULL DEFAULT 0,
  PRIMARY KEY (key, window_start)
)
```

要点：

1. **file_refs 新表**：v2 的"blobs 表 + 文件系统本身"混管引用计数，多用户后
   配额与回收站按用户分桶需要显式登记。迁移时按磁盘扫描回填。
2. 审计 `detail` 用 JSONB——v2 的 detail 是拼字符串，PG 下改结构化，
   取件记录/审计查询免解析。
3. 回收站不再单独建表：`file_refs.trashed` 承载（路径搬入 `.trash/` 的
   现有物理语义不变），清理任务按 `trashed_at` 过期。

## 3. 迁移路径（M1 的执行顺序）

```
1. 安装 PostgreSQL 16（Windows 原生，独立数据目录，仅监听 127.0.0.1）
2. alembic upgrade head          ← 建 v3 baseline schema（0002_baseline）
3. python -m app.migrations.import_sqlite   ← .meta.db → PG 一次性导入
   - users/sessions/shares/audit/blobs/upload_sessions/photos 六类，逐表校验行数
   - vpath 换算：shares.path → 按迁移布局映射为 shared:/private: 前缀
   - audit_log 的 username 回填 user_id（可回填的回填，剩余留 NULL）
4. 双读验证（**切换时点一次性 + 不可变数据日比**，M3 审查修正）：
   原方案"每日 diff 关键计数"不成立——切 PG 后新会话/审计只写 PG，
   SQLite 副本计数必然漂移。改为：
   - 切换时点：全量行数 + 抽样内容 diff（users/sessions/shares/blobs/
     audit 全表行数，随机 20 行逐字段比对）；
   - 切换后一周：每日只比对**不可变存量**（users、blobs、切换前创建的
     shares）行数与校验和；
   - 通过后 `.meta.db` 改名 `.meta.db.v2-retired` 留档，不再回写。
```

回滚方案：切换后 48h 内若发现数据问题，恢复路径 = 停 api → 恢复
.meta.db.v2-retired → 回滚到 v2 代码 tag（M1 期间 v2 tag 不删）。

## 4. SQLite 测试策略

- conftest 用 `pytest-postgresql` 风格的临时库（容器化的 CI 里起 PG service；
  本机开发用 `testing` 模板库建/删），**不**维护 SQLAlchemy 方言双跑。
- 明确放弃"同一代码跑两种库"：测试与生产同库，方言差异（CITEXT、JSONB、
  TIMESTAMPTZ）不必写兼容层。
- 仅启动阶段（M0）在 SQLite 上跑纯逻辑单测（清洗/路径规则），M1 后全量切 PG。

## 5. 备份与恢复（M5，推翻 v2"不做每日备份"决策）

| 项 | 方案 |
|----|------|
| PG | 每日 `pg_dump -Fc` → 第二块盘 `backup/db/`，保留 14 份滚动 |
| 文件数据 | 每日增量：`robocopy /MIR` 不行（会镜像删除），用 `/E /XO` 至 `backup/files/`；`.trash/.chunks/.zips` 排除 |
| 一致性 | 备份窗口内 PG 与文件可能不一致（blob 指针 vs 文件），接受：恢复手册含"以 file_refs 为准重建孤儿/补缺"校验步骤 |
| 恢复演练 | M5 出口标准：从 `backup/db` + `backup/files` 完整恢复到沙箱 SHARE_ROOT 并通过回归清单 |
| 水位 | 备份盘 <10% 剩余时告警（与 /healthz 联动） |

## 6. 依赖变更清单（pip）

```
psycopg[binary,pool]   PG 驱动 + 连接池
alembic                schema 迁移
pyotp                  TOTP（M3）
argon2-cffi            （可选）密码哈希升级，M3 评估；默认保留 scrypt 兼容
```

新增依赖逐项在 CHANGELOG 记录理由（PLAN-v3 §6 约定）。
