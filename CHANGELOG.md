> 更早版本（v0.4.1 ~ v0.20.0+60，2026-08-20 ~ 09-09，共 103 条）的完整变更史
> 见 [CHANGELOG-archive-v0.4-v0.20.md](CHANGELOG-archive-v0.4-v0.20.md)；
> 里程碑视角浓缩史见 README「演进」与 项目记忆/02-里程碑。

## 2026-09-11 — App v0.28.1+75：反馈闭环首次实战（修历史报错 + 管理分区提前）

反馈通道上线后的第一批真实反馈（data/反馈/，主人手机提交），当日闭环：
- **🐛 347f「反馈通道历史记录报错」→ 已修复**：历史列表把接口返回的
  `{"items":[...]}` 当裸数组强转（`r.data as List`）抛类型异常。兼容两种形态
  （Map 取 items / List 直用）。服务端日志全 200 定位到客户端，5 分钟修复。
- **📦 b304「管理面板位置提前，检查更新/关于放最后」→ 已实装**：「管理」分区
  移到反馈通道卡之后（普通功能前），「通用」组（关于与检查更新收尾）自然成为
  最后分区。
门禁 dart 0 / flutter 152；aapt 0.28.1/75（104602968 B，sha256 de449ac2…），
三件套 65 条。反馈状态已在 data/反馈/ frontmatter 回写（已修复/已实装），
App「检查更新」到 0.28.1 即可看到。

## 2026-09-11 — 服务端 v3.41 + App v0.28.0+74：反馈通道（想法/Bug 直达开发端）

主人需求：手机上有新想法/新 Bug 能及时发给电脑端（Agent）查看，并能看历史与
处理状态。头脑风暴三轮定稿：**反馈不进 DB，落成 `data/反馈/` 下的 md 文件**——
消费者是电脑端 Agent，直接读写文件即闭环；截图存 `反馈/img/`，Agent 直接看图。
**状态机**：已提交 → 处理中 → 已修复（bug）/ 已实装（想法）/ 已拒绝 / 已暂缓，
每条带"备注"（如 "v0.28.0+74 已实装"）。

**服务端（app/api/feedback.py，5 端点 + 契约 126→130）**
- POST /api/feedback（multipart：类型 idea|bug|other/标题≤80/正文≤2000/截图≤3
  每张≤12MB；登录即可发，成员也能报；限 20 条/天）。
- GET /api/feedback（own-only；admin 全量）· GET /{id}（详情，own/admin）·
  GET /{id}/img/{name}（截图鉴权读取，路径穿越防护）· GET /api/admin/feedback。
- frontmatter 平铺"键: 值"（不引 YAML），Agent 翻状态=改文件两行，列表即时反映
  （测试钉住该工作流：改"状态/备注"后 GET 立即变化）。

**App（v0.28.0+74）**
- 「我的」页宫格下新增"💬 反馈通道"卡 → 新页双 Tab：
  提交（类型分段选择/标题/详情/截图≤3 预览可删，file_picker withData 零新增
  依赖；自动附带 App 版本+系统信息）+ 历史（状态彩色徽章：灰已提交/蓝处理中/
  绿已修复/青已实装/红已拒绝/橙已暂缓；开发备注；下拉刷新；点开详情含截图）。
- me_page 入口卡不动宫格（5 项一行正好）。

**测试**：test_feedback.py 4 条（落盘+可见性/图片鉴权+穿越/Agent 改文件翻状态/
校验拒绝）；门禁 dart analyze 0 / flutter 152 / SQLite 220 / PG 双引擎 218+2 skipped；
APK aapt 0.28.0/74（104602968 B，sha256 c440462d…），三件套 64 条，线上核验通过
（提交落盘/列表/401/状态翻转演示）。

## 2026-09-11 — App v0.27.1+73：文件页多选新增"复制路径"

多选操作条新增**复制路径**按钮（下载/打包之后）：把所选条目的服务器真实地址
一键复制到剪贴板——abs_path 优先（9.6-P1-3 字段），旧服务端回退逻辑路径；
多选每行一条，文件夹同样适用，方便批量交给脚本/Agent 处理。单选也复用同一按钮
（与详情面板"复制路径"同语义）。
**门禁：dart analyze 0 / flutter test 152。**APK aapt 核对 0.27.1/73
（104454956 B，sha256 58e739af…），三件套 63 条，latest 线上核验通过。

## 2026-09-11 — 服务端 v3.40：通知管理（公告+站内通知）+ 会话治理增强

主人两条需求头脑风暴落地（概念划分：**公告**=主页长期布告，**通知**=个人收件箱
推送；会话治理补"按账号"维度）。**App 无变更（仍 v0.27.0+72）**。
**门禁：pytest 216（SQLite）+ 214/2 skipped（PG 双引擎）· ruff · mypy ·
vue-tsc · web 6。已重启上线核验。**

**通知管理**
- 新增**公告栏**（announcements 表，双引擎建表）：站长发布/删除，全家主页可见
  ——总览 Dashboard 公告卡 + 文件页顶部可关闭横幅（按公告 id 记住"不再显示"）。
  GET /api/announcements（登录即可读）+ POST/DELETE admin。
