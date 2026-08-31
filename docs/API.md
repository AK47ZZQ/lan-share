# LAN Share API 参考

Base URL：`https://<host>:8765`（经 Caddy）；开发模式 `http://127.0.0.1:8766`。

**认证约定**
- 🔓 公开：无需登录
- 🔑 登录：需要有效会话 Cookie（`ls_session`，登录后由服务端签发）
- 🎫 票据：一次性/短时效凭证
- 🧩 Basic：WebDAV 专用，管理员账号密码

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
| GET | `/authcheck` | 🔓 | Caddy forward_auth 子请求入口；200=会话有效 / 401 |

## 文件

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
| GET | `/api/photos?limit=&offset=` | 🔑 | 照片时间线 `{total, items:[{path,name,taken,thumb}]}`（EXIF 日期倒序） |

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
| POST | `/api/share` | 🔑 | form：`path, expires_hours, password?, max_downloads?` → `{token}` |
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
| 其它 `/api/*` | 120 次/分/IP |
| `/api/upload/chunk/*`、`/authcheck`、`/thumb`、`/preview`、`/dav` | 不限（仍有认证保护） |

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
| POST | `/api/login/totp` | 🔓 | `{username, password, code}` 两步登录第二步（动态码错误计入防爆破） |
| POST | `/api/auth/totp/setup` | 🔑 | 生成 TOTP 密钥，`{secret, otpauth}`（未验证前不生效） |
| POST | `/api/auth/totp/enable` | 🔑 | `{code}` 验证动态码后开通两步验证 |
| POST | `/api/auth/totp/disable` | 🔑 | `{old_password}` 关闭两步验证（需原密码） |

`POST /api/login`：已启用 TOTP 的账号密码正确时返回 `{"totp_required": true}`
且**不发会话**；`/api/auth/me` 增加 `totp_enabled` 字段。

### 管理（require_admin，非 admin 403）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/users` | 成员列表 `{id, username, role, is_active, quota_bytes, ...}` |
| PATCH | `/api/admin/users/{uid}` | `{is_active?, quota_bytes?, role?}`；停用即吊销全部会话；最后一个 admin 不可降级/停用 |
| DELETE | `/api/admin/users/{uid}` | 删号（grants/会话级联；私人目录改名 `.orphaned/<uid>-<名>/`） |
| POST | `/api/admin/users/{uid}/username` | `{value}` 管理员改成员用户名（校验同自助改名的保留词/重名规则；私人目录同步改名；成员会话保持） |
| POST | `/api/admin/users/{uid}/password` | `{value}` 管理员重置成员密码（≥8 位；该成员全部会话吊销） |
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

### 其他

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/healthz` | 🔓 | `{status, db, data_dir, disk_free_gb, disk_used_pct}` |
| GET | `/api/disk/detail` | 🔑 | 全员可查；`categories` 仅 admin；成员返回 `private_used/quota_bytes` |
| GET | `/api/qr?text=` | 🔑 | 任意文本二维码 SVG |
