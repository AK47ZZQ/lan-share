# LAN Share App · 技术设计方案（APP-DESIGN v0.1）

> 配套需求见 `APP-REQUIREMENTS.md`。选型已定：**Flutter 跨端**（用户决策：未来
> iPhone 规划 + 上架路径）。本方案解决 Flutter 方案的全部技术风险点，尤其是
> **后台备份执行**这一最大风险。

## 1. 技术选型（决策表）

| 决策项 | 结论 | 理由 |
|--------|------|------|
| 框架 | Flutter（stable 通道，Android minSdk 26 / iOS 13） | 用户决策（iOS 规划+上架）；UI 一套代码双端 |
| 状态管理 | **Riverpod 2** | 编译期安全、无 BuildContext 耦合，适合全局会话/备份状态 |
| 网络 | **Dio** + 拦截器 | Bearer 注入、401 统一处理、进度回调、取消（上传暂停用） |
| 序列化 | kotlinx 无——**Dart: json_serializable** | 与服务端 JSON 契约对齐（下划线字段名） |
| 安全存储 | flutter_secure_storage | Token（Android Keystore / iOS Keychain 加密） |
| 设置存储 | shared_preferences | 非敏感开关 |
| 备份账本 | **SQLite（sqflite 裸 SQL）** | 设计原选 drift；落地改裸 SQL：单表场景省掉 build_runner 代码链，类型安全由 LedgerRow 模型类保证（§8.6） |
| 哈希 | crypto（流式 sha256） | 秒传指纹；照片 ≤100MB 全落在现有指纹协议内 |
| 图片 | flutter_cache_manager + CachedNetworkImage | 缩略图两级缓存 |
| 后台调度 | **平台薄原生层 + Dart 传输核心**（§4 专述） | Flutter 后台最大风险点的解法 |

依赖原则（沿 PLAN-v3 §6 精神）：每新增一个 pub 依赖在 CHANGELOG 记录理由。

## 2. 应用架构（分层）

```
presentation/   页面 + Riverpod Provider（照片墙/文件/传输/分享/我的）
domain/         实体与用例（UploadUseCase、BackupPolicy、ShareUseCase）
data/
  ├─ api/       Dio + ApiClient（全部端点、Bearer 拦截器、401 → 重登录事件）
  ├─ backup/    媒体扫描器（平台通道）、账本 Repository(drift)、备份引擎
  └─ local/     SecureStore / Prefs
platform_bridge/  MethodChannel：媒体扫描、后台调度注册、前台服务通知（薄）
```

原则：**传输协议 100% Dart**（init→chunk→finish、SHA256 流式、重试退避）——
可单测、双端共享；**只有"何时唤醒"交给原生**（各平台后台机制的薄封装）。

## 3. 认证与 Token（后端契约新增 · 需服务端实现）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/auth/token` | POST | 会话 Cookie 换设备 Token：`{name}` → `{token(仅此一次明文), device_id, name, created_at}` |
| `/api/auth/devices` | GET | `{[{id, name, created_at, last_seen, current}]}` |
| `/api/auth/devices/{id}` | DELETE | 吊销设备（被吊销端下次请求 401） |

服务端实现要点：Token 落库存 SHA256 哈希；90 天滑动过期；中间件按
`Authorization: Bearer` 认证（与 Cookie 并存，WebDAV Basic 已有先例）；
吊销写审计；`is_active=false` 账号即失效（复用现有踢出机制）。
**App 侧**：所有请求带 `Authorization: Bearer`；401 → 清 Token → 登录页。

## 4. 后台备份引擎（核心设计）

**风险陈述**：Flutter 应用退后台后 Dart isolate 会被冻结（iOS 尤甚），
"纯 Dart 后台备份"不可行。解法：**职责拆分——原生层管"何时醒"，Dart 层管"怎么传"**。

```
原生(Android)                     Dart(main)
WorkManager 周期任务 ──┐
MediaStore Observer ───┤ MethodChannel ──▶ BackupEngine.run(batch)
前台服务(长批量)       ──┘                  ├─ 账本 diff（跳过已备份）
                                           ├─ sha256 流式 → init（秒传探测）
                                           └─ chunk 上传（断点/重试）→ finish
原生(iOS)                         （整批在前台服务通知保护下执行）
BGTaskScheduler ───────▶ 同上（后台窗口短：iOS 策略=小块多餐，大批量引导用户
                           开前台充电备份，App 内明示）
```