- **站内通知管理**（/notify-ops 新卡）：全站近 100 条、按成员筛选、单条删除
  （GET /api/admin/notifications?username= + DELETE /{id}）；成员被删后残留通知
  回退显示 #uid。
- **广播彻底删除**：DELETE /api/admin/notifications/broadcast/{bid}——记录与
  已送达站内通知（含已读）一并清除（撤回只删未读，这个删干净）；通知运营页
  sent 行新增"彻底删除"钮。
- 顺手修**存量双引擎 bug**：发送记录统计的 `SUM(read_at IS NULL)` 在 PG 报
  function sum(boolean) 不存在——线上"查看发送记录"本来就会 500；改 CASE WHEN
  双引擎兼容（PG 全量测试抓出）。

**会话治理增强（/governance 会话与设备）**
- **按账号一键下线**：POST /api/admin/users/{uid}/kick（scope=all|web|app）；
  对当前登录账号拒绝 all/web（防自踢，实测 400）。账号汇总 chips（每账号会话/
  设备数 + 一键下线钮）。
- **全部下线（保留我）**：DELETE /api/admin/sessions（排除当前请求自身会话），
  应急"全站强制登出"。
- 按账号筛选框（会话/设备列表即时过滤）；AdminView 成员行新增"🚪 踢下线"
  紧急操作（自己那行不显示）。

**测试**：test_v39_console.py 13→21 条（公告生命周期/全站通知/广播彻底删除/
kick 含自踢防护/全局清空保留自己）；契约 119→126 路径。
**核验**：公告增删+未认证 401、全站通知带成员名、自踢 400、新包
index-Cr2Rg7NA.js 生效。核验时误踢 uid=1（站长）Web 会话一次——重新登录即可。

## 2026-09-10 — 服务端 v3.39：产品定位更新——Web 后台化（App 主攻内容入口）

主人拍板五项决策（详见 `data/笔记/产品定位更新-*.md` §v2）：member 保留 web 内容
浏览；消费体验全部保留（P4 清理作废）；Dashboard 信息拉满；完整管理归 web、App
只做紧急/简单操作。**App 无变更（仍 v0.27.0+72）**。
**门禁：pytest 211（SQLite）+ 209/2 skipped（PG 双引擎）· ruff · mypy · vue-tsc ·
web 6。已重启上线核验。**

**定位声明**：App = 家的内容入口（照片备份/文件/笔记/分享）；Web = 站长管理工作台
（总览/内容/分享治理/通知运营/成员与权限/系统设置/运行状态）；/s/、/up、/login
访客门面不动。

**Web 后台化（P1–P3 全量落地）**
- 后台壳：左侧导航（宽屏常驻、<900px 抽屉）+ 顶栏 + 汉堡钮；路由 '/' 变总览、
  文件迁 /files；meta.admin 守卫统一把成员重定向 /files（不再回流 '/'）。
- 总览 DashboardView（新）：存储水位/成员/照片库/分享/回收站/秒传指纹库/会话设备/
  App 最新版 8 卡 + 最近动态 8 条。
- AdminView 拆分：/admin 只留成员·邀请·授权（改名"成员与权限"）；通知运营
  /notify-ops（手动广播+发送记录/草稿定时/撤回提醒）；系统设置 /settings（公网
  地址+Webhook+健康日报）——解决 AdminView users tab 长期堆卡问题。
- 治理工具 /governance（新）：大文件 TOP50 / 疑似重复（同尺寸聚类，标注"疑似"）/
  Web 会话与 App 设备列表+吊销。
- 运行状态 /status（新）：healthz 探针展示 + 版本对账 + 审计日志近 80 条。
- PWA/照片/音视频/笔记等消费功能**全部保留**（主人决策）。

**服务端（app/api/console.py，8 端点全部 require_admin）**
- GET /api/admin/overview（聚合快照）、/large-files、/duplicates、/audit、
  /sessions、/devices；DELETE /sessions/{hash}、/devices/{id}（吊销，审计留痕）。
- 契约快照 111→119 路径；tests/test_v39_console.py 8 条（admin/member 边界、
  聚类、吊销后立即失效）。

**执行中收口两坑**
- users 表 AST 守卫生效：JOIN users 违规 → 用户名映射走 users_store、概览统计走
  list_for_admin。
- **双引擎盲区再现**：SQLite 行可 `row[0]`、PG RealDictRow 只认列名——单测绿、
  生产 500（KeyError: 0）。改"单值 AS v 按名取 + 列表 dict(row)"，并以
  LANSHARE_TEST_PG_URL 跑 PG 全量（209 passed/2 skipped）验证。**新纪律：碰 DB
  的新端点，门禁加跑 PG 引擎。**

**上线核验**：healthz 双口 ok；overview 返回真实数据（4 成员/467 照片/磁盘 84.4%/
App 0.27.0）；large-files 480 文件；audit/sessions 正常；未认证 401；web 新包
index-DimMkrsK.js 生效。

