# LAN Share 知识库约定（KB-CONVENTIONS）

> 面向两类读者：**人**（把笔记目录用成 Obsidian 式知识库的书写习惯）与
> **agent**（用 API 读写知识库的机器约定）。服务端规则详见
> `app/services/kb.py` 模块头注释；设计规格见
> `data/笔记/知识库与图谱功能设计方案（待确认）.md`（v2 定稿）。

知识库不是新存储——它就是网盘里的 `.md / .txt / .html` 普通文件
（与笔记功能同库同源）。任何目录都可以是一张图谱（多库），PC 端
Obsidian 直接打开同一目录即人机共库。

## 1. 双链语法

```markdown
今天读了[[费曼学习法]]，和[[Zettelkasten 卡片盒]]的思路对上了，
细节见 [[学习/输出倒逼输入|输出逼输入]]。#学习方法
```

- `[[目标]]` / `[[目标|别名]]` 为主；`[[目标#小节]]` 锚点写法 v1 解析时剥掉锚点。
- 目标解析顺序：**aliases 精确 → 文件名（不含扩展名）精确 → 相对路径**；
  撞名时：同目录 > 最短路径 > 字典序；大小写不敏感（服务端跑 Windows）。
- 未命中 = **幽灵节点**（图中虚线，代表"想写还没写"的笔记）。
  带路径分隔符的未命中目标不生成幽灵。
- 标准 markdown 链接 `[text](相对路径.md)` 同样入图。
- 代码块里的 `[[ ]]` 也会被解析（v1 与 Obsidian 同款行为，注意别在代码块里举例）。

## 2. frontmatter 元数据

```markdown
---
title: 费曼学习法
tags: [学习方法, 输出]
created: 2026-09-10
updated: 2026-09-10
aliases: [Feynman Technique, 费曼技巧]
status: seedling   # 数字花园：seedling(苗)/budding(芽)/evergreen(常青)
---
```

- `tags` / `aliases` 建议用列表；写成单值（`tags: 学习方法`）也兼容。
- App 编辑器保存 md 时**自动维护 `created`（缺失才补）与 `updated`（每次刷新）**，
  其余字段不碰。
- 解析失败/缺字段 → 静默按无元数据处理，笔记照常入图。

## 3. 组织习惯（建议，不强约束）

- **一篇一概念，文件名即节点名**：`费曼学习法.md` 优于 `新建笔记 3.md`。
- 每个分类夹放一篇 `MOC.md`（Map of Content），人和 agent 都从这里进。
- `#标签` 写在正文里（行首或空白后），frontmatter `tags` 与其自动合并。
- 图谱页「只看幽灵」= 待写清单（知识缺口提示）。

## 4. Agent 接入

权限与人在 App 完全一致（同一套 Bearer 设备 Token / 会话），无新增暴露面。

| 端点 | 用途 |
|------|------|
| `GET  /api/kb/graph?path=<目录>` | 全图 JSON（节点含预布局坐标/度数/摘要，边含权重） |
| `GET  /api/kb/names?path=<目录>` | 轻量名单（补全/解析用） |
| `GET  /api/kb/backlinks?path=<目录>&file=<笔记>` | 反链（含所在行摘录）+ 出链 |
| `GET  /api/kb/neighbors?path=<目录>&file=<笔记>&depth=1` | 以某篇为中心的子图（depth≤2） |
| `GET  /api/kb/context?path=<目录>&file=<笔记>` | **agent 首选**：全文（≤20KB，truncated 标志）+ 一跳邻居/反链摘要 |
| `POST /api/kb/reindex?path=<目录>` | 强制重建索引（需写权限；日常自动失效无需调用） |

`path` = 图谱根目录（与笔记页当前目录同语义），`file` = 笔记的完整相对路径
（与 `/api/notes/list` 返回的 `path` 字段同源）。

### curl 示例

```bash
TOKEN="<设备Token>"; BASE="http://<服务器>:8766"

# 全图
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/kb/graph?path=shared/笔记" | jq '.stats, (.nodes[0])'

# agent 上下文注入：本篇全文 + 一跳邻居摘要（个人库规模下比向量 RAG 更准更便宜）
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/kb/context?path=shared/笔记&file=笔记/学习/MOC.md" | jq .
```

### agent 使用约定

1. 入口：先取 `graph` 或从 MOC 笔记出发，按 `edges` 爬行即可导航知识拓扑，
   不必读全文。
2. 深读：对目标笔记调 `context`，一次拿全文+邻域摘要。
3. 写入：双链写 `[[文件名（不含扩展名）|别名]]`；文件名即节点名；
   新笔记建议补 frontmatter（App 保存会自动维护 created/updated）。
4. 幽灵节点代表计划中的笔记；为 ghost 建内容 = 新建同名文件即可。


## v3.37 增量：分类枢纽节点（folder hubs）

- `GET /api/kb/graph` 响应新增：
  - `nodes[]` 中 `kind: "folder"` 的节点（`id: "folder:<分类路径>"`、
    `path: <分类路径>`、`count: 后代笔记数`）——**含空分类**；
  - 顶层 `folders[]`（同数据的扁平清单，便于渲染分类侧栏）。
- **边语义不变**：`edges` 仍只含 `[[知识链接]]`；目录归属**不在 edges 里**
  （客户端按 `note.category` 自行把笔记归到枢纽下绘制）。度数/孤岛/反链的
  口径全部维持"知识链接"，未被目录归属污染。
- `GET /api/kb/context` 的 `note` 新增 `abs_path`（服务器真实存储路径）。
- 失效指纹已纳入**目录**：空分类的建/删/改名会触发重建。
- 可见性：非空枢纽随"任一后代可见"可见；空枢纽按目录物理归属过 `visible()`。
