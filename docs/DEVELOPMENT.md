# 开发指南

## 测试

```bash
check.bat            # 质量门禁 = pytest 基线（后端改动必跑，跑绿才算改完）
# 或手动：venv python -m pytest tests/ -q
```

- `tests/conftest.py` 在导入 `server` 前把 `SHARE_ROOT` 指向临时目录，
  全套测试不碰真实数据；`client` 夹具恒为匿名态，`auth` 夹具是独立实例的登录态。
- `tests/test_baseline.py`（28 项）是**行为回归网**；`tests/test_multiuser.py`（22 项）覆盖角色/空间/授权边界（拆包/PG/Vue 升级期间不许掉）：
  认证/防爆破、文件增删改移、分块上传与秒传、回收站、分享全链路、
  safe_path 穿越（`%2e%2e` 编码变体——httpx 会归一化裸 `..`，测试必须编码绕过）。
- 改 server.py 后新增行为 → 先补测试再改实现；发现 bug → 先写失败用例再修。
- v3 新增 `tests/test_multiuser.py`（角色/空间/授权边界，对应 DESIGN-v3/PERMISSIONS.md）。
- `tests/test_review_fixes.py`（12 项，2026-09-02 安全修复批）是**安全回归网**，
  改动 `spaces.py` / Caddyfile `/files` / 回收站 / 设备 Token / TOTP 时必须保持全绿：
  特殊段大小写变体（`Private/<他人>/` 不可读）、`/files` 下载判权、回收站越权
  恢复、改密/停用吊销设备 Token、分块超发拒绝、遗留无主分享隔离、TOTP 动态码防重放。
  **改 Caddyfile 也属于攻击面变更**：需同步自查 `handle` 块是否仍全部走后端鉴权，
  并跑本文件回归（纯 pytest 覆盖不到 Caddy 层，至少手动验证未认证 401 / 点目录 403）。
- 新前端：`cd web && npm run build`（产物 web/dist 即时生效，无需重启服务）。
- 前端约定：**禁止使用浏览器原生 alert/confirm/prompt**——统一用
  `ui.ts` 的 toast / confirmDlg / promptDlg（Promise 化，App 根级渲染）；
  登录态统一走 `auth.ts`（禁止各页面自存 me 状态）；**构建必须单独跑
  `npm run build` 并确认退出码**（管道会吞 vue-tsc 的失败）。

## 环境与运行

- Python 3.11 + FastAPI/uvicorn/aiofiles/Pillow/qrcode/pillow-heif + **psycopg**
  + **pyotp**（既有 venv，见 `install-service.bat` 中的 PYTHON 路径；pillow-heif
  为 HEIC 解码插件，缺失时静默降级）
- Caddy v2.11 原生二进制（`caddy.exe`，**不要**换成需要插件的构建）
- PostgreSQL 16：NSSM 服务 `lan-share-postgresql`（127.0.0.1:5433），
  `DATABASE_URL` 在 `lan-share-api` 的 NSSM 环境变量；不设时回退 SQLite
  （`SHARE_ROOT/.meta.db`，仅测试）
- ffmpeg（可选）：仅视频缩略图用，缺失时视频缩略图返回 404，不影响其它功能

```bash
# 开发实例（不碰线上服务）
SHARE_ROOT=C:/somewhere/sandbox python -m uvicorn server:app --host 127.0.0.1 --port 8768
# 或连 PG 调：DATABASE_URL=postgresql://lanshare:<密码>@127.0.0.1:5433/lanshare ...

# 线上（注意 C:\lan-share 是本项目目录的 junction，改这里就是改线上）
net stop lan-share-api && net start lan-share-api     # server.py / app/** 变更
net stop lan-share-caddy && net start lan-share-caddy # Caddyfile 变更
# static/* 与 web/dist 变更无需重启，刷新即生效（v3 拆包后 main.py 直供 dist 真实文件）
# mobile/ 需重新出 APK（见下）
```

## 手机 App（mobile/，Flutter）

- Flutter 3.47 stable 在 `C:\flutter\flutter`（**不在 PATH**）；Android SDK 在
  `C:\tools\android-sdk`（许可已接受）；JDK17（RedHat）供 Gradle 用
- **出包必须用 `scripts\build-apk.bat [debug|release]`**：仓库真实路径含中文，
  NDK/CMake 在中文路径下编不出 `libdartjni.so`——脚本会把 mobile/ 镜像到
  `C:\lan-share-mobile`（纯 ASCII 物理路径）构建后把 APK 拷回
  `mobile\build\apk\`。flutter/dart 命令一律经 `C:\lan-share\mobile`（junction）
  执行，且 Dart 分析器对中文路径敏感
- pub 拉包慢/挂死时切国内镜像：`PUB_HOSTED_URL=https://pub.flutter-io.cn`
  与 `FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn`