## 2026-09-10 — App v0.27.0+72：需求文件《改.txt》头脑风暴落地批

依据 `data/笔记/改.txt`（09-06 需求）。头脑风暴先行：与主人确认两处开放决策
（宫格五入口组合 → 保留现有五项；3.1 笔记交互 → 返回即存）。
**门禁：dart analyze 0 / flutter test 152 全绿。**（纯 App 批，服务端无变更）

**#3.2 分类侧栏改覆盖式抽屉（🔴 立即项）**
- 内联 190dp 挤压列表 → Stack 覆盖层：宽约 70% 屏宽（clamp 230–320dp），
  右侧遮罩点按收回；选中分类/全部/未分类后自动收回；200ms 动画保留；
  子分类折叠（`_expandedCats`）已有保留；拖拽移动到分类不受影响。

**#3.1 笔记编辑器「返回即存」（选定方案）**
- 返回键 = 静默保存 + SnackBar「已保存到 …」，不再每次弹"放弃修改？"；
  内容为空视为误触按放弃处理（本地草稿兜底）；仅保存失败才弹
  "保存失败，要放弃这次修改吗？"

**#2 「我的」页布局重构**
- 功能宫格去掉管理面板 → 5 项一行（自动备份/传输/我的分享/笔记/回收站，
  Wrap 保留作防溢出兜底）；底部新增独立「管理」分区（仅 admin 可见），
  内含管理面板入口，与普通功能隔离。

**#1 文件页复制路径——需求已存在，无需开发**
- 核实 9.6-P1-3 批次已实现：文件详情面板「复制路径」，优先服务端真实
  abs_path、回退逻辑路径，带"已复制路径"提示。

**分发**：APK v0.27.0+72，104454908 B，sha256 edcb2d46…，aapt 核对 versionCode 72；
三件套同步 versions.json 62 条；线上 latest/history 核验通过。

## 2026-09-10 — 服务端 v3.38 + App v0.26.0+71：知识库/图谱整体下线 + 宫格溢出修复

主人决策：知识图谱"算了算了"，删掉所有 KB 功能、笔记回到原有功能；顺带修
「我的」页功能宫格溢出。
**门禁：pytest 203 passed / ruff / mypy 全绿 / dart analyze 0 / flutter test 152 /
web 6 passed + vue-tsc 构建。**

**删除面（三端）**
- 服务端：`app/api/kb.py` + `app/services/kb.py`（graph/backlinks/neighbors/
  names/context/reindex 六端点）与 main.py 挂载；`tests/test_kb.py`（31 条）与
  test_v37_notes_kb 的 kb 段（4 条）；conftest 的 `reset_kb_state`；PyYAML 依赖。
  契约快照重导：**117 → 111 路径**。
- App：`data/kb.dart`（客户端镜像解析）+ `ui/kb_graph_page.dart`（图谱页）+
  batch076 测试（26 条）；notes_page 图谱入口/`[[` 补全/frontmatter 自动维护/
  反链面板/预览双链可点；doc_viewer 双链跳转与 kblink 预处理。
- Web：`views/KbView.vue` + `kb.ts` + `/kb` 路由 + 菜单入口 + api.ts kb 段 +
  FileViewer 双链渲染（preprocessWikilinks/kbOpen 事件链）；dist 重建
  （index-BwxLIFF8.js）。

**保留（笔记主功能化成果不受影响）**
- 底栏 笔记 一级页签、分类横滑栏（含空分类）、点按预览优先、条目复制路径、
  `/api/notes/list` 的 categories+abs_path 契约与回归测试。

**修复**
- 「我的」页功能宫格 6 个入口（v0.25 传输迁入后）单行 Row 溢出屏幕外——
  改 `Wrap`（spaceAround + runSpacing），放不下自动换行。

**上线与分发**
- 服务已重启（lan-share-api），healthz 8765/8766 双口 200；`/api/kb/graph` 已登录
  访问 404；notes categories 正常；web 新包已生效。
- APK：v0.26.0+71，104438524 B，sha256 88e8f78b…，aapt 核对 versionCode 71；
  三件套同步 versions.json 61 条；`/api/app/latest`·`history` 线上核验通过。

## 2026-09-10 — 服务端 v3.37 + App v0.25.0+70：笔记主功能化 & 知识库完善

依据主人 5 项需求（底栏换位 / 手机复制路径 / 空分类修复 / KB 完善 / 预览优先）。
**门禁：pytest 238 passed（SQLite）/ ruff / mypy 全绿 / dart analyze 0 / vue-tsc 0 / web 6 项。**

**服务端**
- `/api/notes/list`：新增顶层 `categories`（path/name/count，**含空分类**）与逐条
  `abs_path`（服务器真实存储路径，与 files 列表同语义）。修复"新建分类成功
  但 App 侧栏看不到"——旧分类树只从笔记 category 聚合，空分类永远不出现。
