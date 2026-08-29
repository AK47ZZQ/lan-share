# 开发指南

## 测试

```bash
check.bat            # 质量门禁 = pytest 基线（后端改动必跑，跑绿才算改完）
# 或手动：venv python -m pytest tests/ -q
```

- `tests/conftest.py` 在导入 `server` 前把 `SHARE_ROOT` 指向临时目录，
  全套测试不碰真实数据；`client` 夹具恒为匿名态，`auth` 夹具是独立实例的登录态。
- `tests/test_baseline.py`（25 项）是**行为回归网**；`tests/test_multiuser.py`（18 项）覆盖角色/空间/授权边界（拆包/PG/Vue 升级期间不许掉）：
  认证/防爆破、文件增删改移、分块上传与秒传、回收站、分享全链路、
  safe_path 穿越（`%2e%2e` 编码变体——httpx 会归一化裸 `..`，测试必须编码绕过）。
- 改 server.py 后新增行为 → 先补测试再改实现；发现 bug → 先写失败用例再修。
- v3 新增 `tests/test_multiuser.py`（角色/空间/授权边界，对应 DESIGN-v3/PERMISSIONS.md）。
- 新前端：`cd web && npm run build`（产物 web/dist 自动挂 /app，无需重启服务）。

## 环境与运行

- Python 3.11 + FastAPI/uvicorn/aiofiles/Pillow/qrcode（既有 venv，见 `install-service.bat` 中的 PYTHON 路径）
- Caddy v2.11 原生二进制（`caddy.exe`，**不要**换成需要插件的构建）
- ffmpeg（可选）：仅视频缩略图用，缺失时视频缩略图返回 404，不影响其它功能

```bash
# 开发实例（不碰线上服务）
SHARE_ROOT=C:/somewhere/sandbox python -m uvicorn server:app --host 127.0.0.1 --port 8768

# 线上（注意 C:\lan-share 是本项目目录的 junction，改这里就是改线上）
net stop lan-share-api && net start lan-share-api     # server.py 变更
net stop lan-share-caddy && net start lan-share-caddy # Caddyfile 变更
# static/* 变更无需重启，刷新即生效
```

## 部署/测试约定

1. **沙箱先行**：所有改动先在 `SHARE_ROOT=测试目录` 的 8768 实例上验证，
   再 `net stop/start` 上线；`test-sandbox/` 目录用完即删，不要提交。
2. **回归清单**（改后端必跑）：setup→login→上传（直传+分块+秒传）→列表/搜索→移动（A/B/C 三入口
   + 重名/成环/同目录边界）→分享（密码对/错/锁定）→票据下载→删除→回收站恢复→改名/改密
   （含防爆破联动）→WebDAV PROPFIND/PUT→401 未认证探测。
3. **前端改动**用真实浏览器点一遍主要链路（登录、浏览、上传、预览、弹窗、拖拽），
   注意移动端视口（390×844）。
4. **调试账号**：接口调试用 `debug` 账号登录（凭据见 `docs/DEBUG-ACCOUNT.md`，不入库），
   审计日志可与管理员操作区分。

## 代码结构（v3 拆包后）

```
app/
├── main.py            应用组装：app+中间件 → 路由模块 → SPA 页面路由/深链回退 → 静态挂载
├── config.py          全部常量与 env（SHARE_ROOT/DB_PATH 等）
├── db.py              SQLite 每线程连接 + 写锁 + init_db + _migrate_v3（幂等迁移）
├── security.py        审计/scrypt/会话/防爆破/限速/require_user/require_admin
├── filetools.py       safe_path/clean_filename/dir_tree/save_upload
├── spaces.py          v3 空间解析 resolve()/授权判定/配额/可见性过滤（安全核心）
├── api/auth.py        认证 API（setup/login/logout/改密/改用户名/authcheck）
├── api/files.py       文件 API（列表/上传/移动/回收站/预览/zip/缩略图/搜索/磁盘）
├── api/share.py       分享 API（外链/票据/QR/取件记录；按 owner 隔离）
├── api/webdav.py      WebDAV（与网页同一 resolve 权限源）
├── api/system.py      定时清理/照片时间线/通用 QR/healthz
├── api/admin.py       成员/邀请/授权/动态流/公网地址
server.py             兼容 shim（NSSM 入口 server:app 不变；测试 import server）
web/                  Vue3 前端工程（Vite+TS）：src/api.ts 客户端、src/auth.ts
                      共享登录态、src/views/ 七个页面；构建产物 dist/ 即时生效
static/               仅 favicon（旧页面已删，git 历史可回滚）
docs/            API.md / SECURITY.md / DEVELOPMENT.md / GIT.md / DEBUG-ACCOUNT.md(不入库)
```

## 前端约定（CSP 硬约束）

- **禁止**任何内联事件处理器（`onclick=` 等）与内联 `<script>`——CSP `script-src 'self'` 会拦截。
- 交互统一走 **`data-act` 事件委托**：元素加 `data-act="动作名"` + `data-*` 参数，
  在 `index.js` 的 `ACTIONS` 表注册处理函数。动态生成的 HTML 同样如此。
- 动态值入属性用 `esc()`；**不再需要** escJs（旧的 JS 字符串拼接模式已移除）。
- 图片加载失败用 document 捕获阶段的 `error` 委托处理（error 事件不冒泡）。
- 新增浮层注意 z-index 层级：列表 < 预览(110) < 弹窗(120) < Toast(200)。
- 按钮/容器内的 `<svg>` 必须显式定宽高（有 `.btn svg` 兜底，但自定义容器要自己加）。

## 后端约定

- 新路由默认挂 `Depends(require_user)`；设计为公开的必须加限速与输入校验（见 docs/SECURITY.md 检查清单）。
- 一切用户提供的路径走 `safe_path()`，文件名走 `clean_filename()`。
- DB 写操作放 `with _db_lock:`；读用线程本地的 `_db()` 连接直接查。
- 可-dispatch 的耗时 IO 用 `async def` + aiofiles；CPU/阻塞型（缩略图、zip）用 `def` 同步端点跑线程池；
  scrypt 这类密码学操作用 `run_in_threadpool`。
- 关键操作 `audit(...)`，action 命名见 `audit_log` 现有值。
- 新增表/列：改 `init_db()` 的 executescript（CREATE TABLE IF NOT EXISTS，服务重启自动迁移）。

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
