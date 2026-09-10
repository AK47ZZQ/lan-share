# LAN Share 项目全貌认知梳理

> 生成时间：2026-09-10 · 依据仓库实测代码（app/ 8170 行 Python、web/ Vue3 工程、mobile/ Flutter 工程 80 个 dart 文件）
> 当前版本：服务端 v3.36 / App v0.24.0+69（v3.36 起新增 `app/store/users.py`
> 仓储层、`app/settings.py`、双引擎测试与 CI；测试基线见下方更新）

---

## 1. 项目定位

**家庭自包含网盘**，面向 2–20 人的小团队（一个家庭）。不是 SaaS，是"自家部署、自家分发、自家维护"的私有系统。

- **部署形态**：Windows 机器上以 NSSM 注册三个常驻服务（`lan-share-api` / `lan-share-caddy` / `lan-share-postgresql`），开机自启。
- **双入口完全对等**：局域网（`https://192.168.1.100:8765`）与公网（樱花 FRP 隧道）功能、认证完全一致——TLS 在 Caddy 终结，认证全部在应用层。
- **三端一体**：Web SPA（Vue3）、原生 App（Flutter）、WebDAV 挂载，共用同一套后端权限模型。

### 一句话架构

> Caddy(8765, TLS) → FastAPI(127.0.0.1:8766) → PostgreSQL 16(127.0.0.1:5433)；
> 文件在 `data/`；Web 是 Vite SPA 由 FastAPI 直供；Flutter App 自家分发。

---

## 2. 技术栈

| 层 | 技术选型 | 说明 |
|---|---|---|
| 反向代理 | **Caddy v2**（原生二进制，无插件） | TLS 终结、forward_auth、body 透传、`:80` 跳转 HTTPS |
| 后端 | **FastAPI 0.133 + uvicorn 0.41 + Python 3.11** | 路由/依赖注入/Pydantic 校验 |
| 元数据库 | **PostgreSQL 16 + psycopg3**（生产）/ **SQLite**（仅测试） | 双引擎适配层，`DATABASE_URL` 未设时回退 SQLite |
| 文件存储 | **本地 NTFS** | 硬链接去重、`data/` 目录树 |
| 图片处理 | **Pillow 12 + pillow-heif 1.6** | 缩略图、EXIF 方向矫正、HEIC/HEIF 解码 |
| 其他后端 | qrcode / pyotp / aiofiles / PyYAML / ffmpeg(可选) | 二维码、TOTP、异步写盘、知识库 frontmatter、视频缩略图 |
| Web 前端 | **Vue 3.5 + Vite 6 + TypeScript 5.7 + vue-router 4** | SFC `<script setup>`；marked / highlight.js / DOMPurify / docx-preview |
| 移动端 | **Flutter 3.47 + Dart 3.13** | Dio + flutter_riverpod + sqflite + flutter_secure_storage + flutter_cache_manager |
| 质量门禁 | **ruff + mypy + pytest + pip-audit**（check.bat）+ CI（.github/workflows） | 测试基线 pytest 231（SQLite）/ 229（PostgreSQL 双引擎）/ web 6 / Dart 179 |

依赖全部**手写钉版**在 `requirements.txt`（与生产 venv 实测一致），不直接 `pip freeze`。

---

## 3. 目录组织

