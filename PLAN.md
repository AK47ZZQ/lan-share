# LAN Share → 个人网盘 演进规划（v2，已交付）

> **本文为 v2 规划的存档。** 当前进行中的规划是 v3 家庭团队版（PostgreSQL +
> Vue 3 + 多用户），见 `PLAN-v3.md` 与 `docs/DESIGN-v3/`。
>
> **状态：已全部交付（2026-08-29）。** P0-P3 全部完成；"每日自动备份"按用户决策不做，
> 托盘小工具暂缓。本文保留作为设计决策记录；日常使用看 README，开发看 docs/DEVELOPMENT.md，
> 接口看 docs/API.md，安全模型看 docs/SECURITY.md，历史看 CHANGELOG.md。
> 修订（2026-08-29 晚）：`:8767` 的 basic_auth 第一道门已按用户要求移除（浏览器弹窗体验差），
> 公网与局域网认证完全统一到应用层登录页，见 CHANGELOG。

> 前提：单管理员自用；公网（樱花FRP）入口与局域网功能完全对等。
> 现有代码：`server.py`（FastAPI）+ `Caddyfile`（Caddy v2）+ `static/`（原生 HTML/JS）。
>
> **v2 修订（对抗性 review 后）**：
> 1. 原版 Caddy 二进制不支持 HMAC 签名校验和 rate_limit（都是第三方插件）→ 认证保护改用原生 `forward_auth`，限速移到应用层。
> 2. `:8767` basic_auth 必须豁免 `/s/*`、`/api/share/*`，否则公网分享链接收件人会被 basic_auth 挡死。
> 3. 分享密码禁止走 URL query（已发现泄露进 Caddy 日志），改为 POST body。
> 4. 目录树语义不进数据库（过度设计）：文件系统保持唯一事实来源，SQLite 只管账户/会话/分享/审计/上传会话；未来去重用 NTFS 硬链接。

## 0. 核心原则

1. **一个入口，一套逻辑**：认证统一在应用层（Cookie 会话）。Caddy 只负责 TLS、反代、`forward_auth` 保护的静态直连和 basic_auth 第一道门。
2. **SQLite 是持久化层**：用户、会话、分享、审计日志、上传会话落库，重启不丢状态。文件系统仍是文件的唯一事实来源。
3. **公网带宽是硬约束**：上传分块可续传；大文件下载走 forward_auth 保护的 Caddy 静态直连，绕开 Python。

## 1. 目标架构

```
外网设备 ──樱花FRP──▶ Caddy :8767 (HTTPS, basic_auth第一道门*，豁免分享路径)
内网设备 ──局域网──▶ Caddy :8765 (HTTPS)
                        │
                        ├─ /files/*      → forward_auth(/authcheck) 通过后 file_server 静态直连
                        ├─ /api/* /s/*   → FastAPI :8766
                        │                   ├─ SQLite: users/sessions/shares/audit/uploads
                        │                   ├─ 应用层限速 + 登录防爆破（按账号锁，FRP下XFF全是127.0.0.1）
                        │                   └─ CSRF: SameSite=Lax + Origin 校验
                        └─ static/       → 网盘UI、登录页、分享页
```

\* basic_auth 只挡扫描器，真实权限在应用层；`/s/*` 与 `/api/share/*` 豁免 basic_auth 以保证分享链接可用。

## 2. 数据模型（SQLite，`data/meta.db`）

```sql
users      (id, username, password_hash scrypt, created_at)
sessions   (token_hash PK, user_id, expires_at, created_at, ip, ua)   -- 存token的SHA256
shares     (token PK, path, name, size, created_at, expires_at,
            password_hash?, max_downloads?, downloads, revoked)
audit_log  (id, ts, ip, action, target, detail)                       -- 保留最近1万条
upload_sessions (P1: 分块续传时引入)
```

## 3. 安全设计（P0，公网全功能的前提）

