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

**发布收尾（09-01 深夜 II/III）**：release 签名（自建 keystore，
`mobile/android/keys/` gitignored + 异地备份；gradle 缺 key.properties 回退
debug 签名）+ 品牌启动图标（`scripts/make-app-icon.py`，PWA 同款
紫渐变 + 白 L，自适应 anydpi-v26 含 monochrome 主题图标 + 遗留圆角，
零新依赖）；当前 **v0.4.5+9**。R8 教训：Flutter 插件默认给 release 开
裁剪/混淆，WorkManager 的 Room 库启动即崩且 debug 从不暴露——
`gradle.properties → shrink=false` 关掉。换签设备须卸载重装

### 8.8 真机反馈与性能批次落地注记（09-01 深夜，v0.4.2~v0.4.5）

- **备份约束收口**：WorkManager 约束只管任务启动——Observer 即时任务原本
  无约束、引擎跑起来后无人复查。引擎逐张经 `lanshare/backup` 通道
  `checkStop` 复查（isStopped/网络是否计费/是否充电，无网按计费）+ 重读
  开关 → `status=paused` 优雅暂停，账本断点约束恢复自动续
- **照片统一落位**：`照片/手机相册/<用户名>/`（手动上传与备份一致）；
  服务端 `_photo_uploader` 按此归属"拍摄者"；`incoming/<u>/手机相册` 存量
  已迁移
- **照片墙准实时**：引擎完成写 `backup_last_done_at` → 照片页 10s 轮询
  → `/api/photos?refresh=1`（扫描节流对 force 放宽 5s）
