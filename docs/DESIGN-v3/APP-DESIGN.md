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
| 备份账本 | **drift**（SQLite ORM） | 类型安全查询；账本对账/迁移 |
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
| M-A3 自动备份 | 备份引擎 + 设置页 + 账本 | 需求 FR-2 全部验收点 + 72h 稳定 | 待开工 |
| M-A4 分享与发布 | 系统分享接收 / 分享管理 / APK 分发 | APK 经自家分享链接装到全家手机 | 待开工（debug APK 已在用自家分享链接分发） |

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

## 9. 风险清单

1. **iOS 后台窗口**：平台硬限制，产品语义已定为"小块多餐+前台引导"，不做全量承诺
2. **国行推送**：轮询起步够家庭用；厂商通道（小米/华为 push）列阶段 2 再评估
3. **自签证书 + 系统 WebView 拉图**：缩略图请求同样需 pinning 配置（Dio 层统一处理，
   不用系统浏览器组件直连）
4. **Flutter 插件维护风险**：后台/安全存储尽量少依赖第三方（安全存储用官方
   flutter_secure_storage；后台自写薄桥）
5. **上架合规**（P2）：软著、隐私政策、商店审核对"局域网文件工具"友好，但 iOS
   审核可能要求演示账号——服务端需保留 demo 账号能力
