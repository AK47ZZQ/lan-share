# 安全模型

> 目标场景（v3）：家庭/小团队多用户（2–20 人），公网入口（樱花FRP 隧道）与
> 局域网功能完全对等。
> 威胁假设：公网扫描器与爆破者可见全部入口；家庭成员之间需要隔离（防误删、
> 防越权看文件）而非对抗性防备；访客账号是最低信任层。

## 认证与会话

- **账号**：账号存元数据库 `users` 表（三角色，见下文授权模型），密码 scrypt（N=32768, r=8, p=1, 32 字节盐）。
- **TOTP 两步验证（v3.5，可选启用）**：账号设置里自助开通（pyotp，RFC 6238，±1 窗口）。
  已启用账号登录分两步：密码正确只返回 `totp_required` 不发会话；密码+6 位动态码经
  `/api/login/totp` 换会话——动态码错误同样计入按账号防爆破（5 次锁 15 分钟），防在线猜码。
  **动态码防重放（2026-09-02）**：命中窗口记录进 `users.totp_last_step`，同一步
  （30s）或更早步的码重复提交一律拒绝；开通/关闭/管理端解除时清零。
  关闭 TOTP 需原密码；admin 重置成员密码会同步清除其 TOTP（手机丢失恢复通道）。
  ⚠️ 取舍：WebDAV Basic 维持仅密码（客户端协议不支持动态码）——TOTP 防的是浏览器密码
  泄露与爆破，WebDAV 凭据泄露风险自担（家庭场景可接受）。
- **事件推送 Webhook（09-01 批次）**：admin 配置的 GET 模板 URL，站外推送
  定向分享/新成员加入事件。⚠️ 取舍：不限制目标是否内网——封 RFC1918 会挡掉
  自托管 ntfy 等局域网推送主用例；配置面仅 admin 可达（与改 Caddyfile 同级
  信任），URL 模板占位符经 URL 编码替换、无响应体回注。
- **成员视角预览（09-01 批次）**：admin 在四个只读接口带 X-As-User 以成员身份
  解析可见性。⚠️ 边界：仅影响"能看到什么"，写操作仍按 admin 真实会话执行；
  成员传该头无效；命中记 `preview_as` 审计。
- **设备 Token（App 专用，M-A0）**：登录会话换长效 `Authorization: Bearer`
  Token，90 天滑动过期、库内只存 SHA256、明文仅签发时返回一次；单账号上限
  20 台、签发限速 20 次/时；同名设备重复签发**原地轮换**（重装/重复登录不产生僵尸设备，
  旧凭据立即失效）；用户可在 App/网页吊销（审计留痕），停用账号即刻失效
  （JOIN users 过滤）。浏览器不自动携带 Authorization 头，无 CSRF 面；
  与 Cookie 会话并存（require_user 先 Cookie 后 Bearer）。Token 响应附
  `Cache-Control: no-store` 防中间缓存。
  **吊销语义（2026-09-02 补齐）**：自助改密、admin 重置密码、admin 停用账号
  三条路径都会同时删除该账号全部 device_tokens——设备 Token 90 天滑动有效期
  不再成为"改密踢端"的旁路（review 2026-09-02 P1-2）；停用后重新启用也不会
  让旧 Token 复活，需重新登录。过期 Token 由每小时清理任务删除。
- **WebDAV 独立密码（v3.7，可选）**：账号设置里自助设置与登录密码无关的
  WebDAV 专用凭据（scrypt 同强度）。设置后 DAV 入口只认独立密码、登录密码
  对 `/dav/` 失效——凭据泄露面隔离，可单独轮换而不动主密码/不解锁 TOTP 账号；
  admin 可重置/清除成员的独立密码（不影响其网页会话）。未设置时回退登录密码
  （兼容存量客户端配置）。改密失败同样计入防爆破锁定（与登录/改密同策略）。
- **会话**：32 字节随机 token 放 HttpOnly + SameSite=Lax + Secure Cookie；DB 只存 token 的 SHA256；
  30 天滑动过期（距上次刷新 >24h 才写库）；改密/登出即吊销。
