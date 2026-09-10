# 项目目录布局与关键路径（v0.21 更新）

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
- `env.bat`：部署端口/python 路径单一事实源（start.bat/stop.bat 已接线；
  install-service/backup.bat/Caddyfile 待运维窗口归位）
- mobile：`data/api/protocol.dart`（分片上限常量）+ `data/api/upload_client.dart`
  （上传协议单实现，两引擎共用）+ `data/media_kinds.dart`（扩展名事实源）+
  `backup/backup_keys.dart`（备份 prefs 键常量）+ `test/upload_client_test.dart`
- 媒体扩展名三端对齐基准：服务端 `app/filetools.py`（web filekinds.ts /
  mobile media_kinds.dart 各持对齐副本）