- `/api/kb/graph`：自动挂载**分类枢纽节点**（`kind: "folder"`，含空分类），
  新建分类/新笔记随指纹变更自动入图。设计要点：枢纽边**只用于布局聚类**
  （把同分类笔记聚成一簇），不进 `edges`——度数/反链/孤岛语义保持"知识链接"
  纯净（有回归测试钉住）；失效指纹纳入**目录**（空分类的建/删/改名不改变任何
  笔记文件，漏掉会导致图谱永不重建）；可见性：任一后代可见⇒枢纽可见，
  **空分类按目录自身物理归属过 visible()**（path=/ 时防"他人目录存在性"泄露）。
- `/api/kb/context`：`note` 增 `abs_path`（图谱/Agent 定位同一文件用）。

**App（v0.25.0+70，已发版：flutter test 179 通过后构建 release 并分发三件套，
sha256 2229f55f…，线上 /api/app/latest·history 核验通过）**
- 底栏换位：`首页/照片/文件/笔记/我的`；传输页改 Navigator.push（首页传输卡、
  文件页入口、"我的"宫格三处可达）。
- 笔记页：分类横滑栏（分类优先显示，含空分类）+ 点按改**预览优先**
  （DocViewer 渲染，编辑走预览页编辑笔）+ 条目菜单新增**复制路径**。
- 图谱页：点按节点即显示**正文预览**（/api/kb/context）+ 打开/复制路径；
  分类枢纽显示成员清单与"只看该分类"。
- **顺带修复两处历史 bug**：分类过滤 `startsWith('$_cat!/')` 的 `!` 是字面量，
  子分类笔记永远匹配不上（App 与 Web 同款各一处）。

**Web**
- /kb：分类枢纽渲染（accent 色、加大节点）+ 点按显示正文预览/成员清单 +
  "只看该分类"；dist 已构建并线上生效。

## 2026-09-10 — 服务端 v3.36：独立复审修复批（工程流程 + 生产等价性 + 架构收口）

依据《项目记忆/11-独立工程复审》执行（该报告 P0/P1/P2/A 全部逐条落地，见其 §九
执行记录）。**门禁终态：pytest 231 passed（SQLite）/ PG 全量另核 / ruff · mypy 全绿 /
web npm test 6 passed。**

**工程流程（P0）**
- **`app/roles.py` 入库**：该模块被 9 个模块（含 security/spaces 安全内核）import
  却从未进版本库 —— fresh clone 会 ImportError 起不来。
- **整批 v3.35 与历史改动分 4 个逻辑提交入库**，工作区清零，重建回滚点。

**生产/测试等价性（P1）**
- **双引擎测试**：`conftest` 支持 `LANSHARE_TEST_PG_URL` —— 在目标库的**临时
  schema** 里建表（`search_path` 隔离，不碰生产 `public` 表，也不需要 CREATEDB
  权限），同一套测试改跑 PostgreSQL，用完 DROP SCHEMA。PG 侧 `_PgConn` /
  `_pg_sql` / `_PG_SCHEMA` 这条**生产真正在跑**的代码路此前零覆盖。
- **新增 `.github/workflows/ci.yml`**：ruff + mypy + pytest(SQLite) 与
  pytest(PG, postgres:16 service 容器) 并行，web 单测独立作业。
- **跨引擎 schema 漂移守卫**（`test_pg_schema_parity.py`）：加表/加列只改
  SQLite 分支漏了 PG → 门禁直接红（"生产库缺列"事故的直接探测器）。

**安全与正确性（P2）**
- **Webhook 重定向逐跳复校验**：原实现 urlopen 默认跟随 302，"发送前校验"只校验
  起点，公网域名 302 到 `127.0.0.1` 即可绕过。出网出口显式化为
  `notify._webhook_open` 命名桩点（原测试 patch 的是 stdlib 全局，改实现会被静默绕过）。
- **WebDAV 真锁**：原 `LOCK` 返回 token 却不记状态（假锁），并发 PUT 可互踩。
  改为 RFC 4918 语义的进程内锁表（他人写 423 / 非锁主 UNLOCK 403 / 到期自释放），
  PUT/MKCOL/DELETE/MOVE/COPY 全部加守卫；不用锁的客户端零影响。
- **真事务**：新增引擎中立、嵌套感知的 `db.transaction()`；改密+吊销、重置密码+
  吊销、PATCH 角色/激活/配额、删号、**邀请码 redeem 建号+认领** 改真事务
  （此前靠补偿删除，中途崩溃留孤儿账号）。`_db_lock` 改 **RLock**，根治嵌套自死锁
  （R1-P0-1 同类）。
- **PG 连接有界化**：全局登记 + 上限（`SHARE_PG_MAX_CONN`，默认 16）+ 空闲 300s
  回收 + 死线程孤儿回收 + atexit 统一关闭，不改任何调用方。

**架构收口（A）**
- **users 表单一主人**：新增 `app/store/users.py` 语义仓储，原散落 **11 个模块
  80 条 SQL** 全部迁出，业务模块不再认识表结构（加一列只改 db.py DDL + 仓储）。