- **登录防爆破**：按**账号**锁定——连续失败 5 次锁 15 分钟（FRP 隧道下所有公网请求的下游 IP 都是
  127.0.0.1，按 IP 锁无意义且会自锁）。WebDAV Basic 认证复用同一套锁定。
  锁定 key 统一小写化（大小写变体不能绕过）。改密/改用户名/WebDAV 独立密码
  的**当前密码确认失败**同样计入锁定（否则会话被劫持后可在限速窗口内无限试
  密码）。**所有路径按入口分账**（`is_public_entry`）：公网侧失败只锁公网侧、
  LAN 侧失败只锁 LAN 侧——公网攻击者挤占不了家人的 LAN 登录。改用户名的
  重名检查用 `LOWER()=LOWER()`（引擎中立：SQLite/PostgreSQL 行为一致，
  "admin"/"Admin" 不会共存），并与 UPDATE 走同一 `_db_lock` 防并发改名。
- **首次初始化**：`/api/setup` 仅在用户表为空时可用；线上已预建账号，该入口永久关闭。

## 网络层

- **TLS**：Caddy 终结 TLS（自签证书）；FRP 隧道为 SNI 透传，浏览器到 Caddy 全程加密。
- **统一应用层认证**：`:8765` 与 `:8767` 配置完全一致，不设 Caddy 层密码（2026-08-29 移除 basic_auth，
  避免浏览器弹窗与网盘登录双轨）；未登录访问由应用层 307 到 `/login`，公网扫描器/爆破由
  登录防爆破（按账号 5 次锁 15 分钟）+ 限速（登录 10/分）抵御，真实权限全部在应用层会话。
- **XFF 防伪造**：Caddy `request_header -X-Forwarded-For` 剥离外部传入的 XFF；
  后端 `client_ip()` 取 XFF **最后一个**值（自己 Caddy 追加的才可信）。限速分桶与审计 IP 均基于此。
- **端口暴露**：FastAPI 只绑 127.0.0.1；对外只有 Caddy 的 8765/8767。
- **公网入口判定与隔离（2026-08-31 加固）**：FRP 为 TCP 隧道，公网流量 client_ip 恒为
  127.0.0.1，内外网只能靠 Host 区分（`is_public_entry()`：Host 与管理面板公网地址的
  netloc 比对，60s 缓存、改地址即时失效）。据此：
   ① **防爆破按入口分账**——公网侧连续失败只锁公网侧（键 `user@pub`），LAN 家人登录
  永不因公网攻击被锁（远程 DoS 隔离）；LAN 登录成功也不解除公网侧锁定；
  ② 登录限速按入口分桶（`login@pub`），公网攻击流量挤占不了家人配额；
  ③ `/healthz` 公网入口 404（磁盘水位不外泄）；
  ④ **API 限速按入口分账（2026-09-03 弱网加固）**：Caddy 反代使内外网 client_ip
  均为 127.0.0.1，原先全家共享 120 次/分——现分 `api:lan`（120）/ `api:pub`（240）
  独立桶，公网翻照片墙不再把局域网家人挤成 429；429 一律带 `Retry-After: 30`。
- **已知结构性限制（家庭场景接受，记录在案）**：
  ① 公网入口为自签证书（Caddy desktop-hermes）；樱花 FRP 的"自动 TLS"证书亦为其私有
  CA（frpc 日志明确提示浏览器会告警），访客需点掉告警且理论上可被 MITM——根除需自有
  域名 + 公共可信证书；
  ② TCP 隧道拿不到访客真实 IP：公网用户共享限速桶、审计 IP 记为 127.0.0.1，无法按 IP
  封禁/溯源公网访客——需 FRP 代理类型（HTTP/HTTPS）才可传递真实 IP。

## 请求防护

- **CSRF**：SameSite=Lax + 中间件校验 Origin（存在时必须与 Host 同源）。
- **限速**（内存令牌桶，按类别分桶）：登录 10/分、分享取件 30/分、其它 API 120/分/IP；
  分块上传、authcheck、缩略图、预览不限（有认证保护）。
- **CSP**：所有页面 `script-src 'self'`（前端已全部外部化 + 事件委托，无任何内联处理器），
  `object-src 'none'`、`frame-ancestors 'none'`；样式保留 `unsafe-inline`（仅 CSS 注入风险，可接受）；
  `img-src` 含 `blob:`（docx 预览的内嵌图片由前端从文档流生成 blob URL，脚本仍被
  `script-src 'self'` 严格限制，风险不变）。
