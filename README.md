# LAN Share — 家庭团队网盘（服务端 v3.37 · App v0.25.0-dev（未发版））

家用小团队（2–20 人）的多用户网盘。局域网与公网（樱花FRP）功能完全对等，
支持多角色成员、私人/共享双空间、目录授权、断点续传、秒传去重、回收站、
沉浸式文件预览（图片缩放/文本/Markdown/Word + **HEIC/HEIF** 缩略图与
EXIF 方向矫正；docx/xlsx/csv/pptx/rtf/doc 兼容提取）、照片时间线、带提取码的
外链分享与**定向分享（站内通知）**、**笔记分类体系（摘要聚合端点）**、
**知识库与图谱（v0.24：`[[双链]]`/知识图谱/agent 结构化读取）**、
**通知管理（发送记录/撤回/草稿定时/按类型偏好）**、**家庭动态流**、**事件推送
Webhook**、可选 **TOTP 两步验证**、手机直传和**上传二维码**、**WebDAV 挂载
（含独立密码）**、**PWA 离线应用**、**Flutter 原生 App（v0.21.3+64：浏览/传输/
相册自动备份（智能对账）/系统分享/管理面板/笔记分类/通知中心，M-A0~A5 全上线）**。
生产库 PostgreSQL 16（SQLite 仅测试）。

```
快速入口
├── 快速开始 .......... 本文 §快速开始
├── 完整 API .......... docs/API.md
├── 安全模型 .......... docs/SECURITY.md
├── 开发指南 .......... docs/DEVELOPMENT.md
├── v3 实施状态 ....... docs/STATUS-v3.md
├── 移动 App 规划 ..... docs/DESIGN-v3/APP-REQUIREMENTS.md + APP-DESIGN.md
├── 版本历史 .......... CHANGELOG.md
├── 交接手册 .......... HANDOVER.md（新维护者从这里开始）
├── 运维手册 .......... docs/OPERATIONS.md（服务/备份/证书/故障）
├── 项目记忆 .......... 项目记忆/（架构现状/里程碑/踩坑/发版SOP/验收红线）
├── Git 工作流 ........ docs/GIT.md
└── 演进规划 .......... PLAN-v3.md（§7 远景：CF Tunnel/移动 App；v2 存档：PLAN.md）
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

**知识库与图谱**（v0.24）：笔记目录即 Obsidian 式知识库（纯 .md 文件，
PC 端 Obsidian 可直接打开同一目录——人机共库）。`[[双链]]`+`#标签`+aliases
frontmatter；App 端知识图谱页（力导向星球图，服务端预布局，拖拽/缩放/长按
高亮一跳/筛选"只看幽灵=待写清单"/搜索定位）、编辑器 `[[` 补全与反链面板、
保存自动维护 created/updated、预览双链可点；Web 端 `/kb` Canvas 图谱同规格；
agent 层六端点（graph/backlinks/neighbors/names/context/reindex，约定见
`docs/KB-CONVENTIONS.md`），context 一键返回"全文+一跳邻居摘要"。

**通知与动态**：定向分享自动发**站内通知**；顶栏 🔔 面板双 Tab（动态/通知，
红点提示新内容）；**家庭动态流**全员可见（谁上传/分享/删除了什么，同人同类
10 分钟聚合、相对时间、新内容红点、点击直达对应目录），独立页 `/activity`。

**通知管理（v3.31）**：全员端——通知中心分类过滤（分享/广播/系统）、单条
已读/删除、**按类型组的通知偏好**（share/admin/system 三组开关，关闭的类型
不再落站内信箱）；管理端——**发送记录**（每条广播的未读/已读统计）、**撤回**
（删除全部未读行，已读留历史）、**提醒未读**（未读成员再推站外 webhook）、
**草稿/定时发送**（到点由服务端周期任务自动发出）。站内写入统一出口尊重偏好；
广播以 `broadcast_id` 贯穿便于组级操作。照片评论/表情回应已下线（照片墙回归
干净浏览，存量数据保留）。

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
同步推）、**🗂️ 通知管理**（v3.31：发送记录/撤回未读/提醒未读/草稿定时）；
**成员视角预览**：以某成员身份登录式只读浏览（标蓝色"👁 视角"，顶栏横幅常驻
+ 一键退出）——核对授权矩阵的"能看到什么"无需真借账号；admin 端点完全隔离
不受波及。

