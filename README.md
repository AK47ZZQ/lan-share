<img src="logo/app-login-brand.svg" height="72" alt="LAN Share">

# LAN Share

[![CI](https://github.com/AK47ZZQ/lan-share/actions/workflows/ci.yml/badge.svg)](https://github.com/AK47ZZQ/lan-share/actions/workflows/ci.yml)
![server](https://img.shields.io/badge/server-v3.70-2563eb)
![platform](https://img.shields.io/badge/platform-Windows%20%C2%B7%20Web%20%C2%B7%20Android-64748b)

**家用小团队网盘（2–20 人）。App 是家的内容入口，Web 是站长管理工作台。**
Windows 自托管，局域网与公网（樱花 FRP）功能完全对等，认证全在应用层。

![站长工作台 · 总览](docs/screenshots/overview.png)

## 快速开始

```bash
# 1. 起服务（NSSM 注册的 Windows 服务，开机自启）
net start lan-share-postgresql
net start lan-share-api

# 2. 前端（改了 web/ 才需要；产物 web/dist 即时生效，不用重启）
cd web && npm install && npm run build

# 3. 开发调试（后端，不碰生产数据）
SHARE_ROOT=D:/test-data python server.py
```

访问 `https://<本机 IP>:8765`。自签证书首次访问点「继续访问」。首启会引导创建管理员账号，其他成员由管理员发邀请码自助注册。

| 入口 | 地址 | 认证 |
|---|---|---|
| Web 全站（Vue） | `https://<IP>:8765` | 应用层登录 |
| 公网（樱花 FRP） | `https://your-frp-domain.com:xxxxx` | 同上，与局域网功能对等 |
| 健康探针 | `/healthz` | 免认证，仅局域网可达 |
| WebDAV | `https://<IP>:8765/dav/` | Basic（成员账号） |

## 功能

| 领域 | 能力 |
|---|---|
| **多用户与权限** | admin / member / guest 三角色；私人 + 共享双空间；目录授权批量矩阵（成员×目录，带有效期）；邀请制建号；私人空间配额；停用即全端踢出 |
| **文件与预览** | 网格/列表双视图、搜索、最近文件、多选批量；统一预览器（图片缩放旋转、文本/Markdown/CSV/docx/xlsx/pptx 渲染、音视频、HEIC/HEIF + EXIF 矫正） |
| **上传与传输** | 自适应分片（≤2GB→5MB … >10GB→64MB）、断点续传、秒传、并发 3 路、队列可暂停/恢复、拖拽/粘贴/整批入队 |
| **分享与协作** | 外链 + 二维码（有效期/提取码/下载次数上限）、定向分享、目录打包下载 |
| **照片** | 时间线（EXIF 优先）、缩略图懒加载、相册精选 |
| **笔记** | 分类体系 + 摘要聚合端点 |
| **通知与动态** | 站内通知中心、家庭动态流、SSE 实时推送、Webhook 事件推送（ntfy/Bark/Telegram）、公告栏 + 反馈通道 |
| **安全** | 应用层认证、可选 TOTP 两步验证、会话与设备治理、审计日志 |
| **客户端** | Vue3 Web（PWA 可离线）、Flutter 原生 App（相册自动备份 / 系统分享 / 通知中心） |

> 完整能力清单与逐版变更见 [CHANGELOG.md](CHANGELOG.md)（130 KB，按版本倒序）。

## 文档

| 你要什么 | 读哪 |
|---|---|
| **改代码前必读**（命令 / 红线 / 踩坑） | [AGENTS.md](AGENTS.md) · [03-踩坑清单](docs/项目记忆/03-踩坑清单.md) |
| 接手项目（半天上手路径 + 风险） | [HANDOFF.md](HANDOFF.md) |
| 完整 API | [docs/API.md](docs/API.md) |
| 安全模型 | [docs/SECURITY.md](docs/SECURITY.md) |
| 运维（服务/备份/证书/故障） | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| 架构与现状 | [docs/项目记忆/01-架构与现状.md](docs/项目记忆/01-架构与现状.md) |
| 版本历史 | [CHANGELOG.md](CHANGELOG.md) |
| App 视觉规范 | [docs/DESIGN-v3/APP-VISUAL-SPEC.md](docs/DESIGN-v3/APP-VISUAL-SPEC.md) |
| 历史归档 | `docs/archive/` · `docs/DESIGN-v3/archive/` · `docs/项目记忆/archive/` |

## 架构

```
外网设备 ──樱花FRP──▶ Caddy :8767 (HTTPS, 剥离入站XFF)
内网设备 ──局域网──▶ Caddy :8765/:443 (HTTPS; :80 跳转 HTTPS)
                        │
                        ├─ /files/*  → forward_auth(/authcheck) 后反代到
                        │               后端判权下载（不得 file_server 直挂，
                        │               会越权读他人 private/；大文件走 FileResponse）
                        ├─ /api/* /dav/* /s/* /up /preview/* /thumb/*
                        │             → FastAPI :8766 (127.0.0.1)
                        │                ├─ PostgreSQL 16（127.0.0.1:5433，
                        │                │  NSSM 服务 lan-share-postgresql；
                        │                │  用户/会话/授权/分享/审计/指纹/
                        │                │  照片/设置/device_tokens），
                        │                │  不设 DATABASE_URL 时回退 SQLite
                        │                │  （仅测试，data/.meta.db）
                        │                ├─ 数据目录 data/（private/<用户>/、
                        │                │  incoming、共享、.trash .thumbs
                        │                │  .chunks .zips）
                        │                ├─ 应用层限速 / CSRF / 防爆破（按入口
                        │                │  分账）/ 审计 / X-As-User 成员视角预览
                        │                ├─ SSE /api/events（实时通知，轮询兜底）
                        │                └─ Bearer 设备 Token（90d 滑动/吊销审计）
                        └─ /assets/* / → FastAPI 挂载 web/dist（Vue3 SPA + 深链回退）
```

**技术栈**：FastAPI + uvicorn、Caddy v2（原生无插件）、PostgreSQL 16 +
psycopg（生产）/ SQLite（测试）、Vue 3 + Vite + TypeScript（web/ 工程）、
Pillow + pillow-heif、qrcode、pyotp、ffmpeg（可选）。
**Flutter App**：mobile/（Dio + flutter_riverpod + sqflite + flutter_secure_storage
+ flutter_cache_manager；完整依赖见 `mobile/pubspec.yaml`）。

## 目录结构

```
lan-share/                     C:\lan-share 是指向本仓库根的 junction（改这里=改线上）
├── server.py                  兼容 shim（NSSM 入口 server:app 不变）
├── app/                       后端包（≈10.3K 行）
│   ├── asgi.py                持 app + 中间件（大包体积守卫等）
│   ├── api/                   28 个按域 APIRouter 模块
│   ├── services/              11 个业务模块（archive/cleanup/backup/metrics/…）
│   ├── store/                 9 个表主人模块（users/photos/shares/…）+ schema.py
│   ├── spaces.py              ⭐ resolve() = 安全核心，所有文件入口必经
│   └── db.py                  双引擎连接（PG 生产 / SQLite 测试）
├── web/                       Vue3 前端工程（构建产物 web/dist 由 FastAPI 挂载）
├── mobile/                    Flutter 原生 App（Dart ≈25.8K 行；见 mobile/README.md）
├── Caddyfile                  反代/TLS/forward_auth/:80跳转
├── certs/                     自签证书配置（san.cnf；*.pem 私钥不入库）
├── scripts/                   构建/运维/契约导出工具（build-apk.bat、export-openapi.py…）
├── tests/                     38 个测试文件（pytest 344 项；设 LANSHARE_TEST_PG_URL
│                              即同一套用例改跑 PostgreSQL——临时 schema 隔离）
├── contracts/openapi.json     OpenAPI 契约快照（131 路径/155 操作）
├── docs/                      API·SECURITY·OPERATIONS·DEVELOPMENT·STATUS-v3·GIT…
│   ├── 项目记忆/               ⭐ 经验与教训（架构/里程碑/踩坑/发版SOP/路线图）
│   │   └── archive/           09-10 审查批 + 09-11 运行日志
│   ├── archive/               旧规划/快照/过程记录
│   └── DESIGN-v3/             App 设计与视觉规范（archive/ 为立项期设计稿）
├── data/                      运行时数据根（不入库：用户文件/.trash/.chunks/…）
│   └── 反馈/                  反馈看板（目录即状态：待处理/进行中/已完成）
├── AGENTS.md                  🔴 AI 代理操作手册（命令/红线/工作流纪律）
├── HANDOFF.md                 交接手册（半天上手路径/凭据/风险/决策记录）
├── CHANGELOG.md               版本历史（v0.20 及以前见 docs/archive/）
├── env.bat                    部署单源（路径/端口/解释器全在这里）
├── backup.bat                 每日备份（**当前暂停**，见 HANDOFF.md §4）
├── check.bat                  本地质量门禁（pytest+ruff+mypy+pip-audit+UI分层）
└── logs/                      服务日志（NSSM 轮转）
```

## 常用运维

```bash
# 改了后端（app/ 或 server.py）——Git Bash 下 sc 静默失败，用 PowerShell
powershell Restart-Service lan-share-api
# 改了 Caddyfile
powershell Restart-Service lan-share-caddy
# 前端改动无需重启：cd web && npm run build

# 质量门禁（改后端必跑）
check.bat

# 查日志
tail -f logs/api-stderr.log logs/caddy-stderr.log
```

### 常见问题

| 问题 | 处理 |
|------|------|
| 证书不受信任 | 自签证书所致，点"继续访问"；或把 `certs/cert.pem` 导入系统根证书 |
| 忘记管理员密码 | 生产库为 PostgreSQL：清空 users 表后用 `/api/setup` 重建（仅空表开放，首用户即 admin），成员账号由管理员邀请重建；删 `data/.meta.db` 仅对未设 DATABASE_URL 的 SQLite 实例有效 |
| 成员如何加入 | 管理 → 邀请 → 生成邀请码 → 对方在登录页"使用邀请码加入" |
| 公网分享打不开 | 确认复制的是 🌐 公网链接（管理面板先配置公网访问地址）；FRP 隧道需在线 |
| 5G 下载 apk 被提示次数上限 | v3 已修（按票据去重计数）；旧分享撤销重建即可 |
| Windows 映射网络驱动器失败 | 原生 WebClient 对自签证书苛刻，改用 RaiDrive/WinSCP |
| 改了前端没生效 | Ctrl+F5 强刷一次；之后资产文件名哈希自动更新 |

## 许可

私有项目，未开源。`data/`、`env/`、`.venv/`、凭据与证书均不入库（见 `.gitignore`）。