- **预览端点**：`/preview?w=` 的宽度量化到固定阶梯（320~3840 十档），登录用户无法用
  任意 w 值灌满缩略图缓存；`.thumbs` 缓存由定时任务回收 30 天未使用的档位；
  缩略图生成写临时文件唯一命名（并发请求不互相覆盖）。
- **Markdown/docx 前端渲染（取舍记录）**：Markdown 经 DOMPurify 消毒后渲染；
  docx 由 docx-preview 解析渲染——文本以 text node 插入、图片走 blob: URL，
  脚本无法执行（CSP 双保险），恶意文档的排版干扰仅影响预览者本人页面，家庭场景接受。
- **审计日志**：登录成败/锁定、上传（含秒传）、删除/恢复、分享创建/取件/撤销/密码错误、
  改密、WebDAV 写操作全记 `audit_log`（保留 1 万条），记录真实客户端 IP。

## 文件系统安全

- **路径边界**：`safe_path()` 拒绝 `..`、点前缀段（`.meta.db`/`.trash` 等不可经 API 访问），
  normcase + 路径分隔符边界校验；文件名经 `clean_filename()` 清洗——
  Windows 非法字符全集（`/ \ : * ? " < > |` 与控制符；手机端 Linux FS 可传来
  后者，漏洗会落盘 500）、NTFS 备用数据流冒号、Windows 保留名（con/aux/nul…）、
  结尾点/空格；WebDAV 各方法路径入口同样逐段清洗。
  **特殊段全量 normcase（2026-09-02 修复 P0-2）**：`resolve()`/`visible()`/
  `enforce_quota()`/授权表同步对 `private`/`incoming` 段一律 normcase 比较——
  Windows 大小写不敏感，`Private/<他人>/` 这类变体不再落入 shared 默认可读分支。
  **SQL 侧同源收紧（2026-09-20 F3）**：/api/photos 的可见性下推 SQL 用
  `LIKE 'private/%'` 判私人段——PG 的 LIKE 大小写敏感（SQLite 只折叠 ASCII），
  磁盘目录存在大小写变体时下推会在 PG 上放行他人 private；下推只是预过滤，
  纯内存的 `visible()` 无条件兜底（生产库只读审计当日 0 变体行）。
- **判权入口唯一性（2026-09-20 修复 D1·P1）**：`spaces.resolve()` 是"虚拟路径 →
  物理路径"的**唯一**判权入口，任何"为省一次调用而抄近路"的写法都会造出第二条
  权限通道。实例：WebDAV `dav_handler` 曾对空路径特判
  `SHARE_ROOT if need == "read"`，于是挂载根的**读**操作不经 resolve()——
  对 shared 无默认权限的 guest `PROPFIND /dav/` 直接 207，列出共享顶层全部
  条目名/大小/mtime；且"只授子目录"的 guest 顺带获得根列举能力
  （vkey `shared/` 不匹配 `shared/photos`，但当时根本没做这次匹配）。
  同批 F1 已修过同一格的**写**侧，读侧漏网。修法取减法：
  `resolve(user, "", need)` 对 member/admin 本就返回 SHARE_ROOT，特判冗余且危险。
  自查要点：见到 `if path else …` / `x or DEFAULT_PATH` 一类的"退化默认值"，
  必须问"这条捷径上还做不做判权"。
  回归：`tests/test_ablation_pins.py` 由消融实验驱动补钉
  （`python scripts/ablate-security-core.py` 会把 12 条安全不变量逐条改坏，
  报告测试有没有报警；存活项须复核分类后方可进 `EXPECTED_SURVIVORS`）。
