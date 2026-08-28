# LAN Share — 个人网盘

单管理员自用网盘。局域网与公网（樱花FRP）功能完全对等，统一应用层认证；
支持断点续传、秒传去重、回收站、在线预览、照片时间线、分享外链、手机直传和 WebDAV 挂载。

```
快速入口
├── 快速开始 .......... 本文 §快速开始
├── 完整 API .......... docs/API.md
├── 安全模型 .......... docs/SECURITY.md
├── 开发指南 .......... docs/DEVELOPMENT.md
├── 版本历史 .......... CHANGELOG.md
└── 演进规划 .......... PLAN.md
```

## 快速开始

```bash
# 生产模式：NSSM 注册的 Windows 服务常驻（开机自启）
net start lan-share-api && net start lan-share-caddy
net stop  lan-share-caddy && net stop  lan-share-api   # 停止（先 caddy 后 api）

# 开发调试（仅 FastAPI，端口 8766，无 TLS）
python server.py
# 或指定数据目录
SHARE_ROOT=D:/test-data python -m uvicorn server:app --port 8768
```

| 入口 | 地址 | 认证 |
|------|------|------|
| 局域网 | `https://192.168.1.100:8765` | 应用层登录 |
| 公网（樱花FRP） | `https://your-frp-domain.com:xxxxx`（→ Caddy :8767） | basic_auth 第一道门 + 应用层登录 |
| WebDAV | `https://192.168.1.100:8765/dav/` | Basic（管理员账号） |

> 自签证书：浏览器首次访问点"继续访问"即可；首次进入会引导创建管理员账号。

## 功能总览

**文件管理**：目录浏览、排序/筛选、搜索、最近文件、新建/重命名/移动语义的批量操作、
多选打包 zip 下载（一次性 URL）。

**上传**：小文件直传；大文件自动 5MB 分块 + **断点续传**（每块自动重试 3 次）+
≤100MB 客户端预计算 SHA256 的**秒传**；服务端合并时强制校验哈希。
相同内容的文件以 **NTFS 硬链接去重**，多份引用只占一份磁盘。

**回收站**：删除（界面与 WebDAV 一致）移入 `.trash/` 保留 30 天，可恢复/单项删/清空，到期自动清理；
覆盖写、移动覆盖的旧版本同样进回收站。

**分享**：链接 + QR 码，可设有效期 / 提取码 / **下载次数上限**；可撤销、可查取件记录（含来源 IP）；
提取码错误 5 次锁 15 分钟；密码走 POST body 不进访问日志。

**预览**：图片/视频/音频/PDF/文本内嵌预览，音视频支持 Range 拖动；图片与视频自动生成缩略图并缓存。

**照片时间线**：按 EXIF 拍摄日期（缺失回退文件时间）倒序的网格视图，增量扫描 + SQLite 缓存。

**手机直传**：主界面 📲 出二维码，手机扫码打开 `/up` 直传 `incoming/`（复用分块续传/秒传）。

**WebDAV**：`/dav/` 支持 PROPFIND/GET/PUT/MKCOL/MOVE/COPY/DELETE/LOCK，可挂载为网络磁盘
（推荐 RaiDrive / WinSCP）；DELETE 与覆盖行为同界面一样进回收站。

## 架构

```
外网设备 ──樱花FRP──▶ Caddy :8767 (HTTPS, basic_auth第一道门*, 剥离入站XFF)
内网设备 ──局域网──▶ Caddy :8765 (HTTPS)
                        │
                        ├─ /files/*  → forward_auth(/authcheck) 后 file_server 静态直连
                        │               （大文件下载不经过 Python）
                        ├─ /dav/* /api/* /s/* /up
                        │             → FastAPI :8766 (127.0.0.1)
                        │                ├─ SQLite data/.meta.db（用户/会话/分享/审计/blob指纹/照片）
                        │                ├─ 数据目录 data/（.trash .thumbs .chunks .zips 自动管理）
                        │                └─ 应用层限速 / CSRF / 防爆破 / 审计
                        └─ /static/*  → 前端（原生 HTML/CSS/JS，CSP: script-src 'self'）
```

\* basic_auth 豁免 `/s/*` 与 `/api/share/*`，保证分享收件人可用。

**技术栈**：Caddy v2.11（原生无插件）、FastAPI + uvicorn、SQLite（标准库）、
原生 HTML/CSS/JS（事件委托，零构建）、Pillow、qrcode、ffmpeg（可选，视频缩略图）。
密码哈希用标准库 scrypt——**除既有依赖外不引入任何新 pip 包**。

## 目录结构

```
lan-share/                  ← 本目录（C:\lan-share 是指向这里的 junction）
├── server.py               FastAPI 后端（全部后端逻辑）
├── Caddyfile               反代/TLS/forward_auth/basic_auth
├── static/                 前端（index/login/up/share 四页 + 同名 .js + favicon.svg）
├── docs/                   API / SECURITY / DEVELOPMENT 文档
├── backup/v1/              v1 备份与回滚说明
├── PLAN.md                 演进规划（P0-P3 全部完成）
├── CHANGELOG.md            版本历史
├── install-service.bat     NSSM 注册服务
├── start.bat / stop.bat / start-silent.vbs
└── logs/                   服务日志（NSSM 轮转）

C:/lan-share/data/          ← 文件数据根（环境变量 SHARE_ROOT 可改）
├── apk/ documents/ media/ tmp/ incoming/ 照片/   ← 用户文件
├── .meta.db                SQLite 元数据（隐藏，勿手删）
└── .trash/ .thumbs/ .chunks/ .zips/              ← 回收站/缩略图缓存/分块临时/打包临时
```

## 常用运维

```bash
# 改了 server.py 后
net stop lan-share-api && net start lan-share-api
# 改了 Caddyfile 后（静态文件 static/* 不需要重启）
net stop lan-share-caddy && net start lan-share-caddy

# 查日志
tail -f logs/api-stderr.log logs/caddy-stderr.log
```

### 常见问题

| 问题 | 处理 |
|------|------|
| 浏览器提示证书不受信任 | 自签证书所致，点"继续访问"；或把 `certs/cert.pem` 导入系统"受信任的根证书颁发机构" |
| 忘记管理员密码 | 删除 `data/.meta.db` 后重启服务，重新走初始化（注意：会同时清掉分享与审计记录） |
| 公网上传慢 | FRP 隧道带宽有限，大文件走局域网；中断后重新选同一文件会自动续传 |
| Windows 映射网络驱动器失败 | 原生 WebClient 对自签证书苛刻，改用 RaiDrive/WinSCP 或先导入证书 |
| 改了前端没生效 | Ctrl+F5 强刷；前端静态文件无需重启服务 |

## 路线图状态

P0 安全地基 ✅ · P1 传输可靠性 ✅ · P2 分享与移动端 ✅ · P3 WebDAV/照片时间线 ✅。
未做：每日自动备份（明确不做）、系统托盘小工具（暂缓）。
