# LAN Share App（Flutter 跨端）

> **App 子模块技术文档**：工程结构 / 传输协议 / 备份与分享机制 / 构建签名。
> 功能总览、里程碑状态与版本历史见根目录 `README.md` 与 `CHANGELOG.md`
> （当前 **v0.21.2+63**）——本文不重复演进史，只讲 mobile/ 内部怎么工作。

家庭网盘 LAN Share 的原生移动客户端。需求见 `docs/DESIGN-v3/APP-REQUIREMENTS.md`，
技术方案见 `docs/DESIGN-v3/APP-DESIGN.md`。

## 目录结构

```
lib/
├── main.dart               入口（main）+ 后台 isolate 入口（backupMain）
│                           + 登录态↔传输引擎/备份调度联动
├── backup/                 ★ M-A3 自动备份（纯 Dart 引擎，后台 isolate 跑）
│   ├── backup_engine.dart  账本 diff → 轮前对账(空间/改名/相册被删, v0.21) →
│   │                       sha256 → init 秒传 → 分片 → finish
│   ├── backup_ledger.dart  sqflite 单表账本（(id,mtime,size) 变更判定；
│   │                       v0.21 状态机加 server_missing + 相册级管理）
│   ├── backup_config.dart  开关/约束配置（enabled 键被 Kotlin 直读）
│   ├── backup_round.dart   轮次结果 JSON → 中文状态文案（首页/详情页共用）
│   ├── backup_bridge.dart  主 isolate → 原生调度桥（挂/撤周期任务/权限/立即备份）
│   └── media_source.dart   MediaStore 查询/临时文件通道封装
├── data/
│   ├── api/                Dio 客户端（Bearer 注入/401 统一/TOFU 证书信任）
│   ├── local/              SecureStore（Token 双写兜底）/ Prefs
│   └── totp.dart           ★ M-A5 内置验证器 RFC 6238 引擎 + otpauth:// 解析
│                           （HMAC-SHA1+Base32 自实现，零新依赖）
├── share/                  ★ M-A4 系统分享接收桥 + 分享 API 封装
│   ├── share_bridge.dart   takePending 拉取/ incoming 推送 → 入上传队列 → 传完删副本
│   └── share_service.dart  创建/列表/撤销/成员/链接（复用服务端分享契约）
├── state/session.dart      登录(+TOTP) → Cookie 换设备 Token
├── transfer/               ★ M-A2 传输核心（纯 Dart，可单测，双端共享）
│   ├── transfer_task.dart  任务模型 + 队列 JSON 落盘载体
│   └── transfer_engine.dart 上传 init→chunk→finish / 下载 Range / 调度
└── ui/                     首页(沉浸渐变欢迎头+卡片综合面板/最近上传) /
                            照片(拍摄·入库双序时间线+查看器平移手势/长按菜单) /
                            文件(双布局+新建 md·txt / 复制路径 / 上传二维码) /
                            传输中心(汇总条) /
                            笔记(分类体系：侧栏嵌套树/卡片列表带摘要/置顶收藏/
                            拖拽与批量移动/排序切换；md·txt·html 三编辑器带
                            30s+失焦自动保存，on_dup=overwrite 覆盖保存) /
                            通知中心(分类过滤/单条已读·滑动删除/偏好开关) /
                            分享管理 / 我的(备份设置/授权卡/验证器/更新) /
                            登录页 v2(预检/生物识别/配对码) / 管理面板

android/app/src/main/kotlin/app/lanshare/lanshare_app/   ★ M-A3/M-A4 原生薄桥
├── LanshareApp.kt          Application + 通知渠道 + MediaStore Observer（防抖 10s）
├── BackupScheduler.kt      WorkManager 周期任务（15min，仅 Wi-Fi+仅充电默认开）
├── BackupWorker.kt         CoroutineWorker：前台保护 → 拉起后台 isolate → 结果/进度处理
├── ShareReceiver.kt        ★ M-A4 系统分享接收：content:// 先拷落地 → ShareInbox 排队
└── MediaStoreBridge.kt     媒体查询/拷贝临时文件（Dart 通道实现）
```

## 传输协议（与网页 v3.6 全量对齐）

- 上传：`POST /api/upload/init`（sha256 秒传探测，≤200MB 预计算）→
  `POST /api/upload/chunk/{sid}?index=N`（5MB 起自适应，服务端原子写）→
  `POST /api/upload/finish/{sid}`（服务端拼装 + sha 必校验）
- 断点：重启后 `GET /api/upload/state/{sid}` 探测服务端已收块号做并集；
  会话过期（3 天 TTL）自动作废重开
- 下载：`GET /preview/{path}` + `Range: bytes=N-`（206 续传；416 视为已完成；
  服务端不支持 Range 回 200 时自动从头）
- 恢复：队列 JSON 落盘（`documents/transfer_queue.json`，800ms debounce +
  5s 强制刷盘），杀进程重启后登录即自动续传；登出全部挂起