```
lan-share/
├── server.py              兼容 shim：唯一身份是 NSSM 入口 server:app（14 行）
│                          ⚠️ if __name__=="__main__" 块不可删，删了服务秒退
├── app/                   后端包（全部实现，8170 行）
│   ├── asgi.py            app = FastAPI() + security_middleware（依赖图最底层）
│   ├── main.py            组装根：include_router × 16 → SPA 路由 → 静态挂载
│   ├── config.py          全部常量与 env（SHARE_ROOT / 限速 / 分片协议上限）
│   ├── db.py              双引擎适配（PG autocommit / SQLite WAL+锁）
│   ├── security.py        认证·会话·scrypt·防爆破·限速·审计·公网入口判定
│   ├── spaces.py          resolve() 虚拟路径→物理路径判权（安全核心）
│   ├── filetools.py       safe_path / clean_filename / save_upload / 媒体扩展名事实源
│   ├── contract.py        OpenAPI 契约归一化（dav operationId 确定化）
│   ├── api/               16 个路由模块（APIRouter，见 §5）
│   ├── services/          业务服务层：kb / archive / cleanup / passwords / accounts
│   └── store/             settings.py — settings KV 读写唯一入口 + 全部 key 常量
├── web/                   Vue3 前端工程（产物 web/dist 由 main.py 直供）
│   └── src/  api.ts · auth.ts · router.ts · ui.ts · theme.ts · uploader.ts
│             components/（12 个）· views/（10 个页面）
├── mobile/                Flutter 原生 App（80 dart 文件，见 §6）
├── Caddyfile              反代 / TLS / forward_auth / :80 跳转
├── certs/                 自签证书（*.pem 私钥不入库）
├── data/                  运行时数据根（private/shared/incoming/.trash/.chunks/.thumbs）
├── docs/                  API / SECURITY / DEVELOPMENT / STATUS-v3 / DESIGN-v3 / KB-CONVENTIONS …
├── 项目记忆/              01 架构现状 · 02 里程碑 · 03 踩坑 · 04 发版SOP · 05 红线 · 06/07 审查
├── tests/                 28 个测试文件（pytest 基线 231 项；含 PG 方言/schema 漂移守卫）
├── scripts/               build-apk.bat（镜像到 ASCII 路径）· migrate_sqlite_to_pg.py
├── check.bat              质量门禁（pytest baseline）
├── backup.bat             每日 23:30 pg_dump -Fc + 文件增量
├── PLAN-v3.md             v3 规划（v2 存档在 PLAN.md）
└── CHANGELOG.md           版本历史
```

---

## 4. 后端架构与依赖关系

### 4.1 分层（v0.23 架构审查重构后）

依赖**严格单向向下**，`asgi.py` 处于依赖图最底层（**只 import config/db/security，绝不 import 任何 api 模块**）。这一设计是为了消除历史上 11 对 `main ↔ api` 的循环 import——此前各 api 模块 `from app.main import app`，而 main.py 底部又反向 import 各模块，导致 app 对象无法工厂化。

```
main.py（组装根）
    ↓ include_router
app/api/*（16 个 APIRouter，不再直接持有 app）
    ↓
services/ + store/（业务服务层，不持路由）
    ↓
security.py · spaces.py（安全与权限核心）
    ↓
asgi.py · db.py · config.py · filetools.py（基础设施）
```

### 4.2 应用对象与中间件（app/asgi.py）

`security_middleware` 是全局唯一 HTTP 中间件，按顺序做五件事：

1. **审计上下文**：Cookie 会话或 Bearer 设备 Token 认证（含 DB 往返，进线程池），结果挂 `request.state._ls_user` 供 `require_user` 复用，省掉每请求第二次会话查询。
2. **CSRF**：带 `Origin` 的请求必须与 `Host` 同源。
3. **healthz 管控**：公网入口返回 404（防磁盘水位泄露）；公网地址未配置时按 Host 兜底判定（仅私网/loopback/localhost 放行）。
4. **请求体大小上限**：防"未认证端点灌巨包导致内存 DoS"。大体积白名单仅限 DAV、分片收块、直传 multipart；`/api/upload/init|state|finish` 是小 JSON 端点必须留在检查内（FastAPI 先读 body 后鉴权）；chunked 无 CL 一律 411/413。
5. **分桶限速**：`/api/login`（公网 30/分 vs 局域网 10/分）、`/api/share/`、`/authcheck`、`/api/*`（公网 600 vs 局域网 120）各自独立桶。

### 4.3 双引擎数据层（app/db.py）

