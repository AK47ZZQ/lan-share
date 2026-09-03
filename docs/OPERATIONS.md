# LAN Share 运维手册（OPERATIONS）

> 面向日常维护：服务管理、数据库、备份恢复、证书、网络、常见故障。
> 架构背景见 `项目记忆/01-架构与现状.md`；交接视角见根目录 `HANDOVER.md`。

## 1. 服务管理（NSSM 三服务）

| 服务名 | 内容 | 监听 |
|---|---|---|
| `lan-share-api` | `server.py`（uvicorn，FastAPI） | `127.0.0.1:8766`（仅本机，Caddy 反代） |
| `lan-share-caddy` | `caddy.exe`（仓库根 Caddyfile） | `:8765/:8767/:443`（TLS）+ `:80/:8080`（308 跳转） |
| `lan-share-postgresql` | PostgreSQL 16 | `127.0.0.1:5433` |

```powershell
# 顺序：停 → 先 caddy 后 api；起 → 先 api 后 caddy
net stop lan-share-caddy;  net stop lan-share-api
net start lan-share-api;   net start lan-share-caddy
# 或
powershell Restart-Service lan-share-api
# ⚠ Git Bash 里 `sc start/stop` 会静默失败（不报错也没生效）——用 PowerShell。
```

- `DATABASE_URL` 配在 lan-share-api 服务的环境变量（NSSM）里；不设则回退
  SQLite `SHARE_ROOT/.meta.db`（仅应急/测试）。
- 日志：`C:\lan-share\logs\`；NSSM 自身输出亦重定向于此。

## 2. 健康与水位

```bash
curl -k https://192.168.1.100:8765/healthz
# 返回进程存活 + DB 连通 + 数据目录可写 + 磁盘水位
```

- 磁盘水位在 `GET /api/system/stats`（管理端）也有；App 首页"服务端存储"卡
  同源。备份前确认目标盘空间 ≥ 数据目录。

## 3. 数据库（PostgreSQL 16）

- 连接：`127.0.0.1:5433`，库 `lanshare`，用户 `lanshare`，密码在
  `C:\pginstall\pgpass.conf`（不在任何仓库文件里）。
- 数据目录：`C:\pginstall\data`。
- 手工备份：
  ```bat
  C:\env\pgsql\bin\pg_dump.exe -h 127.0.0.1 -p 5433 -U lanshare -d lanshare ^
    -Fc -f C:\lan-share-backup\db\lanshare-manual.dump
  ```
- 恢复（应急）：
  ```bat
  C:\env\pgsql\bin\pg_restore.exe -h 127.0.0.1 -p 5433 -U lanshare -d lanshare --clean ^
    C:\lan-share-backup\db\lanshare-YYYYMMDD.dump
  ```
- 回退 SQLite（仅应急）：移除服务环境变量 `DATABASE_URL` 后重启 api——
  使用 `SHARE_ROOT\.meta.db`（内容可能陈旧，慎用）。
- 迁移规范：所有 DDL 走三引擎迁移层（PG/SQLite/MySQL 兼容），新加列必须
  三份方言都写。

## 4. 文件备份（backup.bat）

- 计划任务：`schtasks /Create /TN "lan-share-backup" /SC DAILY /ST 03:30
  /TR "C:\lan-share\backup.bat"`（已注册）。
- 行为：PG custom-format dump（按日期，保留最近 2 天）+ 数据目录拷贝到
  `BACKUP_DEST`（默认 `C:\lan-share-backup`，可用环境变量覆盖）。
- **每月做一次恢复演练**：备份目录 → 空 data 目录 → 起服务 → healthz +
  抽查文件可下载。备份没恢复过等于没备份。

## 5. 证书与信任（TLS）

- 证书：`C:\lan-share\certs\cert.pem + key.pem`（自签），Caddyfile 引用。
- 客户端信任模型 = **TOFU**（首连弹"信任此服务器"，指纹入库）：
  - App：重装/`pm clear` 会重置信任（重新弹一次确认框，预期行为）；
  - 备份引擎遇证书变更不擅自信任 → 发"已暂停"通知，进 App 确认（0.8.14 通知图标可见）。
- 换证书 = 所有客户端重新 TOFU 一次，选在家人方便的时间做。
- 备份通知/进度通知小图标 = `drawable-*/ic_notification.png`（白云剪影），
  生成源同 `scripts/make-app-icon.py`。

## 6. 网络

- 局域网入口 `https://192.168.1.100:8765`（主机已设静态 IP）。
  **建议路由器侧再做 DHCP 静态绑定**（双保险；主机换网卡后需同步更新）。
- 公网：樱花FRP 隧道 → 与局域网同一 Caddy 入口，功能完全对等；
  管理面板「公网访问地址」`public_base_url` 决定分享/二维码里的公网地址，
  也是 `is_public_entry()` 的 Host 匹配依据（改地址后立即生效，见 app/security.py）。
  **2026-09-03 全链路 E2E 实测通过**（App TOFU→登录→缩略图→上传→分享→取件，指纹比对一致）。