- **Android**：WorkManager `PeriodicWorkRequest`（15min 起，系统约束：NetworkType
  UNMETERED + 可选充电）承载常规扫描；`ContentObserver` 监听 MediaStore 变更做
  即时触发（注册在 Application 级进程内）；单批 >50 张自动升级前台服务
  （常驻通知，合规且防杀）。flutter_workmanager 插件维护一般，**自写约 200 行
  Kotlin 桥**（注册/参数透传/结果回传），不引第三方后台插件
- **iOS**：BGTaskScheduler 后台窗口有限，策略为"小块多餐 + 明示引导"：设置页
  说明 iOS 系统限制，推荐"晚上插电开 App 挂前台备份"；此为平台约束如实告知
- **状态机**：`pending → hashing → uploading → done | error(attempts<5 退避重试) |
  skipped(dup)`；账本字段：`media_id PK, generation, size, sha256, remote_path,
  status, attempts, last_error, uploaded_at`
- **对账**：启动时对最近 N 条 `uploading` 状态调 `/api/upload/state` 探测续传；
  账本与 MediaStore 双删不同步——以"单向备份、网盘不随相册删除"为产品语义
- **限流与礼仪**：单设备并发 ≤3 块；整机失败率 >30% 自动暂停 30 分钟（防风暴雨）

## 5. 网络与证书信任策略（自签证书专项）

家庭服务器为自签 HTTPS。**不做**"允许一切证书"的全局放宽，采用逐服务器显式信任：

1. 首次连接出现证书错误 → 展示证书指纹（SHA256）+ 服务器地址，要求用户显式勾选
   "信任此服务器"
2. 信任后按 **SPKI 指纹 pinning** 校验；服务器换证书 → 提示重新确认
3. token 等敏感头仅在 pinning 通过的连接上发送
4. 公网（未来可信证书域名）走系统正常校验，无需 pinning

## 6. 服务端地址与入口

- 登录页预填上次地址；支持多地址保存与切换（LAN `https://192.168.1.100:8765` /
  公网 `https://your-frp-domain.com:xxxxx` / 未来 CF 域名）
- 自动备份仅在"当前可达 + 非 metered"判定通过的服务器上执行；公网地址默认不
  执行自动备份（流量保护，可开）——与服务器侧公网上传提醒策略呼应

## 7. UI / 品牌

- Material 3，延续网页品牌：主色 `#5b6cff`→`#8a5bff` 渐变、深浅主题跟随系统
- 五 Tab 信息架构与页面清单见需求文档 FR 区块；图标沿用 PWA 的渐变 L
- 中文唯一语言（首版）

## 8. 里程碑与出口标准

| 里程碑 | 内容 | 出口标准 | 状态 |
|--------|------|---------|------|
| M-A0 契约冻结 | Token 三端点上线（含测试）、本设计评审通过 | 服务端测试含 Bearer 路径；App 骨架可登录 | ✅ 2026-09-01 |
| M-A1 只读可用 | 登录(+TOTP) / 照片墙 / 文件浏览 | 小米真机日常浏览一周 | ✅ 已交付（模拟器端到端验证；真机验收进行中） |
| M-A2 手动传输 | 上传/下载/传输中心（暂停续传重试） | 500MB 杀进程续传验收（需求 FR-5） | ✅ 2026-09-01（模拟器 E2E 全程验证） |
| M-A3 自动备份 | 备份引擎 + 设置页 + 账本 | 需求 FR-2 全部验收点 + 72h 稳定 | ✅ 2026-09-01（FR-2 验收点模拟器全通过；72h 挂机留真机阶段） |
| M-A4 分享与发布 | 系统分享接收 / 分享管理 / APK 分发 | APK 经自家分享链接装到全家手机 | ✅ 2026-09-01（模拟器 E2E 全通过；加固+M-A4 APK 已上传 App安装包，sha256 与本地构建一致） |

## 8.5 M-A2 传输引擎落地注记（2026-09-01）

实现为 `mobile/lib/transfer/`（纯 Dart，ChangeNotifier 单例 + Riverpod provider）：

- **断点真值在服务端**：恢复一律 `GET /api/upload/state/{sid}` 探测做并集，
  本地 received 只是加速缓存；会话 404（3 天 TTL）自动作废重开
