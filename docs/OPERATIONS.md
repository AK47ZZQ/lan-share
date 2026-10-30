# LAN Share 运维手册（OPERATIONS）

> 面向日常维护：服务管理、数据库、备份恢复、证书、网络、常见故障。
> 架构背景见 [项目记忆/01-架构与现状.md](_internal/项目记忆/01-架构与现状.md)；
> 命令速查见根目录 [README.md](../README.md) 的快速开始一节。

## 1. 服务管理（NSSM 三服务）

| 服务名 | 内容 | 监听 |
|---|---|---|
| `lan-share-api` | `server.py`（uvicorn，FastAPI） | `127.0.0.1:8766`（仅本机，Caddy 反代） |

> **端口/python 解释器改一处生效**：仓库根 `env.bat` 是部署单一事实源
> （07 批次5），start.bat/stop.bat/install-service.bat（2026-09-12 C1 批补
> AppRotateOnline 并对齐活服务 NSSM 配置）/backup.bat（2026-09-12 C4 批收敛
> 到 env.bat + %~dp0，E2E 实跑通过）已全部接线；改端口或 venv 路径只动它。
> 仅剩 Caddyfile 的同值归位——Caddyfile 不吃 bat 变量、需 {$env} 占位符改造
> 且动 TLS/反代配置须窗口验证，评估风险>收益暂缓。
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

