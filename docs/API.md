# LAN Share API 参考

Base URL：`https://<host>:8765`（经 Caddy）；开发模式 `http://127.0.0.1:8766`。

**认证约定**
- 🔓 公开：无需登录
- 🔑 登录：需要有效会话 Cookie（`ls_session`，登录后由服务端签发）
- 🎫 票据：一次性/短时效凭证
- 🧩 Basic：WebDAV 专用，成员账号密码（未设独立密码时为登录密码）

**通用错误格式**：`{"detail": "..."}`；429=限速/锁定，401=未认证，403=禁止，404=不存在，410=已过期。

## 认证

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/auth/state` | 🔓 | `{needs_setup, logged_in, username}` |
| POST | `/api/setup` | 🔓* | `{username, password}` 创建管理员；*仅用户表为空时可用，创建后永久关闭 |
| POST | `/api/login` | 🔓 | `{username, password}`；失败 5 次锁账号 15 分钟；10 次/分/IP |
| POST | `/api/logout` | 🔓 | 吊销当前会话 |
| GET | `/api/auth/me` | 🔑 | `{username, role, quota_bytes}`；401=未登录（前端以此为登录态探测） |
| POST | `/api/auth/password` | 🔑 | `{old_password, new_password}`；改密吊销全部会话并重签当前 |
| POST | `/api/auth/username` | 🔑 | `{new_username, password}` 改用户名，需当前密码确认（失败计入防爆破锁定，锁定期 429）；登录态不变；空白/控制字符/超 64 字符 400，同名（不分大小写）400，密码错误 403 |
| GET | `/api/auth/webdav-password` | 🔑 | `{set}` 是否已设置 WebDAV 独立密码（不回传哈希） |
| POST | `/api/auth/webdav-password` | 🔑 | `{current_password, webdav_password}` 设置/清除（空串=清除）WebDAV 独立密码；需当前密码确认（失败计入防爆破）；≥8 位 |
| POST | `/api/auth/token` | 🔑 | 📱`{name}` 会话换设备 Token（App 用，M-A0）：`{token, device_id, name, created_at}`——**明文仅本次返回**，库内只存 SHA256；90 天滑动过期；20 台/账号；20 次/时；**同名设备轮换**（旧凭据即失效，不产生僵尸设备） |
| GET | `/api/auth/devices` | 🔑 | 📱本账号设备列表 `{devices:[{id, name, created_at, last_seen, current}]}`（current=本次请求所用 Bearer 设备） |
| DELETE | `/api/auth/devices/{id}` | 🔑 | 📱吊销本人设备（被吊销端下次请求 401）；他人设备 404 |

> 📱 **Bearer 认证（App 专用，与 Cookie 并存）**：所有 🔑 端点均可带
> `Authorization: Bearer <token>` 访问（无需 Cookie）。浏览器不会自动携带该头，
> 无 CSRF 面；停用账号即刻失效；审计操作者同等记录。
| GET | `/authcheck` | 🔓 | Caddy forward_auth 子请求入口；200=会话有效 / 401 |

## 文件

> **成员视角预览（09-01）**：`GET /api/files`、`GET /api/photos`、
> `GET /api/photos/facets`、`GET /api/shares`、`GET /api/photo/meta` 支持请求头
> `X-As-User: <成员名>`——admin 会话以该成员身份解析可见性（只读；成员传头
> 无效；目标不存在 404；命中记 `preview_as` 审计）。

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/files/{path}` | 🔑 | 目录列表 `{path, items:[{name,path,is_dir,size,size_human,modified,ext}]}`；隐藏（点前缀）目录不返回 |
| POST | `/api/upload/{path}` | 🔑 | multipart 单请求上传（≤2GB）；返回 `{sha256, deduped, ...}` |
| POST | `/api/upload/init` | 🔑 | `{dest_dir, filename, size, sha256?}` → 命中指纹 `{instant:true,...}` 秒传硬链接；否则 `{session, chunk_size, received:[]}`。分片大小自适应（≤2GB→5MB / ≤5GB→16MB / ≤10GB→32MB / 更大→64MB）；总大小硬上限 64GB |
| GET | `/api/upload/state/{sid}` | 🔑 | `{received:[块序号]}`（断点续传，chunk_size 与 init 一致） |
| POST | `/api/upload/chunk/{sid}?index=N` | 🔑 | 原始字节流写第 N 块（不受常规限速）；原子落盘，字节数不符整块即拒（中断不留半截分片） |
| POST | `/api/upload/finish/{sid}` | 🔑 | 合并 + SHA256/大小校验 → 入库去重；同名覆盖时旧版本进回收站；`{path, size, sha256, deduped}` |
| POST | `/api/mkdir/{path}` | 🔑 | 逐段清洗后创建目录 |
| POST | `/api/move` | 🔑 | `{sources:[路径], dest_dir}` 批量移动（≤500 项）；同盘 rename（硬链接/指纹不受影响）。逐项返回 `{results:[{src, ok, dest\|error}]}`；重名原子改名 `(2)`/`(3)`（`os.rename` 目标存在必抛错，无覆盖窗口），移入自身子目录 400，已在目标目录 400 |
| POST | `/api/rename` | 🔑 | `{path, new_name}` |
| DELETE | `/api/files/{path}` | 🔑 | 移入回收站（非物理删除） |
| GET | `/preview/{path}` | 🔑 | 内联预览，支持 Range（音视频拖动） |
| GET | `/thumb/{path}` | 🔑 | 图片/视频缩略图 JPEG（缓存于 `.thumbs/`） |
| GET | `/api/search?q=` | 🔑 | 文件名搜索，最多 50 条 |
| GET | `/api/recent?limit=` | 🔑 | 最近修改（≤100） |
| GET | `/api/disk` · `/api/disk/detail` | 🔑 | C 盘用量 + 网盘统计（`share_size` 按文件索引去重，硬链接只计一次；detail 附各一级目录逻辑大小 `categories`） |
| GET | `/api/photos?limit=&offset=&month=&uploader=&refresh=` | 🔑 | 照片时间线 `{total, items:[{path,name,taken,month,uploader,thumb}]}`（EXIF 日期倒序；jpg/jpeg/png/webp/bmp/heic/heif）；`month=YYYY-MM`/`uploader` 过滤；`refresh=1` 放宽扫描节流（App 备份完成后拉最新，地板 5s 防滥用） |