- **下载**走 `/preview` + `Range`（206 续写 `.part`；416 视为已完整；200
  自动从头）——服务端 FileResponse 原生支持，已补 pytest 契约测试锁定
- **队列落盘** `documents/transfer_queue.json`：800ms debounce + 最长 5s
  强制刷盘（纯 debounce 会被高频分片进度"饿死"，杀进程只剩旧快照）
- **登录态联动**：登录 → 加载队列并自动续传；登出 → 全部挂起（不置
  userPaused，重新登录即自动恢复）
- **秒传阈值** App 端 200MB（package:crypto 纯 Dart 哈希慢于网页 JS 引擎场景
  的实测取舍），超限不预计算，服务端 finish 仍自算校验
- 实测顺带修复的服务端 bug：**同目录重传同名文件秒传失效**——秒传分支
  `move_to_trash(dest)` 会把 sample（=dest 自身）一起挪走导致 os.link
  WinError 2，现以 samestat 先判同文件直接判定完成（`files.py`）；静默
  `except OSError` 改为 WARNING 日志，不再吞错

## 8.6 M-A3 自动备份落地注记（2026-09-01）

按 §4 设计落地，职责拆分兑现：**原生只管"何时醒"，传输协议 100% Dart**。

- **Kotlin 薄桥**（`mobile/android/.../`，4 个文件约 300 行）：`BackupScheduler`
  （PeriodicWorkRequest 15min + 约束 UPDATE 原地换）、`BackupWorker`
  （CoroutineWorker：前置检查 → setForeground → 拉起后台 isolate → 按结果
  success/retry → 完成通知）、`BackupDartRunner`（FlutterEngineGroup 每次任务
  拉起独立引擎跑 `backupMain`，用完即毁；"ready" 握手防 invoke 早于 handler）、
  `MediaStoreBridge`（query 全量紧凑数组 / open 拷缓存临时文件）。未引第三方
  后台插件，仅新增 `androidx.work:work-runtime-ktx`
- **Dart 侧**（`mobile/lib/backup/`）：`backup_engine.dart`（账本 diff → 逐张
  sha256 流式 → init 秒传 → 分片 → finish；并发 1 顺序传；失败率 >30%(≥10 张)
  冷却 30min 防风暴雨）、`backup_ledger.dart`（sqflite 单表）、`media_source.dart`、
  `backup_bridge.dart`、`backup_config.dart`
- **账本字段相对 §4 设计的调整**：`mtime` 代替 `generation`（DATE_MODIFIED 全
  API 级可用，(id,mtime,size) 变了即重传）；增列 `session_id`/`received`/`name`
  （跨进程续传凭据与账本页展示）；`uploading` 中断行由下轮 diff 自动纳入
  （state 探测续传），等价设计里的"启动对账"
- **约束默认**：仅 Wi-Fi + 仅充电时默认开（FR-2）；`runOnce` 一次性任务不带
  约束（用户显式动作立即生效）；开关状态经 `flutter.backup_enabled` 键被
  Kotlin 直读（shared_preferences 同文件，免双写）
- **前台服务**：Worker 开始即 setForeground（dataSync 类型，Android 14 合规），
  12+ 后台启动受限时 try/catch 降级——小批量 10min 窗口内完成，大批量靠账本
  断点分多轮跑完，不丢数据。**实测踩坑：前台通知与结果通知同 ID（1001）会
  在 Worker 结束时被系统连带撤掉 → 结果通知改用 2001**
- **§6 公网保护**：域名非 `.local/.lan`、IP 非 RFC1918/localhost 一律判公网，
  默认 skip（可开"公网地址也备份"）
- **E2E 硬验收**（模拟器 API 35）：60 张照片开启即全自动备份（服务端 60/60、
  字节数一致、抽查 sha 一致）；"立即备份"重跑服务端 mtime 零变化（账本去重）；
  视频默认不传；250MB 杀进程续传——**服务端 .chunks 会话 part-000000..012 的
  mtime 在续传前后原封不动（零重传的物理证据），仅新增 13..49，finish 后
  262144000 字节 + SHA256 一致**；模拟器重启后 JobScheduler 周期任务持久恢复
  （约束 CHARGING+CONNECTIVITY 未满足即等待）；ContentObserver 防抖 10s 即时
  触发；完成通知可见
