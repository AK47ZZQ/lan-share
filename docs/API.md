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
| GET | `/api/auth/me` | 🔑 | `{username, role, quota_bytes, nickname, avatar_path}`；401=未登录（前端以此为登录态探测） |
| POST | `/api/auth/password` | 🔑 | `{old_password, new_password}`；改密吊销全部会话并重签当前 |
| PATCH | `/api/auth/profile` | 🔑 | `{nickname}` 改显示昵称（≤32 字符、去控制字符；空=清空恢复显示用户名）。昵称只是显示层，登录/路径/权限仍用 username |
| POST | `/api/auth/avatar` | 🔑 | multipart `file` 上传/替换头像：PIL 解码（60MP 钳制）→ 中心裁方 → 512px JPEG 存 `data/.avatars/<uid>.jpg`；≤8MB，限速 6 次/分 |
| GET | `/api/auth/avatar/{username}` | 🔑 | 取用户头像 JPEG（无头像 404；Cache-Control private 300s） |
| POST | `/api/auth/verify-password` | 🔑 | `{password}` 高危操作本人口令复核（清空服务端备份等），不发会话；限速 5 次/分，429=过频 |
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
| GET | `/api/files/{path}` | 🔑 | 目录列表 `{path, items:[{name,path,is_dir,size,size_human,modified,ext,abs_path}]}`；`abs_path`=服务器真实存储路径（正斜杠，"复制路径"用，v3.31）；隐藏（点前缀）目录不返回 |
| POST | `/api/upload/{path}` | 🔑 | multipart 单请求上传（≤2GB）；`?on_dup=` 控制同名覆盖行为（v3.31：`trash`=旧版本进回收站【默认】，`overwrite`=直接覆盖不留版本——笔记编辑器就地保存专用）；返回 `{sha256, deduped, ...}` |
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
| GET | `/api/photos?limit=&offset=&month=&uploader=&refresh=` | 🔑 | 照片时间线 `{total, items:[{path,name,taken,month,uploader,thumb,abs_path}]}`（EXIF 日期倒序；jpg/jpeg/png/webp/bmp/heic/heif）；`month=YYYY-MM`/`uploader` 过滤；`refresh=1` 放宽扫描节流（App 备份完成后拉最新，地板 5s 防滥用）；`abs_path` 为服务器真实存储路径（v3.31） |

## 笔记（v3.31）

笔记 = 网盘里的普通文件（.md/.markdown/.txt/.html/.htm），分类 = 笔记目录下的
子文件夹（可嵌套）。本端点只做"带摘要的一次性聚合列表"；新建/重命名/移动/
删除分类与笔记复用上方 files 通道（mkdir/rename/move/delete），不另立分类存储。

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/notes/list?path=&q=&sort=` | 🔑 | `{total, items:[{name, path, category, ext, size_human, modified, mtime, ctime, summary}]}`；`category`=相对笔记目录的子路径（`""`=未分类）；`summary`=正文前 200 字纯文本（html 先去标签，只读文件头 8KB）；`q` 命中文件名或内容前 8KB（大小写不敏感）；`sort=modified`\|`created`\|`name`；单目录树聚合上限 2000 篇（防误选网盘根目录全盘扫描） |

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
  创建时自动给收件人发站内通知（走统一出口，尊重接收方通知偏好）
- `GET /api/members` 🔑 在用成员名单（定向分享收件人下拉用，不含自己）
- `GET /api/shares` 按创建者隔离（admin 全见 + `owner` 字段），响应为 **以 token 为键的字典**；带密码分享
  含 `password` 明文（创建时保存，旧存量分享无此字段）
- `DELETE /api/shares/{token}` 与 `GET /api/shares/{token}/history` 仅创建者/admin
- 下载计数**按票据去重**：下载管理器的并发 Range 分段同一 `tkt` 只计 1 次
- `GET /api/share/{token}/qr` 🔑 需登录（按请求 Host 生成对应域的二维码）

### 通知与推送（v3.31 通知管理）

站内通知统一经 `insert_notification()` 落库——先查接收方**通知偏好**（按类型组
share/admin/system 三组开关，存 settings `notify_prefs_user_<uid>`），关闭的
类型不落库；站外 webhook 推送不受偏好影响。

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/notifications` | 🔑 | `{unread, items[50]:[{id,type,title,body,link,created_at,read}]}`（id 倒序） |
| POST | `/api/notifications/read` | 🔑 | 全部标记已读 |
| POST | `/api/notifications/{id}/read` | 🔑 | 单条标记已读（v3.31）；返回 `{changed}` |
| DELETE | `/api/notifications/{id}` | 🔑 | 删除本人单条通知（v3.31） |
| GET | `/api/notifications/prefs` | 🔑 | 本人通知偏好 `{share, admin, system}`（缺省全 true） |

