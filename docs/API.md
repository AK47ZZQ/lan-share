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
| GET | `/api/auth/me` | 🔑 | 当前用户名 |
| POST | `/api/auth/password` | 🔑 | `{old_password, new_password}`；改密吊销全部会话并重签当前 |
| GET | `/authcheck` | 🔓 | Caddy forward_auth 子请求入口；200=会话有效 / 401 |

## 文件

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/files/{path}` | 🔑 | 目录列表 `{path, items:[{name,path,is_dir,size,size_human,modified,ext}]}`；隐藏（点前缀）目录不返回 |
| POST | `/api/upload/{path}` | 🔑 | multipart 单请求上传（≤2GB）；返回 `{sha256, deduped, ...}` |
| POST | `/api/upload/init` | 🔑 | `{dest_dir, filename, size, sha256?}` → 命中指纹 `{instant:true,...}` 秒传硬链接；否则 `{session, chunk_size:5242880, received:[]}` |
| GET | `/api/upload/state/{sid}` | 🔑 | `{received:[块序号]}`（断点续传） |
| POST | `/api/upload/chunk/{sid}?index=N` | 🔑 | 原始字节流写第 N 块（不受常规限速） |
| POST | `/api/upload/finish/{sid}` | 🔑 | 合并 + SHA256/大小校验 → 入库去重；`{path, size, sha256, deduped}` |
| POST | `/api/mkdir/{path}` | 🔑 | 逐段清洗后创建目录 |
| POST | `/api/rename` | 🔑 | `{path, new_name}` |
| DELETE | `/api/files/{path}` | 🔑 | 移入回收站（非物理删除） |
| GET | `/preview/{path}` | 🔑 | 内联预览，支持 Range（音视频拖动） |
| GET | `/thumb/{path}` | 🔑 | 图片/视频缩略图 JPEG（缓存于 `.thumbs/`） |
| GET | `/api/search?q=` | 🔑 | 文件名搜索，最多 50 条 |
| GET | `/api/recent?limit=` | 🔑 | 最近修改（≤100） |
| GET | `/api/disk` · `/api/disk/detail` | 🔑 | 磁盘用量 / 分类占用 |
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
| PUT | 覆盖前旧版本移入回收站；≤2GB |
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
