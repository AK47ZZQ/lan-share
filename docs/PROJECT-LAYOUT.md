# 项目目录布局与关键路径（2026-09-13 更新）

> 结论先行：**唯一真源是本仓库**。生产路径、构建镜像全部是指向/镜像本仓库的辅助目录，改代码只改这里。

## 目录清单

| 路径 | 性质 | 说明 |
|---|---|---|
| `C:\Users\<你的用户名>\Desktop\AI Agent 共享空间\协作产物\lan-share` | **本仓库（真源）** | 服务端 + web/ + mobile/ + 文档，git 仓库 |
| `C:\lan-share` | junction → 本仓库 | NSSM 三服务（api/caddy/postgresql）的实际运行入口：`server.py`、`Caddyfile`、`data/`。在资源管理器或命令行访问它 = 访问本仓库 |
| `C:\lan-share-mobile` | git 镜像（同 commit） | **ASCII 路径构建镜像**：Dart 分析器/部分 gradle 步骤在中文路径下会崩（见项目记忆），APK 构建与分析从这里跑。发版前 `git fetch + reset --hard` 同步到目标 commit |
| `C:\lan-share-backup` | 数据备份（17G） | `db/ files/ pre-v35-20260829/`——**数据安全线，禁止清理**（路线图决策） |
| `C:\pginstall\pgpass.conf` | 环境文件 | PG16@5433 口令，backup.bat 依赖，不入库 |

## 运行时数据（在仓库内、gitignore）

- `data/`：网盘数据根（用户文件 + `.meta.db` 元数据库 + `.avatars/` 头像 + `.zips/`）
- `logs/`：api/caddy 日志（排障先看 `api-stdout.log`，500 的 traceback 在 `api-stderr.log`）

## 发版链路速查

1. 改代码（本仓库）→ pytest + web build + dart analyze 全绿
2. APK：在 `C:\lan-share-mobile` 同步镜像后构建（scripts/build-apk.bat）
3. 分发三件套：APK + version.json + versions.json（发版纪律见项目记忆 05-验收红线）
4. 生产 API 重启：`powershell Restart-Service lan-share-api`（Git Bash 下 `sc` 会静默失败）

## v0.21 新增的落点

- `app/api/notes.py`：笔记聚合列表端点（/api/notes/list，摘要+分类归属；
  分类 CRUD 复用 files 通道，不另立存储）
- 数据库：`notifications.broadcast_id` 列（双引擎迁移）+ `admin_broadcasts`
  表（广播记录/草稿/定时）；`photo_comments` / `photo_reactions` 不再建表
  （功能下线，存量库旧表保留不删）
- `data/.avatars/<uid>.jpg`：用户头像（v0.17，点前缀目录，不进照片扫描/文件列表）
- `data/反馈/`：App 反馈通道落盘（v0.28）。目录即状态：`待处理/`（已提交）、
  `进行中/`（处理中）、`已完成/`（frontmatter 细状态：已修复/已实装/已拒绝/已暂缓）；
  每条一个 md（frontmatter：编号/类型/状态/备注/标题/版本/设备/用户）+ `img/` 截图；
  `看板.md` 由服务端在列表查询/新反馈提交时自动重生成（勿手编）。Agent 工作流：
  `ls 待处理/` 领任务 → 处理完移入 `已完成/` 并改 frontmatter 状态+备注
- `tests/test_pg_sql_dialect.py`：PG 方言回归（v0.17；LIKE 字面 `%` 必须被
  `_pg_sql` 翻倍）

## v0.22 / v0.23 新增的落点（架构重构两批，07 报告 §八/§九）

- `app/asgi.py`：app 对象 + security_middleware（依赖图底层）；`app/main.py`
  变纯组装根（include_router ×16）——11 对 main↔api 循环 import 消除
- `app/store/settings.py`：settings KV 唯一读写入口 + key 常量
  （v3.36 起上移为 `app/settings.py`，消 security→store 分层倒置；
  `app/store/` 留给表仓储——`app/store/users.py` 是 users 表唯一 SQL 出口）
- `app/services/`：archive（zip 票据）/ cleanup（注册式定时清理）/
  passwords（scrypt+哑哈希）/ accounts（改名流程+用户名校验）
- `app/api/` 按域拆分：uploads / trash / previews / photos / albums
  （social.py 合流）/ qr；files.py 1030→460 行
- `app/contract.py` + `contracts/openapi.json` + `tests/test_contract.py` +
  `scripts/export-openapi.py`：API 契约快照（111 路径入库，变更即测试红灯）
- `env.bat`：部署端口/python 路径单一事实源（start.bat/stop.bat/install-service.bat/
  backup.bat 已接线（2026-09-12 C1/C4 批）；仅 Caddyfile 暂缓——需 {$env} 占位符
  改造且动 TLS/反代配置须运维窗口）
