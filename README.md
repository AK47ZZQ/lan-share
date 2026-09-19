# LAN Share — 家庭团队网盘（服务端 v3.58 · Web v3.55 · App v1.1.5+109）

家用小团队（2–20 人）的多用户网盘。**产品定位（2026-09-10）**：**App 是家的
内容入口**（照片备份/浏览、文件、笔记、分享、传输），**Web 是站长管理工作台**
（总览/内容/分享/通知运营/成员与权限/系统设置/治理工具/运行状态）；成员仍可经
Web 浏览内容，`/s/` 分取页、`/up` 直传页、`/login` 访客门面保持消费级体验不变。
局域网与公网（樱花FRP）功能完全对等，
支持多角色成员、私人/共享双空间、目录授权、断点续传、秒传去重、回收站、
**Web 管理台（总览仪表盘/内容/分享/通知运营/成员与权限/系统设置/治理工具/
运行状态）**、**会话与设备治理（按账号一键下线）**、
沉浸式文件预览（图片缩放/文本/Markdown/Word + **HEIC/HEIF** 缩略图与
EXIF 方向矫正；docx/xlsx/csv/pptx/rtf/doc 兼容提取）、照片时间线、带提取码的
外链分享与**定向分享（站内通知）**、**笔记分类体系（摘要聚合端点）**、
**通知管理（发送记录/撤回/草稿定时/按类型偏好）**、**公告栏 + 反馈通道**
（App「我的」直达开发端，状态：已提交/处理中/已修复/已实装…实时可追）、
**家庭动态流**、**事件推送 Webhook**、**SSE 实时通知**（`/api/events`）、
可选 **TOTP 两步验证**、手机直传和**上传二维码**、**WebDAV 挂载
（含独立密码）**、**PWA 离线应用**、**Flutter 原生 App（v1.1.0+104：浏览/传输/
相册自动备份（智能对账）/系统分享/管理面板/笔记分类/通知中心，M-A0~A5 全上线）**。
生产库 PostgreSQL 16（SQLite 仅测试）。

> **文档导航**：改代码看 [AGENTS.md](AGENTS.md)（命令与红线）· 接手看
> [HANDOFF.md](HANDOFF.md)（半天上手路径 + 凭据 + 风险）· 经验与教训看
> [docs/项目记忆/](docs/项目记忆/README.md)（踩坑/架构/里程碑/路线图/协作偏好）。

## 快速入口

```
├── 快速开始 .......... 本文 §快速开始
├── 完整 API .......... docs/API.md
├── 安全模型 .......... docs/SECURITY.md
├── 开发指南 .......... docs/DEVELOPMENT.md
├── v3 实施状态 ....... docs/STATUS-v3.md
├── 移动 App 规划 ..... docs/DESIGN-v3/APP-REQUIREMENTS.md + APP-DESIGN.md
├── App 视觉规范 ...... docs/DESIGN-v3/APP-VISUAL-SPEC.md（配色/排版/令牌/响应式）
├── 版本历史 .......... CHANGELOG.md
├── 交接手册 .......... HANDOFF.md（新维护者从这里开始）
├── AI 操作手册 ....... AGENTS.md（命令速查 / 红线 / 工作流纪律）
├── 运维手册 .......... docs/OPERATIONS.md（服务/备份/证书/故障）
├── 项目记忆 .......... docs/项目记忆/（架构/里程碑/踩坑/发版SOP/验收红线/路线图）
├── Git 工作流 ........ docs/GIT.md
└── 📦 历史归档 ........ docs/archive/ · docs/DESIGN-v3/archive/ · docs/项目记忆/archive/
                         （各目录内 README 说明"为什么归档、活文档在哪"）
```

## 快速开始