- SQL 统一用 `?` 占位符书写，PG 适配层 `_pg_sql()` 在执行前把 `?` 转 `%s`、把字面 `%` 翻倍为 `%%`（psycopg 把裸 `%` 当占位符前缀）。
- 行访问统一按 key（`r["col"]`）：SQLite 用 `Row`，PG 用 `dict_row`。
- **SQLite**：每线程独立连接 + `_db_lock` 全局串行 + WAL；`init_db()` + `_migrate_v3()` 幂等增量迁移。
- **PG**：`psycopg.connect(autocommit=True)`；`_PG_SCHEMA` 全量 `CREATE TABLE IF NOT EXISTS` + 逐列 `ALTER TABLE ADD COLUMN IF NOT EXISTS` 补列；断线自愈（超 30s 未用先 ping `SELECT 1`）。
- 首个（最小 id）用户自动晋升 admin，但**仅当库内已无 admin 时**（避免管理员被降级后重启"复活"）。

### 4.4 权限核心（app/spaces.py）—— 全项目最关键的模块

**虚拟路径（VPath）模型**：

| 虚拟路径 | 物理路径 | 可见性 |
|---|---|---|
| `private:<path>` | `SHARE_ROOT/private/<username>/<path>` | 仅本人（admin 也无跨用户寻址） |
| `shared:<path>` | `SHARE_ROOT/<path>` | 按角色 + grant |
| 裸路径 | 等价 `shared:<path>` | 兼容 v2 客户端 |

**角色语义**：`admin`（shared 读写全权 + 仅本人 private）/ `member`（shared 默认只读，写需 grant）/ `guest`（shared 全部凭 grant）。

**关键函数**：`resolve(user, path, need)` 解析并判权（无权限一律 403 不区分 404 防探测）；`visible()` 供搜索/照片/回收站过滤；`_grant_hit()` 按路径段前缀命中（带 30s TTL 缓存）；`enforce_quota()` 仅私人空间计逻辑字节。