- mobile：`data/api/protocol.dart`（分片上限常量）+ `data/api/upload_client.dart`
  （上传协议单实现，两引擎共用）+ `data/media_kinds.dart`（扩展名事实源）+
  `backup/backup_keys.dart`（备份 prefs 键常量）+ `test/upload_client_test.dart`
- 媒体扩展名三端对齐基准：服务端 `app/filetools.py`（web filekinds.ts /
  mobile media_kinds.dart 各持对齐副本）

## v1.0 视觉规范批新增的落点（2026-09-11，App 未发版）

- `mobile/lib/ui/theme/design_tokens.dart`：**设计令牌单一事实源**——`Gap`
  间距（4pt 网格 8 档）/`Radii` 圆角（8 档）/`Motion` 动效/`IconSizes`/
  `Shadows` 阴影/`Strokes` 描边。**有意不 import 任何项目文件**（纯 Flutter
  基础库），由 app_theme 反向依赖，避免循环 import。
- `mobile/lib/ui/theme/responsive.dart`：断点 600/840 + `WindowSize` +
  `ResponsiveContext` 扩展（pagePadding/gridColumns/isLandscape）+
  `AdaptiveContent`（清单页限宽）+ `sliverGutter()`（sliver 场景限宽，把
  留白折算进 SliverPadding，**不动 Sliver 结构**）。
- `mobile/test/visual_tokens_test.dart`：14 项令牌阶梯与断点边界回归
  （599/600/839/840 四边界 + 限宽收敛 720），App 测试基线 **152→166**。
- `docs/DESIGN-v3/APP-VISUAL-SPEC.md`：视觉设计规范 v1.0（11 节，含改 UI
  自检清单）——改 App 视觉/布局/配色以它为准。
- 被改造的页面：`home_page`（内容限宽 720）、`me_page`（同）、`photos_page`
  与 `album_detail_page`（瀑布流列数 3→3/4/6 自适应）、`files_page`（网格
  列数从自定义 440/640 阈值统一到全局断点）、`ui/theme/app_theme.dart`
  （Brand 圆角指向 Radii 单源 + 排版 8 级 + FAB/IconButton/Tooltip 主题）。

## v3.52 / v3.53 新增的落点（2026-09-12 ~ 09-13）

- `app/store/`：**六表 store 化**（v3.52 B1）——sessions / devices / share_links /
  notifications / admin_broadcasts / audit 各一 store 模块，`db.py` 701→298、
  schema 定义迁 `app/store/schema.py`；**api 层对这六表的裸 SQL 归零**；
  `app/store/users.py` 仍是 users 表唯一 SQL 出口（AST 守卫）。
- `app/api/admin/`、`app/api/notify/`：admin.py / notify.py 按域拆子模块
  （门面聚合，URL 零改动）；`app/services/webhook.py` 推送独立成模块。
- `app/deps.py`：**路由依赖收编**（preview_viewer×6 / share 守卫×3 / previews 404×2）
  ——新路由复用依赖从此单点。
- `app/api/events.py`：**SSE 实时通知 `GET /api/events`**（双鉴权 Web/App /
  线程桥接 / 每用户 3 连接 + 全局 64 上限 / 15s 心跳）；Web 铃与 App inbox_poll
  实时优先、轮询兜底（**开新流前必须 cancel 旧流**，见 09-13 SSE 429 修复）。
- `app/api/search.py` + note_search 小写镜像表：**笔记全文搜索**，`/api/search`
  的 `kind` 字段向后兼容。
- `app/api/metrics.py` + `app/services/backup*.py`：**备份编排与 `/api/metrics`**
  ——进程内每日兜底 dump（B5）+ **内联验证 restore drill**（v3.53：SQLite
  `integrity_check`+settings 可读 / PG `-Fc`+`pg_restore -l` 钉 public.settings）
  + 磁盘水位 webhook 告警（含 6h 冷却）；`/api/metrics` 首个 Web 消费者是
  StatusView 备份卡。
- `tests/test_dir_browse_contract.py`：目录浏览契约 6 例（is_dir 恒有 / 文件路径
  400 语义 / 尾斜杠双形态 / 特殊字符文件名端到端 / 缺目录 404 / **HEAD 200**）。
- `tests/test_share_concurrency.py`：/s/ 分享**发票并发对抗**（8 真实线程 vs 限 3 次）。
- `contracts/openapi.json`：README 与各处引用改以 **132 路径 / 154 操作** 为准
  （09-13 全量实测）；`/preview` 的 HEAD 用**叠装饰器单方法路由**注册
  （不用 `api_route` 多方法——operationId 依赖 methods 集合哈希序、快照不可复现）。
- `mobile/lib/ui/dir_picker_page.dart`：`dirEntriesFromItems()` 纯函数只留目录
  （a6df 根修）；`mobile/lib/ui/server_albums_page.dart` 删用户名预编码
  （双重编码 %2540 修复）。