**移动端**：PWA 已支持——手机浏览器"添加到主屏幕"即得全屏独立应用
（自定义图标/启动画面/静态资产离线缓存，仅安全上下文注册，自签环境静默降级）。
**原生 App（Flutter 跨端，源码 `mobile/`）**：M-A0~A5 里程碑（设备 Token /
照片墙浏览 / 手动传输 / 相册自动备份 / 系统分享与分享管理 / 登录页 v2 全家桶
+ 内置验证器）**全部完成**，此后进入**版本功能批次**阶段（v0.6.0 → 最新
**v0.21.3+64**：五项 P0 修复（笔记覆盖保存/首页跳转/标题重复等）+ 笔记分类
体系 + 沉浸欢迎头 + 通知中心增强 + 备份智能对账；照片点赞评论下线）。
完整演进与验收口径见 `CHANGELOG.md`、根目录「路线图状态」、
`项目记忆/02-版本里程碑.md`；App 工程结构/协议见 `mobile/README.md`。

**发布与分发**：App 自 **v0.9.0+37 起正式签名**，此后版本演进（0.9→0.20）详见
`CHANGELOG.md` 与 `项目记忆/02-版本里程碑.md`（不在本文重复罗列，避免与演进
史脱节）。APK 经自家分享链接在 `App安装包/lanshare.apk` 分发（30 天有效，同文件
覆盖 + 新建分享）；新版本通过 App 内「关于与检查更新」升级，无需手动换包。
R8 已关（WorkManager/Room 在裁剪下启动即崩，见 `docs/DEVELOPMENT.md`）。
**当前测试基线：pytest 238 项（SQLite）+ PostgreSQL 双引擎 / web 单测 6 项 + vue-tsc / Dart 179 项 + analyze 0 / 契约 117 路径 / ruff·mypy 全绿。**
完整规划见 `docs/DESIGN-v3/APP-*.md`，构建与 SDK 选型见 `docs/DEVELOPMENT.md §手机 App`。

**WebDAV**：`/dav/` 挂载为网络磁盘，走与网页相同的空间与权限解析。
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
                        │               后端判权下载（review 2026-09-02：
                        │               不得 file_server 直挂，会越权读
                        │               他人 private/；大文件走 FileResponse）
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
lan-share/
├── server.py               兼容 shim（NSSM 入口 server:app 不变）
├── app/                    后端包：config/settings/db/security/filetools/spaces/main
│                           api/{auth,files,share,webdav,system,admin,notify,social,notes}/
│                           store/{users}（表仓储：users 表唯一 SQL 出口）
├── web/                    Vue3 前端工程（构建产物 web/dist 由 FastAPI 挂载，
│                           node_modules + dist 已 gitignore）
├── mobile/                 Flutter 原生 App（v0.21.0+61；技术文档见 mobile/README.md）
├── Caddyfile               反代/TLS/forward_auth/:80跳转
├── certs/                  自签证书配置（san.cnf；*.pem 私钥不入库）
├── scripts/                工具（build-apk.bat 镜像构建；migrate_sqlite_to_pg.py 迁库）
├── docs/                   API / SECURITY / DEVELOPMENT / STATUS-v3 / DESIGN-v3 / DEBUG-ACCOUNT / GIT
├── data/                   运行时数据根（不入库：用户文件/.meta.db/.trash/...）
├── PLAN-v3.md              v3 规划；PLAN.md 为 v2 存档
├── CHANGELOG.md            版本历史
├── backup.bat              每日 23:30 自动跑（pg_dump -Fc + 文件增量）
├── check.bat               本地质量门禁（pytest 基线）
├── tests/                  231 项测试（基线 + 多用户 + v3.5 + 大文件 + 公网防护
│                           + 四小项 + 设备 Token + 兼容性回归 + 配对码 + 两步验证恢复码
│                           + App 批次 + 安全修复回归 + v0.17/v0.20 批次 + PG 方言/
│                           schema 漂移守卫 + AST 回潮守卫；照片互动测试已随功能下线移除。
│                           设 LANSHARE_TEST_PG_URL 即同一套用例改跑 PostgreSQL——
│                           在目标库的临时 schema 建表，不碰生产 public 表）
└── logs/                   服务日志（NSSM 轮转）
```

## 常用运维

```bash
# 改了后端（app/ 或 server.py）
net stop lan-share-api && net start lan-share-api
# 改了 Caddyfile
net stop lan-share-caddy && net start lan-share-caddy
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