## 回收站

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/trash` | 🔑 | 列表 `{id,name,orig_path,size_human,days_left,...}` |
| POST | `/api/trash/{id}/restore` | 🔑 | 恢复到原位置（占用时自动加"(恢复)"后缀） |
| DELETE | `/api/trash/{id}` | 🔑 | 彻底删除单项 |
| DELETE | `/api/trash` | 🔑 | 清空 |

## 批量打包

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/zip` | 🔑 | `{paths:[...]}`（≤500 项）→ `{url, name}`；一次性下载 URL，15 分钟有效 |
| GET | `/api/zip/{token}` | 🔑 | 下载 zip（发送后自动清理临时文件） |

## 分享

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/share` | 🔑 | form：`path, expires_hours, password?, max_downloads?` → `{token, password?}`（提取码明文仅创建响应一次性返回，不再落库回显） |
| GET | `/api/share/meta/{token}` | 🔓 | `{name, size_human, expires_in, needs_password, max_downloads, downloads}` |
| POST | `/api/share/{token}/prepare` | 🔓 | JSON `{password?}` → 校验（错 5 次锁 15 分钟）→ `{url:"/api/share/{t}/dl?tkt=..."}`；票据 10 分钟内可复用；达次数上限 410 |
| GET | `/api/share/{token}/dl?tkt=` | 🎫 | 流式下载（支持 Range 续传）；计数在响应完成后 +1 |
| GET | `/api/share/{token}/qr` | 🔑 | 分享链接 QR 码 SVG（URL 按请求 Host 生成） |
| GET | `/api/shares` | 🔑 | 全部活跃分享（含下载计数） |
| GET | `/api/shares/{token}/history` | 🔑 | 取件记录（成功下载 + 密码错误，含 IP，最近 50 条） |
| DELETE | `/api/shares/{token}` | 🔑 | 撤销（立即失效） |

## 手机直传 / 工具

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/up` | 🔑 | 手机直传页（未登录 302 → `/login?next=/up`） |
| GET | `/api/qr?text=` | 🔑 | 任意文本 QR 码 SVG（≤512 字符） |

## WebDAV（`/dav/`）

认证：会话 Cookie 或 `Authorization: Basic base64(user:pass)`（复用 scrypt 校验与防爆破锁定）。

| 方法 | 行为 |
|------|------|
| OPTIONS | 返回 `DAV: 1, 2` 与 Allow 列表 |
| PROPFIND | Depth 0/1（按 1 处理 infinity）；不列出点前缀条目 |
| GET / HEAD | 文件内容，支持 Range |
| PUT | 覆盖前旧版本移入回收站；≤20GB（流式写盘） |
| MKCOL | 创建目录（保留名拒绝） |
| DELETE | 移入回收站 |
| MOVE / COPY | Destination 头定位；`Overwrite: T` 时旧目标进回收站 |
| LOCK / UNLOCK | 应答假锁（不真正持锁），满足客户端兼容 |
| PROPPATCH | 应答成功但不落盘（客户端兼容） |