**多处刻意的安全加固**（都是踩过的坑）：
- `_normcase()`：Windows `os.path.normcase` 会把 `/` 归一成 `\`，导致授权前缀 `startswith` 永不命中——统一回正斜杠。
- `Private/`（大小写变体）在 NTFS 上就是 `private/`，特殊段判定必须 normcase，否则跨用户越权。
- `App安装包/` 目录写操作 admin-only（防持写 grant 的成员植入恶意 APK 推给全体升级 = RCE）。
- `sample_readable()`：秒传越权防线（Dedup Oracle 封堵）——"服务端存有该内容"≠"当前用户可读"。

---

## 5. API 模块清单（16 个）

| 模块 | 行数 | 职责 |
|---|---|---|
| `auth.py` | 813 | setup / login / TOTP / 改密 / 改用户名 / 设备 Token / 配对码 / WebDAV 密码 |
| `admin.py` | 615 | 成员 / 邀请 / 授权矩阵 / 动态流 / 公网地址 / 导出 |
| `files.py` | 460 | 列表 / 移动 / 复制 / 删除 / 改名 / zip 端点 / 搜索 / 最近 / 磁盘 |
| `photos.py` | 422 | 照片时间线（扫描器 / ffprobe 补扫 / facets / meta） |
| `share.py` | 393 | 外链创建 / 取件（密码换一次性票据）/ QR / 取件记录 / 撤销 / 定向分享 |
| `notify.py` | 392 | 站内通知 + 事件推送 Webhook |
| `albums.py` | 339 | 精选专辑（管理端 + 成员端合流） |
| `webdav.py` | 332 | WebDAV（Basic 哑哈希等时，与网页同一 resolve 权限源） |
| `uploads.py` | 319 | 分块上传协议 + 直传 + blob 秒传指纹 |
| `healthreport.py` | 237 | 健康日报（HTTP 探活 + Webhook） |
| `appver.py` | 178 | App 版本分发（sha256 缓存） |
| `previews.py` | 152 | `/preview`（?w= 中图档）+ `/thumb`（PIL/ffmpeg + 像素炸弹拦截） |
| `trash.py` | 130 | 回收站（列表 / 恢复 / 删除 / 清空 + vpath 兼容） |
| `kb.py` | 111 | 知识库六端点（graph/backlinks/neighbors/names/context/reindex） |
| `notes.py` | 106 | 笔记聚合列表 |
| `qr.py` | 43 | 通用 QR SVG（品牌云朵） |

服务层：`kb.py`（947 行，双链解析 + 图谱索引 + 服务端预布局）、`cleanup.py`（注册式定时清理）、`archive.py`（zip 票据表）、`accounts.py`（改名通用流程）、`passwords.py`（scrypt + 哑哈希等时校验）。

---

## 6. 主要业务流程

### 6.1 上传（最复杂的链路）

**四条路径统一**：直传（小文件）/ 分块（大文件）/ 秒传（命中指纹）/ WebDAV PUT。

- **自适应分片**：≤2GB→5MB / ≤5GB→16MB / ≤10GB→32MB / 更大→64MB。阶梯定义在 `config.chunk_size_for()`——**单一事实源**，init/state/chunk/finish 四处一致，客户端 clamp 也必须引用下发值不写死。
- **断点续传**：分片落在 `.chunks/<sid>/part-NNNNNN`；客户端跨刷新用 localStorage 存会话号；会话 TTL 3 天。
- **原子性**：每块先写唯一名 `_tmp_*` 再 `os.replace` 落位（不匹配 `part-*`，续传不会误信半截分片）；finish 时重组到 `.uploading-*` 临时文件再原子落位。
- **秒传**：≤100MB 客户端预算 sha256 → `init` 查 `blobs` 表 → 命中且**样例对当前用户可读** → `os.link` NTFS 硬链接，零字节传输。
- **同名覆盖进回收站**：三条路径（分片/秒传/单请求）统一，旧版本不直接丢失（`on_dup=overwrite` 除外，供笔记就地编辑）。
- **防滥用**：并发会话 ≤8、未完成总量 ≤64GB、分片超发立即停止接收、SHA256 不符销毁整个会话（防反复全量重组的磁盘 IO DoS）。

### 6.2 分享（票据机制）

`POST /api/share`（创建）→ 存 `shares` 表（含 expires_at / password_hash / max_downloads / owner_id / target_user_id）→ 密码走 POST body 换**一次性票据**（TICKET_TTL 10 分钟）→ `GET /api/share/{token}/dl?ticket=` 下载。

- **提取码明文停存**：只在创建时一次性显示，不再落库（防一次 dump 全泄露）。
- **下载次数按票据去重计数**（下载管理器多分段不重复计）。
- **定向分享**：`target_user_id` 指向站内成员 → 自动发站内通知 + Webhook 推送。
- 成员只能管理自己的分享，管理员可见全部。
- 只支持分享文件，文件夹需先打包（zip 走 `archive.py` 票据表）。

### 6.3 认证

`setup`（首用户即 admin，空库才开放）→ `login`（防爆破按入口分账：公网失败只锁公网侧）→ 可选 TOTP 第二因子（`_totp_match_step` ±1 窗口 + `totp_last_step` 防同窗口重放）→ Cookie 会话（`SameSite=Lax` / `HttpOnly` / `Secure` 按 XFP）或 Bearer 设备 Token（90 天滑动，单账号上限 20 台）。

WebDAV 有**独立密码**（与登录密码隔离，可单独轮换），解密/校验统一走 `services/passwords.py` 的 scrypt + 哑哈希等时策略。

### 6.4 知识库图谱（v0.24 最新特性）

笔记目录即 Obsidian 式知识库（纯 .md 文件，PC 端 Obsidian 可直接打开同一目录）。服务端 `services/kb.py` 解析 `[[双链]]`+`#标签`+aliases frontmatter，缓存失效指纹用 `sha256(排序后的 (相对路径, size, mtime_ns) 列表)`（"max(mtime)+文件数"抓不住改名）。图谱布局持久化在 `<root>/.kb/layout.json`，增量初始化。

### 6.5 其它

回收站（`.trash/` 保留 30 天）· 照片时间线（EXIF 拍摄时间 / 入库时间双排序）· 事件推送 Webhook（GET 模板 + `{title}/{body}` 占位符）· 通知管理（发送记录 / 撤回 / 草稿定时 / 按类型偏好）· 家庭动态流（同人同类 10 分钟聚合）· 健康日报。