- **settings 降 L0**：`app/store/settings.py` → `app/settings.py`，消除
  security → store 分层倒置；`app/store/` 留给表仓储。
- **async 路由禁裸 DB**：trash / share / uploads / auth 共 **27 处**同步 DB 往返
  全部抽同步助手 + `run_in_threadpool`（单进程 uvicorn 下曾会阻塞全体请求）。

**回归守卫（新增 3 条 AST 级守卫，把"约定"变成 CI 可执行断言）**
- `users` 表 SQL 只允许出现在 store 与 db.py；
- `async def` 自身作用域内禁止 `_db()`；
- PG schema 必须覆盖 SQLite 全部表与列。

**Web 端补测（原四端唯一零测试）**
- `web/test/`：Node 内置 runner，零依赖零网络；含 `filekinds.ts` ↔
  `app/filetools.py` 跨端扩展名契约守卫（历史"三端漂移"事故同类）。

**已知未做（见 11 号报告 §9.3）**：12.5G 用户数据清理（需站长决策）、照片全盘
扫描改增量索引（需独立批次）、拆 god 文件（Flutter 侧本机无法 `flutter test`
验证）、运维项（需运维窗口）。**服务未重启**（本工作区即线上，需主人确认）。

## 2026-09-10 — 服务端 v3.35：安全加固与"约定收口"第一批

依据《项目记忆/09-项目建议报告》与《10-改进计划-v3.35》执行，**行为等价 + 纯增强**，
不含功能变更、不改 API 形状（契约仍 117 路径）。

**安全小项（B）**
- **Webhook SSRF 防护**（`notify.assert_public_webhook_url`）：scheme 白名单 +
  字面 IP 私网/回环/链路本地/保留段判定 + 域名解析结果须全为公网；**保存时**
  与**发送前**双处校验。实测拒绝 `ftp://`、`127.0.0.1`、`192.168.*`、`[::1]`、
  `169.254.*`，公网域名放行。残留（有意接受并已文档化）：不防 DNS rebinding 与
  urllib 默认 302 跟随——webhook 仅 admin 可配置，作纵深防线足够。
- **管理端用户列表不再拉取密码哈希**：SQL 由 `SELECT webdav_password_hash` 改为
  `(webdav_password_hash IS NOT NULL) AS webdav_set`，字段名与类型不变。
- **APK SHA 缓存加锁**：`appver._apk_sha_cache` 增 `threading.Lock`，消除 App
  后台高频检查更新的并发读写竞争。

**约定收口（A/C）——"存在 N 份的约定"收成"唯一事实源"**
- **路径转换单一事实源**：新增 `filetools.to_rel()`，收编 9 个模块里手写的
  `str(x.relative_to(SHARE_ROOT)).replace("\\","/")` **38 处 → 1 处**；顺带修掉
  webdav 两处漏 `replace` 的审计记录（Windows 下带反斜杠）。
- **角色常量单一事实源**：新增 `app/roles.py`（`ADMIN/MEMBER/GUEST/ALL_ROLES`），
  服务端 9 文件 **39 处**角色字面量收编。**刻意不动**通知分组与保留用户名集合
  （同名不同义，roles.py 内已显式警告勿复用）。
- **私有符号公共化**：`filetools._file_dict → file_dict`、
  `_RESERVED_NAME → RESERVED_NAME`，消除跨模块 import 下划线私有符号的 3 个点。

**文档**
- README 修正过期测试基线（pytest 169→210、Dart 144→179，补契约 117 路径）。
- 《09-项目建议报告》勘误：`check.bat` 实测已含 pytest/ruff/mypy/pip-audit 四段，
  原"门禁缺段"结论作废并移入"已完成勿重复"清单。

**运维与备份（欠账清理）**
- **备份恢复演练 Tier-1 实测通过**：最新 dump `lanshare-20260910.dump` 的 sha256
  与 sidecar 一致、`pg_restore -l` 可读、**16 张表**齐全。
- **文件数据侧新增 manifest 校验**（`backup.bat` 产出 `files-manifest.txt`：
  文件数 + 总字节；`scripts/restore-drill.bat` 比对，不一致即 `DRILL FAILED`）。
  补上"robocopy 增量且不删、跑残了看不出来"的盲区；首条基线
  **7454 文件 / 20,147,257,165 字节**。演练文档见 `docs/OPERATIONS.md §4`。
- **待办逐条复核**：关闭 3 项过期/已完成条目（ZZQZZQ 授权——实为 admin 无需授权；
  成员视角预览——早已实现；check.bat 四段门禁——早已齐备），未关闭项逐条注明原因。

**门禁**：pytest 210 / ruff / mypy 全绿。

## 2026-09-10 — v0.24.0+69：知识库与图谱（双链/图谱/agent 三层，全端上线）

知识库 = 笔记的第三种视图（方案 A 加层，零新存储）：`data/笔记/知识库与图谱功能设计方案（待确认）.md` v2 定稿 → M-K0~K4 一版全交付。