公网入口手工探活（**每日 12:00 只读巡检** ZCode 定时任务，2026-10-24 重建——
原 01:00 cron 巡检 09-19 起静默停摆已废弃；新巡检不 import app/、不锁文件，
异常落 `logs/patrol/ALERT-*.md`；以下为手工排查用）：
主隧道 `curl -sk -o /dev/null -w "%{http_code}" https://your-frp-domain.com:xxxxx/healthz`
**期望 404**（healthz 对公网收敛是设计行为，404=隧道通且 Host 匹配生效）；
备用隧道 `https://your-frp-domain.com:xxxxx/healthz` 同判；局域网基线
`https://192.168.1.100:8765/healthz` 期望 200。公网探针非 404 时：
`powershell Get-Service SakuraFrpService` + 查看 `C:\ProgramData\SakuraFrpService\Logs\`
最新日志尾部；恢复后按 settings 表 `notify_webhook_url` 的 {title}/{body} 模板发告警。

**限速观测**（v3.69）：`/api/metrics` 的 `requests.rate_rejects` 是桶级限速拒绝的
**进程启动累计**计数（配 `uptime_seconds` 解读，不随 1h 窗口滑动）——家人报"某功能
突然加载失败"时先看这里：对应桶有读数即为限速命中，据此调 `app/config.py` 桶阈值。

**8.3 短名（2026-09-28 已执行治本）**：`fsutil 8dot3name query C:` 应显示"卷状态 1
（已禁用）"；回滚 `fsutil 8dot3name set C: 0`。应用层另有别名段拦截（safe_path/
_clean_parts 双闸），存量别名（TRASH~1 等）留在盘上但不可经 API 寻址。

**运行指标**（v3.52）：`/api/metrics`（admin）给近 1h 请求状态码分布与
p50/p95/avg 延迟（asgi 中间件逐请求采集，环形缓冲 2048）、磁盘已用百分比、
兜底备份最近一轮状态。磁盘已用 ≥85%（`DISK_WATERMARK_PCT`）自动 webhook
告警，6 小时冷却（`ALERT_COOLDOWN`）防轰炸；恢复不复位、冷却过后再次越线
才二次告警。

**入口分账速查**：Caddy 反代使所有请求 client_ip=127.0.0.1——防爆破、登录限速、
API 限速（api:lan 120 / api:pub 240）全部按 Host 分账；改公网地址走管理面板
「公网访问地址」主/备两栏，`reset_netloc_cache()` 即时生效。

## 2. 健康与水位

```bash
curl -k https://192.168.1.100:8765/healthz
# 返回进程存活 + DB 连通 + 数据目录可写 + 磁盘水位
```

- 磁盘水位在 `GET /api/system/stats`（管理端）也有；App 首页"服务端存储"卡
  同源。备份前确认目标盘空间 ≥ 数据目录。

## 3. 数据库（PostgreSQL 16）

- 连接：`127.0.0.1:5433`，库 `lanshare`，用户 `lanshare`，密码在
  `C:\lan-share\env\pginstall\pgpass.conf`（2026-09-21 迁入项目 env/，gitignored，
  不在任何仓库文件里）。
- 数据目录：`C:\lan-share\env\pginstall\data`（2026-09-21 迁入项目）。
- **版本错配备忘（2026-10-24 实证）**：服务器是 zonky 的 **PG 16.4**
  （`env\pginstall\zonky\bin`，服务端最小集——无 psql/pg_restore），
  客户端工具集（`env\pgsql-bin`）是 **17.4**。dump 由 17.4 的 pg_dump 产出、
  恢复进 16.x 会报一条 `transaction_timeout` 未知参数——可忽略，数据完整。
  上面的手工备份/恢复命令里 `C:\env\pgsql\bin` 是**迁移前旧路径**，
  现一律用 `C:\lan-share\env\pgsql-bin`。
- 注意：`postgresql.conf` 的 `log_directory` 必须用**新绝对路径**
  `C:/lan-share/env/pginstall/logs`——曾因沿旧路径 `C:/pginstall/logs` 导致服务起不来。
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

## 4. 文件备份与恢复演练（backup.bat + restore-drill.bat）

> ⛔ **2026-09-13 起：外部主备份已暂停（主人决策）。**
> 计划任务 `lan-share-backup` **已禁用**（定义保留未删），等**第二块 SSD 到位后**
> 再恢复——届时先补一次全量备份，再启用任务，并跑一次 restore-drill 验证。
> `C:\lan-share-backup\files\`（7499 文件 / 20.1GB 旧快照）**已按主人确认删除**；
> 保留 `db\`（PG dump）、`files-manifest.txt`、`pre-v35-20260829`。
> 磁盘已用 **88% → 80%**（+21G，含回收站清空；主人手动完成终清）。
>
> **恢复备份（第二块盘到位后）**：
> ```bat
> rem 本机 schtasks.exe 被安全策略屏蔽，用仓库脚本（走 COM）
> C:\Users\<你的用户名>\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe ^
>   C:\lan-share\scripts\_ops_toggle_backup_task.py enable
> ```
> 任务定义与删除前的完整文件清单保存在部署本地的审计目录（未随仓库发布）。
>
> ⚠️ **停机期间无异地备份**（知情决策）。另注意 `db\lanshare-20260911.dump` 是
> **0 字节空档**，最新有效 dump 为 `lanshare-20260912.dump`。
> ℹ️ 本手册 §4.0 的**进程内兜底备份（B5）未停**——它是独立第二条链。

> v3.31 调整（改进建议 9.6-P2-4）：`robocopy` 加 `/MT:2`（默认 8 线程会把
> 磁盘 IO 吃满，备份窗口内 Web/App 列表与缩略图明显变慢）；`pg_dump` 加
> `--lock-wait-timeout=5000`（拿不到锁 5 秒放弃而非挂死；ACCESS SHARE 不阻塞
> 业务写，正常瞬时获得）。
>
> **v3.35 新增**：文件数据侧 **manifest 校验**（补上"robocopy 增量且不删，
> 跑残了看不出来"的盲区）。

- 计划任务（备份）：`schtasks /Create /TN "lan-share-backup" /SC DAILY /ST 23:30
  /TR "C:\lan-share\backup.bat"`（已注册；**2026-09-13 起 Disabled**）。
  > 时间口径核对（2026-09-10）：README/HANDOFF 写 23:30，而 `backup.bat` 注释与本
  > 手册原写 03:30——**已按实测统一为 23:30**（备份产物 mtime 为 23:30 / 00:33，
  > 与 23:30 吻合）。如需确认，`schtasks /Query /TN "lan-share-backup" /V /FO LIST`，
  > 四处（README / HANDOFF / 本手册 / backup.bat 注释）一并改。
- 行为：PG custom-format dump（按日期，保留最近 2 天）+ 数据目录拷贝到
  `BACKUP_DEST`（默认 `C:\lan-share-backup`，可用环境变量覆盖）。
- **产物清单**：`db\lanshare-YYYYMMDD.dump`（+ `.sha256` sidecar）、`files\`
  （数据目录增量副本）、`files-manifest.txt`（文件数 + 总字节，v3.35 新增）。
- ⚠️ **删除"备份"前必须先验证它是否含独有内容**：2026-09-13 实测发现
  `files\` 里有 5 个目录（Hermes快传/apk/简历/logo图片/手机上传）在项目
  `data\` 中**并不存在**，且 `照片\` 远大于项目侧——备份**不是**纯副本。
  一律先 `ls` 对比两侧、确认无误再删。

### 4.0 进程内每日兜底备份（v3.52，`app/services/backup.py`）

外部计划任务已 **Disabled（2026-09-13 主人决策）**——**进程内兜底备份（本节）
现在是当前唯一的自动元数据备份链**；v3.52 起服务进程内每日运行：清理循环
每小时醒一次，过 `BACKUP_HOUR`（默认 4 点）后的首轮把元数据库 dump（SQLite
在线 backup API / PG 走 pg_dump）+ settings 全量 JSON 快照写到
`SHARE_ROOT/.backups/`，滚动保留 `BACKUP_KEEP=7` 组；状态存 settings
`backup_last_status`（healthreport `backup.orchestrator` 与 `/api/metrics`
可见）。失败走 webhook 告警（与水位告警同通道）。两道备份互相独立：
外部任务失效时兜底仍每日产出，恢复演练仍以上节产物为准。

**dump 内联验证（2026-09-12 功能批）**：兜底 dump 落盘后立即做引擎恰当的
完整性校验——SQLite `PRAGMA integrity_check` + settings 表可读；PG 走
`-Fc` custom 格式（与外部主备份格式一致）+ `pg_restore -l` 只读归档目录。
验证失败按备份失败处理（webhook 告警上门、当日幂等位不写，次日自然重试）；
校验工具缺失呈现 `verified=None`（不假装通过也不误报），healthreport 日报
与 `/api/metrics` 均可见。动机：9-11 晚外部任务被杀留 0 字节 dump 无告警的
同构风险——"生成成功"不等于"产物有效"，最后防线自身必须有验证。注意
`integrity_check`/`pg_restore -l` 只证明结构完整，不证明业务数据正确，
每月 Tier 2 全量恢复演练仍不可替代。

- 兜底 dump 的应急恢复（PG custom 格式；`--clean` 恢复前先停 api 服务）：
  ```bat
  C:\env\pgsql\bin\pg_restore.exe -h 127.0.0.1 -p 5433 -U lanshare -d lanshare --clean ^
    C:\lan-share\.backups\meta-YYYYMMDD-HHMMSS.db
  ```
  SQLite 引擎部署（无 DATABASE_URL）时 `.backups\meta-*.db` 即库文件本体，
  停服后直接改名替换 `SHARE_ROOT\.meta.db`。

### 4.1 恢复演练（restore-drill.bat）

**Tier 1 — 只读，安全，可定期自动跑**（`scripts\restore-drill.bat`）：
1. 取 `%BACKUP_DEST%\db` 里最新的 dump；
2. 校验 sha256 与 `.sha256` sidecar 是否一致（**sidecar 是大写、`sha256sum`
   是小写，比对须忽略大小写**）；
3. `pg_restore -l` 读一遍归档（截断/损坏的 dump 会在这里失败）；
4. **比对 `files-manifest.txt` 与实际文件数/总字节**（v3.35 新增）。

建议每周日 04:00 跑（**需主人执行**——本机 `schtasks` 受安全策略限制，
AI 无法代跑）：
```bat
schtasks /Create /TN "lan-share-restore-drill" /SC WEEKLY /D SUN /ST 04:00 ^
  /TR "C:\lan-share\scripts\restore-drill.bat"