- **core library desugaring**（v0.6.0 起）：flutter_local_notifications 硬性
  要求，build.gradle.kts 已开 `isCoreLibraryDesugaringEnabled` +
  `desugar_jdk_libs:2.1.4`——新加插件报 "requires core library desugaring"
  时查这里，不要关
- **App 内检查更新发版约定**（v0.6.0 起）：把新 APK + `version.json`
  （{"version","build","filename","sha256","notes"}）放进共享空间
  `App安装包/` 即完成发版（/api/app/latest|download）；App 对比 build 号
  自动提示，无需再手动发分享链接换包
- **交付前必过**：`dart analyze` 零问题 + `flutter test` + 安卓模拟器全流程
  （登录→信任框→照片/文件/我的/分享接收/分享管理）；模拟器经 NAT 可直连宿主机
  LAN 地址。⚠️ **改了 Kotlin 必须再跑一次真构建**——`dart analyze`/`flutter test`
  都不编译 Kotlin（154201b 曾带着编译错误提交，M-A4 构建时才暴露）。
  ⚠️ **改了照片墙/图片链路必须加跑快滑压力测试**：连滑 5 屏再快速返回，
  已加载图片不得变转圈/坏图（0.4.4 曾因 shrinkWrap + ETag 304 双 bug 翻车，
  现架构=Sliver 虚拟化 + 四层缓存，见 mobile/README 已知取舍）
