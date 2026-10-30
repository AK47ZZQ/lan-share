# Git 工作流约定

> 本仓库为单管理员个人项目，本地 Git 管理。原则：**简单、可回滚**。
> AI 代理的提交纪律（禁 `add -A`、中文提交信息用 `-F` 文件、多会话避撞）
> 见根目录 [README.md](../README.md) 与本文档第 4 节。

## 什么在库里、什么不在

| 入库 | 不入库（见 `.gitignore`） |
|------|--------------------------|
| server.py / Caddyfile / docs/ / *.bat / web/src/ / mobile/lib/ | `data/`（用户文件 + .meta.db）、`logs/`、`__pycache__/` |
| README / PLAN-v3 / CHANGELOG | `caddy.exe`（49MB）、`certs/*.pem`（私钥严禁）、`web/node_modules/`、`web/dist/`、`mobile/build/`、`.mimosa/`、`.pytest_cache/` |
| mobile/android/、mobile/ios/ 源码与工程配置（Kotlin 薄桥、清单、pubspec 等） | 构建缓存同理不入库：`mobile/android/.gradle/`、`mobile/.dart_tool/`、`mobile/ios/Pods/`；`C:\lan-share-mobile\`（镜像构建工作区，脚本自动管理） |
| `certs/san.cnf`（证书生成配置） | `caddy.exe`（49MB，官网下载）、`certs/*.pem`（私钥严禁入库） |

## 日常流程

```bash
git status && git diff              # 改动前先看
git add -A && git commit -m "..."   # 小步提交，一个改动一个 commit
```

- **分支**：日常直接在 `main` 上提交（单人项目，分支收益低）；做有风险的大改动
  （如重构上传逻辑）时开 `feat/xxx` 分支，验证通过再合并。
- **提交信息**：第一行 ≤50 字概括，正文写"为什么"；格式参考现有 log（`git log --oneline`）。
- **打标签**：对外可见的稳定版本打 `vX.Y` 标签（`git tag -a vX.Y -m "..."`），版本节奏对应 CHANGELOG 条目。

## 回滚

```bash
git checkout -- <file>              # 丢弃单个文件未提交改动
git revert <commit>                 # 撤销某次已提交的改动（生成反向 commit）
git checkout v2.0 -- server.py      # 把某文件恢复到某标签版本
```

## 部署与本仓库的关系

服务实际运行的是本目录（`C:\lan-share` junction 指向此处），改代码 → 重启服务（README §常用运维）
→ 验证通过 → commit。**验证通过再提交**，避免把坏版本留在最新 commit 里。

## 异地备份（已配置）

远端：`https://github.com/AK47ZZQ/lan-share.git`（GitHub Private）。仓库代码
（不含 data/ 用户文件与私钥）已完整推送，2026-09-06 首次全量同步（30 提交 +
v2.0 标签）。**每次提交后顺手 push**，保持远端与本地同 commit：

```bash
git push origin main --tags
git status                                   # 看到 "ahead N" 即本地未推送
```

恢复：从远端 clone 后先看 [README.md](../README.md) 与 [DEVELOPMENT.md](DEVELOPMENT.md)；`data/` 与 `certs/*.pem`
不入库，需从本机 `C:\lan-share-backup`（数据备份）与 `C:\lan-share\certs`
（TLS 私钥）另行恢复——远端只保代码，不保运行数据。