---

## 7. 数据模型（PG 主表）

| 表 | 用途 |
|---|---|
| `users` | 账号：role / is_active / quota_bytes / totp_* / webdav_password_hash / nickname / avatar_path |
| `sessions` | Cookie 会话（存 token 的 SHA256） |
| `device_tokens` | App Bearer Token（90d 滑动） |
| `shares` | 分享（token / expires_at / password_hash / max_downloads / owner_id / target_user_id） |
| `grants` | 目录授权（user_id × path，can_read / can_write / expires_at） |
| `invites` | 邀请码 |
| `blobs` | 秒传指纹（sha256 → sample_path） |
| `trash` | 回收站登记 |
| `upload_sessions` | 分块上传会话 |
| `photos` | 照片索引（taken_at / mtime_ns / duration / codec / width / height / added_at） |
| `notifications` + `admin_broadcasts` | 站内通知与广播组 |
| `audit_log` | 审计（保留 10000 条，username 可回填） |
| `settings` | KV 设置（唯一入口 store/settings.py） |

---

## 8. 前端架构

### Web（web/src）

- `main.ts`：`createApp(App).use(router).mount('#app')` + PWA Service Worker 注册（仅安全上下文）。
- `router.ts`：10 条路由——`/`（主页）`/login` `/photos` `/kb` `/trash` `/shares` `/activity` `/admin`（meta.admin）/ `/s/:token`（外链分享页）/ `/up`（手机直传）。
- `api.ts`：全量 API 客户端（cookie 会话）+ 全部 TS 接口定义。
- `auth.ts`：登录态唯一来源（禁止各页面自存 me 状态）。
- `ui.ts`：toast / confirmDlg / promptDlg（Promise 化，**禁用浏览器原生 alert/confirm/prompt**）。
- `uploader.ts`：上传队列共享引擎（HomeView / UpView 共用）——状态队列、暂停/恢复、跨刷新断点续传、速度采样。
- `components/`：12 个组件（FileViewer / UploadQueue / DirPicker / NotificationsBell / ShareModal / ContextMenu …）。
- `views/`：10 个页面。

**约定**：Markdown/HTML 渲染必须走 `DOMPurify.sanitize`；主题用 CSS 变量 + `theme.ts`；构建必须单独跑 `npm run build`（管道会吞 vue-tsc 失败）。

### 移动 App（mobile/lib，80 dart 文件）

按职责分 8 个目录：

| 目录 | 内容 |
|---|---|
| `data/` | API 客户端（api/）、本地存储（local/）、媒体类型、自动切换、TOTP、更新服务 |
| `transfer/` | 传输引擎（自写，断点/秒传语义自主可控）+ 任务模型 + 本地文件 |
| `backup/` | 相册自动备份引擎（WorkManager + sqflite 账本 + 前台下服务 + 智能对账） |
| `share/` | 系统分享接收与分享管理 |
| `state/` | Riverpod 会话与设置状态 |
| `ui/` | 30+ 页面（files/photos/home/me/login/kb_graph/notes/transfers/trash/admin …） |
| `main.dart` | 入口 |

**关键约束**：R8/minify 全关（WorkManager Room 反射在裁剪下启动即崩）；构建必须走 `scripts/build-apk.bat`（中文路径镜像到 `C:\lan-share-mobile`）；相机照片走本地媒体代理绕开自签 HTTPS 证书问题。

---

## 9. 关键实现细节与陷阱（务必先读）