- **release 签名（2026-09-01 起）**：`flutter build apk --release` 经
  `scripts\build-apk.bat release` 出正式签名包。密钥库
  `mobile/android/keys/lanshare-release.jks`（alias `lanshare`，10000 天，
  证书指纹 `CF:31:1C:B4:…:07:78`），凭据在 `mobile/android/key.properties`——
  两者均 gitignore，**异地备份在 `<仓库外的私有备份位置>\`，
  丢了就无法覆盖安装**（全家都得卸载重装）。gradle 侧 `key.properties` 的
  `storeFile` 相对 **android/ 根**（rootProject）解析，别按 `app/` 相对路径写。
  ⚠️ **R8 已关（`android/gradle.properties` → `shrink=false`）**：Flutter 插件
  默认给 release 开裁剪/混淆，WorkManager 的 Room 库在 R8 下启动即崩
  （`Failed to create an instance of WorkDatabase`，任何 Dart 代码之前）；
  家庭 App 无混淆收益，只涨 ~4MB，稳定优先。debug 构建不受影响
- **签名切换注意**：debug 包与 release 包签名不同，**不能覆盖安装**——
  已装旧 debug 包的设备须先卸载（数据如登录态/备份账本随之清除；账本丢失
  后重传由服务端秒传兜底，不产生重复流量）。分发件现为
  `App安装包/lanshare.apk`（v0.5.2+13，品牌图标见 `scripts/make-app-icon.py`）
- 新增 pub 依赖按 §6 原则记 CHANGELOG

## 部署/测试约定

1. **沙箱先行**：所有改动先在 `SHARE_ROOT=测试目录` 的 8768 实例上验证，
   再 `net stop/start` 上线；`test-sandbox/` 目录用完即删，不要提交。
2. **回归清单**（改后端必跑）：setup→login→上传（直传 + 分块 + 秒传 + 跨刷新续传 + 覆盖进回收站）→列表/搜索→移动（A/B/C 三入口
   + 重名/成环/同目录边界）→分享（密码对/错/锁定）→票据下载→删除→回收站恢复→改名/改密
   （含防爆破联动）→WebDAV PROPFIND/PUT→401 未认证探测。
3. **前端改动**用真实浏览器点一遍主要链路（登录、浏览、上传、预览、弹窗、拖拽），
   注意移动端视口（390×844）。
4. **调试账号**：接口调试用 `debug` 账号登录（凭据见 `docs/DEBUG-ACCOUNT.md`，不入库），
   审计日志可与管理员操作区分。

## 代码结构（v0.23 重构后：asgi 底层化 + api 按域 + services 层）

```
app/
├── asgi.py            app = FastAPI() + security_middleware（依赖图底层，不 import 任何 api；
│                      07 批次2：11 对 main↔api 循环 import 由 APIRouter 化消除）
├── main.py            组装根：include_router（16 个 api 模块）→ SPA 页面路由/深链回退 → 静态挂载
├── config.py          全部常量与 env（SHARE_ROOT/DB_PATH/TTL/限速/MAX_CHUNK_SIZE 分片协议上限）
├── contract.py        OpenAPI 契约归一化（dav operationId 确定化，导出脚本与快照测试共用）
├── db.py              双引擎（PG：psycopg autocommit；SQLite：每线程连接 + 写锁 +
│                      init_db + _migrate_v3 幂等迁移）
├── security.py        审计/scrypt/会话/防爆破/限速/preview_user + reset_auth_gates（测试）
├── filetools.py       safe_path/clean_filename/dir_tree/save_upload + IMG_EXTS/VID_EXTS
│                      （媒体扩展名单一事实源，三端对齐基准）
├── spaces.py          resolve()/授权判定/配额/可见性 + 私人空间守卫（is_own_private_root 等，
│                      07 批次3 上收为公共 API）（安全核心）
├── settings.py        settings KV 读写唯一入口 + 全部 key 常量（07 批次1 事实源；
│                      v3.36 自 store/ 上移至配置层，消 security→store 分层倒置）
├── store/users.py     **users 表唯一 SQL 出口**（v3.36 A1 仓储：业务模块不认识
│                      表结构，加一列只改 db.py DDL + 本文件；读返回原始行，
│                      写统一 _write——事务内推迟 commit、事务外立即提交）
├── services/          业务服务层（不持路由，07 批次3 成型）
│   ├── archive.py     zip 打包票据表（issue/pop_ticket + sweep_expired）
│   ├── cleanup.py     注册式定时清理（通用五件套 + 各域 register_sweep）
│   ├── passwords.py   scrypt + 哑哈希等时校验（登录/DAV 同源）
│   └── accounts.py    改名通用流程 rename_user_everywhere + clean_username 校验
├── api/files.py       文件管理（列表/移动/复制/删除/改名/zip 端点/搜索/最近/磁盘）
├── api/uploads.py     分块上传协议 + 直传 + blob 秒传指纹（07 批次3 拆出）
├── api/trash.py       回收站（列表/恢复/删除/清空 + vpath 兼容）
├── api/previews.py    /preview（?w= 中图档）+ /thumb（PIL/ffmpeg + 像素炸弹拦截）
├── api/photos.py      照片时间线（扫描器/ffprobe 补扫/facets/meta + photos_scan_bump）
├── api/albums.py      精选专辑（管理端 + 成员端合流；settings JSON 锁内原子读改写）
├── api/qr.py          通用 QR SVG（品牌云朵）
├── api/auth.py        认证 API（setup/login/TOTP/改密/改用户名/设备 Token/配对码/WebDAV 密码）
├── api/share.py       分享 API（外链/票据/取件记录；按 owner 隔离；读侧 safe_path 过闸）
├── api/webdav.py      WebDAV（Basic 哑哈希等时；与网页同一 resolve 权限源）
├── api/admin.py       成员/邀请/授权/动态流/公网地址/导出（zip 票据走 archive 官方 API）
├── api/notify.py      站内通知 + 事件推送 Webhook
├── api/appver.py      App 版本分发（sha256 缓存）
├── api/healthreport.py 健康日报（HTTP 探活 + Webhook）
├── api/notes.py       笔记聚合列表
├── api/console.py     管理台（v3.39）：总览聚合/大文件 TOP/疑似重复/审计/会话与设备（含 kick/全清）
├── api/feedback.py    反馈通道（v0.28）：multipart 提交（截图≤3）/own-only 列表/详情/截图鉴权读取；
│                       落盘 data/反馈/（待处理|进行中|已完成 三目录即状态）+ 看板.md 自动重生成
server.py             纯入口（NSSM server:app；uvicorn __main__ 块不可删——漏了服务秒退）
web/                  Vue3 前端工程（Vite+TS）：src/api.ts 客户端、src/views/ 九个页面；
                      构建产物 dist/ 由 main.py 直供（gitignore node_modules + dist）
mobile/               Flutter App（mobile/lib/ 源码 + android/ ios/；build/ 与 .dart_tool/ 已 gitignore）
certs/                自签证书配置（san.cnf；*.pem 私钥不入库）
data/                 运行时数据根（不入库：用户文件/.meta.db/.trash/...）
docs/            API.md / SECURITY.md / DEVELOPMENT.md / STATUS-v3.md / GIT.md / DEBUG-ACCOUNT.md(不入库)
                       DESIGN-v3/ {OVERVIEW,FRONTEND,DATABASE,PERMISSIONS,APP-*}
