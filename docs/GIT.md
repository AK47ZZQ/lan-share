# Git 工作流约定

> 本仓库为单管理员个人项目，本地 Git 管理。原则：**简单、可回滚**。

## 什么在库里、什么不在

| 入库 | 不入库（见 `.gitignore`） |
|------|--------------------------|
| server.py / Caddyfile / static/ / docs/ / *.bat | `data/`（用户文件 + .meta.db 账号库） |
| README / PLAN / CHANGELOG / backup/v1 | `logs/`、`__pycache__/` |
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

## 异地备份（未配置）

仓库目前仅本机一份。如需异地备份，建议：

```bash
# 方案：私有远端（GitHub Private / 自建 Gitea），数据文件不在库里，push 安全
git remote add origin <url> && git push -u origin main --tags
```