### SSE 实时通知（v3.52）

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/events` | 🔑 | SSE 长连接（`text/event-stream`）。事件为**信号**而非载荷：`{"kind":"notification"}`（有新站内通知/广播，收到后拉 `/api/notifications`）、`{"kind":"feedback"}`（新反馈，仅 admin 视角提醒）。每用户 ≤3 连接、全局 ≤64（超限 429）；15s 心跳注释行；`retry: 5000` 断线重连；队列满丢最旧。Web NotificationsBell 与 App 前台收件箱均为 SSE 优先 + 轮询兜底 |
| PUT | `/api/notifications/prefs` | 🔑 | `{share?, admin?, system?}` 部分更新偏好（v3.31） |
| POST | `/api/admin/notify` | ⚙️ | 手动广播 `{title, body?, user_ids?}`（空=全体在职）；落 `admin_broadcasts` 记录（broadcast_id 贯穿站内行，支持撤回/统计）；逐人写入尊重偏好；`user_ids` 显式定向但全不匹配 → 404；返回 `{sent, webhook, broadcast_id}` |
| GET | `/api/admin/notifications/sent` | ⚙️ | 发送记录+草稿箱 `{items:[{id, title, body, target_count, status: sent\|draft\|scheduled, scheduled_at, sent_at, withdrawn_at, created_at, created_by, unread, read}]}`（新在前，≤100 条；unread/read 为该广播站内行的统计） |
| POST | `/api/admin/notifications/broadcast/{bid}/withdraw` | ⚙️ | 撤回：删该广播全部**未读**站内行（已读的保留为历史），标记 withdrawn_at；`{removed}` |
| POST | `/api/admin/notifications/broadcast/{bid}/remind` | ⚙️ | 提醒未读：对仍处未读的成员再推一次站外 webhook（站内不重复落）；`{unread}` |
| POST | `/api/admin/notifications/draft` | ⚙️ | 保存草稿 `{title, body?, user_ids?, scheduled_at?}`（unix 秒；`scheduled_at` 非空即定时广播，到期由服务端周期任务自动发送）；`{id}` |
| POST | `/api/admin/notifications/draft/{bid}/send` | ⚙️ | 立即发送草稿/定时项（已发送 409） |
| DELETE | `/api/admin/notifications/draft/{bid}` | ⚙️ | 删除草稿/定时项（已发送的不可删） |
| GET | `/api/admin/notifications?username=&limit=` | ⚙️ | 全站站内通知（v3.40，新→旧 ≤200）；`username` 可选按成员筛；已删成员行回退 `#uid` |
| DELETE | `/api/admin/notifications/{nid}` | ⚙️ | 删除任意成员的单条站内通知（v3.40） |
| DELETE | `/api/admin/notifications/broadcast/{bid}` | ⚙️ | **彻底删除广播**（v3.40）：`admin_broadcasts` 记录 + 已送达全部站内行（含已读）一并清除；`{notifications_removed}` |

> 广播写库于 `admin_broadcasts` 表（双引擎迁移 v3.31）；`notifications.broadcast_id`
> 列为存量库自动补列。定时广播由 startup 周期任务（60s）扫描到期 `scheduled` 记录发送。

### 相册（v3.5）