## 自动备份（M-A3）

职责拆分（APP-DESIGN §4）：**原生只管"何时醒"，传输协议 100% Dart**。

- **触发**：WorkManager 周期任务（15 分钟起步，系统约束：仅 Wi-Fi→UNMETERED、
  仅充电可选，默认均开）+ MediaStore 变更防抖 10s 即时触发 + 手动"立即备份"
  （一次性任务带与周期任务相同的约束，读同一份 prefs——0.4.2 前即时任务
  不带约束，拍照会绕过仅 Wi-Fi/仅充电）；JobScheduler 跨重启持久
- **执行**：Worker 拉起独立 FlutterEngine（`backupMain` 入口，用完即毁）→
  MediaStore 全量扫描 → 账本 diff（新文件/变更文件/未完成项）→ 逐张
  sha256 → init（秒传）→ 分片 → finish；断点凭据 session_id 落账本，
  恢复一律服务端 state 探测
- **约束收口（0.4.2）**：WorkManager 约束只在任务启动时把关，跑起来后系统
  叫停/离开 Wi-Fi/拔充电都管不住——引擎**逐张**经 `lanshare/backup` 通道
  `checkStop`（isStopped/网络是否计费/是否充电）复查 + 重读开关，违反即
  优雅暂停（status=paused，账本断点，约束恢复后下个周期自动续）
- **礼仪**：并发 1 顺序传；失败率 >30%（≥10 张）冷却 30 分钟；失败项
  attempts>5 置 error，账本页可手动重试
- **保护**：单批在 dataSync 前台服务通知下执行（Android 12+ 后台启动受限时
  降级，靠账本断点分多轮跑完）；公网地址默认不备份（§6 流量保护，可开）
- **语义**：单向备份——相册里删掉的照片不会从网盘删除；备份位置（v0.7.0+18
  重构）固定 `照片|视频/<用户名>/<相册名>/` 三级目录（账本带相册维度，
  改名用户自动落新目录）；手动上传落 `照片/<用户名>/手机上传/`；视频默认
  不备份（开关可开）；排除相册按相册维度勾选
- **智能对账（v0.21，轮前三步）**：每轮上传前先对账再干活——
  ① **空间预检**：`GET /api/disk` 剩余 < 本轮待备总量 → 整轮 skip
  （`insufficient_space`，状态文案可见，清理后自动续备）；
  ② **改名识别**：reconcile 上报的 bucket 迁移中，同方向 ≥5 张且旧相册已
  从本地扫描整体消失 → 判定改名：`POST /api/rename` 改服务端目录 +
  `applyBucketRename` 批量改账本指向（恢复 done），**不重传**（rename
  失败自动回落为正常重传兜底）；
  ③ **相册被删识别**：账本 done 相册（safe 名）与服务端目录集对比，缺失
  的 done 行转 `server_missing` 态**不再自动重传**——备份详情页出现提示卡，
  逐相册选择「重新备份」（回 pending，sha 秒传重建引用）或「从备份移除」
  （删账本行）
- **服务端副本管理（v0.21）**：备份设置页「管理服务端相册」——列出
  `照片|视频/<用户名>/` 全部相册，逐个删除服务端副本（进服务端回收站
  30 天可恢复）并同步清理账本该相册全部记录
- **验收**（模拟器 API 35 E2E）：60 张全自动备份 sha 一致；账本去重重跑
  mtime 零变化；250MB 杀进程续传**零重传**（服务端分片文件 mtime 前后一致
  的物理证据）；重启手机周期任务自动恢复

## 分享（M-A4）

**接收（FR-6）**——原生只管"接住并落地"，上传复用传输引擎：

- **触发**：系统分享面板选"LAN Share"（SEND/SEND_MULTIPLE，任意类型）；
  纯文本分享落 `分享文本_<时间戳>.txt`
