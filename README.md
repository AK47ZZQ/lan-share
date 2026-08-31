# LAN Share — 家庭团队网盘（v3.7）

家用小团队（2–20 人）的多用户网盘。局域网与公网（樱花FRP）功能完全对等，
支持多角色成员、私人/共享双空间、目录授权、断点续传、秒传去重、回收站、
沉浸式文件预览（图片缩放/文本/Markdown/Word + **HEIC/HEIF** 缩略图与
EXIF 方向矫正）、照片时间线、带提取码的外链分享与**定向分享（站内通知）**、
**家庭动态流**、**事件推送 Webhook**、可选 **TOTP 两步验证**、手机直传和
**WebDAV 挂载（含独立密码）**、**PWA 离线应用**、**Flutter 原生 App（M-A1）**。
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
旋转（大屏原图/小屏 1280 档省流量）、文本/代码高亮、Markdown/CSV/docx 渲染、
音频连播、视频倍速/画中画、信息面板（EXIF 尺寸/相机，预览内直接分享/重命名/
删除）、⛶ 全屏；**手机端独立配置**（精简顶栏/底部操作条/下滑跟手关闭/捏合
缩放/安全区），与 PC 互不影响；触屏居中/touchcancel 收尾干净；多图预览自适应
（253 张不撑破视口）。缩略图懒加载带微光占位与失败自动重试，大文件夹（数百张）
滚动流畅。

**通知与动态**：定向分享自动发**站内通知**；顶栏 🔔 面板双 Tab（动态/通知，
红点提示新内容）；**家庭动态流**全员可见（谁上传/分享/删除了什么，同人同类
10 分钟聚合、相对时间、新内容红点、点击直达对应目录），独立页 `/activity`。

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
下载管理器多分段不重复计）；提取码明文保存可在管理页查看；**双地址复制**
（局域网/公网，公网地址在管理面板配置）；**分享管理页**（/shares：状态
徽章与筛选/下载进度/取件记录含来源 IP/撤销）；链接与提取码独立复制；
成员只能管理自己的分享，管理员可见全部。

**回收站**：删除（界面与 WebDAV 一致）进 `.trash/` 保留 30 天，可恢复/彻底
删除/清空（清空仅 admin）；覆盖写与移动覆盖的旧版本同样进回收站。

**照片时间线**：按 EXIF 拍摄日期倒序的瀑布流，按用户过滤可见性。

**手机直传**：`/up` 页上传到 `incoming/<用户名>/`；文件页 `incoming/` 卡片
即本人目录。

**HEIC/HEIF**（iPhone 默认照片格式）：通过 `pillow-heif` 解码，缩略图 /
中图档 / 照片时间线 / EXIF 元数据全链路支持；缩放前自动 EXIF 方向矫正
（iPhone 照片旋转不再横躺）。Android 浏览器看不到 HEIC 原图，App / 网页
预览都走服务端转码的 JPEG。

**管理面板**：成员多选 × 目录多选 **授权批量矩阵**（带有效期与到期显示）、
成员账号弹窗（改用户名/重置密码/WebDAV 独立密码、随机密码生成）；
**成员视角预览**：以某成员身份登录式只读浏览（标蓝色"👁 视角"，顶栏横幅常驻
+ 一键退出）——核对授权矩阵的"能看到什么"无需真借账号；admin 端点完全隔离
不受波及。

**移动端**：PWA 已支持——手机浏览器"添加到主屏幕"即得全屏独立应用
（自定义图标/启动画面/静态资产离线缓存，仅安全上下文注册，自签环境静默降级）。
**原生 App（Flutter 跨端，源码 `mobile/`）**：

| 里程碑 | 状态 | 内容 |
|--------|------|------|
| M-A0 | ✅ | 设备 Token 三端点 + Bearer 中间件（90 天滑动/SHA256 存储/吊销审计/停用即失效/同名设备轮换）；84 项测试 |
| M-A1 | ✅ | 照片墙（无限滚动 + 全屏查看 + 缩放）/ 文件浏览（面包屑栈导航）/ 我的（设备管理 + 登出）/ 自签证书 TOFU 信任流；模拟器端到端验证通过 |
| M-A2 | ⏳ | 上传/下载/传输中心（暂停续传重试）；500MB 杀进程续传验收 |
| M-A3 | ⏳ | 自动备份引擎 + 设置页 + 账本 |
| M-A4 | ⏳ | 系统分享接收 / 分享管理 / APK 自举分发 |