## 路线图状态

**知识库与图谱 ✅**（v0.24.0+69：M-K0 服务端引擎+六端点 / M-K1 App 图谱页 /
M-K2 编辑器双链四件套 / M-K3 agent 层与约定文档 / M-K4 web `/kb`；
红队对抗审查后收口——可见性收口/三端解析镜像/字节截断等 8 项修复）。
挂账 v2：`[[#锚点]]` 小节锚、代码块内 `[[ ]]` 跳过解析、rename 断链 fixup、
web 端笔记视图。

**笔记主功能化 & 知识库完善 ✅**（服务端 v3.37 + App v0.25-dev：底栏换位
笔记↔传输；`/api/notes/list` 下发 **categories 含空分类** + abs_path——修复"新建
分类成功但侧栏看不到"；KB 图谱自动挂载**分类枢纽节点**（kind=folder，空分类也入
图，失效指纹纳入目录）；`/api/kb/context` 增 abs_path；App 笔记点按改**预览优先**
/ 分类横滑栏 / 复制路径 / 图谱点按即读；Web /kb 同步：枢纽渲染 + 点按显示内容 +
只看该分类。**App 未发版**——须 flutter test + 真机验证后构建 APK。已实测抓到并
修复两处历史 bug：App 分类过滤 `'$_cat!/'` 字面量 `!`、Web 同款；服务端已上线核验
（Web dist 已构建生效））。

**服务端 v3.36 ✅**（独立复审修复批：`app/roles.py` 与整批历史改动入库 + 回滚点；
**双引擎测试**（同一套用例跑 SQLite 与 PostgreSQL，PG 侧此前零覆盖）；新增 CI
（ruff/mypy/pytest×2 引擎/web 测试）；Webhook 重定向逐跳复校验；WebDAV 真锁
（RFC 4918，原 LOCK 为假锁）；`db.transaction()` 真事务（redeem/改密/删号等）；
PG 连接有界化；**users 表单一主人**（`app/store/users.py`，11 模块 80 条 SQL 迁出）；
settings 降 L0；27 处 async 内裸 `_db()` 清零；3 条 AST 回潮守卫；Web 端补测。
已重启上线并核验。门禁 SQLite 231 / PG 229+2skipped / ruff·mypy 全绿 / web 6 项。
详见 `项目记忆/11-独立工程复审-2026-09-10.md` §九执行记录）。

**服务端 v3.35 ✅**（安全加固与"约定收口"第一批：Webhook SSRF 防护 / 管理端不再
拉取密码哈希 / APK SHA 缓存加锁 + `filetools.to_rel()` 收编 38 处路径转换 /
`app/roles.py` 角色常量化 / 私有符号公共化。计划见 `项目记忆/10-改进计划-v3.35.md`）。