- **M-K0 服务端**：`app/services/kb.py` 引擎（wikilink/别名/路径解析、撞名
  裁决同目录>最短路>casefold、PyYAML 归一化、缓存指纹=排序(relpath,size,mtime)
  哈希、LRU4+每根构建锁、确定性 FR 预布局+`.kb/layout.json` 增量持久化）+
  `app/api/kb.py` 六端点（graph/backlinks/neighbors/names/context/reindex，
  全部 require_user+resolve 同源授权）；依赖 +PyYAML==6.0.3（safe_load+64KB）
- **M-K1 App 图谱页**：CustomPainter 手绘力导向（服务端预布局零仿真冷启动）、
  拖节点局部增量/平移/双指缩放、长按高亮一跳、筛选抽屉（含"只看幽灵=待写
  清单"）、搜索定位脉冲、ghost 一键创建；LOD >300 隐标签/>800 边降透明
- **M-K2 编辑器双链**：`[[` 补全浮层（/api/kb/names 数据源）、保存时
  frontmatter 自动维护 created/updated、反链面板（含摘录）、md 预览与
  DocViewer 的 `[[ ]]` 渲染成可点链接
- **M-K3 agent 层**：context 端点（全文≤20KB 字节+一跳邻居摘要）+
  `docs/KB-CONVENTIONS.md`《知识库约定》（含 curl 接入示例）
- **M-K4 web 图谱**：`/kb` 路由 Canvas 2D 手绘 KbView（与 App 同规格），
  FileViewer 落点 + marked 双链渲染（kbOpen 事件页内跳转）+ 节点卡反链
- **测试与审查**：pytest 179→**210**（test_kb.py 31 条）；flutter test
  152→**179**（batch076 26 条）；契约快照 111→**117 路径**；
  dart analyze 0 / vue-tsc+vite 绿
- **双路审查收口**（审查代理 + 红队对抗代理，全部实证复现）：
  - **P0 级越权（红队 V1/V2）**：`path=/` 全库扫描不过 visible()，member 可见
    他人 private/incoming 笔记元数据/摘要/反链摘录与幽灵名——kb 全部出口加
    `_vis_plan` 可见性收口 + 幽灵需可见源；**notes.py 同款存量洞一并修**
  - 红队 V3 摘录逐目标重读放大冷构建→构建循环单次读取；V4 只读请求不落盘
    `.kb/`（persist=write 权限）；V5 三端解析不一致→客户端严格镜像服务端
    （带路径不回退文件名/别名、撞名 casefold 裁决）；V7 病态 YAML 预检
    （连续括号>64 跳过）；审查 P1-2 context 改字节级截断；V8 web label 转义
  - 门禁：check.bat 四段全绿（pip-audit 的 httpx2/httpcore2 为共享 venv 存量）

## 2026-09-10 — v0.23.1+68：重构批三轮审查收口

v0.23.0 重构批经 3 轮审查（自查 → 双路对抗审查代理 → 对抗性补测回归）：

- **修复 1 处协议语义回归**（自查 R1-A，双审查代理交叉确认）：UploadClient
  统一 state 探测为"并集"，改变了 backup 引擎原"替换"语义（账本以服务端
  state 为唯一真值）——resume 增加显式 merge 参数，两引擎各保原语义
- **新增 8 条 UploadClient 单测**（144→152）：分片数学边界（size=0/整除/
  末片截断/单片）、init 防御校验（畸形 sid/0/负/超协议上限拒绝、64MB 接受）、
  resume 两种 merge 语义回归钉
- 双路审查结论：服务端路由顺序/行为保真/cleanup 五件套/安全中间件逐行核查
  无 P0/P1；App 协议重构与旧实现全路径等价
- 门禁：pytest 179 / ruff+mypy 33 文件 / dart analyze 0 / flutter test **152**
- 审查建议挂账：cleanup_loop 的取消机制随 lifespan 迁移一并处理（P2）


## 2026-09-10 — v0.23.0+67：架构审查重构第二批（消循环 + 拆上帝模块 + 协议单实现）

依据《07-内聚耦合与可维护性审查》§5.2~5.5 执行：

- **批次2 服务端消循环**：app 对象与 security_middleware 迁居 `app/asgi.py`
  （依赖图底层），11 个 api 模块全部改 APIRouter，main.py 组装根统一
  include_router——**11 对 main↔api 循环 import 消除**；server.py 瘦身为纯
  入口（uvicorn main 块保留）；**测试解钉**：conftest 与 17 个测试文件弃
  server shim 改走 app.* 官方路径，security/share/auth/photos 新增官方
  reset 接口替代直掏私有字典
- **批次3 拆上帝模块**：files.py(1030行) 按域拆为 uploads（分块协议）/trash/
  previews + services/archive（zip 票据表收口，admin 导出不再偷 _zips）；
  system.py(750行) 拆为 photos（时间线+扫描器）/albums（**与 social.py 合流
  ，四处理清成单模块**）/qr；webdav/auth 的私人空间守卫上收 spaces.py 公共
  API；新增 services/cleanup.py **注册式**清理（uploads/archive/share 各自
  register_sweep，system 不再掏别家私有状态）；services/passwords.py 收口
  scrypt+哑哈希等时校验（webdav 改引，消函数体反向 import）；
  services/accounts.py **改名流程双实现合流**（P1 级重复 + normcase 漂移
  收口），clean_username 用户名校验同源