- **落地**：Kotlin 收到即把 content:// 拷进 `cache/share_inbox/`（授权方可
  随时回收 URI，必须先落地），显示名经 OpenableColumns 查询、文件名洗 `/ \`
- **交接**：`lanshare/share` 通道——Dart `takePending` 拉取 + Kotlin
  `incoming` 事件推送；Dart handler 未就绪时推送失败静默留队列，登录后
  （HomeShell 就绪）拉取兜底，冷启动分享进入与热分享统一处理，不丢件
- **上传**：复用传输引擎分片续传到 `incoming/<用户名>/分享暂存/`，展示名
  剥掉时间戳壳；**上传完成即删本地副本**（分享件是拷贝，源在分享方 App）；
  Kotlin 24h 扫残兜底；未登录时留在 Kotlin 队列，登录自动继续

**管理（FR-7）**：我的分享列表（大小/剩余时效/下载次数/提取码/定向徽章）+
撤销确认 + 复制链接（多服务器地址逐个给"局域网/公网"标签复制钮）；创建面板
有效期 1/3/7/30 天、提取码、下载上限、定向成员（`/api/members`）；入口 =
文件页文件操作单（guest 隐藏）+ 我的页。端点全复用服务端契约，零后端改动。

**验收**（模拟器 API 35 E2E）：单文件/文本/SEND_MULTIPLE 兜底三路径落库
字节数一致；传输中心显示已上传且 share_inbox 清空；创建 3 天分享 →
`/api/shares` 可见 → UI 撤销 → 服务端确认删除。

## 构建

```bat
scripts\build-apk.bat debug|release
```

仓库在中文路径下，NDK/CMake 构建会失败——脚本自动镜像源码到
`C:\lan-share-mobile`（ASCII）构建后把 APK 拷回 `mobile\build\apk\`。
依赖安装用国内镜像（脚本内已设 `PUB_HOSTED_URL`/`FLUTTER_STORAGE_BASE_URL`）。

替代：仓库已建 ASCII 结点路径 `C:\lan-share` → 真实目录，在该路径下
直接 `flutter build apk --debug` 也可（M-A2/M-A3 实测，避开中文路径同理）。

启动图标由 `scripts/make-app-icon.py`（hermes venv 的 PIL）生成：**云朵渐变
品牌 logo**（紫蓝渐变 + 白描边云，v0.8.5 起与 PWA/网页同源；早期"白 L"已废），
产出自适应（anydpi-v26 + monochrome）与遗留圆角两套；改品牌先改脚本常量
再重跑、重新构建。开屏圆角由 flutter_native_splash 自带 drawable 承担
（v0.15.1 起，勿依赖系统缓存）。

## 签名与发布（v0.4.0+4 起 release 为正式签名）

- 密钥库 `android/keys/lanshare-release.jks` + 凭据 `android/key.properties`
  （均 gitignore；**异地备份** `<仓库外的私有备份位置>\`，
  丢失即无法覆盖安装）。gradle 从 `key.properties` 读签名，缺文件时回退
  debug 签名（保证无密钥环境 `flutter run --release` 可用）
- `storeFile` 路径相对 **android/ 根**（rootProject）解析——写 `keys/xxx.jks`
  而非 `app/keys/xxx.jks`，否则 `validateSigningRelease` 找不到文件
- **R8 已关**（`android/gradle.properties` → `shrink=false`）：Flutter 插件
  默认给 release 开裁剪/混淆，WorkManager 的 Room 库在 R8 下启动即崩
  （`Failed to create an instance of WorkDatabase`，早于任何 Dart 代码），
  debug 构建从不暴露；家庭 App 无混淆收益，关闭仅 +4MB
- debug 与 release **签名不同不能覆盖安装**：已装 debug 包的设备换装
  release 须先卸载（登录态/备份账本随之清除，账本丢失由服务端秒传兜底）
- 分发：`App安装包/lanshare.apk` 经自家分享链接分发（30 天）；换包直接
  覆盖同名文件并新建分享

## 已知取舍

- Token 与证书指纹**双写** Keystore + SharedPreferences 副本：部分安卓机
  （含模拟器）进程被杀后 EncryptedSharedPreferences 冷启动解密失败，互斥
  方案会掉登录；副本在应用沙盒内，且 Token 服务端可吊销
- >200MB 文件不预计算 sha256（纯 Dart 哈希慢），秒传对大文件不生效，
  服务端 finish 仍会自算校验
- 分享接收的缓存副本**上传完成即删**：传输引擎队列恢复只对"未完成"任务查
  本地文件存在性，已完成任务副本被清不改判错误（否则重启满屏假错误）
- **图片链路缓存为四层**（0.4.3~0.4.5）：AuthImage 进程内已解析文件记忆表
  （滚动往返零转圈）→ AuthCacheManager 磁盘缓存 5000 对象/180 天（默认
  200 会被大相册反复挤掉）→ 服务端 .thumbs 内容寻址缓存（JPEG draft 快
  解码）→ 网页/PWA 走 ETag 304（Caddy 对 /thumb/* 放开 no-store）。⚠️
  自写 FileService 必须**剥 If-None-Match**：cache manager 带条件头走 304
  时拿不到文件体，全员坏图（0.4.4 真机复现）
- **照片时间线为 Sliver 虚拟化**（0.4.4）：CustomScrollView + 按日
  SliverGrid + 置顶改随滚日期头（悬浮头压照片，真机反馈弃用）；勿改回
  shrinkWrap 网格——整组销毁重建会让已加载图片划走即消失
- **sqflite 账本连接进程级共享**（v0.7.1 根因）：引擎 isolate 的 close()
  会杀死主 isolate 的连接——账本永不主动 close，连接在进程内全局复用
- 更完整的版本化踩坑（含 MediaStore 发布复查、edge-to-edge、Reorderable
  header 等 v0.20 项）见 `项目记忆/03-踩坑清单.md`
