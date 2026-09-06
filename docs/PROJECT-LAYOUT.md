# 项目目录布局与关键路径（v0.17 整理）

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

## 本次（v0.17）新增的落点

- `data/.avatars/<uid>.jpg`：用户头像（点前缀目录，不进照片扫描/文件列表）
- `tests/test_pg_sql_dialect.py`：PG 方言回归（LIKE 字面 `%` 必须被 `_pg_sql` 翻倍）