- 樱花FRP 落地事实：启动器 `SakuraFrpService`（NSSM 式自启服务，v3.1.8）；
  配置/日志在 `C:\ProgramData\SakuraFrpService\`；隧道增删改在樱花FRP 网页面板或启动器。
  节点为 anycast（当前落地 223.85.20.43，会变）——一律用 `your-frp-domain.com` 域名，勿写死 IP。
- **主隧道 LANShare**（`https://your-frp-domain.com:xxxxx`）：纯 TCP 透传 → `127.0.0.1:8765`（Caddy），
  端到端 TLS=本机自签证书，App TOFU 指纹 `D3:7A:…:B4:D3`。
- **备用隧道**（`https://your-frp-domain.com:xxxxx`，2026-09-03 启用）：原 HermesAgent 改造。
  frpc「自动HTTPS」探测到 Caddy 已直接监听 `:8801`，自动切到 **HTTPS 反代模式**——
  公网段证书为樱花自动签发（与主隧道不同源），本地段仍是 Caddy 同一套规则。
  两个推论：① App/浏览器首连备用入口会弹一次证书确认（指纹不同，安全上理应如此）；
  ② `public_base_url_alt` 已入库，`is_public_entry()` 主/备双 Host 同时命中
  （防爆破分账、healthz 收敛对备用入口同样生效，测试 test_public_alt.py）。
  若想要与主隧道同级的端到端透传：在面板把备用隧道改为纯 TCP→8765 即可，App 侧无需改动。
- 免费档限额（见启动器日志）：**10 Mbps**/隧道、**2 条**隧道、**5 GiB/月**流量
  （每月 1 日重置，签到可累加）。公网侧定位=出门在外应急浏览/小文件，
  大备份与大文件传输走局域网（App 备份公网默认跳过、网页公网上传有提醒，均为既有设计）。
- 公网探活巡检：`curl -sk -o /dev/null -w "%{http_code}" https://your-frp-domain.com:xxxxx/healthz`
  正常应返回 **404**（healthz 对公网收敛是设计行为，404 恰恰证明 Host 匹配生效；
  连不上=隧道/节点故障，去启动器日志看 frpc 重连）。
- 环境坑：本机模拟器/虚拟机内 DNS 可能整体失效（宿主 Tailscale MagicDNS 劫持所致，
  TCP 正常仅解析坏）——Android 模拟器用 Private DNS 解决：
  `settings put global private_dns_mode hostname` +
  `settings put global private_dns_specifier dns.alidns.com`。真机无此问题。
- 遗留噪音：另一条隧道 **HermesAgent** 指向 `127.0.0.1:8801`（无服务监听），
  启动器日志持续报连接失败；是主人另一项目占用的免费档第 2 条隧道，勿动。
- 端口一览：80/8080 跳转；8765 主入口；8767/443 备用同规则；8766 仅本机。

## 7. 存储治理

- 回收站 `.trash`：App/网页可清空（admin 可清全员）；服务端有清理循环
  （`cleanup_loop`，异常容错 + 每轮 try/except）。
- `.chunks`（分片暂存）、`.zips`（打包临时）：服务端定期清理；
  若发现异常膨胀先查 `logs/` 再手工清。
- `.thumbs`（缩略图）：可整体删除重建（内容寻址，自动重生成）。
- 磁盘预警：App 首页"待备份 > 剩余空间"有提醒；备份前看一眼。

## 8. 常见故障速查

| 症状 | 先查 | 处理 |
|---|---|---|
| 全站 502 | `powershell Get-Service lan-share-*` | api 停了 → Restart-Service；再查 `logs/` |
| healthz 200 但登录失败 | 是否触发防爆破（5 次锁 15 分钟） | 等 15 分钟或重启 api（内存限速表） |
| 手机 App 连不上 | 预检红点 | 主机 IP 是否变了（DHCP）→ 改服务器地址；证书信任重置 |
| 大文件 WebDAV 413 | Caddyfile `max_size` | 确保 ≥ 服务端上限（现 25GB/20GB） |
| 某类文件 500 | `logs/` 最新 traceback | 中文文件名/特殊字符类已修；新形态按栈定位 |
| 照片不进时间线 | 上传落位目录是否符合 `照片\|视频/<用户名>/...` | 服务端 `_photo_uploader` 语义 + 重启服务 |
| 备份不跑 | App 设置页**备份诊断卡**（网络/电源/调度/最近一轮） | 计费网络+仅 Wi-Fi 会停；未插电不跑自动备份（手动可跑） |
| Web 改动不生效 | dist 是否重建；SW 缓存 | `npm run build`；/static 下换内容要换名或升 CACHE |
| 通知图标是灰块 | 资源名混淆是否被重新打开 | gradle.properties `enableResourceOptimizations=false` |

## 9. 升级发布节奏（引用）

- App / Web / 服务端发版细节：`项目记忆/04-发版SOP.md`。
- 每次发版后：`CHANGELOG.md` + `docs/STATUS-v3.md` 基线数字 + `项目记忆/`
  同步（三件套之外的第 4 件：文档）。