v2 个人网盘（P0–P3）✅ → v3 家庭团队版：M0 拆包 ✅ · M1 数据层 ✅（v3.5 起
**PostgreSQL 16 生产库**，SQLite 仅测试）· M2 Vue 全站 ✅ · M3 多用户核心 ✅
（TOTP 可选启用）· M4 团队体验 ✅（定向分享/通知/动态流/共享相册双维度/
大文件上传/暂停续传/移动 PWA）· M5 可靠性 ✅（每日 pg_dump 备份保留 2 天；
公网入口隔离加固——防爆破分账/healthz 公网 404/限速分桶；第二块备份盘与
恢复演练挂账）· M6 增量 ✅（HEIC/WebDAV 独立密码/事件推送 Webhook/成员
视角预览）。
**移动方向**：PWA 阶段 0 ✅（添加到主屏幕）；原生 App（Flutter 跨端）
M-A0 契约 ✅ · M-A1 浏览 ✅ · M-A2 手动传输 ✅ · M-A3 相册自动备份 ✅
（WorkManager+账本+前台服务，杀进程续传/重启恢复验收通过）· M-A4 分享与
发布 ✅（系统分享接收/分享管理；经七轮审查加固）· 发布收尾 ✅（release
签名 + 品牌图标）· 真机反馈批次 ✅（备份约束收口/照片统一落
`照片/手机相册/`/首页面板/Sliver 虚拟化时间线）· 全功能实测修复批 ✅ ·
M-A5 登录页 v2 全家桶 ✅（方向 A 布局/连接预检/生物识别一键登录/扫码填
地址/配对码免密登录/两步验证恢复码/App 内置验证器，v0.5.2+13）·
**全板块功能补齐 ✅**（v0.6.0+14：照片墙视频播放/文件页基本功/传输批量/
分享二维码外发编辑/深色模式/应用锁/检查更新闭环/备份排除相册/回收站页）·
**应用锁热修 ✅**（v0.6.1+15：开启即锁/解锁被弹回/登出逃不掉三连环根修，
LockGate 单测 + 录指纹 E2E 七项全过）· **动态与备份完善 ✅**（v0.6.2+16：
排除相册"无法读取"根修——主引擎漏注册媒体通道，排除/回补 E2E 闭环；
首页家里动态栏目；备份失败红字/实时进度/电池豁免引导）·
**动态更名与清空回收站 ✅**（v0.6.3+17：首页「动态」；回收站清空仅
admin+二次确认；排除相册页加固）· **备份落位重构 ✅**（v0.7.0+18：
自动备份进 `照片|视频/<用户名>/<相册名>/` 三级目录，账本加相册维度、
升级秒传迁移，改名用户自动落新目录；手动上传落 `照片/<用户名>/手机
上传/`；服务端照片归属兼容新旧落点）· **功能持续批次 ✅**（v0.8.1
六轮审查加固 → v0.11 照片墙瀑布流/推送轮询/相册共创 → v0.14 专辑
文件夹式重构 → v0.15 自动切换（探测三道门+抖动四件套）/存储详情 →
v0.16 传输页重做/彻底删除/清理缓存/安全审查加固 → v0.17 备份暂停/
资料体系/预览大改/笔记 → v0.18 首页卡片/音频预览 → v0.19 富文本笔记/
docx 兼容预览 → v0.20 九项体验批次 → **v0.21.2+63 改进建议 9.6 批次+欢迎头两轮热修**
（五项 P0 修复：笔记覆盖保存+自动保存/首页"我的分享"与备份卡片跳转/
笔记标题重复/通知按钮发白；笔记分类体系 B 方案：/api/notes/list 摘要
聚合+分类栏+置顶收藏+拖拽移动；首页沉浸渐变欢迎头；通知管理全做：
发送记录/撤回/草稿定时/按类型偏好/单条操作；备份智能对账：空间预检/
相册被删识别/改名不重传/管理服务端相册；复制服务器路径；照片查看器
平移手势+长按菜单；照片点赞评论下线）。
规格与设计见 `docs/DESIGN-v3/APP-*.md`。
详见 `docs/STATUS-v3.md`；规划见 `PLAN-v3.md`（§7 远景：CF Tunnel/域名试点）。