```bash
# 生产模式：NSSM 注册的 Windows 服务常驻（开机自启）
net start lan-share-api && net start lan-share-caddy
net stop  lan-share-caddy && net stop  lan-share-api   # 停止（先 caddy 后 api）

# 元数据库（v3.5 起）：PostgreSQL 16，原生服务 lan-share-postgresql（127.0.0.1:5433）
net start lan-share-postgresql   # 数据目录 C:\pginstall\data；DATABASE_URL 配在 lan-share-api 服务环境变量
# 不设 DATABASE_URL 时自动回退 SQLite（SHARE_ROOT/.meta.db，测试默认）

# 前端更新（web/ 工程改动后）
cd web && npm install && npm run build   # 产物 web/dist 即时生效，无需重启

# 开发调试（后端）
SHARE_ROOT=D:/test-data python server.py
```

| 入口 | 地址 | 认证 |
|------|------|------|
| Web UI（Vue 全站） | `https://192.168.1.100:8765` | 应用层登录 |
| 公网（樱花FRP） | `https://your-frp-domain.com:xxxxx` | 应用层登录（与局域网一致） |
| 健康探针 | `/healthz` | 免认证；**仅局域网可达**（公网入口 404，磁盘水位不外泄） |
| WebDAV | `https://192.168.1.100:8765/dav/` | Basic（成员账号，挂载各自可见空间） |

> 自签证书：浏览器首次访问点"继续访问"；裸地址输入会由 :80 端口自动跳转 HTTPS。
> 首次进入引导创建管理员账号；其他成员由管理员发邀请码自助注册。

## 功能总览

**多用户**：admin/member/guest 三角色；每人**私人空间**（仅本人）+ **共享空间**
（默认全员只读，子目录可由管理员授权读写）；**授权批量矩阵**（成员多选 ×
目录多选，带有效期与到期显示，按成员筛选）；邀请制建号；私人空间配额
（弹窗设置）；管理员可直接修改成员用户名/重置密码；停用账号即时全端踢出。

**文件管理**：网格/列表双视图（缩略图砖、四色文件夹）、搜索、最近文件、
多选批量（删除/移动/打包 zip/分享）、⋯ 与右键操作菜单、面包屑 + 返回上一级按钮；
**统一预览器**（文件页/照片页共用）：相邻导航、键盘/触屏手势、图片缩放平移
旋转（大屏原图/小屏 1280 档省流量）、文本/代码高亮、Markdown/CSV/docx/xlsx/
pptx/rtf 渲染、音频连播（App 内播放器）、视频倍速/画中画、富文本 .html 只读
渲染 +「用其他应用打开」兜底、信息面板（EXIF 尺寸/相机，预览内直接分享/重命名/
删除）、⛶ 全屏；**手机端独立配置**（精简顶栏/底部操作条/下滑跟手关闭/捏合
缩放/安全区），与 PC 互不影响；触屏居中/touchcancel 收尾干净；多图预览自适应
（253 张不撑破视口）。缩略图懒加载带微光占位与失败自动重试，大文件夹（数百张）
滚动流畅。

**通知与动态**：定向分享自动发**站内通知**；顶栏 🔔 面板双 Tab（动态/通知，
红点提示新内容）；**家庭动态流**全员可见（谁上传/分享/删除了什么，同人同类
10 分钟聚合、相对时间、新内容红点、点击直达对应目录），独立页 `/activity`。
**SSE 实时通道**（v3.52）：公告/通知发布即推 Web 铃与 App 前台，60s 轮询兜底。

**通知管理**：全员端——通知中心分类过滤（分享/广播/系统）、单条
已读/删除、**按类型组的通知偏好**（share/admin/system 三组开关，关闭的类型
不再落站内信箱）；管理端——**发送记录**（每条广播的未读/已读统计）、**撤回**
（删除全部未读行，已读留历史）、**提醒未读**（未读成员再推站外 webhook）、
**草稿/定时发送**（到点由服务端周期任务自动发出）。站内写入统一出口尊重偏好；
广播以 `broadcast_id` 贯穿便于组级操作。照片评论/表情回应已下线（照片墙回归
干净浏览，存量数据保留）。**公告栏**（v3.40）：主页长期布告，总览卡 + 文件页可关横幅。

