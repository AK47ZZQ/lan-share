# v3 设计 · 架构总览

> v3 规划见 `PLAN-v3.md`；权限模型见 `PERMISSIONS.md`；数据库见 `DATABASE.md`；
> 前端见 `FRONTEND.md`。

## 1. 设计原则（承 v2，改一条）

1. **一个入口，一套逻辑**：认证统一应用层 Cookie 会话；Caddy 只负责 TLS、
   反代、`forward_auth` 保护的静态直连。（不变）
2. **文件系统仍是文件的唯一事实来源**，数据库只管元数据与授权。（不变；
   目录树语义依旧不入库，授权规则除外——见 PERMISSIONS.md）
3. **公网带宽是硬约束**：分块续传、秒传、静态直连绕开 Python。（不变）
4. **（新）每个请求带身份**：v2 隐含"单管理员=全局根"，v3 中所有文件操作
   的第一步都是"按当前用户解析可见空间"，`safe_path` 从全局函数变为
   `resolve(user, virtual_path)`。

## 2. 运行形态（Windows 实体机，NSSM 三服务）

| 服务 | 程序 | 监听 | 说明 |
|------|------|------|------|
| lan-share-api | `python -m app`（2 workers） | 127.0.0.1:8766 | FastAPI 后端 |
| lan-share-caddy | caddy.exe | 0.0.0.0:8765 / :8767 | TLS、反代、静态直连、前端 dist |
| lan-share-pg | PostgreSQL 16 | 127.0.0.1:5432 | 元数据库；数据目录独立盘 |

计划任务（Windows Task Scheduler）：每日 `pg_dump` + 数据目录增量备份到
第二块盘（M5 落地，工具用 robocopy /b 或 rsync 类）。

## 3. 后端拆包结构（M0 的第一刀）

```
app/
├── main.py            入口：create_app()，uvicorn 由 python -m app 启动
├── config.py          全部配置外置：SHARE_ROOT、DB URL、密钥、端口、配额默认值
│                      （env > .env > 默认值；C:\lan-share junction 不变）
├── db.py              连接池 + 事务辅助（psycopg_pool）
├── models/            SQL 与行类型定义（typed dicts / dataclass，不用 ORM 实体）
│   ├── users.py sessions.py grants.py shares.py audit.py blobs.py photos.py
├── repositories/      数据访问层：每个 repository 收拢同域 SQL，测试替身在这里
├── services/          业务逻辑（无 HTTP 概念，可单测）
│   ├── auth.py        密码/会话/防爆破/TOTP/邀请
│   ├── files.py       列表/新建/重命名/删除/恢复
│   ├── move.py        移动（os.rename 原子防覆盖语义保留）
│   ├── upload.py      分块/秒传/合并校验/硬链接去重
│   ├── share.py       外链/定向分享/票据/取件记录
│   ├── photos.py      EXIF 扫描/时间线
│   ├── webdav.py      WebDAV 方法实现
│   ├── stats.py       磁盘/配额统计（NTFS 索引去重算法保留）
│   └── cleanup.py     回收站过期/孤儿分块/临时文件（Worker 驱动）
├── storage/
│   ├── base.py        StorageBackend 接口（open/stat/list/move/link…）
│   └── local.py       本地 FS 实现：safe_path、NTFS 硬链接去重、隐藏目录规则
├── workers.py         后台线程池：缩略图、照片扫描、定时清理（M4 从请求路径移出）
├── api/               FastAPI 路由层：参数校验、权限检查、调 service、审计埋点
│   ├── deps.py        get_current_user / require_admin / rate_limit / csrf
│   ├── auth.py files.py move.py upload.py trash.py share.py webdav.py
│   ├── photos.py admin.py stats.py health.py
│   └── middleware.py  CSRF Origin、结构化日志(request id)、限速
└── migrations/        Alembic 环境（见 DATABASE.md）
```

拆包铁律：**M0 阶段 api 层只是 v2 `server.py` 区块的机械搬运**（区块顺序已在
`docs/DEVELOPMENT.md` 记录），不趁机改逻辑；行为变化全部推迟到 M1–M3。

## 4. 请求链路（多用户后的变化点）