| 项 | 方案 |
|----|------|
| 登录 | 单管理员，scrypt（标准库）哈希；失败 5 次锁该账号 15 分钟（按账号不按 IP，FRP 下 XFF 全为 127.0.0.1） |
| 会话 | HttpOnly + SameSite=Lax + Secure Cookie，30 天滑动过期，登出吊销；DB 存 token 哈希 |
| 限速 | 应用层中间件：/api/* 每 IP 120 req/min，登录 10 req/min（内存令牌桶） |
| CSRF | SameSite=Lax + 对带 Origin 头的状态变更请求校验 Origin 与 Host 一致 |
| 大文件直连 | `/files/*` 经 Caddy `forward_auth` → `/authcheck` 校验会话后由 file_server 直接服务 |
| 分享密码 | POST body 传递，杜绝进访问日志；分享可撤销、可限下载次数 |
| 审计 | 登录成败、删除、分享创建/打开/撤销、上传全记 audit_log |
| 隐藏目录 | `.trash`、`.chunks`、`thumbs` 等点前缀目录：列表/搜索/磁盘统计排除，API 路径拒绝访问 |
| 遗留修复 | safe_path 边界判断（normcase+分隔符）、QR 完整 URL、README 对齐、清理测试残留 |
| 首次部署 | 无用户时 `/login` 页自动切换为"创建管理员"流程，创建入口随用户存在而关闭 |

## 4. 功能路线图

### P0 — 安全地基（公网开放前置条件）【M1，本次实现】
见 §3。存量行为保持：单请求上传、目录浏览、删除、mkdir、share、QR、预览。

### P1 — 网盘核心体验【M2，2026-08-28 已完成】
1. ✅ 分块上传 + 断点续传（5MB/块，upload_sessions 落库，每块自动重试）
2. ✅ 秒传（≤100MB 客户端预检 SHA256）+ 去重（blobs 表 + NTFS 硬链接，finish 时服务端必校验）
3. ✅ 回收站（删除→`.trash/` 保留 30 天，可还原/单项删/清空，到期自动清理）
4. ✅ 缩略图（图片 Pillow / 视频 ffmpeg 抽帧，缓存 `.thumbs/`；短视频 -ss 越界自动退回首帧）
5. ✅ 音视频 Range 拖动（starlette FileResponse 原生 206，已验证）
6. ✅ 批量操作：多选打包 zip（一次性 URL）、重命名、批量删除进回收站

### P2 — 分享与移动端【M3，2026-08-28 已完成】
1. ✅ 分享限下载次数（创建时设定，取件时拦截）
2. ✅ 分享取件记录页（成功下载 + 密码错误尝试，来自 audit_log）
3. ✅ 手机直传页 `/up`：📲 二维码扫码打开（登录页支持 ?next= 回跳），文件直传 `incoming/`，复用分块续传/秒传

### P3 — 可选增强【M4，2026-08-28 已完成（按用户要求跳过每日自动备份）】
1. ✅ WebDAV（`/dav/`，PROPFIND/GET/PUT/MKCOL/MOVE/COPY/DELETE/LOCK；Basic 认证复用 scrypt + 防爆破；DELETE 进回收站）
2. ✅ 照片时间线（photos 表增量扫描，EXIF DateTimeOriginal 优先，前端"照片"标签页网格 + 按日期分组 + 加载更多）
3. ➖ 每日自动备份（用户明确不做）
4. ➖ Windows 托盘小工具（未列入本轮）

## 5. 里程碑验收

| 里程碑 | 验收标准 |
|--------|----------|
| M1 | 重启后登录态/分享不丢；未登录访问任意 API 均 401；错误密码 5 次锁 15 分钟；`/files/*` 未登录 401；分享密码不出现在任何日志 |
| M2 | 500MB 上传断点续传成功；重复文件秒传；删除可还原 |
| M3 | 手机扫码→提取码→下载全流程通；分享可撤销立即生效 |

## 6. 技术栈

FastAPI + uvicorn、Caddy v2（原生，无插件）、SQLite（标准库）、原生 HTML/CSS/JS、Pillow、qrcode。
密码哈希用标准库 scrypt，**不新增任何 pip 依赖**，保持"双击能跑"。