## 限速（应用层，按真实客户端 IP）

| 范围 | 限制 |
|------|------|
| `/api/login` | 10 次/分/IP |
| `/api/share*` | 30 次/分/IP |
| 其它 `/api/*` | 按入口分账：局域网 `api:lan` 120 / 公网 `api:pub` 240 次/分/IP（独立桶，互不挤占） |
| `/api/upload/chunk/*`、`/authcheck`、`/thumb`、`/preview`、`/dav` | 不限（仍有认证保护） |

> 429 统一携带 `Retry-After: 30`（v0.10.1 起）；Caddy 反代使内外网 `client_ip`
> 同为 127.0.0.1，桶必须按入口分账，否则全家共享一个配额。

## v3 多用户（家庭团队版）

**空间语义（适用于所有文件端点）**：路径可为 `private:<path>`（本人私人空间）、
`shared:<path>` 或裸路径（共享空间，兼容 v2）。`shared:private/...` 永远映射到
请求者本人的私人目录；`shared:incoming/...` 写入落到 `incoming/<用户名>/`。
权限：admin 全权；member 共享空间默认只读（需授权写入）；guest 全凭授权；
私人空间仅本人（admin 亦不可读他人私人目录）。

### 认证扩展

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/invites/redeem` | 🔓 | `{code, username, password}` 邀请码建号；原子认领；保留词/分隔符/重名 400 |
| POST | `/api/login/totp` | 🔓 | `{username, password, code}` 两步登录第二步（错误计入防爆破）；`code` 为 6 位动态码或一枚一次性恢复码（连字符/大小写/空白均可） |
| POST | `/api/pair/code` | 🔑 | 已登录方生成一次性 6 位配对码 `{code, expires_in:180}`；同账号单码（重新生成即作废旧码） |
| POST | `/api/pair/claim` | 🔓 | `{code, name}` 手机免密换设备 Token（响应同 `/api/auth/token`）；3 分钟一次性，IP 限速 5 次/分 |
| POST | `/api/auth/totp/setup` | 🔑 | 生成 TOTP 密钥，`{secret, otpauth}`（未验证前不生效） |
| POST | `/api/auth/totp/enable` | 🔑 | `{code}` 验证动态码后开通两步验证；响应含 `recovery_codes`（10 枚一次性恢复码，**明文仅此一次**，库内只存 SHA256） |
| POST | `/api/auth/totp/rc` | 🔑 | `{old_password}` 重新生成恢复码（旧码全作废；需原密码） |
| POST | `/api/auth/totp/disable` | 🔑 | `{old_password}` 关闭两步验证（需原密码；恢复码一并清除） |

`POST /api/login`：已启用 TOTP 的账号密码正确时返回 `{"totp_required": true}`
且**不发会话**；`/api/auth/me` 增加 `totp_enabled`、`totp_rc_left`（剩余恢复
码数）字段。

**配对码**：Web 端「账号设置 → 手机 App 连接」生成，手机 App 免账号密码
配对（场景：长辈手机不输长密码）；签出的设备 Token 与密码登录完全同规则
（同名轮换/上限/90 天滑动/可吊销），安全取舍见 SECURITY.md 已知边界 #11。

### 管理（require_admin，非 admin 403）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/users` | 成员列表 `{id, username, role, is_active, quota_bytes, ...}` |
| PATCH | `/api/admin/users/{uid}` | `{is_active?, quota_bytes?, role?}`；停用即吊销全部会话；最后一个 admin 不可降级/停用 |
| DELETE | `/api/admin/users/{uid}` | 删号（grants/会话级联；私人目录改名 `.orphaned/<uid>-<名>/`） |
| POST | `/api/admin/users/{uid}/username` | `{value}` 管理员改成员用户名（校验同自助改名的保留词/重名规则；私人目录同步改名；成员会话保持） |
| POST | `/api/admin/users/{uid}/password` | `{value}` 管理员重置成员密码（≥8 位；该成员全部会话吊销） |
| POST | `/api/admin/users/{uid}/totp-disable` | 仅解除成员两步验证（含恢复码）：不动密码、不吊销会话——认证器丢失且无法自助关闭时的精准通道 |
| POST | `/api/admin/users/{uid}/webdav-password` | `{value}` 重置成员 WebDAV 独立密码（≥8 位）；`{value:""}` 清除回退登录密码；不动网页会话 |
| GET | `/api/admin/webhook` | 查看事件推送 Webhook 模板 URL |
| POST | `/api/admin/webhook` | `{url}` 设置推送模板（http(s) 且含 `{title}/{body}` 占位符；空串清除） |
| POST | `/api/admin/webhook/test` | 同步发测试消息；失败 502 带原因，未配置 400 |
| GET/POST | `/api/admin/invites` | 邀请码列表 / 创建 `{role: member\|guest, hours}` |
| DELETE | `/api/admin/invites/{code}` | 删除邀请记录（任意状态） |
| GET/POST | `/api/admin/grants` | 目录授权查询 / 创建 `{user_id, path, can_read, can_write, expires_hours?}`（仅 shared 空间目录） |
| DELETE | `/api/admin/grants/{gid}` | 撤销授权 |
| GET | `/api/activity?limit=` | 家庭动态流（audit 过滤 private 目标） |
| POST | `/api/activity/clear` | 清空动态（settings 水位软清空，审计保留） |
| GET | `/api/public-url` | 🔑 全员可读公网访问地址（分享/直传双地址用） |
| GET/POST | `/api/admin/public-url` | 查看/设置公网访问地址（POST 需 admin） |