- **批次4 App 协议单实现**：新增 `data/api/upload_client.dart`——
  init/秒传/state/分片补齐/finish 与全部响应防御校验（会话 id 白名单、
  分片协议上限 clamp）只剩一份，transfer/backup 两引擎换装（07 §4.1-1 的
  "同坑"逐字节拷贝消灭；重试/暂停/账本语义仍归各引擎）
- **批次5 契约与部署**：新增 `scripts/export-openapi.py` +
  `contracts/openapi.json`（111 路径提交入库）+ `test_contract.py` 快照
  比对——服务端契约变更从此 git diff 可见、测试红灯兜底；`env.bat` 部署
  单一事实源（端口/python 路径），start.bat/stop.bat 接线
- 门禁：pytest **179**（178+1 契约）/ ruff+mypy 33 文件 / dart analyze 0 /
  flutter test 144。服务端 v3.33 / App v0.23.0+67


## 2026-09-10 — v0.22.0+66：架构审查重构第一批（P0 分片契约 + 事实源五件套）

依据《07-内聚耦合与可维护性审查》与《06-全量测试与审查报告》合并排期执行：

- **P0 分片契约冲突根治**（App >5GB 文件上传必败）：服务端
  `config.py` 立 `MAX_CHUNK_SIZE = 64MB` 单一事实源并随 /api/upload/init、
  /state 响应下发 `max_chunk_size`；移动端 transfer_engine/backup_engine 两
  引擎防御 clamp 从写死 16MB 改为引用协议常量（新 `data/api/protocol.dart`
  `kMaxChunkSize`）；+3 契约测试钉住 `chunk_size_for` 输出范围与响应字段
  （test_largefile.py）
- **06-P1-1 管理端专辑非原子**：system.py create/delete_album 从裸
  `_load+_save` 改走 `_mutate_albums` 锁内读-改-写（与 social.py 成员侧对
  齐），50 条上限由静默截断改显式 400、重复月份 409 保持
- **06-P1-2 share_download 防御纵深**：读侧补 `safe_path()` 闸门——shares
  表路径若被污染含 `..`/点前缀即 403，不再直接拼 SHARE_ROOT
- **Web admin 路由守卫**（06-P2-1/07 §3.3）：App.vue watchEffect 消费
  `route.meta.admin`，非 admin 直达 /admin 即时重定向，不再短暂渲染管理页
  结构；closeMenu 的 document 监听器补 onUnmounted 清理（06-P2-2）
- **事实源① settings**：新增 `app/store/settings.py`（get_setting/
  set_setting + 全部 key 常量），收编 admin/appver/healthreport/notify/
  security/share 六文件 8 份重复 helper 与 17 处裸 SQL；专辑 key 同步收编
- **事实源② 服务端扩展名**：IMG_EXTS/VID_EXTS 定于 `filetools.py`
  （.gif 进时间线、.m4v/.3gp 获缩略图能力），files.py 与 system.py 共用，
  顺带消除 system→files 的 `from app.api.files import IMG_EXTS` 反向依赖；
  .svg 有意不收（内联预览 XSS 面），注释言明
- **事实源③ Web filekinds 漂移修复**：filekinds.ts 新增 THUMB_EXTS（派生
  集合，自带 .svg 排除）与 .m4v/.3gp/.opus 对齐；HomeView thumbOf 弃 14 个
  手写扩展名改引用、fileIcon 委托 ui.iconForName（.ogg 此前显示 📄）；
  FileViewer thumbOf 停止给 svg 发必 404 的缩略图请求；PhotosView 扩展名
  正则收编 extOfName
- **事实源④ App media_kinds**：新增 `data/media_kinds.dart`
  （kImgExts/kVidExts/kAudExts + extOfName），收编 files_page/transfers_page/
  photos_page/home_page/local_files/app_theme 六处手写集合——根治"文件页不
  识别 .m4v 为视频、传输页识别"漂移，文件页 .m4v/.3gp 顺带获得缩略图
- **事实源⑤ App 备份 prefs 键**：新增 `backup/backup_keys.dart`
  （BackupKeys 16 键），收编 engine/config/settings/home/main 五文件 49 处
  裸字符串——跨 isolate 隐性契约转显式常量（Kotlin 侧共读的
  backup_enabled/backup_worker_state 在注释言明双端约束）
- 门禁：pytest **178**（175+3 契约）/ ruff+mypy 全绿 / dart analyze 0 /
  flutter test 144 / vue-tsc+build 绿。服务 v3.32 / App v0.22.0+66


## 2026-09-10 — 9.9 复审收尾批（P3 清尾 + 备份链路加固）