调试版 APK 通过自家分享链接在 `App安装包/lanshare-debug.apk` 分发（debug
签名，iPhone/上架留到 M-A4）。完整规划见 `docs/DESIGN-v3/APP-*.md`，构建与
SDK 选型见 `docs/DEVELOPMENT.md §手机 App`。

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
                        ├─ /files/*  → forward_auth 后 file_server 静态直连
                        │               （大文件下载不经过 Python）
                        ├─ /api/* /dav/* /s/* /up
                        │             → FastAPI :8766 (127.0.0.1)
                        │                ├─ PostgreSQL 16（127.0.0.1:5433，
                        │                │  NSSM 服务 lan-share-postgresql；
                        │                │  用户/会话/授权/分享/审计/指纹/
                        │                │  照片/设置/device_tokens），
                        │                │  不设 DATABASE_URL 时回退 SQLite
                        │                │  （仅测试，data/.meta.db）
                        │                ├─ 数据目录 data/（private/<用户>/、
                        │                │  共享、.trash .thumbs .chunks .zips）
                        │                ├─ 应用层限速 / CSRF / 防爆破（按入口
                        │                │  分账）/ 审计 / X-As-User 成员视角预览
                        │                └─ Bearer 设备 Token（90d 滑动/吊销审计）
                        └─ /assets/*  → Vue3 SPA 构建产物（web/dist）
```

**技术栈**：FastAPI + uvicorn、Caddy v2.11（原生无插件）、PostgreSQL 16 +
psycopg（生产）/ SQLite（测试）、Vue 3 + Vite + TypeScript（web/ 工程）、
Pillow + pillow-heif、qrcode、pyotp、ffmpeg（可选）。
**Flutter App**：mobile/（Dio + Riverpod + flutter_secure_storage + flutter_cache_manager）。

## 目录结构

```
lan-share/
├── server.py               兼容 shim（NSSM 入口 server:app 不变）
├── app/                    后端包：config/db/security/filetools/spaces/main
│                           api/{auth,files,share,webdav,system,admin,notify}/
├── web/                    Vue3 前端工程（构建产物 web/dist 由 FastAPI 挂载，
│                           node_modules + dist 已 gitignore）
├── mobile/                 Flutter 原生 App（M-A0/A1 已上线：M-A2..4 待）
├── Caddyfile               反代/TLS/forward_auth/:80跳转
├── certs/                  自签证书配置（san.cnf；*.pem 私钥不入库）
├── scripts/                工具（build-apk.bat 镜像构建；migrate_sqlite_to_pg.py 迁库）
├── docs/                   API / SECURITY / DEVELOPMENT / STATUS-v3 / DESIGN-v3 / DEBUG-ACCOUNT / GIT
├── data/                   运行时数据根（不入库：用户文件/.meta.db/.trash/...）
├── PLAN-v3.md              v3 规划；PLAN.md 为 v2 存档
├── CHANGELOG.md            版本历史
├── backup.bat              每日 23:30 自动跑（pg_dump -Fc + 文件增量）
├── check.bat               本地质量门禁（pytest 基线）
├── tests/                  84 项测试（基线 + 多用户 + v3.5 + 大文件 + 公网防护
│                           + 四小项 + 设备 Token + 兼容性回归）
├── logs/                   服务日志（NSSM 轮转）
└── certs/                  自签证书（*.pem 私钥不入库）
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
| 忘记管理员密码 | 删除 `data/.meta.db` 重启（会清掉分享与审计）；成员账号由管理员邀请重建 |
| 成员如何加入 | 管理 → 邀请 → 生成邀请码 → 对方在登录页"使用邀请码加入" |
| 公网分享打不开 | 确认复制的是 🌐 公网链接（管理面板先配置公网访问地址）；FRP 隧道需在线 |
| 5G 下载 apk 被提示次数上限 | v3 已修（按票据去重计数）；旧分享撤销重建即可 |
| Windows 映射网络驱动器失败 | 原生 WebClient 对自签证书苛刻，改用 RaiDrive/WinSCP |
| 改了前端没生效 | Ctrl+F5 强刷一次；之后资产文件名哈希自动更新 |

## 路线图状态

v2 个人网盘（P0–P3）✅ → v3 家庭团队版：M0 拆包 ✅ · M1 数据层 ✅（v3.5 起
**PostgreSQL 16 生产库**，SQLite 仅测试）· M2 Vue 全站 ✅ · M3 多用户核心 ✅
（TOTP 可选启用）· M4 团队体验 ✅（定向分享/通知/动态流/共享相册双维度/
大文件上传/暂停续传/移动 PWA；通知 bot 挂账）· M5 可靠性 ✅（每日 pg_dump
备份保留 2 天；公网入口隔离加固——防爆破分账/healthz 公网 404/限速分桶；
第二块备份盘与恢复演练挂账）。
**移动方向**：PWA 阶段 0 ✅（添加到主屏幕）；原生 App（Flutter 跨端，含相册
自动备份）需求规格与技术设计已成文（`docs/DESIGN-v3/APP-*.md`），待启动
M-A0（设备 Token 后端）。
详见 `docs/STATUS-v3.md`；规划见 `PLAN-v3.md`（§7 远景：CF Tunnel/域名试点）。