- **`/files/*` 静态下载走应用层判权（2026-09-02 修复 P0-1）**：Caddy 对 `/files/*`
  只做 forward_auth 预检 + 反代，**不再直接 file_server 挂 SHARE_ROOT**——
  下载统一落在后端 `/files/{path}` 端点，经 `resolve()` 按 private/incoming/
  grants 判权；点目录（`.meta.db`/`.trash`/`.chunks` 等）由 `safe_path` 拒绝。
  安全要点（v3.57）：**真正判权的是逐条 `resolve()`，forward_auth 只是粗筛**——
  预检曾只认 Cookie，于是同一条 `/files/*` 路径对 Web 走"预检+判权"、对 App
  走"预检 401 后改道 `/preview/` 只有判权"。现预检与后端同等接受 Cookie 与
  Bearer 设备 Token（同一 `require_user`），消除"两套门控"的漂移面；
  放开预检不等于放宽权限：Bearer 在 `/files` 路由内本来就能过 `require_user`。
  App 仍走 `/preview/` 下载（少一次子请求 + 已与播放器缓存链路打通），
  迁移需真机重验下载/续传/暂停，本批不做。
- **上传完整性**：分块合并后强制 SHA256 + 大小校验，不符即拒；秒传只信任服务端计算过的指纹。
- **上传大小上限**：单请求 `/api/upload` 2GB；分片上传总大小硬上限 64GB（配额另约束）；
  WebDAV PUT 20GB（流式写盘）。分块大小按文件大小自适应（≤2GB→5MB / ≤5GB→16MB /
  ≤10GB→32MB / 更大→64MB），兼顾小文件请求数和大文件吞吐量。
  分块接收**超发即断**（2026-09-02）：收到字节数超过该块预期立即停止写盘并拒绝，
  不再"整块收完才比对"；单请求直传先写 `.uploading` 临时文件、完成后
  `os.replace` 原子落位（并发同名不再交错写坏）；复制（API 与 WebDAV COPY）
  进入私人空间前按目标体积计配额。
- **误删保护**：所有删除/覆盖路径（界面删除、WebDAV 删除/覆盖、移动覆盖、**上传覆盖**
  ——分片 finish/秒传/单请求三条路径）统一进回收站保留 30 天。
  **回收站归属判定（2026-09-02 修复 P1-1）**：删除登记物理相对路径；列表/彻底删除/
  恢复三个入口都按 `visible()` 判归属——成员无法枚举 trash id 把他人 private/incoming
  的回收站条目恢复进自己空间；历史 vpath 形态（`private:<名>`，无用户段）条目仅 admin
  可见可操作。
- **下载授权**：`/files/*` 由 Caddy `forward_auth` 预检后反代到后端鉴权端点
  （2026-09-02 起，不再静态直连）；分享下载走一次性票据
  （POST 验证密码后签发，10 分钟 TTL 可复用，密码不进 URL/日志）。
  **遗留分享（owner 为 NULL）仅 admin 可见/管理**，成员列表不可见、不可撤销/编辑。
- **移动防覆盖**：`/api/move` 重名目标用 `os.rename` 原子改名（Windows 下目标存在必抛错），
  没有"预检+覆盖"竞态窗口；单次 ≤500 项；隐藏路径被 `safe_path` 拒绝。
- **去重指纹自愈**：`blobs` 表登记硬链接源路径 `sample_path`；源文件被移动/删除后路径失效，
  下次同内容上传时 `register_blob` 检测到源缺失会自动登记新源（自愈），已建立的硬链接
  关系不受移动影响（rename 保留 inode）。

## 调试账号

- 存在第二账号 `debug`（与管理员同表同权限），供 AI Agent 调试接口使用；凭据在
  `docs/DEBUG-ACCOUNT.md`（已 gitignore，不入库）。
- 审计日志中 `debug` 的操作与主人天然可区分；`debug_account_created` 事件记录在案。
- 停用/轮换方法见该文档。若怀疑凭据泄露：直接删除该账号行并清其 sessions。

## 已知边界（如实记录）

1. ~~WebDAV LOCK 是假锁~~（11 号复审已实装进程内排他写锁，见 API.md）；残留边界：锁表在进程内（单 worker 部署成立）且仅显式 LOCK 的客户端受保护——不用锁的客户端并发写同一文件仍靠「旧版本进回收站」兜底。
2. 数据目录内若人工放入 junction/symlink，词法路径校验不阻止其指向外部（单管理员自担）。
3. **每日自动备份**（v3.5 起）：`backup.bat` 由 NSSM 计划任务 `lan-share-backup`
   每日 23:30 跑（机器 2 点自动关机，原 03:30 从不触发）；脚本：
   `pg_dump -Fc`（NSSM 环境变量里的 `PGPASSWORD` + `pgpass.conf`）+ 文件目录
   robocopy 增量（按 mtime）+ dump 保留 2 天自动清理。`StartWhenAvailable=true`
   错过开机补跑。脚本不向云端投递——**第二块异地备份盘仍待上线**（物理盘未到位）。