- **备份链路可验证**（挂账"恢复演练"的软件侧落地）：backup.bat 每次备份
  写 .sha256 sidecar + dump 体积下限（<10KB 判失败）；新增
  scripts/restore-drill.bat 一键演练（最新 dump 哈希校验 + pg_restore -l
  完整性 + 表数据段计数），全只读、可挂计划任务周跑；lanshare 角色无
  CREATEDB（已实测），全量恢复进 scratch 库写成脚本内文档化步骤待拍板
- **requirements.txt**：手写钉版 20 项（hermes venv 为共享环境不能
  pip freeze），运行时 + 门禁依赖与生产实测版本一致
- **stop.bat 重写**：net stop 服务优先 + 按端口 PID 兜底，弃用 wmic 模糊匹配
- **Web**：highlight.js common→core + 12 语言按需注册（~200KB gzip 大包拆成
  按语言小 chunk）；扩展名列表抽 filekinds.ts 唯一事实源（FileViewer/ui.ts
  双份漂移收口，heif 图标补齐）
- **CHANGELOG 拆分**：主文件只留 v0.21 系列（3.6KB），103 条历史归档至
  CHANGELOG-archive-v0.4-v0.20.md
- 实测坑补录：bat 里裸 `find` 经 Git Bash PATH 解析成 GNU find（把 /c 当
  C 盘根递归扫描挂死）、%TEMP% 被 bash 导出成 /tmp——手动调试 bat 必踩，
  计划任务环境免疫

## 2026-09-09 — v0.21.4+65：改进建议 9.9 全量修复批

- **登录密码内存生命周期**：Token 换取结束（含失败路径）即清内存明文密码——
  此前仅成功路径清理，网络失败时密码残留进程内存
- **首页断网反馈**：三路轮询（磁盘/最近上传/动态）连续失败时显示
  "连不上服务器，正在自动重试…"横幅，恢复后自动消失；仅无响应类失败计入，
  4xx/5xx（服务器可达）不误报
- **欢迎头折叠插值除零守卫**：分母为 0 的极端布局回落 t=0，防 NaN 透传 Opacity
- **Keystore/备份关键路径留痕**：SecureStore 静默降级、备份引擎三处静默吞错
  补 debugPrint——国产 ROM Keystore 间歇失败导致"反复掉登录"时终于有日志可查
- shrinkWrap 九处经实地评估后保留：BottomSheet 内 Flexible+shrinkWrap 是紧凑
  面板的正确写法，盲目移除属视觉回归
- 配套服务端修复（同批）：WebDAV PROPFIND 线程池化（大目录不再冻结全站）、
  DAV Basic 哑哈希等时（堵用户名时序枚举）、start.bat 钉 hermes venv python
  （根治"守护拉起即死"）、APK sha256 按 mtime+size 缓存、_user_field 列名
  白名单、/api/auth/me 容忍损坏 totp_rc、zip 打包票据上限 200、存量 mypy
  三错修复；Web：401 豁免补 /up、成员视角预览登出即清、大文件上传提示去
  硬编码地址、vite 显式 sourcemap:false
- 门禁：pytest 175（+6 回归测试）/ Dart 144 / analyze 0 全绿

## 2026-09-09 — v0.21.3+64：欢迎头色带根治（真机反馈第三轮）

- **色带根因**：FlexibleSpaceBar 背景按展开高绘制，SliverAppBar.backgroundColor
  按收起高露底——两者高度差在下拉回弹/收缩中露出一条纯色色带
- **根治**：欢迎头改 `SliverPersistentHeader` + 自绘 delegate——渐变永远铺满
  当前实际高度（收缩/回弹/过滚任何状态都不露底色），问候行与收起标题随
  shrinkOffset 交叉淡入淡出；`AnnotatedRegion<SystemUiOverlayStyle.light>`
  显式保持状态栏白图标（此前由 AppBar 自动管理）

## 2026-09-09 — v0.21.2+63：首页欢迎头减法瘦身（真机反馈第二轮）

- **欢迎头只留"问候+头像"**：三枚微数据胶囊与下方「服务端存储」卡信息完全
  重复（已用空间同屏出现两次）、"局域网入口·下拉刷新"副行价值低——都删掉，
  头部高度 ~200dp→~105dp，首屏多显示约一条动态
- **收起态工具条做薄**：toolbarHeight 显式 48dp（此前默认 56+偏移显得厚），
  标题在工具条内垂直居中
- **列表底部收紧**：SliverPadding 底部 24→8，"管理卡片"不再悬在大片空黑里
- 清理随删：_headStat/_serverPublic 未用代码

## 2026-09-09 — v0.21.1+62：首页欢迎头热修（真机反馈）

- **标题遮挡修复**：收起态标题"LAN Share"此前由 SliverAppBar.title 承载，
  展开时也渲染、与问候语重叠——改画进 flexibleSpace 的收起态层，
  透明度随折叠进度淡入（展开时完全透明）
- **欢迎头空白收紧**：expandedHeight 196→170、底部内距 32→18、内容间距
  压缩，消除内容下方一大段空渐变；BrandHeader 新增 bottomPadding 参数
  （默认 32 不影响登录页/关于页）

