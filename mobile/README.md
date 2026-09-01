# LAN Share App（Flutter 跨端）

家庭网盘 LAN Share 的原生移动客户端。需求见 `docs/DESIGN-v3/APP-REQUIREMENTS.md`，
技术方案见 `docs/DESIGN-v3/APP-DESIGN.md`。

## 里程碑

| 阶段 | 状态 | 内容 |
|------|------|------|
| M-A0 | ✅ | 设备 Token 契约（登录换 Bearer，90 天滑动过期，可吊销） |
| M-A1 | ✅ | 登录(+TOTP) / 照片墙 / 文件浏览 / 我的（设备管理） / 自签证书 TOFU |
| M-A2 | ✅ | 手动传输：上传（分片续传+秒传）/ 下载（Range 断点）/ 传输中心（进度/速度/暂停/续传/重试/杀进程恢复） |
| M-A3 | ⏳ | 相册自动备份（WorkManager + 账本 + 前台服务） |
| M-A4 | ⏳ | 系统分享接收 / 分享管理 / APK 自举分发（debug 签名先行） |

## 目录结构

```
lib/
├── main.dart               入口 + 登录态↔传输引擎联动
├── data/
│   ├── api/                Dio 客户端（Bearer 注入/401 统一/TOFU 证书信任）
│   └── local/              SecureStore（Token 双写兜底）/ Prefs
├── state/session.dart      登录(+TOTP) → Cookie 换设备 Token
├── transfer/               ★ M-A2 传输核心（纯 Dart，可单测，双端共享）
│   ├── transfer_task.dart  任务模型 + 队列 JSON 落盘载体
│   └── transfer_engine.dart 上传 init→chunk→finish / 下载 Range / 调度
└── ui/                     照片 / 文件 / 传输中心 / 我的
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

## 构建

```bat
scripts\build-apk.bat debug
```

仓库在中文路径下，NDK/CMake 构建会失败——脚本自动镜像源码到
`C:\lan-share-mobile`（ASCII）构建后把 APK 拷回 `mobile\build\apk\`。
依赖安装用国内镜像（脚本内已设 `PUB_HOSTED_URL`/`FLUTTER_STORAGE_BASE_URL`）。

## 已知取舍

- Token 与证书指纹**双写** Keystore + SharedPreferences 副本：部分安卓机
  （含模拟器）进程被杀后 EncryptedSharedPreferences 冷启动解密失败，互斥
  方案会掉登录；副本在应用沙盒内，且 Token 服务端可吊销
- >200MB 文件不预计算 sha256（纯 Dart 哈希慢），秒传对大文件不生效，
  服务端 finish 仍会自算校验