```

## 前端约定（Vue3 + Vite + TS，web/ 工程）

- **M3 拆包后已全面 Vue 化**：M0-M1 时期的事件委托 + `data-act` + `escJs` 模式
  已废弃。SFC `<script setup>` + Pinia（如需全局状态）+ 模板内 Vue 表达式即可，
  无需担心 CSP（预编译模板 + 静态 import 都不需要 `unsafe-eval`）。
- 模板渲染 Markdown/HTML 用 `DOMPurify.sanitize`（`import DOMPurify from
  'dompurify'`）；插值表达式 `{{ v }}` 自动 escape，不用手写 esc。
- SVG 走 `lucide-vue-next` 或封装组件；自定义 `<svg>` 在 `.btn` 外需显式定宽高。
- 主题与色板用 CSS 变量 + Pinia store；不写魔法值。
- **图标的 SVG 内联即可**（不要拼字符串到 innerHTML），避免 CSP 与 XSS 双风险。
- 深浅主题在 `theme.ts` 集中管理；本地偏好 `localStorage` 记忆。
- 弹窗/Toast 走 `ui.ts` 的 `confirmDlg/promptDlg/toast`（已禁用浏览器原生
  confirm/prompt，统一 Promise 风格 + App 根级渲染）；不要在业务里直接 `alert`。

## 后端约定

- 新路由默认挂 `Depends(require_user)`；设计为公开的必须加限速与输入校验（见 docs/SECURITY.md 检查清单）。
- 一切用户提供的路径走 `safe_path()`，文件名走 `clean_filename()`。
- DB 写操作放 `with _db_lock:`；读用线程本地的 `_db()` 连接直接查。
- 可-dispatch 的耗时 IO 用 `async def` + aiofiles；CPU/阻塞型（缩略图、zip）用 `def` 同步端点跑线程池；
  scrypt 这类密码学操作用 `run_in_threadpool`。
- 关键操作 `audit(...)`，action 命名见 `audit_log` 现有值。
- 新增表/列：PG 走 `ALTER TABLE IF NOT EXISTS` 显式补列（`_PG_SCHEMA` + `init_db` 里
  各加一行）；SQLite 走 `_migrate_v3` 的 `PRAGMA table_info` 增量补列；三路幂等迁移。
- 防爆破与限速按入口分账（`is_public_entry`）：所有改密/改用户名/WebDAV 独立密码
  路径都接收 `pub` 参数。

## 已知陷阱（踩过的坑）

| 坑 | 说明 |
|----|------|
| FastAPI 路由顺序 | `POST /api/upload/{path:path}` 通配会吞掉 `/api/upload/init` 等精确路由——精确路由必须注册在通配**之前** |
| ffmpeg 输出扩展名 | 输出到 `.tmp` 必须显式 `-f image2`，否则无法推断格式报错 |
| ffmpeg 短视频抽帧 | `-ss` 越界抽不到帧，先试 `-ss 1` 再退回首帧 |
| scrypt 内存 | Windows OpenSSL 默认 maxmem 不够，必须显式 `maxmem=` |
| Caddy 原生无插件 | rate_limit / HMAC 签头都是第三方模块，别在 Caddyfile 里用 |
| NTFSADS | 文件名带冒号会写进备用数据流，`clean_filename` 已拦截，别绕过它 |
| junction | `Path.resolve()` 会穿透 junction，路径校验用 `absolute()` + 词法边界（safe_path 已处理） |
| os.replace 覆盖 | `os.replace` 目标存在会**静默覆盖**——移动/重命名类操作用 `os.rename`（Windows 下目标存在必抛 FileExistsError，天然防覆盖竞态），/api/move 已按此实现 |
| SQLite 大小写 | `UNIQUE` 约束与 `=` 比较默认大小写敏感——用户名类查询用 `COLLATE NOCASE`，防爆破 key 统一小写 |
| 弹窗叠层 | `showModal` 是追加式——弹窗内导航重渲染前必须先清旧 `.modal-overlay`（目录选择器曾踩坑） |
| 磁盘扫描阻塞 | rglob 全量扫描是同步 IO——统计类端点用 `def`（线程池）而非 `async def`，避免大目录卡事件循环 |

## 更新文档的责任

改了 API、安全行为、前端交互约定或部署方式，**同一次提交里**更新
`docs/API.md`、`docs/SECURITY.md`、`README.md` 与 `CHANGELOG.md`。文档过期比没有文档更糟。