> 照片评论/表情回应（photo_comments / photo_reactions）已于 v3.31 下线：端点删除，
> 存量表与数据保留不再读写。

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

## 反馈通道（v0.28 / v3.41）

落盘于 `data/反馈/`（每条一个 md，frontmatter：编号/类型/状态/备注/标题/版本/设备/用户；
截图在 `反馈/img/`）。目录即状态：`待处理/`=已提交、`进行中/`=处理中、`已完成/`=
frontmatter 细状态（已修复/已实装/已拒绝/已暂缓）。`看板.md` 由服务端自动重生成。

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/feedback` | 🔑 | multipart：`title`(≤80 必填)/`type`(idea\|bug\|other)/`body`(≤2000)/`app_ver`/`device`/`files`(截图≤3，单张≤12MB)；限 20 条/天/人；落 `待处理/` 并刷新看板 |
| GET | `/api/feedback` | 🔑 | 本人列表；**admin 全量**（含 user 字段），新→旧 |
| GET | `/api/feedback/{fid}` | 🔑 | 详情（含 body 与图片 URL 列表）；仅本人/admin |
| GET | `/api/feedback/{fid}/img/{name}` | 🔑 | 截图读取（鉴权：本人/admin；文件名防穿越校验） |
| GET | `/api/admin/feedback` | ⚙️ | 全量视角 |

## 管理台（v3.39–v3.42）

Web 站长工作台供数层；全部 ⚙️。状态流转约定：反馈由 Agent 直接改 md 文件，
无回写 API。

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/admin/overview` | ⚙️ | 总览聚合：users/photos/shares/trash/blobs/sessions/devices 计数 + 磁盘 + `app_latest` + `traffic`（公网隧道流量：今日/本月字节 + 近 14 天序列，Caddy 隧道入口访问日志聚合，见 app/services/traffic.py）+ server_time |
| GET | `/api/metrics` | ⚙️ | 运行指标快照（v3.52）：近 1h 请求 `by_status` 计数与 `latency_ms{p50,p95,avg}`（asgi 中间件逐请求采集，环形缓冲 2048）+ `disk_used_pct` + `backup`（进程内每日兜底备份编排最近一轮状态，`services/backup.py`；外部计划任务备份另见 healthreport `backup` 块） |
| GET | `/api/admin/large-files?limit=` | ⚙️ | 全库大文件 TOP（跳过隐藏目录），`{items:[{path,size,mtime}], total_files, total_size}` |
| GET | `/api/admin/duplicates?min_size=&limit=` | ⚙️ | 疑似重复（同尺寸聚类，不做全盘哈希）：`{groups:[{size,paths,wasted}], group_count}` |
| GET | `/api/admin/audit?limit=&action=` | ⚙️ | 审计日志近况（新→旧）；`action` 非空按前缀筛选（如 `login`→login/login_fail）；响应带 `actions`（现存 DISTINCT 动作全集，供下拉） |
| GET | `/api/admin/sessions` | ⚙️ | 活跃 Web 会话（`token_hash` 仅作吊销句柄） |
| DELETE | `/api/admin/sessions/{token_hash}` | ⚙️ | 吊销单个会话（对端下一跳即 401） |
| DELETE | `/api/admin/sessions` | ⚙️ | **全站会话清空**（v3.40），保留当前请求自身会话；`{revoked}` |
| GET | `/api/admin/devices` | ⚙️ | 活跃 App 设备（device_tokens） |
| DELETE | `/api/admin/devices/{id}` | ⚙️ | 吊销设备（App 需重新配对） |
| POST | `/api/admin/users/{uid}/kick` | ⚙️ | **按账号一键下线**（v3.40）：`{scope: all\|web\|app}`；对自己拒绝 all/web（400 防自踢）；`{sessions, devices}` |
| GET | `/api/announcements` | 🔑 | 公告栏（新→旧 ≤10），登录即可见 |
| POST | `/api/admin/announcements` | ⚙️ | 发布公告 `{title≤80, body≤500}` |
| DELETE | `/api/admin/announcements/{aid}` | ⚙️ | 删除公告 |