```
浏览器/手机
  │ HTTPS
  ▼
Caddy :8765/:8767（剥离入站 XFF）
  ├─ /static/* /assets/*      → Vue dist 直连（index.html 走 try_files SPA 回退）
  ├─ /files/*                 → forward_auth(/authcheck)：
  │                              v3 变化 —— authcheck 校验会话后返回签名下载头，
  │                              由后端按 resolve(user, path) 判定该用户对此路径
  │                              有读权，签发短时效 token，Caddy 侧校验后直连
  ├─ /api/v1/*                → FastAPI：CSRF → 限速 → 会话 → require_role
  │                              → resolve(user, virtual_path) → service → 审计
  ├─ /dav/*                   → Basic 会话适配同上，挂载根 = 该用户可见空间
  ├─ /s/{token} /api/share/*  → 分享票据链路（不要求登录）
  └─ /up                      → 直传页，落到上传者 incoming/
```

补充（M3 审查定稿）：**zip 一次性 URL / 缩略图等"Caddy → 后端换手"的资源**
在多用户下同样要过鉴权——一次性 URL 生成时绑定 `user + 路径 + 过期时间`
（SECRET_KEY HMAC），`/authcheck` 除会话外还需识别该票据并对其中的路径
做 resolve(read)，防止"拿着别人目录里拼出来的 zip 链接直连下载"。

`/files/*` 的 forward_auth 变化是 M3 的安全关键件：v2 的 authcheck 只回答
"登录与否"，v3 必须回答"**这个用户能否读这个路径**"，方案（签名头 vs 短
token query）在 PERMISSIONS.md §6 定稿。

## 5. API 版本策略

- v3 起全部业务 API 挂 `/api/v1/` 前缀；v2 的 `/api/*` 旧路径在 M2 过渡期
  由 FastAPI 双挂（同一 handler），M3 完成后删除。
- 破坏性变更只允许出现在新版本前缀下；OpenAPI schema 随构建导出到
  `docs/openapi.json` 供前端 TS 类型生成。

## 6. 配置外置化（M0）

v2 中散在 `server.py` 头部的常量全部收进 `app/config.py`，来源优先级
env > `.env`（gitignore）> 默认值：

```
SHARE_ROOT        文件数据根（沿用，默认 C:/lan-share/data）
DATABASE_URL      postgresql://lan_share:***@127.0.0.1:5432/lan_share
SECRET_KEY        会话/token 签名密钥（v3 新增，首次启动生成并落权限受限文件）
LISTEN_PORT       8766
WORKERS           2
QUOTA_DEFAULT_GB  每用户默认配额
TRASH_RETENTION   30d
```

## 7. 可观测性（家用降维版）

- **结构化 JSON 日志**：uvicorn access log 关闭，由中间件输出单行 JSON
  （ts, request_id, user, method, path, status, ms, ip）；request_id 贯穿
  同请求的服务与审计写入。
- **/healthz**（免认证）：进程存活 + PG 连通 + 数据目录可写 + 磁盘水位。
- **磁盘水位告警**：>85% 时顶部徽标变红并在动态流置顶提醒（家用网盘
  最真实的风险是磁盘满）。
- 日志仍走 NSSM 轮转（10MB），错误行格式保持人可读。

## 8. Worker 线程池（M4）

请求路径中的重活迁出：视频缩略图（ffmpeg）、照片 EXIF 扫描、回收站/分块
清理、zip 打包大任务。实现为 `concurrent.futures` 线程池 + PG 任务表
（无独立队列中间件——家用规模不需要 Redis/Celery），API 提交任务返回
task_id，前端轮询。SQLite 时代"每线程连接"的教训由 psycopg_pool 吸收。

多 worker 纪律（2 worker 下必须成立）：

- **定时清理/扫描不许双跑**：任务领取用 PG advisory lock
  （`pg_try_advisory_lock(hashtext(job_key))`），拿不到锁的 worker 直接跳过本轮。
- **限速与防爆破计数迁 PG**（M1）：v2 的令牌桶/失败计数是进程内存态，
  单进程成立；2 worker 会各自记一套，全部限额翻倍。改为 PG UPSERT
  计数（`rate_limits(key, window_start, count)` / 复用防爆破表），
  家用请求量级下每次请求一次计数写完全可接受。
- 任务表 `jobs` 见 DATABASE.md §2。