**事件推送（通知 bot）**：管理面板配置 GET 模板 Webhook（`{title}/{body}`
占位符 URL 编码替换），**定向分享 + 新成员加入**自动推送到 ntfy / Bark /
Server酱 / Telegram 等任意兼容渠道；失败只记审计、不影响业务；测试按钮
同步回报。

**上传**：小文件直传；大文件**自适应分片**（≤2GB→5MB / ≤5GB→16MB /
≤10GB→32MB / 更大→64MB——HTTP 往返从 10GB 的 2048 降到 320）+ **断点续传**
（每块重试 3 次；跨刷新 localStorage 存会话号，3 天续传窗口）+ ≤100MB 客户端
SHA256 **秒传**；≥256MB **并发 3 路**；上传队列支持**暂停/恢复/取消/重试**
（进度条带条纹流动动画 + 实时速度与剩余时间 + 内联 sparkline 曲线）；
**同名覆盖进回收站**（分片/秒传/单请求三条路径统一，前端先弹窗确认）；
公网访问上传 ≥2GB 时弹窗提醒耗时与回局域网建议；**拖拽 / 粘贴 / 整批入队**
三入口；批次完成后端按需发系统通知（后台或 >15s）。相同内容 NTFS 硬链接
去重。

**分享**：外链 + 二维码，有效期/提取码/**下载次数上限**（按票据去重计数，
下载管理器多分段不重复计）；**提取码只在创建时一次性显示**（R1-P2-5 起不再
明文落库，防一次 dump 全泄露）；**双地址复制**（局域网/公网，公网地址在
管理面板配置）；**分享管理页**（/shares：状态徽章与筛选/下载进度/取件记录含
来源 IP/撤销）；链接与提取码独立复制；成员只能管理自己的分享，管理员可见
全部。

**回收站**：删除（界面与 WebDAV 一致）进 `.trash/` 保留 30 天，可恢复/彻底
删除/清空（清空仅 admin）；覆盖写与移动覆盖的旧版本同样进回收站。

**照片时间线**：按 EXIF 拍摄日期倒序的瀑布流，按用户过滤可见性；也支持
**入库时间排序**（v0.20 起，`/api/photos?sort=added`，App 照片页可切换）——
二维码/网页直传无 EXIF 的照片不再沉进历史月份。

**手机直传**：`/up` 页上传到 `incoming/<用户名>/`；文件页 `incoming/` 卡片
即本人目录。

**HEIC/HEIF**（iPhone 默认照片格式）：通过 `pillow-heif` 解码，缩略图 /
中图档 / 照片时间线 / EXIF 元数据全链路支持；缩放前自动 EXIF 方向矫正
（iPhone 照片旋转不再横躺）。Android 浏览器看不到 HEIC 原图，App / 网页
预览都走服务端转码的 JPEG。

**管理面板**：成员多选 × 目录多选 **授权批量矩阵**（v0.20 起收敛为共享空间一级
目录选择、子目录随父级继承；授权卡可续期/读写↔只读切换/过期红牌；带有效期与
到期显示，按成员筛选）、成员账号弹窗（改用户名/重置密码/WebDAV 独立密码、随机
密码生成）、**📣 发送通知**（标题+正文直达全员/指定成员站内通知，已配 webhook
同步推）、**🗂️ 通知管理**（发送记录/撤回未读/提醒未读/草稿定时）、
**成员视角预览**：以某成员身份登录式只读浏览（标蓝色"👁 视角"，顶栏横幅常驻
+ 一键退出）——核对授权矩阵的"能看到什么"无需真借账号；admin 端点完全隔离
不受波及。

**移动端**：PWA 已支持——手机浏览器"添加到主屏幕"即得全屏独立应用
（自定义图标/启动画面/静态资产离线缓存，仅安全上下文注册，自签环境静默降级）。
**原生 App（Flutter 跨端，源码 `mobile/`）**：M-A0~A5 里程碑（设备 Token /
照片墙浏览 / 手动传输 / 相册自动备份 / 系统分享与分享管理 / 登录页 v2 全家桶
+ 内置验证器）**全部完成**，此后进入**版本功能批次**阶段（v0.6.0 → 最新
**v1.0.0+100 首个正式版**：笔记分类体系 / 沉浸欢迎头 / 通知中心 / 备份智能对账 /
视觉规范令牌化 / 结构整改 / SSE 实时通知 / 备份与浏览限速隔离 / 暂停备份根修；
照片点赞评论已下线）。
完整演进与验收口径见 `CHANGELOG.md` 与
[docs/项目记忆/02-版本里程碑.md](docs/项目记忆/02-版本里程碑.md)；
App 工程结构/协议见 `mobile/README.md`。

**发布与分发**：App 自 **v0.9.0+37 起正式签名**，APK 经自家分享链接在
`App安装包/lanshare.apk` 分发（同文件覆盖 + 新建分享）；新版本通过 App 内
「关于与检查更新」升级，无需手动换包。发版纪律见
[docs/项目记忆/04-发版SOP.md](docs/项目记忆/04-发版SOP.md)（**三件套**：
APK + version.json + versions.json）。R8 已关（WorkManager/Room 在裁剪下启动即崩，
见 `docs/DEVELOPMENT.md`）。

> **当前测试基线（2026-09-13 实测）**：pytest **344** 项（SQLite）+ PostgreSQL
> 双引擎 **284+3skip** / Web 单测 **19** 项 + vue-tsc + 契约路径对齐守卫 /
> Dart **212** 项 + `dart analyze` 0 + UI 分层守卫（基线 0）/
> 契约 **131 路径（155 操作）** / ruff·mypy·pip-audit 全绿。

**WebDAV 独立密码**（v3.7 起）：与登录密码隔离的挂载凭据，设置后 DAV 入口
只认独立密码，登录密码对 `/dav/` 失效——凭据泄露面分离，可单独轮换，
泄露后不动主密码、不解锁 TOTP 账号。自助设置/清除需当前密码确认（计防爆破）；
admin 可重置/清除成员的独立密码。

**主题**：浅色/深色/跟随系统（头像菜单切换，本地记忆）；深浅模式完整适配。

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

## 演进与文档

**已完成（按阶段）**：v2 个人网盘（P0–P3）→ **v3 家庭团队版 M0–M6 全部交付**
（拆包 / PostgreSQL 生产库 / Vue 全站 / 多用户与授权 / 团队体验 / 可靠性 /
HEIC·WebDAV 独立密码·Webhook·成员视角预览）；**App M-A0~A5 全上线**后进入
版本功能批次（v0.6 → v0.28.10 → v1.0.0）；**09-10 起转入平台期**——产品定位更新
（App 内容入口 / Web 站长工作台）、反馈通道与看板、通知与会话治理、
**知识库/图谱上线后又按主人决策整体下线**（v0.26）、结构整改与视觉规范 v1.0、
重构小尾巴清零、SSE 实时通知、备份验证与加固。

逐版本"做了什么/为什么/踩了什么"的完整叙事在
**[docs/项目记忆/02-版本里程碑.md](docs/项目记忆/02-版本里程碑.md)**；
用户视角的版本说明在 **[CHANGELOG.md](CHANGELOG.md)**；
交付台账与测试基线在 **[docs/STATUS-v3.md](docs/STATUS-v3.md)**（更早批次在
`docs/archive/STATUS-archive.md`）。

**接下来做什么**：见 **[docs/项目记忆/15-路线图与挂账.md](docs/项目记忆/15-路线图与挂账.md)**
（待主人拍板项 / 反馈看板当前态 / 候选方向 / 明确"不做"清单）。
当前唯一挂账重项是**第二块物理盘 + 异地备份**（主人明确搁浅，生产数据仍单拷贝）。