4. 管理员密码强度自担：系统只强制 ≥8 位，建议 12 位以上；泄露疑似时立即用 🔑 改密（会踢掉全部会话）。
5. 自签 HTTPS：浏览器首次访问点"继续访问"；客户端登录页会弹"信任此服务器"指纹确认框（App 走 SPKI pinning 落 Keystore）。
6. 事件推送 Webhook：admin 凭据可配置任意 http(s) URL（含内网/外网），占位符经 URL 编码替换，无内联注入面；admin 失守面等同可改 Caddyfile，信任边界一致。
7. **HEIC 解码**：上传体积受配额与 `MAX_UPLOAD_SIZE` 约束，但 PIL 解码后的内存不受直接限制。家庭自传为主，可接受；公网开放前应在 `filetools.ensure_heif_support()` 加 `Image.MAX_IMAGE_PIXELS` 显式上限。
8. **成员视角预览**（admin 专用）：`X-As-User` 头仅 admin 生效，以目标成员身份做可见性解析；写操作仍按真实会话身份执行（前端顶栏横幅明示），成员传该头无效；管理端点（`/api/admin/*`）完全隔离不受波及。审计 `preview_as` 记录目标用户名与 IP。
9. **客户端密钥材料**：自签 HTTPS 证书私钥 `certs/*.pem` 不入库（可按 san.cnf 重签）；App release 签名 keystore `mobile/android/keys/` 与 `key.properties` 不入库，异地备份在 `<仓库外的私有备份位置>\`——**签名丢失即无法覆盖安装更新**，按最高敏感级保管；release 包已关 R8（无混淆，自分发家庭 App 无逆向动机，换取 WorkManager/Room 稳定性）。
10. **照片时间线两处家庭信任边界**（0.4.2/0.4.3 新面，如实记录）：① `/api/photos?refresh=1` 放宽扫描节流至 5s（App 备份完成后拉最新用）——成员可持续触发全盘 rglob，放大磁盘/CPU 开销，节流下限 5s + 扫描锁防并发，家庭信任模型下接受；② 时间线"拍摄者"按路径 `照片/手机相册/<用户名>/` 推导，成员故意往他人目录传文件可造成归属错——共享空间本就可写（与 WebDAV 同级），admin 可移动纠错，不做服务端强校验。
11. **配对码免密通道**（v0.5.0 新认证面，如实记录）：`/api/pair/claim` 无需凭据即可换设备 Token，安全 = 一次性 6 位码（10^6 空间）+ 3 分钟时效 + 同账号单码 + IP 限速 5 次/分 + `pair_claim_fail` 审计——窗口内单 IP 最多试 15 码，爆破期望成本仍不可行（TTL 60→180s 是给"拿起另一台设备再输码"留时间，**IP 限速才是主闸**）；**配对码本质是"已在场的证明"**（能拿到码的人已登录 Web 或被当面告知），拿到码即等同拿到一次登录权，因此码不要截图/转发。签出的设备 Token 与密码登录同规则（可随时在设备管理吊销）。App 端"生物识别一键登录"的密码存档**仅写 Keystore**（`setStringKeystoreOnly`，不落 SharedPreferences 明文副本）——root 设备上 Keystore 亦非绝对，但攻击面已收敛到本机物理接触 + 提取 Keystore 的组合；主动退出登录即清存档。
12. **两步验证恢复码**（v0.5.1 新增）：开通时下发 10 枚一次性 8 位 hex 恢复码，登录第二步可顶替动态码——**明文仅在开通响应出现一次**，库内只存 SHA256（`users.totp_rc` JSON 数组），用一枚少一枚；恢复码与动态码错误同样计入按账号防爆破，8 位 hex（2^32 空间）+ 限速下爆破不可行；恢复码本质与动态码同级（第二因子），拿到明文即等同拿到一次登录机会，提示用户抄录/入密码管理器，不要转发。admin「仅解除两步验证」不动密码不吊销会话（`admin_totp_disable` 审计），比重置密码（核弹级：改密+全端登出+清 TOTP）副作用小一个量级。已知边界：生物识别一键登录/配对码/设备 Token 续期不经过 TOTP（生物识别如实标注"仍需动态码"）。
13. **App 内置验证器**（v0.5.2 新增）：TOTP 密钥存 App SecureStore **双写**（Keystore+SP 沙盒副本）——secret 读不出=无法生成码=一步都登不进，可靠性优先；SP 副本需 root 可读，敞口与密码档同级可接受。验证器页**不加生物识别门禁**（与第三方验证器等价：手机解锁即可看码，长辈易用优先）；动态码只在本机屏显，无网络传输面。RFC 6238 引擎有官方向量单测防实现偏差。
14. **SSE 实时通知通道**（v3.52 新认证面，`GET /api/events`）：require_user 双鉴权（Cookie/Bearer），匿名 401。事件帧为**信号而非载荷**（`{"kind":"notification"|"feedback"}`，不含标题/正文/用户名），跨用户不可枚举——收不到他人事件也推断不出他人通知内容。资源上界：每用户 ≤3 连接、全局 ≤64（超限 429）、每连接队列 16 帧满则丢最旧、15s 心跳，断连 finally 摘除——内存与连接数均有界，不存在"挂一个连接耗尽服务"的面；连接上限 429 不泄露他人存在性。事件发布与落库同源（insert_notification 内），偏好关闭的用户不发信号，不产生旁路流量。

## 安全检查清单（改动后自查）

- [ ] 新增路由是否挂了 `Depends(require_user)`（或明确设计为公开并限速）？
- [ ] 新增文件路径入参是否经过 `safe_path()`？
- [ ] 新增用户输入是否经 `clean_filename()` 或 Pydantic 模型校验？
- [ ] 前端是否零内联处理器（CSP `script-src 'self'` 会直接拦截）？
- [ ] 分享/下载类 URL 是否可能带入密码或长时效凭证？

## v3 多用户授权模型

详细设计与边界用例见 `docs/DESIGN-v3/PERMISSIONS.md`，要点：

- **角色**：admin / member / guest。角色管粗粒度，目录授权（grants）管例外。
- **空间**：`private:<path>` 仅本人（任何 vpath 写法都无法触达他人私人目录，
  admin 也不例外——管理动作接口暂缺，属已知限制）；`shared:<path>` 默认
  member 只读、写需授权；guest 全凭授权。裸路径等价 `shared:`（兼容 v2 客户端）。
- **解析算法 `resolve(user, path, need)`**：分隔符归一（反斜杠）→ 段级清洗
  （`..`/点前缀拒绝）→ normcase 大小写折叠（Windows 大小写不敏感，防变体绕过）
  → 前缀按路径段匹配（`shared/photos` 不会误命中 `shared/photos2`）→
  物理路径再过 `safe_path` 双保险。全部文件入口（Web API / WebDAV / 分享 /
  zip / 缩略图 / 直传）必须经过它，`SHARE_ROOT` 只允许在 storage 层拼接。
- **授权表 grants**：目录级，路径段前缀命中；过期失效；撤销立即生效
  （2026-09-03 修复：Windows `os.path.normcase` 会把 `/` 归一成 `\`，
  前缀命中与移动/改名同步的段拼接曾全盘失效——现以正斜杠规范化比较，子目录
  授权语义恢复；回归测试锁定）
  （进行中的下载不追溯，新请求即 403）。目录改名/移动在同一事务内同步
  grants/shares/file_refs/photos 的路径。
- **账号治理**：邀请码一次性原子认领；停用账号 Cookie 会话与 WebDAV Basic
  同时踢出；删号时授权/会话级联清除、私人目录改名 `.orphaned/` 防同名复用；
  最后一个 admin 不可降级/停用。
- **分享隔离**：分享记录 owner_id，列表/撤销/取件记录仅创建者与 admin；
  提取码明文随创建保存（家庭场景取舍，数据库由管理员保管）。
- **定向分享（v3.5）**：share 记录 target_user_id 后仅目标成员与 admin 登录可取
  （免提取码），对无关者一律 404 不泄露存在性；收件人收到站内通知（每用户只读
  自己的 notifications，无越权读取入口）。
- **管理员凭据管理**：admin 可直接改成员用户名（校验与自助改名一致，
  私人目录同步改名）与重置密码（≥8 位，重置即吊销该成员全部会话）；
  全部记审计（admin_rename_user / admin_reset_password）。
- **下载计数**：分享下载按票据去重（同一 tkt 的并发 Range 分段只计 1 次），
  下载管理器不会误触 max_downloads。
- **数据可见性**：搜索/最近/回收站/照片时间线按用户过滤（他人私人目录、
  他人 incoming 子树不可见）；`/api/disk/detail` 的目录构成仅 admin；
  家庭动态流过滤 private 目标。
- **公开面**：`/healthz` 局域网入口免认证（磁盘水位探活）；**公网入口 404**
  （避免水位信息泄露）；分享外链与取件页不要求登录（分享的本意）；`/up`
  直传页未登录显示页内引导（登录后带 next 回跳，Cookie 30 天内免重复登录）。
- **访问地址**：Caddy 监听 :80（跳 HTTPS）、:443/:8765/:8767；分享双地址
  （局域网/公网）由前端拼装，公网地址存 settings 表（全员可读，非敏感）。

## v0.16.3 新增防线（2026-09-06，三轮审查+对抗审查）

- **请求体大小上限**：非大文件端点（POST/PUT/PATCH）要求 Content-Length ≤ 1MB，
  缺 CL 一律 411、chunked 一律 413；大体积白名单精确到分片收块 `/api/upload/chunk/`
  与直传 multipart `/api/upload`（`/api/upload/init|state|finish` 仍在闸内）。
- **App 分发目录写保护**：`App安装包/` 写操作仅 admin（防持写 grant 的成员植入
  带"自洽 sha256"的恶意 APK 推送全体客户端升级）。
- **分享提取码不再落库**：只存加盐哈希，明文在创建响应中一次性下发；存量列已
  一次性清洗；分享计数在 prepare 发票时原子递增（防并发取票绕过 max_downloads）。
- **一次性消耗必须"先认领、再动手"（check-then-act 是这类缺陷的共同形状）**：
  所有"同一份资源至多被 N 个请求各自拿一次"的性质，都必须由**单语句原子认领**实现，
  不允许"SELECT 判存在 → 后删/后改"。已就位的四把同一族原语：
  `shares.consume_download`（`UPDATE…WHERE downloads<max`）、`ttl_state.take`
  （`DELETE…RETURNING`）、`ttl_state.put_if_absent`（`ON CONFLICT DO NOTHING`）、
  `trash.take`（`DELETE…RETURNING`，2026-09-21 D5：并发恢复同一 id 实测 10 轮里
  2 轮出 3 个赢家 ⇒ 同一内容落出多份副本）。
  认领之后的失败**必须补偿**：`trash.put_back` 保留原 id 与 `deleted_at`
  （否则搬移失败会让 .trash 里的文件再也点不到，或把 30 天倒计时续期）。
  删除侧的对偶问题是"两条并发删除各自算出唯一 trash 目标名"，用**源路径条带锁**
  （`filetools._TRASH_STRIPES`，单进程多线程拓扑下足以封死）解决；
  反向消融实测：加锁 0/10 红、退回加锁前 2/10 红（`tests/test_adversarial_concurrency.py`）。
- **公网入口判定加源地址复核**：`X-LanShare-Entry: public` 且 client_ip 为
  127.0.0.1（frpc 本机转发）才按公网分账——LAN 设备直连隧道端口不再放大限速。
- **解析面**：PIL 解码前像素钳制 60MP（thumb/preview，防解压炸弹 DoS）；
  zip 打包前按磁盘余量预计价（保留 5GB）。
- **命名/改名链**：用户名三入口统一校验（路径分隔符/Windows 设备保留名）；
  改名失败回滚用户名与精选专辑归属；`App安装包` 外的 DAV MOVE 也同步 grants。
- **HSTS** max-age 提升至 1 年。