### 分享扩展

- `POST /api/share` 记录 `owner_id`；新增可选表单字段 `target`（成员用户名）=
  **定向分享**：仅该成员与 admin 登录后可取（免提取码），对无关者 404；
  创建时自动给收件人发站内通知
- `GET /api/members` 🔑 在用成员名单（定向分享收件人下拉用，不含自己）
- `GET /api/notifications` 🔑 `{unread, items[50]}` 本人站内通知；
  `POST /api/notifications/read` 🔑 全部标记已读；`GET /api/shares` 按创建者隔离
  （admin 全见 + `owner` 字段），响应为 **以 token 为键的字典**；带密码分享
  含 `password` 明文（创建时保存，旧存量分享无此字段）
- `DELETE /api/shares/{token}` 与 `GET /api/shares/{token}/history` 仅创建者/admin
- 下载计数**按票据去重**：下载管理器的并发 Range 分段同一 `tkt` 只计 1 次
- `GET /api/share/{token}/qr` 🔑 需登录（按请求 Host 生成对应域的二维码）

### 相册（v3.5）

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/photos?month=&uploader=` | 🔑 | 时间线条目带 `month`/`uploader`，可按月份/拍摄者过滤 |
| GET | `/api/photos/facets` | 🔑 | `{months[], uploaders[]}` 维度分布（按本人可见范围统计） |
| GET | `/api/photo/meta?path=` | 🔑 | 图片元数据 `{width, height, camera}`（EXIF；非图片 404） |
| GET | `/api/albums` | 🔑 | 精选专辑列表（`{id, title, month}`） |
| POST | `/api/admin/albums` | ⚙️ | `{title, month}` 新增精选专辑（同月唯一，month=YYYY-MM） |
| DELETE | `/api/admin/albums/{id}` | ⚙️ | 移除精选专辑 |

### 公网入口清单（免登录，App v0.10.0+）

`GET /api/app/entries` → `{"public": url|null, "public_alt": url|null, "cert_sha256": "AA:BB:…"}`

- 主/备隧道地址来自管理面板「公网访问地址」（settings 表 `public_base_url` / `public_base_url_alt`）
- 证书指纹与本机 `certs/cert.pem` 的 SHA256（DER）一致，App 端 TOFU 预校验逐字可比
- 免登录是有意设计（App 认证前就要拿入口清单）；两项均非机密，healthz 等敏感面仍按 Host 收敛

### App 分发与更新历史（v0.6.0 / v0.9.0）

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/app/latest` | 🔑 | 最新分发版本 `{version, build, filename, notes, sha256, available, size, size_human}`；数据源 `App安装包/version.json` |
| GET | `/api/app/history` | 🔑 | 更新历史 `{items: [{version, build, date, notes}]}`（新版本在前）；数据源 `App安装包/versions.json`，宽容解析（缺文件/坏条目→空列表） |
| GET | `/api/app/download` | 🔑 | APK 文件流（审计 `app_download`） |

> 发版约定：`App安装包/` 下 **APK + version.json + versions.json 三件套**一起更新
> （version.json.notes=检查更新弹窗文案；versions.json=App「更新历史」栏目）。

### 其他

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/healthz` | 🔓 | `{status, db, data_dir, disk_free_gb, disk_used_pct}` |
| GET | `/api/disk/detail` | 🔑 | 全员可查；`categories` 仅 admin；成员返回 `private_used/quota_bytes` |
| GET | `/api/qr?text=` | 🔑 | 任意文本二维码 SVG（**ECC-Q + 中心云朵 logo**，分享码同源） |