- **图片链路四层缓存**：AuthImage 进程内已解析文件记忆表（滚动往返零转圈）
  → AuthCacheManager 磁盘缓存 5000 对象/180 天 → 服务端 .thumbs 内容寻址
  （draft 快解码）→ ETag 协商缓存 304（Caddy 对 /thumb/* 放开 no-store）。
  **教训**：自写 FileService 必须剥 `If-None-Match`——cache manager 带
  条件头走 304 时拿不到文件体，全员坏图（0.4.4 真机复现）
- **时间线 Sliver 虚拟化**：CustomScrollView + 按日 SliverMainAxisGroup +
  SliverGrid，日期头随内容滚动不悬浮；照片量不设限；长按多选（下载/
  删除进回收站/单选分享，guest 隐藏危险操作）

**顺带修复**：154201b 的"progress 刷新前台通知"代码无法编译（在
BackupDartRunner 作用域引用 applicationContext、把 ForegroundInfo 直接
notify）——该提交实未编译过；拆出 `progressNotification()` 返回 Notification
并用 run(ctx) 的 ctx。教训：**提交前必须过一次真构建，analyze/单测不编译 Kotlin**

### 8.9 全功能实测修复批（2026-09-02 深夜 IX，v0.4.6+10）

v0.5.2 之前的 App 改动都按"修改→单测→真构建→小切片模拟器回归"的
轻流程过，但**没在模拟器上做过一次按用户日常路径走一遍的端到端真连**。
本次按"登录 → 首页 → 照片 → 文件 → 传输 → 备份 → 分享管理 → 系统分享
接收 → 我的"九模块顺序跑，每步都用 server-side 物理证据核对，挖出 4
个存量 bug——其中 1 个 P1 是 v0.4.3 起的"照片墙准实时"**从未真正生效**：

- **P1 跨 isolate 共享首选项缓存**：`BackupDartRunner` 由 WorkManager 拉
  起，是独立 FlutterEngine（独立 isolate）。引擎完成一次备份后写
  `flutter.backup_last_done_at` 与 `flutter.backup_pending_count`——但 UI
  isolate 的 `shared_preferences` 插件**只在首次 `getInstance()` 时把磁
  盘文件缓存到内存首帧**，后续 `getInt(...)` 都读旧值，等同于 UI 永远
  看不到引擎写过。证据链：① jobscheduler 看 `WM-WorkerWrapper` 0.24s
  SUCCESS（确认任务跑过且快速跳过）；② adb root 直读
  `/data/data/.../shared_prefs/FlutterSharedPreferences.xml` 确认键值已更
  新；③ `sqlite3` pull `databases/backup_ledger.db` 看 `ledger` 表 9 条
  done 无新增（确认本次确实没新文件进来，引擎行为本身正确）。综合结论
  是 UI 读不到 ≠ 引擎没写。修：`photos_page.dart` 10s 轮询前与
  `home_page.dart` 的 `_loadLocal` 首行各加 `await sp.reload()`。教训：
  任何"插件首帧缓存到进程内"副作用都要进 checklist，不靠用户报
- **P2 复制分享链接 snackbar 插值错位**：`shares_page.dart:96`
  `'已复制（$_addrTag(chosen)）'` 把**闭包对象本身**当字符串插值——Dart
  单标识符 `$x` 是合法语法且编译器不警告，运行时 `toString()` 输出
  `Closure: () => String`，用户看到的是元数据不是标签。补花括号
  `'已复制（${_addrTag(chosen)}）'`。教训：Dart 字符串插值单标识符务必
  显式花括号
- **P2 照片页/文件页手动上传完成无联动**：picker 选 5 张图走完上传任务
  → 相册列表不动——非备份路径没人监听 `engine` 状态，靠 10s 备份轮询
  兜底，体感延迟。修：两页 `_pickAndUpload` 收集本次入队任务，订阅引
  擎，所有任务 `finished` 且 `_path == dirAtEnqueue` 时 `_refresh()`。
  订阅前快照当前目录是为了防用户切走目录后被旧 listener 强刷
- **P2 备份位置文案过期**：设置页文案 `incoming/$username/手机相册/` 与
  引擎实际 `destDir = 照片/手机相册/$username/` 不符（v0.4.2 真机反馈
  第一批已统一）。改文案不改变逻辑（`backup_engine.dart:106` 的
  `destDir` 一直是新路径，只是显示侧未跟）。教训：批量重构时显示侧要
  grep 一遍硬编码路径兜底

**四回归实测**：v0.4.6+10 模拟器上一一核验——① 备份位置文案显示
`照片/手机相册/<用户名>/`；② 复制「局域网」标签 snackbar =
`已复制（局域网）`；③ 手动上传 5 张，相册列表 1s 内多 5 张；④ 引擎跑
一次后照片页 10s 内强刷新文件（jobscheduler 拉到任务 + adb root 读
prefs 值已变 + 录屏确认列表刷新）。

**模拟器环境注记**（写给后续接手人，不属 App bug）：
- 模拟器无 Wi-Fi、只有蜂窝 3G（计费）→ WorkManager `UNMETERED` 约束
  **永不满足** = 系统层宁停不偷跑。**开发期关掉"仅 Wi-Fi 备份"开关**才
  能跑测试；真机 Wi-Fi 环境下不受影响
- 网络验证进入 `PARTIAL_CONNECTIVITY` 时 Google 204 探测不可达，
  CONNECTED 任务也会卡——`settings put global captive_portal_http_url/
  https_url/fallback_url/other_fallback_urls` 指向
  `http(s)://connect.rom.miui.com/generate_204` + `svc data disable/enable`
  重验网络即可

**发布**：release 包已覆盖 `App安装包/lanshare.apk`（与 v0.5.2+13 同分发
链接，30 天有效）。

### 8.10 App 内置验证器（2026-09-02 傍晚，v0.5.2+13）

- **动机**：两步验证补全后用户指出"手机上没有认证器 App"——把验证职责
  推给第三方 App 违背家庭自包含定位；且 App 已有扫码（mobile_scanner）与
  安全存储（SecureStore），内置成本很低
- **引擎**：RFC 6238 自实现 ~90 行——HMAC-SHA1 走已有 `crypto` 包、
  Base32（RFC 4648）手写；`otpauth://totp/` 解析兼容 pyotp provisioning
  （issuer 前缀路径 + digits/period 参数）。**RFC 附录 B 官方向量单测
  全过**——加密算法类改动必须有标准向量背书，不靠"看起来对"
- **验证器页**：扫码绑定（`ScanPage` 参数化 `ScanMode.otpauth`，只收
  otpauth://totp/，与服务器地址模式互不误收）+ 手输密钥兜底；条目卡片 =
  30 秒倒计时圆环（末 5 秒红）+ 3+3 分组大字码；解除只删本机条目
- **登录闭环**：needTotp 六格步骤检测同账号绑定 → 「本机验证器」卡片
  显示当前码，点按 `fill()` 进六格自动提交——**两步验证在 App 内从
  "要切出去看码"变成一次点按**
- **取舍**：TOTP secret 走 SecureStore **双写**（与密码档同通道）而非
  Keystore-only——secret 读不出=无法生成码=一步都登不进，可靠性优先；
  验证器页不加生物识别门禁（与装第三方验证器等价：手机解锁即可看码），
  长辈易用优先

## 8.8 登录页 v2 + 连接体验落地注记（2026-09-02，v0.5.0+11）

- **布局方向 A**：上半屏渐变品牌区（云朵 Logo + slogan）+ 下半屏圆角
  阴影浮层卡片；**服务器地址从表单首位降级为状态条**（短主机名 + 预检
  状态点），点击弹 bottom sheet（历史地址/扫码添加/手动输入）——地址是
  "设置项"不是"登录项"，绝大多数登录只输账号密码
- **连接预检**：对 `$addr/api/auth/me` 发 GET，**任意 HTTP 状态码（含
  401）=在线**，仅连接超时/失败=不可达（无需服务端健康检查端点）；
  `badCertificateCallback` 放行自签（预检只答可达性，信任归 TOFU）；
  独立 Dio 不带凭据、`dio.close(force: true)` 用完即关
- **生物识别一键登录**：密码登录成功后凭据存档 `SavedCredentialStore`
  **仅写 Keystore**（`setStringKeystoreOnly`，不落 SharedPreferences
  明文副本——密码比可吊销 Token 敏感）；`local_auth` 门禁
  （`biometricOnly:false` 兼容锁屏 PIN；`getAvailableBiometrics` 判
  已录入，有硬件未录入不显入口）；**主动退出清档、Token 自然失效不清**
  （掉线正需一键重登）；MainActivity 换 `FlutterFragmentActivity`
  （local_auth 硬性要求，MethodChannel 钩子不变）
- **配对码免密登录**：`/api/pair/code` + `/api/pair/claim`（服务端契约
  见 API.md 认证扩展）；登录页"无法输密码？使用配对码登录"模式切换；
  与 TOTP 共用 `_CodeField` 六格组件（跳格/粘贴分发/填满自动提交/
  登录失败自动清空重输）；场景 = 长辈手机不碰密码
- **扫码填地址**：`scan_page.dart`（mobile_scanner，新依赖 #11）；
  只收 http(s) 链接；相机权限拒绝走 `errorBuilder` 友好提示
- **细节包**：记住最后登录用户名预填、密码可见性切换、错误 shake、
  深浅色自适应
- **踩坑两枚**：① SingleChildScrollView 子项用 `Spacer()`（unbounded
  主轴 flex）→ 布局期异常**整页白屏且无红屏无日志**（曾误判
  FlutterFragmentActivity，对照构建排除）；滚动容器内禁用 flex 撑满
  技巧。② 验证码输入格用 TextEditingController 必须 State 持有，
  build 里现建会丢光标且泄漏

### 8.9 两步验证补全（2026-09-02 午后，v0.5.1+12）

- **一次性恢复码**：开通时 10 枚 8 位 hex（`a1b2-c3d4` 展示形态），登录
  第二步顶替动态码、用一枚少一枚；明文仅开通响应一次，库内只存 SHA256；
  重新生成需原密码。**手机丢失的自助通道**——此前只能 admin 重置密码
  （admin 自己丢手机则只能动库），是两步验证最大的可用性缺口
- **App 登录第二步**：六格动态码 ↔ 恢复码文本框切换（「认证器不可用？
  使用恢复码登录」）；生物识别按钮下如实标注"开启两步验证的账号仍需
  输入动态码"——生物识别只免密码不免第二因子，语义不夸大
- **Web 同批**：登录页动态码换六格（`CodeField.vue` 对齐 App 手感）；
  开通后恢复码面板（复制全部/已妥善保存）；admin 成员列表 🛡️ 2FA 徽章
  + 仅解除 TOTP（不动密码不登出）
- **顺带修存量 bug**：全局确认框（ui.ts dlg）与业务弹窗同 `z-index:400`，
  动态 teleport 的模态 append 在 body 末尾会盖住确认框——**弹窗内再弹
  确认框整层不可见且不可点**（浏览器实测解除流程时撞出）。`.dlg-overlay`
  提到 450。教训：**teleport 层级要留出全局浮层的栈位**
- **验证**：pytest 92 全绿（+5 配对码）；模拟器端到端真连（预检红→绿/
  密码登录/退出清档/记住用户名/配对码免密全程）；生物识别弹窗与扫码
  取景为真机特性，待真机验收

### 8.11 全板块功能补齐大批次（2026-09-02 深夜 X，v0.6.0+14）

头脑风暴定稿"每板块必须+实用清单"后一次实施 35 项。架构决策与踩坑：

- **视频播放 vs 自签 HTTPS**：video_player 在 Android 走 ExoPlayer 自有
  网络栈，**插件不暴露证书回调**——TOFU（§5）对它无效。方案：MediaProxy
  （`lib/data/api/media_proxy.dart`）在 127.0.0.1 起 HTTP 服务，用
  TrustStore 信任的 HttpClient 带 Bearer 回源 `/preview/` 流式转发，
  Range/Content-Range/206 原样透传（拖进度依赖）；路径带随机秘钥防同机
  其它 App 枚举拉流；`networkSecurityConfig` 仅对 loopback 放行明文，
  不为自签开任何明文口子。E2E：2 秒彩条 mp4 播放到完 + 控制条拖动
- **视频进时间线是服务端工程**：`/api/photos` 扫描 PHOTO_EXTS 不含视频，
  备份的视频在照片墙**从未出现过**。VIDEO_EXTS 进扫描 + `media_type`
  响应/筛选参数；photos 表加 duration 列（PG `ADD COLUMN IF NOT EXISTS` +
  SQLite PRAGMA 增量，双引擎幂等）；ffprobe 补时长与容器 creation_time
  （手机视频 mtime 常是拷入时间，比图片更需要这层）；每轮探针至多 20 条
  收敛。**坑**：`CASE WHEN ? IS NOT NULL` 悬空参数让 psycopg 报
  IndeterminateDatatype → 两段式 executemany（引擎中立写法优先）
- **MediaProxy 每请求读 prefs/token 而非缓存**：服务器地址可切换（§2
  switchServer），缓存 base 会打旧地址；完成事件低频，读开销可忽略
- **检查更新发版约定**：管理员把 APK + `version.json`（version/build/
  filename/sha256/notes）放共享空间 `App安装包/`；`/api/app/latest`
  读 json、filename 取 basename + resolve 前缀校验双防穿越；App 对比
  **build 号**（单调发布号，比 version 字符串可靠）→ 下载 → OpenFilex
  拉起安装器（REQUEST_INSTALL_PACKAGES；用户仍需确认"未知来源"）。
  本次发版即用此通道写入 0.6.0(14)
- **排除相册不动账本**：Kotlin 桥 query 加 BUCKET_DISPLAY_NAME（第 6
  元素，缺列机型退空串）+ 新 buckets 统计方法；引擎在**扫描阶段过滤**，
  账本零改动——取消排除后同 media_id 自动重回待备集合，语义可回退
- **应用锁放壳层**：HomeShell 持 WidgetsBindingObserver，resume 即锁；
  锁屏即隐私遮罩（内容完全不上屏）；逃生口 = 登出（凭据清空密码重登），
  不做 PIN 备份（生物识别硬件故障属极端场景，登录页无凭据本就进不来）
- **闭包空值提升**：bottom sheet builder 闭包里读可空局部变量无法提升，
  先物化 `final entries = ...toList()` 再进闭包——analyze 两轮才揪出
- **依赖纪律**：新增 5 项均在 pubspec 附理由；flutter_local_notifications
  强制 core library desugaring（build.gradle.kts isCoreLibraryDesugaringEnabled
  + desugar_jdk_libs 2.1.4），否则 release 出包直接失败

### 8.12 热修应用锁恶性锁死（2026-09-02 深夜 XI，v0.6.1+15）

真机报恶性 bug：开启应用锁立即上锁、指纹解锁后被弹回锁屏、退出登录重登
照样锁——三连环彻底锁死。§8.11"应用锁放壳层"的设计是错的方向性根源：
**"resume 即锁"里 resume 不等于"从后台回来"**。三个投影一个根因：

- **指纹弹窗自带生命周期**：BiometricPrompt 弹出→Activity inactive
  （部分机型到 paused），关闭→resumed。任意 resumed 即锁 = 弹窗自己关
  闭就把刚解开的锁打回去；开启前的"验证以开启应用锁"弹窗同理把刚打开的
  开关立刻拉闸。事件落盘顺序与 setState 竞态，哪边赢看机型看时机
- **initState 无条件置锁**：`_locked = appLock` 让每次 HomeShell 挂载
  都锁——登出重登也锁，"退出登录"逃生口形同虚设，恶性程度倍增
- **修法（三层）**：① 落锁判定收进纯逻辑 `LockGate`（state/settings.dart，
  单测 6 场景）：paused/hidden 才算真后台，resumed 消费标记一次性；②
  `biometricBusyProvider` 忙标记——锁屏解锁与开启验证两处 authenticate
  共用，弹窗在屏期间 resumed 一律不判定；③ `sessionUnlockedProvider`
  进程级解锁标记：冷启动默认 false（appLock 开启即锁屏，且不受 settings
  异步加载时序影响），解锁成功/开启验证/登出逃生置 true——重登不再锁。
  判定整体从 widget 状态搬进 provider 后，"锁不锁"是纯函数
  `appLock && !unlocked`，行为可单测、时序无竞态
- **锁屏可用性**：加"指纹解不开？可退出登录后用密码重新进入"常驻提示；
  解锁异常 toast 给明路。**不做 PIN 备份**的决策维持（登录页本就无凭据
  进不来，登出逃生已足够），前提是逃生口必须真的能用
- **最大教训——生物识别必须录入后真跑**：本 bug 在"九模块真连冒烟"
  时因"生物识别弹窗是真机特性，待真机验收"被跳过，而跳过的恰是唯一会
  崩的路径。模拟器完全能录指纹（Settings 录入流程 + `adb emu finger
  touch 1` 喂触摸），半小时就能拿到真解锁链路。**新规矩：凡 local_auth
  相关改动，出包前模拟器实录指纹走一遍开→锁→解→逃 → 收紧进发版
  checklist**
- 另：BiometricPrompt/ChooseLockPassword 均设 FLAG_SECURE——screencap
  全黑、uiautomator dump 拿不到节点，自动化只能盲输（`input text` +
  `keyevent 66`）；锁屏 UI 验证靠截锁屏本身（非 secure）

### 8.13 v0.6.2：排除相册根修 + 家里动态 + 备份完善（2026-09-02 夜）

**排除相册"无法使用"根因**：`MainActivity` 从未注册 `lanshare/media`
通道——`MediaStoreBridge` 注释写"主引擎与 Worker 引擎都注册"，事实是只有
`BackupDartRunner`（后台 isolate）注册过。排除相册页在主 isolate 调
`buckets()` 必吃 `MissingPluginException`，页面永远"无法读取相册列表"。
第二条防线一并补上：原生桥约定"出错也回 success + `{"error":...}` JSON"，
此前 Dart 侧没人识别——`buckets()` 会把错误串当数据解出 TypeError，
`open()` 会把错误串当文件路径用。现在 `MediaSource.mediaPayload()` 统一
转 PlatformException（公开纯函数可单测）。**教训**：注释说"注册了"不等于
真注册了；通道/桥类改动一律模拟器真跑后才能发版（与应用锁同款教训，
进发版 checklist）。E2E 闭环：排除 LSTest1 → 服务端只落 LSTest2 →
取消排除 → 4/4 回补（账本不动、扫描过滤语义完好，§8.11 的设计兑现）。

**家里动态**：服务端 `/api/activity` 本就存在（Web 动态页同源，隐私过滤
与 10 分钟聚合全在服务端），手机端零服务端改动：`data/activity.dart`
（模型+文案纯函数，与 Web activity.ts 同语义）+ 首页动态卡（5 条，30s
刷新）+ `ui/activity_page.dart` 全量页（100 条 + 下拉刷新）。自动备份的
上传天然进动态流（同一 upload 审计入口）。手机端暂不做"点击跳转对应
目录"（Web 有），语义差异留后续。

**备份完善头脑风暴（本批落地 3 项 + 后续候选）**：

- 本批落地：① 首页备份卡失败红字（引擎每轮回写账本 error 数，点击直达
  账本页）；② 首页实时"备份中 X/Y"（引擎逐张落 prefs，首页 2s 轮询——
  通知栏之外的第二只眼，引擎 finally 兜底清零防卡死）；③ 电池优化豁免
  引导（REQUEST_IGNORE_BATTERY_OPTIMIZATIONS + `battery`/
  `batteryRequest` 桥方法 + 设置页引导行）——小米/华为激进省电把
  WorkManager 周期任务压到几小时一次甚至不跑，是"备份不自动跑"投诉的
  常见根因。
- 后续候选（待主人拍板，按价值排序）：**相册→服务器子目录映射**（现在
  全部平铺 `照片/手机相册/<用户名>/`，按相册分子目录更整洁，但改既有
  落位语义，需迁移策略）；**备份统计微图**（账本 sparkline：最近 7 天
  每日张数，纯展示）；**完成通知点击直达备份设置页**（深链路由）；
  **HEIC 上传前转 JPEG**（省空间但耗电耗时，服务端 pillow-heif 已能直出
  缩略图，紧迫性低）；**"备份到加密包"**（远期，与 Web 端加密分享联动）。
  明确不做：删除同步（单向备份是安全线）、原图压缩选项（家庭场景忠实
  原样最不意外）。

### 8.14 备份/上传落位重构（2026-09-02 夜提出并拍板，v0.7.0+18 实施）

用户拍板的落位模型（用户原话归纳）：**类型总夹/用户名/手机相册名/文件**，
四个类型夹同规则；存量不迁移；换相册重传无所谓；改名用户要有交代。

**最终落位**：
- 自动备份：`照片/<用户名>/<相册名>/`（图片）、`视频/<用户名>/<相册名>/`
  （手机相册里的视频随相册走，不进照片夹）；相册名经 `safeBucketName`
  安全化（非法字符→`_`、首尾点/空白去除、纯点/空→`未分类`、截断 60 字
  符），落位锁死在类型前缀内不逃逸
- 照片页手动上传：`照片/<用户名>/手机上传/`（选择器不返回来源相册，
  给不了相册层，与备份的相册子目录分开）
- 文件页上传保持当前目录；分享接收保持 `incoming/` 不变
- 存量不迁移（服务端不知来源相册，无法可靠归位；迁移断分享链接）；
  账本 v2 加 bucket 列，(id,mtime,size,bucket) 任一变化视为待备——
  升级后存量媒体因 bucket='' ≠ 新值**全部重备一次**，sha 秒传零流量，
  只在新落位建引用；手机上把照片挪相册同理，服务端旧副本保留

**改名用户的交代**（用户点名要考虑的场景）：账本本就按账号隔离
（`backup_ledger_user` 变化即清账本重对账）。服务端改名后：新落位自动
变成 `类型/<新用户名>/…`，全量重备走 sha 秒传零流量；旧用户名目录成为
孤儿副本，由管理员手动清理（不自动删——删错了找不回）。

**服务端配套**：`_photo_uploader` 增加新落位归属——`照片|视频/<u>/<相册>/…`
第二段必须**命中真实用户名**（请求时从 users 表取集合传入）才判归属，
否则 `照片/同学&生活&学校/子目录/…` 这类手工整理目录会被误判成用户；
旧落点 `照片/手机相册/<u>/…` 朴素前缀语义保持（存量不迁移所以旧路径
长期存在）。上传端点零改动（dest_dir 已逐段清洗）。

**验证链**：pytest 119（+1 归属回归：新落点/跨类型/非用户二段/未知用户/
旧落点兼容）→ Dart 55（+11：安全化/落位拼装）→ 模拟器 E2E：升级后
LSTest1/LSTest2 全量重备进各自 `照片/debug/<相册>/`（秒传零流量，
账本 v2 迁移即触发）→ 服务端目录结构核实 → /api/photos 归属正确。

### 8.15 备份状态卡三项修复：database_closed 根修 + 排除感知统计 + 照片/视频分列（2026-09-02 夜 IV，v0.7.1+19）

用户实测报障：①排除相册保存后状态卡数字不动；②状态卡无法实时显示；
③备份账本打不开。模拟器复现（停 API 制造账本未完成行 → 排除 → 观察）
后确认**一个根因贯穿三个症状**：

**根因**：后台备份引擎（独立 isolate + 独立 FlutterEngine）每轮结束在
`finally` 调 `BackupLedger.I.close()`。sqflite 在 Android 侧的连接是
**进程级共享**的（isolate 间不是各自独立的 SQLite 句柄），这一关把主
isolate 的连接一并打死；主 isolate 的 `BackupLedger.open()` 缓存的
`_db` 仍非空（但底层已关闭），之后每一次查询都抛
`DatabaseException(database_closed 4)`——设置页 2s 统计轮询每次抛未处理
异常（旧版无捕获）→ 统计卡冻结假死；账本页 `recent()` 失败且无错误态 →
永远转圈。**教训：跨 isolate 的共享资源，任何一侧的"清理"都是对另一侧的
破坏；close() 这类方法要么不提供，要么注释里写明谁有权调用。**

**修复（三层防御）**：
1. 删掉引擎侧 `close()` 调用与方法本身——引擎资源回收交给
   `BackupDartRunner` 的 `engine.destroy()`；账本类注释写明连接进程级
   共享、不可从后台 isolate 关闭。
2. `open()` 打开缓存句柄前查 `isOpen`，死句柄自动弃掉重开——未来任何
   来源（插件行为/引擎销毁牵连）导致的连接关闭都能自愈。
3. 统计轮询与账本页加载全部 try/catch：统计失败保留上次数字（下个 tick
   重试），账本页给"账本暂时打不开 + 原始错误 + 重试"错误态（0.6.3
   排除页同款）。另把账本 v1→v2 迁移幂等化（先 `PRAGMA table_info`
   查列再 ALTER + 吞 duplicate column），主 isolate 与后台引擎同时从
   旧版升级库结构不再互相炸。

**排除感知统计（症状①的另一半根因）**：排除相册只影响扫描阶段、账本行
不动——被排除相册的 pending/uploading/error 行会永远留在账本里，原
`stats()` 直接 GROUP BY 出来，待备份/失败数字永远等不到归零。现
`stats(excluded:)` 用 `bucket IN (...) AND status != 'done'` 查出被排除
相册的未完成项，从对应计数中扣掉、单列"已排除相册中还有 N 项不参与备份"
（done 行不受影响——排除前已备份的仍是已备份）。引擎回写
`backup_failed_count` 同样排除感知：被排除项的失败红字不再永挂首页。
聚合逻辑抽纯函数 `statsFromGroups`（GROUP BY 结果 + 排除查询结果 →
统计），单测无需真 SQLite。

**照片/视频分列（用户新需求）**：`stats()` 改 `GROUP BY status, kind`；
状态卡两行式"已备份 照片 X · 视频 Y（大小）/ 待备份 照片 A · 视频 B ·
失败 C 项"；首页备份卡待备数分类型回写（`backup_pending_photos/videos`）
+"张"改"项"；账本页每行类型图标 + 相册名进副标题 + 状态/类型双排筛选
chips（`recent(kind:)`）。

**E2E 验证链**（模拟器）：停 API + 媒体观察者自动触发轮次制造 1 pending
+ 1 error 行 → 排除 ExclTest 保存 → 状态卡即时"待备份 照片 0 · 失败 0 项
+ 已排除相册中还有 2 项"（不重启不等待）；取消排除 → 自动补传至"已备份
照片 12"；推视频 → "视频 1"；服务端核实 `照片/debug/ExclTest/*`、
`视频/debug/Movies/vid_1.mp4`；重试失败项在排除状态下保持挂起（符合
语义，取消排除后自动放行）；引擎多轮结束后统计卡仍实时刷新（close 根修
回归）。修复前同场景实测账本页显示 `DatabaseException(database_closed 4)`
错误态——即用户"打不开"的直接证据。

### 8.16 照片墙实时更新扩面：脉搏轮询 + 强刷节流独立（2026-09-03 凌晨，v0.7.2+20）

用户报"照片页实时更新又挂了"，要求扩宽检测范围（担心新建文件夹里的照片
不显示）。排查结论分两半：

**扫描范围本就无死角**：`_scan_photos` 是 `SHARE_ROOT.rglob("*")` 全盘
递归，新建文件夹里的照片天然覆盖（归属按路径推导，非用户名二段的手工
目录归"共享空间"但对成员可见）。用 pytest 钉死此行为，防止将来有人加
目录白名单。

**真缺口一：触发面**。照片墙自动刷新此前只认 `backup_last_done_at`
（本机备份引擎传完东西才写）+ 本机手动上传的引擎订阅。网页上传、其他
设备备份、文件页上传、分享接收、WebDAV——一概不触发（当晚家里人网页
传 178 项，手机墙面纹丝不动 = 用户看到的"挂了"）。修法：照片页新增
**20s 脉搏轮询**——`GET /api/photos?limit=1&refresh=1` 轻探测（响应仅
一个缩略项），total/首张路径与上次快照不同即整页强刷。至此触发面三层
互补：本机备份（10s prefs，即时）→ 本机手动上传（引擎订阅，即时）→
**任何来源（20s 脉搏，兜底）**。

**真缺口二：强刷节流被顶掉**。`refresh=1` 强扫与普通请求共用同一个
60s 时间戳（force 只放宽到 5s）——网页正刷照片墙时，App 的强刷常被
5s 内的普通命中直接 return，扫了个寂寞，墙拿到的是旧库。修法：force
走**独立的 `_last_force_scan` 5s 节流**，普通请求保持 60s 全盘扫描
防护；force 扫描同时刷新两个时间戳。强刷从此确定性生效。新约束进
conftest 白盒重置清单（`_last_force_scan = 0.0`）。

**取舍**：20s 脉搏在照片页挂载期间持续运行（Tab 常驻 IndexedStack），
每次一个 limit=1 请求 + 服务端至多 5s 一次全盘 rglob——与首页 30s 的
/api/disk 全库统计同量级，家庭 NAS 负载可忽略。

**E2E**（模拟器）：宿主机往新建文件夹 `照片/debug/E2E脉搏测试/` 丢
照片，全程不碰手机，~40s 内自动出现在墙上；删除侧由用户真实删除操作
顺带验证（墙面自动跟随其删除）。pytest 122（+3：force 不被普通命中
顶掉/60s 节流仍在/新文件夹+非用户名目录+任意顶层目录媒体必进时间线）。

### 8.17 备份触发根修与全链路可观测（2026-09-03 上午，v0.7.3+21）

**审计根因**：手动"立即备份"沿用了自动任务约束 `setRequiresCharging(仅
充电时，默认开)`——未插电时任务进 WorkManager 队列永远等充电器：不跑、
不通知、不报错，且每次再点只是 REPLACE 掉旧的排队任务。自动备份仅在
夜间充电时运行，白天新照片"一直显示待备份"。模拟器恒为充电中，历次
E2E 从未暴露。**教训入册：约束类 bug 必须显式模拟约束缺失才能测到**
（`adb shell cmd battery unplug`；注意模拟器电池 mock 约 30s 会被系统
重置，持续等待状态无法在模拟器保持）。

**修复**：
1. 手动路径：`MediaObserver.enqueueManual` 专用入队（unique 名
   `lanshare_backup_manual`、REPLACE、无充电/电量/网络约束），
   trigger="manual" 经 `backupMain` 贯穿 `BackupEngine.run(trigger)`；
   引擎对 manual 跳过"未充电"暂停（`_pauseReason`）并豁免失败冷却，
   保留 Wi-Fi 流量保护与系统叫停检查。自动/周期任务约束语义不变。
2. 可观测：`BackupState.record` 把 Worker/队列每个出口写入 prefs
   （queued/queued_wait(充电|wifi)/running/skip_disabled/skip_idle/busy/
   done/paused/error/dart_not_ready/run_timeout）；入队即落状态——排队
   期间原本一行代码都不执行；引擎每轮结束写 `backup_last_round` JSON。
3. UI：首页备份卡"最近一轮/排队"状态行（琥珀=需注意）；设置页备份
   诊断卡（网络/电源/通知/最近调度/最近一轮，`diag` 通道一次取齐，
   通知被关可跳系统设置）。状态→文案映射抽纯函数
   `backup_round.dart`（roundView/queueView/workerView，11 项单测）。

**遗留候选**（头脑风暴未实施）：WorkManager getWorkInfos 实时查询入
诊断卡；手动轮 setExpedited 进一步压启动延迟；MIUI 自启动/省电白名单
引导页（检测 MIUI ROM 给出图文指引）；单轮条数上限与"大迁移分夜跑"。

### 8.19 六轮 review + 对抗性审查：管理面板删除失效等 5 个 P0（2026-09-03 傍晚，v0.8.1+23）

用户报管理面板删除按钮失效，要求全面审查。流程：第 1 轮人工 API 契约
逐端点核对（App 请求 vs `admin.py/auth.py/notify.py` 服务端签名），第
2–5 轮四个审查专员并行（状态生命周期 / UI 交互排版 / 安全权限 / 对抗性
边界），第 6 轮修复+验证。方法论沉淀：

- **契约错是删除失效的头号嫌疑**：服务端 `DELETE /api/admin/invites/{code}`
  与 `/api/admin/grants/{gid}` 只有 DELETE 方法，App 复用 `_post` 助手 →
  405 → 无提示失败。修法：新增 `_del` 助手（dio.delete + 中文错误
  toast + `_loadAll`），三类删除统一走它。**教训：UI 助手函数按"方法"
  分家，不要按"有无 body"分家。**
- **`_prompt` 空值语义**："取消"与"留空提交"必须区分（`allowEmpty`
  返回 `''` vs 取消返回 `null`）；凡文案带"留空=清除"的流程必须真机
  点一遍。配额清除走 0（服务端 `quota_bytes or None` → NULL），
  不能发 null（Pydantic 与缺省字段不可区分，直接跳过更新）。
- **错误中文化统一出口**：`data/api/errors.dart` `errDetail(e)`——
  DioException 取 `response.data['detail']`（服务端全中文 detail），
  fallback HTTP 状态码；替换全部 `e.toString()` 直出。
- **fromJson 防脏数据**：服务端字段一律 `as T? ?? fallback`，尤其
  列表页 model（一条脏数据崩整页）。
- 其余落地：`_busy` 防连点、`_loadDirs` 路径逐段 encode、zip 临时名
  时间戳前缀、多选栏横向滚动、改用户名后清缩略图缓存、通知 `read`
  字段 + 本地同步去红点、专辑 chip 主色区分、幽灵 uid 清理、
  "已删除(#id)" 回退文案。
- 0.8.2 补审（本人 5 轮逐行）：配额输入 0=不限 + <10GB 一位小数展示
  （`.round()` 使 <1GB 显示成 0，不动保存即误清——**数值输入框的初始
  值要检查"展示值是否是合法语义值"**）；webhook 测试按钮改
  ValueListenableBuilder 跟随输入（controller 变更不触发整页重建）；
  fire-and-forget 方法里 await 的初始化调用（dio()/prefs）也要进 try。

### 8.18 功能对齐网页版：管理面板/账号安全/打包下载/专辑/通知（2026-09-03 午后，v0.8.0+22）

用户要求把 Web 能力全部迁移到 App。盘点后差距三块，服务端端点早已齐备
（零服务端改动）：

**管理面板**（`admin_page.dart`，我的页 admin 可见）：成员 Tab（列表徽章
role/2FA/停用、bottom sheet 操作=改角色/改用户名/重置密码/解除两步验证/
WebDAV 密码重置/配额/停用启用/删除；底部卡=公网访问地址+事件推送
webhook 保存测试）；邀请 Tab（成员/教访 72h 邀请码创建复制吊销）；授权
Tab（批量：成员 chips × 目录两层树（跳过 private/incoming）× 读写 ×
有效期，按成员过滤 chips、撤销）。后端契约要点：管理员改用户名/重置
密码/解 2FA 走 `AdminCredBody{value}`（改用户名 `{"value":new_name}` 不
需要密码；解 2FA 无 body）；授权路径服务端规范化为 `shared/` 前缀存储。
Web"视角预览"（setPreviewAs 全局请求头）未迁移，App 需全局 dio 注入
改造，记 §9 遗留。

**我的页"账号与安全"**：修改用户名（/api/auth/username + SessionNotifier
.applyRename 本地同步；账本账号隔离自动接管重对账）；WebDAV 独立密码
（GET/POST /api/auth/webdav-password，留空提交=清除）；生成配对码
（/api/pair/code 大字弹窗 180s）；重新生成恢复码（/api/auth/totp/rc，
新码仅本次明文展示）。

**文件打包下载**：多选栏"打包"——POST /api/zip {paths} → url 下载到
临时目录 → share_plus 系统分享面板；服务端打包耗时以 toast 反馈。

**照片精选专辑**：横排 InputChip（/api/albums），点击=按月过滤；
admin 经月份选择页"新建精选专辑"（标题→选月份）与长按删除。

**通知中心**（notifications_page.dart）：首页动态卡铃铛（未读徽标 30s
一刷）→ /api/notifications 列表 + 打开即 POST read（与 Web 铃铛同语义）。

**E2E**：模拟器 debug=admin 真账号：三 Tab 真实数据渲染（3 成员/公网
地址预填/授权列表）、配对码真生成、通知中心空态正确；邀请创建→删除、
授权创建→撤销 API 闭环。**事故与教训**：E2E 以"列表最后一条"定位测试
授权时误删 id=12（ZZQZZQ 的 shared/PC软件安装包）——查实主人已重组
根目录（原 shared/* 目录更名到根），该授权本已失效（重建报"目录不存在"），
对 ZZQZZQ 实际访问零影响；**真实数据环境禁止"删除最后一个"式定位，
测试数据要么自建自清、要么按唯一标记定位**。

### 8.20 全仓十轮 review + 五轮对抗审查（2026-09-03 深夜，v0.8.3+25）

用户要求"亲自 10 轮完整代码 review + 5 轮对抗性审查，覆盖代码的所有模块
与功能"。10 轮视角：R1 后端 API 契约 / R2 认证安全 / R3 服务层 / R4 Web
数据层 / R5 Web 页面 / R6 Mobile 数据层与备份引擎 / R7 Mobile 页面 / R8
原生桥 / R9 前后端契约交叉 / R10 并发边界与资源泄漏；A1-A5 攻击者视角：
认证绕过与 IDOR / 路径穿越 / 注入 / 竞态滥用 / 信息泄露与配置。后端 16
+ Web 30 + Mobile 43 Dart + 6 Kotlin + Caddyfile 全部逐文件过。共 13 项
修复（详见 CHANGELOG），**最深的一条教训**：

> **Windows `os.path.normcase` 把 `/` 转成 `\`，所有建立在
> `gp + "/"` 段拼接上的前缀匹配逻辑（授权命中 `_grant_hit`、移动/改名
> 同步 `sync_grants_on_move`）在 Windows 上永不命中** —— 授权"递归到
> 子目录"的语义（PERMISSIONS.md E10）在实际部署平台（Windows 宿主 +
> NTFS）是坏掉的，而全量测试跑在 Windows 却全绿：存量测试只设计了
> "精确目录"用例，前缀方向从未被执行到。修复只改一行归一化
> （`os.path.normcase(s).replace("\\", "/")`），补 4 条前缀/子树回归。
> 方法论补丁：**"平台相关路径规范化"必须配跨分隔符断言**；review
> 对"拼接式字符串匹配"保持警觉（`+ "/"` 这类隐式分隔符假设）。

其余要点：阶段式清理循环（system.py）的异常容错补齐；直传配额与
WebDAV 配额两条计数口径修正；前后端"null=未提供"契约在管理面板
配额流上第二次咬人（App 0.8.1 已修、Web 0.8.3 补修）——教训：**契约
缺陷是跨端共债，修一端必须排查另一端**；Caddy 代理层 2GB 请求体上限
与服务端 20GB WebDAV 特性不一致（代理先于应用层截断）。

## 9. 风险清单

1. **iOS 后台窗口**：平台硬限制，产品语义已定为"小块多餐+前台引导"，不做全量承诺
2. **国行推送**：轮询起步够家庭用；厂商通道（小米/华为 push）列阶段 2 再评估
3. **自签证书 + 系统 WebView 拉图**：缩略图请求同样需 pinning 配置（Dio 层统一处理，
   不用系统浏览器组件直连）
4. **Flutter 插件维护风险**：后台/安全存储尽量少依赖第三方（安全存储用官方
   flutter_secure_storage；后台自写薄桥）
5. **上架合规**（P2）：软著、隐私政策、商店审核对"局域网文件工具"友好，但 iOS
   审核可能要求演示账号——服务端需保留 demo 账号能力