```

**Tier 2 — 全量恢复，手工，需一次性开权限**：
`lanshare` 角色默认无 `CREATEDB`，所以要先（二选一）：
```sql
ALTER ROLE lanshare CREATEDB;            -- 一次性
```
然后按 `restore-drill.bat` 末尾的步骤：加一条 pgpass → `createdb lanshare_drill`
→ `pg_restore -d lanshare_drill <dump>` → 抽查 `users`/`photos`/`shares` 行数 →
演练完 `DROP DATABASE lanshare_drill`。

> **每月至少做一次 Tier 2。备份没恢复过等于没备份。**
> 2026-09-10 复核：Tier 1 已实测通过（最新 dump sha256 一致、归档可读、
> **16 张表**齐全、manifest 基线 7454 文件 / 20,147,257,165 字节）。
> 另注：dump 中仍含已下线的 `photo_comments` / `photo_reactions` 两表
> （v0.21 下线但存量表未 DROP），可在下次大版本迁移时清理。
>
> **2026-10-24 首次执行 Tier 2（临时集群法，未开 CREATEDB）**：
> `env\pginstall\zonky\bin\initdb.exe` 起一次性集群（`-U lanshare --auth=trust`
> 仅绑 127.0.0.1:5434，存活约 6 分钟）→ `env\pgsql-bin\createdb/pg_restore`
> 恢复当日 dump → 行数与生产逐项一致（users=3/photos=5352/settings=10/
> 22 表）→ `pg_ctl stop` + 删目录 + 端口/生产库复核。此法额外证明「dump
> 能在全新实例拉起」，可作月度演练默认路径。两条噪音/坑：① pg_dump 17.4
> 产的 dump 恢复进 16.4 报 1 条 `transaction_timeout` 未知参数（可忽略，
> 数据完整）；② 交互式 Git Bash 下 `pg_ctl -w start` 可能不返回（服务器
> 其实已起）——免 `-w` 启动后按端口轮询判断；后台任务被强杀会把"分离的"
> postgres 一并带走，演练请用一次性前台脚本跑完。

- 异地/第二块盘备份：**仍挂账**（第二块盘到位后连同 Tier 2 一起落地）。

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
  已关闭 frpc「自动HTTPS」，现为**纯 TCP 端到端透传** → Caddy（:8801/8765 同一套规则）——
  公网段出示的正是本机自签证书（指纹 `D3:7A:…:B4:D3`，与主隧道逐位一致）。
  因此 App 已信任主隧道后切备用**静默自动信任、零弹框**（2026-09-03 模拟器实测）。
  `public_base_url_alt` 已入库，`is_public_entry()` 主/备双 Host 同时命中
  （防爆破分账、healthz 收敛对备用入口同样生效，测试 test_public_alt.py）。
  若未来在面板改回「自动HTTPS」：公网段会换回樱花自动证书，App 需重新信任一次（指纹不同源）。
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
  （判期权威 = 行侧 `deleted_at`，v3.67.1 P0 起——文件 mtime 保留原始值不改写，
  分片会话可用 `DELETE /api/upload/sessions/{sid}` 立即释放）
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

- App / Web / 服务端发版细节：见 [CHANGELOG.md](../CHANGELOG.md) 对应版本条目。
- 每次发版后：更新 `CHANGELOG.md` 与本文档基线数字
  同步（三件套之外的第 4 件：文档）。