- **遗留**：72h 连续挂机验收留真机阶段（FR-2 验收总纲）；iOS BGTaskScheduler
  属 M-A4 后远期

## 8.7 M-A4 分享与发布落地注记（2026-09-01）

**FR-6 系统分享接收**——原生只管"接住并落地"，上传 100% 复用传输引擎：

- **Kotlin 侧**（`ShareReceiver.kt` + MainActivity/Manifest）：MainActivity 增
  SEND/SEND_MULTIPLE `*/*` intent-filter；收到即把 content:// 流拷进
  `cacheDir/share_inbox/`（授权方可随时回收 URI，必须先落地），显示名/大小经
  OpenableColumns 查询、文件名洗 `/ \`、同秒多件加 `(n)` 序号；纯文本分享落
  `分享文本_<ts>.txt`。路径排队 `ShareInbox.pending`，`lanshare/share` 通道
  Dart 侧 `takePending` 拉取 + Kotlin `incoming` 事件推送——与备份桥反向的
  同款容错：Dart handler 未就绪时推送失败静默，登录后拉取兜底，不丢件
- **Dart 侧**（`lib/share/share_bridge.dart`）：取走排队 → 复用
  TransferEngine 分片续传上传到 `incoming/<用户>/分享暂存/`（展示名剥掉
  `<13位时间戳>_` 壳）；监听引擎，**上传完成即删本地副本**（分享件是拷贝，
  源在分享方 App）；传输引擎相应修正：`_load()` 只对"未完成"任务查本地文件
  存在性，已完成任务副本被清不改判 error（否则重启满屏假错误）。24h 兜底
  扫残在 Kotlin sweepStale。接入点在 HomeShell（登录壳就绪即消费，冷启动
  分享进入与热分享统一）；未登录时留 Kotlin 队列，登录自动继续
- **FR-7 分享管理**（`shares_page.dart` / `share_create_sheet.dart` /
  `share_service.dart`）：列表（大小/剩余时效/下载次数/提取码/定向徽章）+
  撤销（确认框）+ 复制链接（保存多地址时逐地址给"局域网/公网"标签复制钮，
  RFC1918/localhost/.local 判局域网）；创建面板有效期 1/3/7/30 天 +
  提取码 + 下载上限 + 定向成员（/api/members）；入口=文件页文件操作单
  （guest 隐藏）+ 我的页。端点全复用现有契约（POST /api/share 等），零服务端改动

**E2E（模拟器 API 35）**：单文件 SEND → 服务端字节数一致；文本分享落 txt；
SEND_MULTIPLE 单 Uri extra（`am` 指令只能这样发）→ 兜底路径收下（真机
ArrayList 路径同核心代码）；传输中心显示已上传且 share_inbox 清空；创建
分享（3 天）→ /api/shares 可见 → 我的分享撤销 → 服务端确认删除

**发布**：加固版+M-A4 APK（163,842,221B，sha256 7ce1f3ef…）已传
`App安装包/lanshare-debug.apk`，家人经既有分享链接取新包
（09-01 深夜起换正装：release 签名 v0.4.0+4 → `App安装包/lanshare.apk`，
见 DEVELOPMENT.md §手机 App）

**顺带修复**：154201b 的"progress 刷新前台通知"代码无法编译（在
BackupDartRunner 作用域引用 applicationContext、把 ForegroundInfo 直接
notify）——该提交实未编译过；拆出 `progressNotification()` 返回 Notification
并用 run(ctx) 的 ctx。教训：**提交前必须过一次真构建，analyze/单测不编译 Kotlin**

## 9. 风险清单

1. **iOS 后台窗口**：平台硬限制，产品语义已定为"小块多餐+前台引导"，不做全量承诺
2. **国行推送**：轮询起步够家庭用；厂商通道（小米/华为 push）列阶段 2 再评估
3. **自签证书 + 系统 WebView 拉图**：缩略图请求同样需 pinning 配置（Dio 层统一处理，
   不用系统浏览器组件直连）
4. **Flutter 插件维护风险**：后台/安全存储尽量少依赖第三方（安全存储用官方
   flutter_secure_storage；后台自写薄桥）
5. **上架合规**（P2）：软著、隐私政策、商店审核对"局域网文件工具"友好，但 iOS
   审核可能要求演示账号——服务端需保留 demo 账号能力