| 陷阱 | 说明 |
|---|---|
| FastAPI 路由顺序 | `POST /api/upload/{path:path}` 通配会吞掉 `/api/upload/init`——精确路由必须注册在通配**之前** |
| `os.replace` 静默覆盖 | 移动/重命名类操作用 `os.rename`（Windows 下目标存在必抛 FileExistsError，天然防覆盖竞态） |
| safe_path 用 `absolute()` | `Path.resolve()` 会穿透 junction，路径校验用 `absolute()` + 词法边界 |
| SQLite 大小写 | 用户名类查询用 `COLLATE NOCASE`，防爆破 key 统一小写 |
| SQLite 写锁 | 锁内异常必须 rollback，否则未决写事务逸出会锁死该连接后续写操作 |
| scrypt maxmem | Windows OpenSSL 默认 32MB 不够，必须显式 `maxmem=128MB` |
| 磁盘扫描阻塞 | rglob 全量扫描用 `def`（线程池）而非 `async def`，避免卡事件循环 |
| ffmpeg 抽帧 | 输出 `.tmp` 必须显式 `-f image2`；`-ss` 越界先试 `-ss 1` 再退回首帧 |
| Caddy 契约 | `max_size(25GB) ≥ WebDAV 上限(20GB)`，两处是契约关系；分片单块 ≤64MB |
| 公网入口标记 | Caddy 在隧道端口注入 `X-LanShare-Entry: public`，LAN 端口剥离——Host 头可伪造，物理端口才可信 |
| CSP | `script-src 'self'`（脚本全外部化），`style-src 'unsafe-inline'` |
| App 分发三件套 | APK + `version.json` + `versions.json`，漏一件用户少一块功能 |

---

## 10. 代码入口点速查

| 入口 | 文件 | 说明 |
|---|---|---|
| 服务进程入口 | `server.py` | NSSM `server:app`；⚠️ `__main__` 块不可删 |
| ASGI 应用对象 | `app/asgi.py` | `app = FastAPI()` + 全局中间件 |
| 应用组装 | `app/main.py` | include_router 顺序 → SPA 路由 → 静态挂载；末尾调 `init_db()` |
| 数据库初始化 | `app/db.py:init_db()` | 双引擎建表 + 幂等迁移 |
| 权限解析 | `app/spaces.py:resolve()` | 所有文件操作的唯一判权入口 |
| 认证依赖 | `app/security.py:require_user / require_admin` | 新路由默认挂 `Depends(require_user)` |
| Web 前端 | `web/src/main.ts` → `router.ts` | Vue SPA |
| App 前端 | `mobile/lib/main.dart` | Flutter |
| 反代配置 | `Caddyfile` | TLS / forward_auth / 端口标记 |
| 运维 | `check.bat` / `backup.bat` / `logs/` | 门禁 / 备份 / 日志 |

---

## 11. 测试与质量门禁

- **pytest 231 项**（28 个测试文件；另设 `LANSHARE_TEST_PG_URL` 可整体切 PostgreSQL），其中 `test_baseline.py` 是行为回归网，`test_multiuser.py` 覆盖角色/空间/授权边界，`test_review_fixes.py` 是安全回归网（改 `spaces.py`/Caddyfile/回收站/设备 Token/TOTP 必跑）。
- `tests/conftest.py` 在导入前把 `SHARE_ROOT` 指向临时目录，**全套测试不碰真实数据**。
- **Dart 179 项** + `dart analyze` 0 问题。
- ruff 全绿（显式豁免 BLE001/B008/B904 等刻意风格）；mypy 有 3 项历史遗留。
- 改后端必跑 `check.bat`；改 Caddyfile 属攻击面变更需手动验证。

---

## 12. 维护建议（新接手者路线）

1. **先读**：`README.md` → `项目记忆/01-架构与现状.md` → `项目记忆/03-踩坑清单.md`（动手前扫一遍）→ `docs/DEVELOPMENT.md` → `docs/SECURITY.md`。
2. **改代码前心里有数**：真实数据与测试共库（E2E 必须自建自清）；R8/资源优化不要随手打开；中文路径一律走 `C:\lan-share` 短路径。
3. **凭据不入库**：站长账号、debug 账号、keystore、PG 密码、自签证书都在这台机器上，不在 git 里。
4. **文档随代码同步**：改了 API / 安全行为 / 交互约定 / 部署方式，同一次提交里更新对应文档（"文档过期比没有文档更糟"）。
5. **技能沉淀**：项目有大量反复踩坑的既有记忆（`项目记忆/`），先查再动手。
