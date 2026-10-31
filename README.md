<div align="right">
  <sub><a href="README.en.md">English</a> &nbsp;·&nbsp; <b>简体中文</b></sub>
</div>

<div align="center">
  <img src="logo/app-login-brand.svg" height="84" alt="LAN Share">
  <h1>LAN Share</h1>
  <p><b>把家里吃灰的那台 Windows 电脑，变成全家人的私人网盘。</b></p>
  <p>没有公网 IP 也能用。不用买 NAS。照片不进任何云厂商的机房。</p>

  [![CI](https://github.com/AK47ZZQ/lan-share/actions/workflows/ci.yml/badge.svg)](https://github.com/AK47ZZQ/lan-share/actions/workflows/ci.yml)
  ![server](https://img.shields.io/badge/server-v3.72-2563eb)
  ![app](https://img.shields.io/badge/app-v1.1.18%2B125-2563eb)
  ![platform](https://img.shields.io/badge/platform-Windows%20%C2%B7%20Web%20%C2%B7%20Android-64748b)
  ![tests](https://img.shields.io/badge/tests-729%20passed-16a34a)
  ![license](https://img.shields.io/badge/license-AGPL--3.0-blue)
</div>

---

## 起因

家里那台 Windows 台式机常年插着电，一年电费没多少，但它实际上什么也没干。同一时间我在给网盘续费，免费额度还越缩越小。

我想把它用起来。教程看了不少，全卡在同一个地方：我没有公网 IP。

宽带在运营商的大内网里，光猫拨号也拿不到独立地址。DDNS 填了域名，解析出来是 100.64 开头的地址——根本到不了我家。市面上讲自建网盘的教程，八成默认你有公网 IP，剩下两成讲到"申请公网 IP"就没下文了，因为多数家庭宽带现在申请也不给。

后来绕过去了，用的是内网穿透。隧道一连，从外网就能打开家里的服务。慢一点，但能用；而且本来也不需要多快——全家人的照片，谁在乎多等两秒。

那还租网盘干嘛。

顺带一提，这也是为什么整个项目跑在 Windows 上。那台机器就是 Windows，为它换个系统不值得。

## 能干的事

不是"把文件夹共享出去"——那个 SMB 就够了，用不着写这么一堆。这里要的是一家人日常用的东西。

手机拍完照自动传回家。不用每月插线导一次，也不用担心哪天手机丢了照片跟着丢。

每个人有自己的账号和私人空间，别人看不见。共享盘按目录单独授权，可以设有效期。

发给亲戚的链接带提取码和过期时间，到期自动作废，不用惦记着去删。

不在家也能用，公网和局域网功能一模一样。这条我用得最多——出差时找一份家里的扫描件。

<p align="center">
  <img src="docs/screenshots/overview.png" width="560" alt="站长工作台 · 总览">
</p>
<p align="center"><sub>Web 端 · 站长工作台：存储构成、成员与配额、动态流、限速观测</sub></p>

## 手机端

Web 那套是给自己管东西用的，App 才是家人每天打开的那个。Flutter 原生写的，不是套网页的壳。

<p align="center">
  <img src="docs/screenshots/app-home.png" width="182" alt="首页">
  <img src="docs/screenshots/app-photos.png" width="182" alt="照片">
  <img src="docs/screenshots/app-todo.png" width="182" alt="待办">
</p>
<p align="center">
  <img src="docs/screenshots/app-notes.png" width="182" alt="笔记">
  <img src="docs/screenshots/app-me-1.png" width="182" alt="我的">
  <img src="docs/screenshots/app-me-2.png" width="182" alt="账号与安全">
</p>
<p align="center"><sub>首页 · 照片时间线 · 待办 · 笔记 · 我的 · 账号与安全</sub></p>

相册备份是主要的那个。拍完就传，首页能看到上次备份是什么时候，省得老惦记。

照片按 EXIF 拍摄时间归月，三列瀑布流，可以切照片和视频，右上角能直接跳日历找某个月。

改密码、WebDAV 独立密码、配对码、恢复码、验证器、应用锁，都堆在"账号与安全"一个页面里，不用到处翻。

手机和 Web 是同一个后端、同一份权限。手机上删的东西，网页刷新就没了。

## 装之前先看看

| 项 | 版本 | 说明 |
|---|---|---|
| 系统 | Windows 10 / 11 | 用 NSSM 注册服务；Linux 得自己写 systemd unit |
| Python | 3.11+ | 后端 |
| PostgreSQL | 16 | 生产用；跑测试可以退回 SQLite |
| Node.js | 18+ | 只在构建前端时需要 |
| Caddy | v2 | 反代和 TLS，丢进 `bin/caddy.exe` |
| NSSM | 2.24+ | 把 Python 和 Caddy 挂成 Windows 服务 |

**用一键安装的话，上面这一整张表都不需要**——运行时、前端产物、二进制全在安装包里。

路径和端口全在 **`env.bat`** 一个文件里，默认按仓库自己所在位置推导。装到别处不用改脚本，挪走就行。

## 装起来

### 一键安装（推荐）

下载 `LANShare-Setup-x.y.z.exe`，双击。**不用装 Python、不用装数据库、不用装 Node。**

```
双击 → 选数据目录 → 完成 → 浏览器自动打开
```

安装包自带 embeddable Python 3.11 + 依赖、Caddy、NSSM、构建好的前端产物，一共 33 MB。
装完任务栏会多一个托盘图标，**点一下弹出控制面板**：

- **绿** = 服务正常，**黄** = 响应异常，**红** = 服务没跑
- 面板里能看 API/Web 监听状态、局域网地址和二维码
- **「启动局域网服务」一键开关**：自动配防火墙规则、把监听从 `127.0.0.1` 放开到所有网卡、
  改完立刻用局域网 IP 自检，不通会告诉你卡在哪一步
- 公网访问：填进内网穿透（如 sakuraFrp）给出的地址，点「验证连通性」当场测通不通
- 右键菜单还有「打开网盘」「复制局域网地址」「打开数据文件夹」「重启服务」「看日志」

安装器版用 **SQLite**，不需要 PostgreSQL。数据默认在 `C:\ProgramData\LANShare\data`，
安装时可以改到别的盘。卸载会问你是否删数据，默认保留。

命令行也能用（手动排查或改端口时）：

```bash
python -m lanshare_cli doctor            # 环境自检，只读
python -m lanshare_cli status            # 服务状态 + 探针
python -m lanshare_cli install --dry-run # 只检查不执行
python -m lanshare_cli install --api-port 8866 --web-port 8865   # 换端口
```

### 手动安装（开发 / 想用 PostgreSQL）

```bash
# 1. 依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. 数据库（建库就行，首次启动自动建表）
createdb -h 127.0.0.1 -p 5433 -U postgres lanshare

# 3. 前端（构建一次，产物由后端直接挂）
cd web && npm install && npm run build && cd ..

# 4. 二进制：caddy.exe 放进 bin\，nssm.exe 丢进 PATH
#    下载地址在 docs/OPERATIONS.md 的「依赖」那节

# 5. 注册服务并启动（管理员权限 PowerShell）
.\install-service.bat
net start lan-share-postgresql
net start lan-share-api
net start lan-share-caddy
```

浏览器开 `https://<本机 IP>:8765`。自签证书会报不安全，点"继续访问"就进去了。首启会引导你建管理员账号，家里其他人由你发邀请码自己注册。

### 平时怎么动

```bash
powershell Restart-Service lan-share-api     # 改了后端
powershell Restart-Service lan-share-caddy   # 改了 Caddyfile
cd web && npm run build                      # 改了前端（不用重启）
check.bat                                     # 动了后端，跑一遍
```

| 入口 | 地址 | 认证 |
|---|---|---|
| Web 全站（Vue 3） | `https://<IP>:8765` | 应用层登录 |
| 公网（隧道） | `https://<你的隧道域名>:<端口>` | 一样，功能没有阉割 |
| WebDAV | `https://<IP>:8765/dav/` | Basic（成员账号） |
| 健康探针 | `/healthz` | 免认证，只有局域网能到 |

探针故意不暴露到公网。它报的是内部状态，不该让别人扫得到。隧道怎么配见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

## 有什么

| 领域 | 有什么 |
|---|---|
| **多用户** | admin / member / guest 三个角色；私人空间和共享空间分开；目录授权按"成员 × 目录"配，能设有效期；邀请制建号；私人空间配额；停用账号全端立刻踢下线 |
| **文件** | 网格和列表两种视图、搜索、最近文件、多选批量；预览器统一处理图片（缩放旋转）、文本 / Markdown / CSV / docx / xlsx / pptx、音视频，HEIC 和 HEIF 也能开（含 EXIF 方向矫正） |
| **传输** | 分片按文件大小自适应（2 GB 以内 5 MB，10 GB 以上 64 MB）、断点续传、指纹秒传、三路并发、队列能暂停恢复、拖拽粘贴批量入队 |
| **分享** | 外链加二维码，能设有效期、提取码、下载次数上限；也能定向分享给某个成员；目录可打包下载 |
| **照片** | 时间线（优先读 EXIF）、缩略图懒加载、相册精选、手机端自动备份 |
| **笔记待办** | 分类笔记、待办清单、日历视图 |
| **通知** | 站内通知中心、家庭动态流、SSE 实时推、Webhook 接 ntfy / Bark / Telegram |
| **安全** | 应用层认证、可选 TOTP 两步验证、会话和设备治理、审计日志、按入口分开记账的防爆破 |
| **客户端** | Vue 3 Web（PWA，能离线装）、Flutter 原生 App（相册备份 / 系统分享 / 通知中心） |

逐版改了什么看 [CHANGELOG.md](CHANGELOG.md)，130 KB，按版本倒着排。

## 跟别人比

|  | LAN Share | Nextcloud | 群晖 DSM | 商业网盘 |
|---|---|---|---|---|
| 需要公网 IP | 不用，走隧道 | 建议有 | 需要 | — |
| 硬件成本 | 0（旧电脑） | 0（得自备机器） | 3000 起 | — |
| Windows 原生 | 是 | 勉强 | 是 | — |
| 代码量 | 能通读 | 百万行级 | 闭源 | 闭源 |
| 数据在哪 | 自己硬盘 | 自己硬盘 | 自己硬盘 | 厂商机房 |

Nextcloud 功能比这儿多得多，是正经成熟产品，跟着它走不会错。这个项目只盯着"一台 Windows 机器 + 一家人"这一个场景，企业级那套全砍了，代码量压到我自己能读懂、能随手改的程度。

你要一个能随便改的自留地，还是要开箱即用的全家桶，自己判断。

## 什么情况别用

说几条劝退的：

- 要 LDAP、合规审计、S3 后端、多节点的，用 Nextcloud 或 MinIO
- 想开箱即用的，这套得自己装 Python、PostgreSQL、Caddy，还得会用管理员 PowerShell
- 机器不是 Windows 的，服务脚本全是 `.bat` 加 NSSM，Linux 上要自己写 systemd unit
- 要 iOS 客户端的，`mobile/ios/` 有工程但没构建验证过，目前只发 Android APK

## 架构

```
                         ┌───────────────────────────────┐
外网设备 ──隧道 :8767──▶ │  Caddy v2（TLS / 反代 / 鉴权） │
内网设备 ──局域网 :8765─▶│  剥离入站 XFF，注入入口标记     │
                         └───────────────┬───────────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────┐
        ▼                                ▼                            ▼
  /files/* 先 forward_auth         /api/* /dav/* /s/*           /assets/* →
  再交后端判权下载                /up /preview/* /thumb/*      web/dist
  （不 file_server 直挂，          ──▶ FastAPI :8766           （Vue 3 SPA
   否则越权读他人 private/）            │                       + 深链回退）
                                        ├─ PostgreSQL 16 :5433
                                        ├─ data/ 数据目录
                                        ├─ 限速 / CSRF / 防爆破 / 审计
                                        └─ SSE 实时事件
```

`/files/` 先走 forward_auth 再交给后端判权，是因为直接 file_server 挂出去的话，路径一拼就能读到别人的 private 目录。这个坑踩过。

**后端** FastAPI + uvicorn · **网关** Caddy v2（原生，没装插件）· **数据库** PostgreSQL 16 + psycopg（生产）/ SQLite（测试）
**前端** Vue 3 + Vite + TypeScript · **App** Flutter（Dio + Riverpod + sqflite + secure_storage）
**图像** Pillow + pillow-heif（HEIC / EXIF）· **其他** pyotp（TOTP）、qrcode、ffmpeg（可选）

## 多大

```
后端 app/       12,628 行      68 个模块（api 29 · services 14 · store 11）
Web  src/        6,789 行      Vue 3 + TypeScript
App  lib/       29,172 行      Flutter
安装部署         2,157 行      installer/ 1,142 · lanshare_cli/ 1,015
测试 tests/     11,025 行      67 个文件 · 594 项
API 契约        143 路径 / 167 操作   （contracts/openapi.json）
```

安装包 **34 MB**（LZMA2 压缩，解压后 136 MB：embeddable Python 82 MB + Caddy 48 MB + 其余）。

同一套测试能在 SQLite 和 PostgreSQL 上都跑（临时 schema 隔离，不脏库）。本地门禁 `check.bat` 跑八段：pytest 基线、ruff、mypy、pip-audit、UI 分层守卫、SQLite 方言守卫、文档基线守卫、安全消融。

## 文档

| 你要什么 | 读哪 |
|---|---|
| 完整 API | [docs/API.md](docs/API.md) |
| 安全模型 | [docs/SECURITY.md](docs/SECURITY.md) |
| 运维（服务 / 备份 / 证书 / 故障） | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| 开发环境与命令 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| 目录结构 | [docs/PROJECT-LAYOUT.md](docs/PROJECT-LAYOUT.md) |
| App 视觉规范 | [docs/DESIGN-v3/APP-VISUAL-SPEC.md](docs/DESIGN-v3/APP-VISUAL-SPEC.md) |
| 版本历史 | [CHANGELOG.md](CHANGELOG.md) |

<details>
<summary><b>常见问题</b></summary>

| 问题 | 处理 |
|---|---|
| 证书不受信任 | 自签证书，点"继续访问"；或者把 `certs/cert.pem` 导进系统根证书 |
| 忘了管理员密码 | 清空 users 表，再用 `/api/setup` 重建（只有空表才开放，第一个用户就是 admin）。成员账号由管理员重新邀请 |
| 家人怎么加进来 | 管理 → 邀请 → 生成邀请码 → 对方在登录页点"使用邀请码加入" |
| 公网分享打不开 | 先确认复制的是公网链接（管理面板里要先配公网地址），再看隧道在线没 |
| Windows 映射网络驱动器连不上 | 系统自带 WebClient 对自签证书很挑，换 RaiDrive 或 WinSCP |
| 改了前端没生效 | Ctrl+F5 强刷。之后文件名带哈希，会自动更新 |
| 公网看不到健康探针 | 故意的，探针只在局域网可达，不往公网暴露内部状态 |
| `install-service.bat` 说找不到 nssm | 把 `nssm.exe` 放 PATH，或者设环境变量 `LANSHARE_NSSM` 指过去 |

</details>

## 许可

[GNU AGPL-3.0](LICENSE)。

拿去用、拿去改、自己搭，都行。只有一条硬规矩：你要是改完了挂到网上给别人当服务用，你的改动也得按 AGPL-3.0 开源。自己家里用不算。

`data/`、`env/`、`.venv/`、凭据和证书都不入库，`.gitignore` 里写着。
