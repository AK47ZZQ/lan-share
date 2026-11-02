# LAN Share 环境矩阵

在 Windows Sandbox 里把安装器跑过一组**刻意刁难**的环境，每次发版前过一遍。

## 为什么

2026-11-02 用户装 3.88 报了 8 个问题，其中至少 5 个是「我的机器上不存在、
他的机器上存在」的环境假设：

| 问题 | 触发条件 |
|---|---|
| initdb 权限被拒 | 装在 Program Files |
| Caddyfile token 切错 | 安装路径含空格 |
| pg_dump 找不到 | 装了但 PATH/回退目录不对 |
| 端口冲突 | 别的软件先占了 8766 |
| 状态判断错乱 | 无 LAN 网卡 / IPv6 双栈 |

这些**本机永远复现不了**，因为本机已经装好了、路径是 ASCII、网卡在。
所以必须在一个干净容器里，针对每种环境单独装一遍。

## 跑法

```powershell
# 宿主上运行，会自动拉起 Windows Sandbox
powershell -ExecutionPolicy Bypass -File scripts/env-matrix/run.ps1

# 指定安装包（默认取 build\out 里最新那个）
powershell -ExecutionPolicy Bypass -File scripts/env-matrix/run.ps1 `
  -Setup "build\out\LANShare-Setup-3.89.exe"
```

结果写到 `scripts/env-matrix/result-<时间>.txt`，同时沙箱窗口里能看到进度。

## 场景

| 场景 | 刁难什么 | 期望 |
|---|---|---|
| `default` | Program Files + 默认数据目录 | 装完三个服务 Running，HTTPS 200 |
| `space` | 路径含空格 `C:\LAN Share Test` | 同上（Caddyfile token 不被切错） |
| `cjk` | 中文路径 `C:\网盘测试` | 同上 |
| `drive` | `subst D:` 造真盘符，装到 D 盘 | 同上，且 C 盘 Program Files / ProgramData 零残留 |
| `portbusy` | 先占住 API 端口 | **安装必须明确失败**并说出占用者，不能装作成功 |
| `uninstall` | 装完走一遍卸载 | 零残留（服务/注册表/防火墙/文件） |

## 已知的沙箱限制

- 沙箱内没有 VC++ 运行库，但安装包自带，不用管
- 沙箱每次都是全新的，不适合测「升级」——升级要去真机测
- 沙箱内网卡是 NAT，没有真实局域网，`lan enable` 只能验证到「绑了 0.0.0.0」
- 跑完沙箱会自动关机（`shutdown /s`），不留进程

## 加新场景

在 `inside.ps1` 的 `$scenarios` 里加一项，写清楚「刁难什么 + 期望什么」。
**一个场景只刁难一件事** —— 混在一起失败时说不清是谁的锅。
