> 更早版本（v0.4.1 ~ v0.20.0+60，2026-08-20 ~ 09-09，共 103 条）的完整变更史
> 见 [CHANGELOG-archive-v0.4-v0.20.md](docs/archive/CHANGELOG-archive-v0.4-v0.20.md)；
> 里程碑视角浓缩史见 README「演进」与 docs/项目记忆/02-里程碑。

## 2026-09-18 — 服务端 v3.56：双引擎 SQL 可移植性 + 后台任务可见性（架构复查批）

全库架构复查发现的**一个线上故障 + 两类系统性盲区**，本批全部只动服务端，
**App 与 Web 无需更新、不重发包**。

**🔴 修：缩略图预热在生产库上从未运行过一次**
- v3.54「照片墙性能根修」加的后台预热，其 SQL 用了 SQLite 独有的 `rowid`
  排序键——测试默认跑 SQLite 所以全绿，生产跑 PostgreSQL 每 30 秒抛一次
  `column "rowid" does not exist`，被循环的保活异常处理吞成一行日志，
  **静默停摆数日**（日志实测 3069 次）。结果：那次性能修复只落地了一半，
  照片墙首次浏览仍在按需现生成缩略图。
- 同句还有第二个坑：`ORDER BY added_at DESC` 对 NULL 的位置两引擎相反
  （SQLite 沉底 / PG 置顶），存量未打点的旧行会在生产上顶满候选集。
- 现在：排序键改双引擎中立写法 + 启动期把存量 `added_at` 空洞回填清零。
  **重启后预热会第一次真正开始工作，照片墙首屏随时间继续变快。**

**🟠 补两道同类门禁（治"这类问题以后必须当场红"）**
- 新增**存储层读函数双引擎冒烟矩阵**：9 个表主模块的每个公开读函数在真引擎上
  执行一遍（此前那条查询在整轮测试里从未被执行过）。
- 新增**SQLite 专有语法守卫** `scripts/check-sqlite-isms.py`：静态扫业务层 SQL
  字面量，命中 `rowid`/`PRAGMA`/`AUTOINCREMENT`/`INSERT OR REPLACE` 等即红，
  已并入 `check.bat`（五段→六段）与 CI。
- 反向消融已验：把 `rowid` 塞回去，SQLite 那轮仍全绿（复现当初为什么漏）、
  PG 那轮红 3 项、静态守卫红 1 项。

**🟠 新增：后台任务停摆会在每日健康日报里点名**
- 新增循环健康登记（`services/loop_health`）：预热、清理各分项、定时广播、
  日报本身，每轮成败计数；**连续**失败达阈值的任务在每日健康日报里显示为
  「⚙️ 后台任务停摆：<任务名>（连续 N 轮）+ 最近错误」，管理端
  `/api/metrics` 亦返回 `loops` 明细。再不会出现"坏了但看起来很健康"。
- 清理调度改为分项隔离：以前一个清理项抛错会跳过其后所有清理项与数据库过期
  行收口；现在各自吞、各自记，坏一项不影响其余。

**🟢 顺带：6 个索引补齐 + 索引定义单源**
- 按核实过的真实查询补 6 个索引（预热排序、sessions 按用户删、分享/回收站
  每小时过期清理、广播按组过滤、审计待回填段），全库频率最高的排序查询
  不再每 30 秒全表扫。
- 索引从"PG 与 SQLite 各写一遍"改为一份定义拼进两条建库路径，并新增守卫
  直接问库"你身上有哪些索引"（防止声明了却没建成、或列序对不上导致白建）。

**验证**：pytest **395** 项 SQLite 全绿（原 368，+27 项）+ PG 双引擎 **392 全绿**
（3 项为引擎条件性 skip）/ ruff·mypy·UI 分层守卫·SQLite 专有语法守卫全过 /
web 19 单测过 / 契约重导出（138 路径不变，仅 `/api/metrics` 描述变化）。
新守卫做过反向消融（把 `rowid` 塞回去：SQLite 仍全绿、PG 红 3 项、静态守卫红 1 项）。
**部署需重启 `lan-share-api`**（索引与回填在服务启动时自动完成）。

## 2026-09-17 — App v1.1.0+104 + 服务端 v3.55：效率舱首秀（反馈 20260917224102-1a63）

「番茄钟/待办类功能融合」提案（`20260917224102-1a63`）的 **M0+M1 合并发版**：
底栏 IA 重构 + 待办/家庭清单完整落地。计划全文见
`docs/效率舱-最终计划-20260917.md`（v5，含两轮对抗审查）。

**App 端（1.1.0+104）**
- **底栏改版**：`首页 / 照片 / 文件 / 笔记 / 我的` → `首页 / 照片 / 效率 / 笔记 / 我的`。
  文件页不再是底栏页签——**首页快捷栏本来就有"文件"入口**，"我的"页功能宫格里
  与底栏重复的"笔记"也换成了"文件"（宫格仍 5 项不换行，零布局破坏）；
  文件页本体改为随点随开（与传输页同款手法），所有入口与深链保持可达。
  顺带修了一个 v0.25 遗留 bug：收到分享文件后自动跳转会落到笔记页（陈旧下标），
  现在直达文件页。
- **新页签「效率」**：待办清单完整落地——今天（含逾期）/ 未来 7 天 / 以后 /
  已完成（折叠）四分组；勾选完成带 3 秒撤销；优先级左色条与红/橙截止日；
  长按待办可改期/调优先级/删除；今日聚合卡（已完成/总数）。
  访客不显示效率页签。
- **家庭清单（融合核心）**：新建"全家共享"清单，家人都能看、都能勾；
  可把待办**指派给家人**（对方头像+首字角标显示），指派与完成都会给对方
  发站内通知（10 分钟窗口内自动合并成一条，买菜买 20 样不会炸出 20 条通知）。
  个人清单仅自己可见（管理员也看不到——私人待办是隐私）。

**服务端（v3.55）**
- 新增 `todo_lists`/`todos` 两表与 `/api/todos*` 八个端点（契约 134→138 路径）；
  家庭数据可见性在 store 层收口（member/admin 可见，guest 不可见）；
  待办删除走软删除墓碑（防多人同时操作"复活"），30 天后物理清除。
- 到期提醒兜底：`todo_due` sweep 挂清理循环（1 小时粒度——**打开 App 时的
  到期摘要是主要提醒途径，不承诺精确到分**）。
- 备份零新增：`pg_dump -Fc` 全库 dump 自动覆盖新表（M1 验收抽查确认）。

**验证**：pytest 368 项 SQLite 全绿 + PG 双引擎全绿（既有 2 skip）/ ruff·mypy
/ pip-audit / UI 分层守卫全过；flutter test 213 全过 + dart analyze 0；
web vue-tsc 0 + 19 单测过 + dist 已发布；生产库 debug 账号 API 冒烟全链路通过。
待办长按多选、离线缓存、番茄钟、日记、日历按四期计划在 v1.2~v1.4 交付。

## 2026-09-14 — App v1.0.3+103 + 服务端 v3.54：照片墙性能根修（反馈 31cd）

1.0.2 上线后首条实战反馈「照片加载很慢，滑动时特别卡」的根修批。
用附图 + 代码审查定位出三层叠加根因，全部落地：

**加载慢（服务端根因 + 客户端放大）**
- **服务端缩略图预热（v3.54）**：/thumb 此前全按需生成——批量上传后
  首次浏览照片墙，视口内每个格子都是冷缩略图，视频 ffmpeg 抽帧最坏
  25 秒/个，8 路并发全部堵在服务端生成队列里整屏转圈。新增后台预热
  循环（`services/thumb_warmup`，与清理调度同型挂 lifespan）：每 30 秒
  按入库时间新→旧低速率补齐 `.thumbs/` 缺失缩略图（单张串行不抢核、
  每轮 ≤8 张、视频每轮 ≤2、张间小睡 0.3s），浏览命中直接回热图。
  生成实现从端点抽出共用（`previews.generate_thumb`），键公式有回归
  测试钉死（错一位=整轮 CPU 白烧）。
- **增量合并不再打回转圈（客户端）**：1.0.2 的增量合并仍按位置匹配
  列表项——插入新照片后，视口内**未变**的格子也会拿到别人的 URL 集体
  重拉。现在格子按路径带 key + findChildIndexCallback、分组 sliver 按
  日期带 key，元素随内容复用，合并只影响真正变化的位置。
- **拉取按需截断**：翻页此前每页固定要 300 条但只留 120 条，白烧 2.5 倍
  服务端扫描与流量。

**滑动卡（客户端）**
- **高亮刷新收敛**：滚动跨月时月份轴高亮此前走整页 setState——每次跨月
  重建全部 sliver 树 + O(n) 分组重算，快滑就是重建风暴。改 ValueNotifier
  收敛到轴自身局部重建。
- **撤掉时间轴实时模糊**：BackdropFilter 在滑动中每帧对轴区域
  saveLayer+blur，是低端机显著掉帧源；半透明实色观感相近、成本近零。
- **解码缓存扩容 4 倍**：默认 1000 张/100MiB 对 320px 缩略图只够驻留
  约 250 张，来回滚动必然反复淘汰重解码。扩到 4000 张/384MiB。
- **预加载窗口加大**（250→600px）：快滑时缩略图请求提前起步。

门禁：pytest **355**（新增预热键公式/视频限额 3 测）/ PG 双引擎 /
flutter **213** / dart analyze 0 / ruff·mypy 全绿。

## 2026-09-14 — App v1.0.2+102：照片墙体验批（4 条反馈全量处理）

凌晨新提交的 4 条照片墙/App 反馈全部落地（1 修复 + 3 实装），集中在
"照片多了之后好不好用"：

**修复**
- **时间线锁死单月（6c58）**：右缘月份轴此前点按是"按月过滤"，时间线被
  锁死在一个月份里，往上翻不回别的月份。现在改为**全时间线定位跳转**——
  点月份直接跳到那个月的照片，跳完照样可以自由上下翻；轴上高亮还会跟着
  滚动实时走到当前月份；日期标题加大加粗，一眼能看到段落。跳转偏移按
  与筛选口径一致的月份计数推算（服务端 facets 新增 media_type/include_dirs
  参数，已重导契约），几千张照片里跳转也是秒级定位。

**实装**
- **照片多了不卡（6e62）**：后台刷新（备份完成/新照片脉搏）从"清空整墙
  重拉"改为**增量合并**——没变化时界面纹丝不动，有变化也只更新数据不再
  全屏转圈、不再把你的浏览位置弹回顶部；多选删除改为 4 路并发（大批量
  删除约 4 倍速完成）。
- **多选滑动连选（def1）**：长按进入多选后手指不要抬起来，继续滑动经过
  的照片会自动勾选（从已选照片上起手则是反向取消）；滑到屏幕上/下边缘
  会自动滚动接着选。长按多选时自动刷新暂缓，**不会再被顶回列表顶部**。
- **返回手势不再突然退出（b7c1）**：以前在任意页面拉返回手势都可能直接
  退到桌面。现在：多选中先退多选 → 非首页页签回到首页 → 在首页 2 秒内
  连续两次返回才会退出（第一次有提示）。

门禁：pytest **352** / PG 双引擎 / flutter **213** / dart analyze 0 /
ruff·mypy 全绿；契约 **134 路径（159 操作）**。

## 2026-09-14 — App v1.0.1+101：反馈闭环完善（7 条积压反馈全量处理）

当晚上半场新提交的 7 条反馈全部落地（3 修复 + 4 实装），并把"反馈"本身
升级成了完整闭环：

**修复**
- **手机网页端侧边栏点不开（3e4c）**：路由监听把 `sideOpen` 收进了响应式
  依赖——点汉堡打开的同一帧又被同步关回去，抽屉永远渲染不出来。改为独立
  route watch（写入不建依赖）；顺带修了 toast 弹出期间挡住顶部点击的问题。
  网页端刷新即生效，无需更新 App。
- **反馈历史状态不更新（f824）**：服务端状态一直实时在变，是 App 只在进页
  时拉一次、切历史页不刷新、详情用旧缓存。现在切「历史记录」自动刷新，
  详情每次打开实时拉取。
- **备份页已备份数与主页照片数对不上（61fa）**：两者本就是不同口径（本机
  备份账本 vs 服务端全部可见照片），页面补了口径说明；同时修了真问题——
  服务端相册目录被删的项此前错算进「待备份」永不消化，现单列一行并可在
  备份详情一键重新备份/移除。

**实装**
- **反馈可二次补充（e9d1）**：反馈详情底部新增补充说明，带时间戳追加，
  任何状态（含已完成）都能补，服务端同步新增 `POST /api/feedback/{fid}/append`。
- **照片墙自定义展示范围（b58a）**：筛选行新增「文件夹」入口，按目录勾选
  要看的范围，服务端 `include_dirs` 下推（新增 `GET /api/photos/folders`），
  分页/脉搏口径一致，选择按设备记忆。
- **私人相册不上墙（b4e0）**：新增管理员「照片墙隐藏」开关
  （`/api/photos/wall-excludes`，全局生效，文件页不受影响）；顺带堵掉一个
  隐私泄露——反馈截图目录此前被照片墙全盘扫描收录、全家可见，已改为系统
  目录永不上墙并清理存量。
- **文件夹长按多选（91fc）**：文件页长按文件夹直接进多选（与照片墙/笔记页
  一致）；原目录管理菜单收进多选态「⋮」（钉住/重命名/详情）。

门禁：pytest **351** / PG 双引擎 / flutter **213** / dart analyze 0 /
ruff·mypy·UI 守卫全绿；契约 **134 路径（159 操作）**。



## 2026-09-13 — 🎉 App v1.0.0+100：首个正式版（三 bug 根修 + 全量终审）

**这是 App 的第一个正式版**。从 2026-09-01 的 v0.4.0 起，经历 M-A0~A5 六个
里程碑与 v0.5~v0.28 的持续迭代，功能面与工程护栏均已稳定到可冠 1.0 的水准。
本版在发版前做了**两轮代码审查 + 两轮对抗式测试**（新增 54 个安全/可靠性回归
用例，全部经"消融验证"确认有效），并修掉三个用户实测反馈的根因问题。

**① 备份/浏览互相挤占导致"加载失败"（882a）**
根因：`/api/upload/init|state|finish` 与直传 multipart 全部计入 `/api/*` 交互
限速桶（120/分），照片备份一批就是几十上百次调用——打满后**同一出口 IP 的所有
API 都被 429**（生产日志实锤：171 次 init 429，连带照片墙 /api/photos、
/api/notifications、/api/recent、/api/app/entries 一起被拒），用户看到的就是
"备份上传照片时，照片墙和文件偶发加载失败"、SSE 断连退化成轮询继续被拒。
修法：**上传控制面拆出独立限速桶**（`UPLOAD_RATE_LIMIT=600`，公/局域网分账），
备份不再挤掉浏览、刷列表也挤不掉备份；App 侧同时给 GET 补上 **429 退避重试**
（按 `Retry-After` 退避，上限 3s），把"被限速"从报错降为"慢一点"。

**② 网页端文件页 F5 刷新白屏（249b）**
根因：网页文件页的 Vue 路由是 `/files`，而服务端把**裸 `/files` 也注册成了下载
端点**——两者冲突。刷新时请求落到下载端点，`resolve("")` 解析出 SHARE_ROOT 目录
→ 返回 JSON 400「目录请在界面打包下载」，SPA 永远没机会启动 = 白屏（用户原话
正是这句 detail，是根因的直接指纹）。修法：只保留 `/files/{path:path}`（该形态
不匹配无路径的裸 `/files`），裸 `/files` 自然落到 SPA 深链回退；下载走
`/files/<路径>` 不受影响（Caddy `handle /files/*` 本就要求带斜杠）。

**③ 暂停备份按钮点了没反应（933e）**
根因：**跨 isolate 的 SharedPreferences 缓存坑**（与 0.4.6 批记录的"跨 isolate
prefs 缓存"同源，方向相反）。备份引擎跑在独立 isolate，其 `SharedPreferences`
缓存是**每 isolate 一份**——前台 UI 写入 `paused=true` 后，后台引擎仍读到旧值
`false`，于是暂停点了不生效、照片照传。修法：`BackupConfig.load(fresh: true)`
强制 `reload()` 重读平台侧，引擎的两处读取（轮起跑 + 逐张前暂停复查）都改用它。

**④ 更新包"取消安装后要重下"（f6f2）**
下载前先查本地缓存：同一版本的包只要在 `updates/` 且 sha256/size 校验通过就直接
复用，不再让用户白等几十 MB 流量（校验不过的残包会清掉重下）。

**⑤ 终审加固**：上传会话守卫改 **fail-closed**（原 `user_id` 为 NULL 时短路放行
任何人——该列是后加列、历史行即 NULL，等于任意登录用户可收尾/破坏他人遗留上传
会话）；契约快照重导出（131 路径，裸 `/files` 移除所致）。

**门禁**：pytest **344**（+54 对抗/回归）/ flutter **212** / dart analyze 0 /
ruff·mypy·pip-audit / UI 分层守卫全绿；契约 131 路径（155 操作）。

## 2026-09-13 — App v0.28.10+84：反馈三连修复（浅色 SnackBar / 更新历史排序 / 分类抽屉点空白）

用户升级 0.28.9+83 后连报三条，当班全清：
①**浅色主题 SnackBar 恒黑**（959d）：浅色分支 snackBg 硬编码深海军 #23253A，
「欢迎，debug」等所有浮层提示在浅色下不随主题变——改为 surfaceContainerHighest
浅灰浮层，文字/action 颜色亮度随模式（Tooltip 同语言配套）。②**更新历史缺
v0.28.7-9**（0fce）：服务端数据修复（无需发版）——条目已登记但被追加到列表
末尾（0.4.1 之后），App 按服务端顺序渲染且约定新版本在前；已按 build 降序重排。
③**分类抽屉点空白不收**（cdfc）：Flutter 经典陷阱——遮罩 GestureDetector 子级
是裸 SizedBox.expand（不可命中），默认 deferToChild 下点击被父级遮罩色装饰吞掉，
v0.27.0 的「点空白收回」从未真正生效——显式 `HitTestBehavior.opaque` 修复；
全仓扫描仅此一处同型。
测试：Snackbar/Tooltip 两例规格测试同步改为亮度感知断言；flutter 206 /
dart analyze 0。

## 2026-09-13 — 服务端：备份验证钉死 public.settings（测试 schema 残留假阳性，P1）

值守班续。PG 双引擎测试指向生产库、异常中断时 conftest 的 DROP SCHEMA 未
执行 → `ls_test_*` 残留 schema（3 个×15 表）混进生产库；备份验证的宽松
`\bsettings\b` 正则匹配到测试 schema 的 settings——**public.settings 缺失
也能"验证通过"**。`_pg_toc_has_public_settings()` 按 pg_restore -l 行末三
段精确取 schema/table 只认 public；+1 回归测试（消融还原宽松正则即红）；
残留 schema 已 DROP CASCADE 清零（前核实无活动测试连接）。
门禁 pytest **287**；服务已重启。

## 2026-09-13 — App v0.28.9+83：文件页缩略图请求噪声治理

自主值守班续。files_page `_thumbUrl` 守卫从 previewable 收紧为图/视频——
服务端 /thumb 仅支持 IMG_EXTS|VID_EXTS，doc/audio 每个文件在列表里都白打
一次请求吃 404（生产日志 /thumb/笔记/*.md 404 实锤）并污染缩略图负缓存；
刷新放行负缓存处同步对齐口径（原 null 断言隐患一并消除）。recent_page 本
就按 isMedia 守卫不动。dart analyze 0 / flutter 206。

## 2026-09-13 — 服务端：/preview 显式支持 HEAD（App 大小预检首次生效）

自主值守班续。FastAPI 不像 Starlette 自动补 HEAD，`/preview` 的 HEAD 一直
405（生产日志+curl 实锤）——App 文档查看器 previewChecked 的 content-length
大小预检从未生效，超大文档整文件下载后才被前端丢弃。HEAD 单独成路由（叠
装饰器共享实现；api_route 多方法的 operationId 依赖 methods 集合哈希序、
跨进程不稳定，快照不可复现）。+1 契约测试，openapi 快照重导出。
门禁 pytest **286**；nssm restart 后线上 HEAD 实测 200+content-length。

## 2026-09-13 — App v0.28.8+82：SSE 连接累积修复（429 风暴根因）

自主值守班续（同日第二小版）。`inbox_poll._openSse` 开新流前先 cancel 旧
CancelToken——快速前后台切换时旧 SSE 连接要等服务端下一个心跳（≤15s）才被
回收，累积可撞每用户 3 连接乃至全局 64 上限，挤占所有成员的实时通知通道
（09-13 生产日志 4 连 429 实锤）。cancel 对已完成的流是无害 no-op；旧流被
断最坏损失一次信号（信号语义本就重拉全量）。dart analyze 0 / flutter 206。

## 2026-09-13 — App v0.28.7+81：bug·a6df 新建笔记卡死根修 + 服务端相册双重编码修复

自主值守班（功能发现记录见 docs/archive/feature-proposal-2026-09-13.md；同班完成
夜间挂账收尾：cff7e10 已推、CI 全绿）。①**目录选择器只留目录**（a6df 根
因）：DirPickerPage 此前不过滤 is_dir，.md 笔记等文件被当文件夹展示，点进
去 browse(文件路径) 服务端必 400「目录加载失败」，新建笔记卡死在选目录一
步（09-12 生产日志两次 400 实锤）——抽 `dirEntriesFromItems()` 纯函数过滤
+排序，batch076 5 例 + 消融（去过滤必红）。②**服务端相册页双重编码**：
ServerAlbumsPage 对用户名预编码后再进 listDir 按段编码，邮箱用户名 `%40`
变 `%2540` 必 404 且被静默吞，相册列表恒空——删预编码一行归一给统一编码
层。flutter 201→**206** / dart analyze 0。

## 2026-09-12 — 服务端 v3.53：兜底备份内联验证（restore drill）

下午自主批（主人离场，功能发现记录见 docs/archive/feature-proposal-2026-09-12.md）。
B5 兜底 dump 落盘后立即验证：SQLite `integrity_check`+settings 表可读 / PG
改 `-Fc` custom 格式（与外部主备份统一）+ `pg_restore -l` 只读校验；验证
失败按备份失败处理（webhook 上门、幂等位不写次日重试），工具缺失诚实呈现
`verified=None`。healthreport 日报与 `/api/metrics` 透传验证状态。全量
pytest 267（基线 263+4）+ 消融实验（禁用验证→损坏 dump 用例转红）+ 生产库
只读 E2E 冒烟（-Fc dump 454KB / pg_restore -l 63 表可读）三重验证。

## 2026-09-12 — App v0.28.6+80：bug·0663 修复分发

值班反馈清零批（27ce CI 三连修 + 0663 笔记页 404 特化回退）之后补出包：
aapt 核验 versionCode=80（104619348 B，sha256 3c94cb78…），三件套 70 条，
线上分发即时生效（服务端 versions.json 动态读取）。同批提交含 CI 修复
（GitHub Actions a349e5f 全绿，27 连红终结）与 PG 双引擎门禁补课
（本机全量 254 passed/2 skipped）。清理维护批：STATUS 旧段归档
（docs/STATUS-archive.md）、PLAN×2 与 PROJECT-OVERVIEW 移 docs/archive/。

## 2026-09-12 — 值班组：服务端 v3.52（存储层/SSE 实时通知/全文搜索/备份与指标/反馈闭环）

主人离场 7 小时值班，B0~B11 十二连块（八个功能/重构提交 + 本条）。核心四件：

**存储层六表 store 化（B1）**：`app/store/` 新增 notifications/photos/shares/
trash/devices 五个表主人模块 + schema.py（db.py 701→298 行只留连接/方言适
配），api/services 层六表裸 SQL grep 归零；settings 表按 11 号复审 A2 决策
不 store 化（尊重不翻案）。

**SSE 实时通知（B3）**：`GET /api/events`（cookie/Bearer 双鉴权、线程池
call_soon_threadsafe 桥接、每用户 3 连接/全局 64 上限、15s 心跳、满队丢最
旧信号语义）；发布点=站内通知唯一出口 + 新反馈 admin 信号；Web 通知铃
EventSource 优先、App inbox_poll dio 流式分帧零新依赖，均轮询兜底。

**全文搜索 + 备份/指标（B4/B5）**：note_search 小写镜像表（LIKE 双引擎同
构）+ /api/search kind 字段向后兼容（private 对 admin 不可见铁律钉住）；
进程内每日兜底备份（外部计划任务第二道防线，失败走 webhook）+ asgi 中间件
逐请求指标 + 磁盘水位告警（6h 冷却）+ `/api/metrics`；契约 130→**132** 路径。

**反馈看板 4 条闭环（B6）**：bug·97ac 白边（根因 `--bg-2` 令牌全仓未定义）、
other·0069 上限 12→25MB config 化（深因：feedback multipart 被中间件 1MB
钳制 413，原上限从未生效——已入大包白名单）、other·e464 白名单加 txt/md、
idea·3ead 由 B3 承接；bug·27ce 留守待定性、bug·0663 无正文附排查备注。
测试 222→**247**（SSE 6 + 笔记搜索 4 + 备份指标 6 + webdav 协议 7 + 反馈
附件 2）。

## 2026-09-11 — 全项目优化收尾批：App 批次 B + 大扫除 / 服务端 v3.51 / Web 测试护栏

主人拍板"除第二块物理盘外全做"后的多批次执行，四个提交（a5a54c4 / 85fedb4 /
bc67c5b / 191ec9f），每批独立门禁与终审。

**App 批次 B（a5a54c4）**：UI 层 51 处裸 dio 全部收编 data 层（新建
admin/notifications/photos/album 四个 api 模块 + 扩展既有五模块），分层守卫
基线 51→**0**——此后 UI 新增直发直接红灯；`notes_page` 1573 行拆四文件
（1231+306+35+6），`Note.fromJson` 收口契约解析。终审 0 P0/P1/P2。

**Web 测试护栏（85fedb4）**：web 测试 6→**19** 项（字节/时间/速度/编码/动态
文案纯函数钉进 `src/pure.ts`——vue 的 ESM bundler 构建进不了 node，纯函数
留在 ui.ts 等于测试不可达）+ **契约路径对齐守卫**（提取 web 全部 /api/ 字面量
比对 openapi 130 路径，替代重量级 codegen 的本仓方案）。

**服务端 v3.51 + Web 流量卡（bc67c5b）**：
- **F4 公网流量仪表**：Caddyfile 只给隧道块（:8767/:443/:8801）挂 access log
  （roll 5×10MiB）——"有日志≡走隧道≡消耗樱花FRP 配额"判别零歧义；
  `app/services/traffic.py` 聚合今日/本月/14 天序列并入 `/api/admin/overview`，
  运行状态页新增流量卡（免费档占比 80% 预警色）。零新端点零契约变更。
- **A5 收口**：精选专辑存储核心下沉 `app/services/albums.py`，services 层
  import app.api.* 归零；A6 复评关闭（kb 下线后前提消失），结论入 10 号文档 §七。
- **F5 依赖**：httpx2→2.12/httpcore2→2.10（mcp 依赖树 PYSEC×6，服务端自身
  304 包本就零漏洞），pip-audit 清零。
- 服务重启 + 线上核验通过。**门禁：pytest 222（+2 流量）/ ruff / mypy 43 文件
  全绿。**

**App 大扫除（191ec9f）**：33 个 UI 文件约 **400 处**视觉字面量等值替换为
Gap/Radii/IconSizes 令牌（仅数值恰等，不匹配值保留；评价报告 P2 的 ~199 处
存量就此清账）+ semanticLabel 逐页补齐 10 处。终审 0 P0（唯一 P1 经结构核实
为误报：placeholder 垫底层非独立焦点节点）。

**SOP 联动**：服务端已重启生效；App 随 v0.28.4+78 发版（令牌化零观感变化 +
读屏标签，用户可升可不升）。

## 2026-09-11 — App v0.28.3+77：结构整改批次 A + 视觉规范 v1.0 发版（纯加固，无新功能）

把 09-11 三个「未发版」批次（视觉规范 v1.0 / 批次 A 整改 / 批次 A 复查）一并
打包上线。**用户可见的变化刻意压到最小**：① 修掉「操作提示在返回上一页后静默
丢失」（提示改挂 App 级 messenger）；② 平板宽屏下提示条不再横跨整屏（限宽
480）；③ 读屏（TalkBack）从完全不可用到底部主干可用（照片有描述、装饰图形
不占焦点、空态连读）；④ 手机竖屏观感零变化，平板/横屏下内容限宽 720 居中、
照片墙 3/4/6 列自适应。其余全部是内部工程加固（拆文件/收编重复件/分层守卫/
测试 152→191）。发版前独立复审 0 个 P0/P1，2 个 P2（focusedBorder 硬编码、
规范文档漏改 0.6）已修并复跑门禁全绿。
- 门禁：dart analyze 0 / flutter test 191 全绿 / UI 分层守卫 51/51 基线通过；
  aapt 0.28.3/77（104619352 B，sha256 ac9b790b…），三件套 67 条；线上
  `/api/app/latest`+`/history` 已核验，App「检查更新」到 0.28.3 即可看到。

## 2026-09-11 — App 整改批次 A 复查（4 轮 review + 3 轮对抗性审查，随 v0.28.3+77 发版）

对批次 A 的全部改动做多轮自审与对抗性审查，**发现 7 项问题并全部修复**。

**Review 发现**

1. **死代码（最重）**：批次 A 新建的 `Motion` / `IconSizes` 令牌、`responsiveValue` /
   `AdaptiveContent` / `AdaptiveSplit`、`showToastLong`、`Gap` 的 8 个便利常量
   **全部零调用点**——这正是本仓自我批评过的"建了基础设施没人用"，我自己犯了
   同款；更糟的是新增的 25 项测试**只验令牌取值、不验是否被使用**，给了虚假的
   安全感。**处理**：能用的接进真实调用（`Motion.enter/exit` → 页面转场曲线、
   `IconSizes.lg` → AppBar 图标、`AdaptiveContent` → me_page 限宽、`Gap` →
   responsive 内部），其余按 YAGNI 删除（`AdaptiveSplit` / `responsiveValue` /
   `showToastLong` / Gap 便利常量）。
2. `AdaptiveContent` 的 `constrainHeight` 参数写成
   `constrainHeight ? double.infinity : double.infinity`——三元两边相同，参数
   形同虚设，删除。
3. **分层守卫的路径守卫缺陷**：脚本用相对路径，目录不存在时 `os.walk` 返回空、
   counts 也为空，于是"扫到 0 处违规"被当成**通过**——静默假绿比报错危险得多。
   加"目录存在"与"扫到 0 个 dart 文件"双检查。
4. 守卫正则要求点链无空格，`ApiClient . I . dio` 可绕过；改为
   `ApiClient\s*\.\s*I\s*\.\s*dio`（顺带拦"方法引用"写法）。

**对抗性审查发现**

5. **me_page 限宽改造引入边距回归**：改用 `AdaptiveContent` 后，其默认
   `pagePaddingH` 与各分组自带的 12 边距**叠加成 24**，违反"手机竖屏零变化"的
   承诺。修：传 `padding: EdgeInsets.zero`，让组件只负责"宽屏居中限宽"。
6. **收编 `_toast` 抹掉了平板优化**：`admin_page` 原本自带 `width: 360`（宽屏下
   提示不该横跨整屏），统一时被一并删除。修：`showToast` 按屏宽自适应（宽于
   600 时限宽 480）——把这个优化从"某页特例"提升为**全 App 共用行为**。
7. `trash_page` 里 `Colors.red` 硬编码 + `fontSize: 12` 字面量（恰好就在我刚
   立下的"不许写字面量"规矩旁边）→ 改 `cs.error` 与 `textTheme.bodySmall`。

**同时修正 10 处文档引用**（已删 API 仍被 CHANGELOG / STATUS / HANDOFF /
PROJECT-LAYOUT / 01-架构 / mobile README / APP-VISUAL-SPEC / 评价报告提及）。

门禁：`dart analyze lib` 0 问题 / `flutter test` **191 全绿** / 分层守卫通过
（存量 51 处）。

**方法论（已进 03 踩坑清单）**：**测试通过不等于东西有用**。25 项新测试全绿，
却完全掩盖了"新建令牌与组件零调用"这一事实——测试护栏防的是"改坏"，而
"建了不用"要靠另一类检查（本次靠 grep 调用点发现）。两者都要有。

## 2026-09-11 — App 结构与护栏整改（批次 A，随 v0.28.3+77 发版）

主人要求按评价建议全量整改、把五个维度拉到 5 分。执行中先确认了边界：
**iOS 适配 / 离线模式 / i18n 属新功能开发**（需真机构建与验证），**全量重写页面
在 UI 零护栏下风险大于收益**（评价报告 §九已列为"明确不建议"）。故按
「**先补护栏、再拆结构**」推进——没有护栏就重构等于闭眼做手术。本批交付：

- **补上 UI 层测试护栏**（此前 152 项测试里只有 5 处能测 UI、golden 0 个）：
  新增 `test/ui_widgets_test.dart`（25 项）——品牌组件渲染、主题里 9 类组件样式
  的落地值、中文排版规则、无障碍语义。**用属性断言而非像素 golden**：CI 跑
  Linux、baseline 在 Windows 生成，字体渲染差异会让 golden 必然假红，护栏的
  价值在"能守住"而非"长得像 golden"。
- **收编 11 份重复 `_toast`**：新建 `ui/widgets/kit.dart`，删除 11 个页面里各自
  的实现（已发生微漂移：admin_page 多传 `width: 360`、feedback_page 漏 `mounted`），
  改写 91 处调用点。提示改挂 App 级 `scaffoldMessengerKey`——调用点不传 context，
  既避开 91 处 `use_build_context_synchronously` 警告，又修掉「页面在 await 期间
  被 pop 后提示静默丢失」的真实缺陷。另提供 `showConfirmDialog` 统一危险操作确认框。
- **拆分 `backup_settings_page.dart`**（1523 行装 4 个页面类）→ 4 个文件
  （826 / 379 / 179 / 184 行），用 `export` 门面保既有调用方零改动。
- **收编回收站 4 处裸 dio** 到新的 `data/api/trash_api.dart`（响应模型随之下沉，
  契约只解析一处）。
- **新建 UI 分层守卫** `scripts/check-ui-layering.py`：`lib/ui/` 不得 import dio
  或调 `ApiClient.I.dio()`。用「**每文件违规数**基线」而非行号基线——行号随任何
  编辑漂移，基线会天天假红。已接入 `check.bat`（第五段）与 CI。存量 55 处登记
  为基线，本批回收站清零后降至 **51 处**（数字只准降不准升）。
- **补无障碍**（此前全仓 `Semantics`/`semanticLabel` **0 处**）：从共享组件层
  打底——`BrandCloud`/`FileTypeTile`/`SectionTitle` 图标 `ExcludeSemantics`
  （装饰图形不占语义），`EmptyState` 用 `MergeSemantics` 合并成一句连读，
  `AuthImage` 新增 `semanticLabel`（照片墙缩略图是读屏唯一信息来源，已接）。
- 门禁：`dart analyze lib` 0 问题 / `flutter test` **191 全绿**（166 + 25 新增）/
  `python scripts/check-ui-layering.py` 通过。
- 过程中测试当场抓出**文档与代码不一致**：规范写"输入框常态描边 0.6"、代码是
  0.8。处理方式不是改测试迁就代码，而是把 0.8 收进 `Strokes.widthInput`
  （输入框作为可交互控件本就该比静态卡片略粗）并让文档对齐。
- ⚠️ 环境坑：本仓 Dart 文件**行尾 CRLF/LF 混用**（`feedback_page`/`me_page` 是
  CRLF，`admin_page`/`files_page` 是 LF）。批量脚本若只匹配 `\n`，会漏掉 CRLF
  文件里的定义，进而把 `void _toast(` 定义行也当调用替换——**批量改写前必须
  先 dry-run 核对命中数**（本次靠此拦下一次会破坏 2 个文件的替换）。

## 2026-09-11 — App 视觉规范 v1.0：设计令牌 + 响应式适配（随 v0.28.3+77 发版）

主人要求"设计并实现 Flutter 移动端 UI 与视觉美化方案"（风格/配色/排版/核心
页面布局/交互元素外观/多尺寸适配）。审计后确认 v0.8.4/v0.8.5 已建成的主题引擎
（六色板 + Material 3 + 品牌组件库）**不推倒重做**——那样会破坏既有观感与全部
UI 测试；真正的缺口是**响应式完全缺失**（平板/横屏下卡片被拉满整屏、照片墙
固定 3 列）与**令牌散落各页**（间距 8/12/16 混用无单源）。故按"新增基础设施 +
页面最小改动"推进，**手机竖屏观感零变化**。

- **新增令牌层** `lib/ui/theme/design_tokens.dart`：`Gap` 间距 8 档（4pt 基准
  网格）/ `Radii` 圆角 8 档 / `Motion` 动效 / `IconSizes` / `Shadows` 4 档 /
  `Strokes`。该文件**不 import 任何项目文件**（纯 Flutter 基础库），避免与
  `app_theme.dart` 形成循环依赖。
- **新增响应式** `lib/ui/theme/responsive.dart`：断点 600 / 840 三档 +
  `WindowSize` + `ResponsiveContext` 扩展（`pagePadding` / `gridColumns`）+
  `AdaptiveContent`（清单页限宽）+ **`sliverGutter()`**（sliver 场景限宽，
  把留白折算进 `SliverPadding`，**不动 Sliver 结构**，守住照片墙性能红线）。
  核心策略是"**内容不无限拉伸**"：超过 720 逻辑像素即居中留白。
- **主题改造** `app_theme.dart`：`Brand` 四个圆角常量改指向 `Radii` 形成单一
  事实源（旧引用零改动）；排版补全 8 级层级 + 中文行高 1.25–1.5 +
  `letterSpacing` 全归零（M3 默认给 label 加的 0.1–0.5 字距会让中文发散）；
  新增 FAB（16 圆角）/ IconButton / Tooltip 品牌化主题。
- **页面适配**：首页与「我的」内容限宽 720 居中（品牌头仍铺满全宽）；照片墙
  与专辑瀑布流 3 → 3/4/6 列自适应；文件页网格列数从自定义 440/640 阈值统一到
  全局断点（3/4/5）。
- **规范文档** `docs/DESIGN-v3/APP-VISUAL-SPEC.md`：11 节——风格定位、配色体系、
  字体排版、空间令牌、响应式规范、三个核心页面、交互元素 17 类外观表、动效、
  可访问性、落地清单、**改 UI 自检清单**。
- 门禁：`dart analyze lib` 0 问题 / `flutter test` **166 全绿**（原 152 + 新增
  14 项令牌阶梯与断点边界回归）；后端未动故未跑 check.bat。**未构建 APK、未改
  version——随下次发版三件套一起上线**。
- ⚠️ 环境插曲：`flutter analyze` 在沙箱下分析服务器 exit 255 崩溃，改跑 dart SDK
  原生分析器（`bin/cache/dart-sdk/bin/dart.exe analyze lib`）稳定秒回；flutter
  命令除原有四件套外**还需 `LOCALAPPDATA`**（否则报 pub cache 找不到直接退出）。
  两条均已进 `docs/项目记忆/03-踩坑清单.md`。

## 2026-09-11 — App v0.28.2+76：修分类侧边栏透明叠影（反馈 7063）

反馈看板第三条反馈（主人手机 10:57 提交，附截图）当日闭环：
- **🐛 7063「分类侧边栏bug——变透明了」→ 已修复**：v0.21（9.6 批）给分类侧栏
  写的半透明背景（`surfaceContainerHighest @ alpha 0.45`）在内联布局时代底下
  无内容、无感知；v0.27.0 改覆盖式抽屉后面板底下就是笔记列表，列表文字全部
  透过面板叠影（截图可见「分类/未分类/收藏」与列表文字重叠）。修复 = 面板
  背景改完全不透明，右侧 30% 黑遮罩（点空白收回区）语义不变。一行修复。
- 门禁：dart analyze 0 / flutter test 152 全绿；aapt 0.28.2/76（104619348 B，
  sha256 90c3e08a…），三件套 66 条；线上 `/api/app/latest`+`/history` 已核验。
  反馈已回写，看板自动重生成，App「检查更新」到 0.28.2 即可看到。
- ⚠️ 环境插曲：`~/.gradle/caches/journal-1/journal-1.lock` 被内核层句柄钉死
  （所有进程打不开/删不掉，Restart Manager 查无持有者），gradle 启动即崩——
  重启电脑释放后按 SOP 原路径构建成功（中途试过的 GRADLE_USER_HOME 缓存拷贝
  方案已按主人要求删除）。

## 2026-09-11 — Web v3.49：AdminView 按 tab 拆分（纯重构）

07 审查重构小尾巴收尾件：320 行的 AdminView 拆成父组件 + 三页签子组件
（web/src/components/admin/）：
- `UsersPane`（成员列表/角色/视角预览/账号弹窗/配额/导出/踢下线/停用删除，
  弹窗状态随行内聚）、`InvitesPane`（建码/复制/撤销）、`GrantsPane`
  （批量授权表单/成员筛选/撤销）。
- 数据仍由父组件集中加载（users/invites/grants/dirOptions），保持"切页签
  不重新请求"的原行为；各页签变更后 emit('refresh') 回连父级 loadAll。
- 门禁：vue-tsc 0 错 + build 绿；浏览器实测三页签渲染与交互正常
  （成员 4 行/邀请按钮/授权批量表单+目录选项，截图留证）。

## 2026-09-11 — Web v3.48：api.ts 按域拆模块（门面保兼容，纯重构）

07 审查重构小尾巴：462 行的 api.ts 拆成 api/ 目录九个文件，**调用方 25 个
文件零改动**（原导出全部保留，`api` 对象仍为单一入口）：
- `api/types.ts` 类型全集 · `api/core.ts` handle/enc/jpost/getAs（401 豁免
  与成员视角预览头统一在此）。
- 域对象：`authApi`（登录/两步/资料/账号安全/设备配对）、`consoleApi`
  （管理台/审计/通知运营/成员权限/邀请授权/webhook/日报/公网地址/导出）、
  `notifyApi`（站内通知/家庭动态/成员）、`filesApi`（文件/回收站/搜索/磁盘）、
  `photosApi`（时间线/相册）、`sharesApi`（分享/取件/公开取件 publicApi）、
  `api/upload.ts`（分块上传引擎/秒传/公网大文件提醒）。
- 门面 api.ts：`export *` + 各域展开合并为 `api`，键名与拆分前一致。
- 门禁：vue-tsc 0 错 + build 绿（线上 hash 更新）；浏览器实测登录态下
  总览/运行状态两页数据加载正常（adminOverview/auditLog/轮询三接口全通）。
- 排查记录：BroadcastItem 手抄漏 3 字段被 vue-tsc 抓住——拆分时类型必须
  逐字搬运，不凭印象重写。

## 2026-09-11 — v3.47：操作审计按类型筛选（/api/admin/audit + 运行状态页）

v3.39 方向内已批准候选③。在现有审计数据上加筛选，不动表结构与留痕逻辑：
- **服务端**：GET /api/admin/audit 新增 `action` 查询参数（前缀匹配，
  如 login → login/login_fail/login_locked）；响应新增 `actions` 字段
  （audit_log 现存 DISTINCT 动作全集，受 AUDIT_KEEP 水位约束）供下拉。
  **契约有意变更**：contracts/openapi.json 已重导出（仍 130 路径，
  变化在该端点参数与响应 schema）。
- **Web**：运行状态页审计卡加"全部类型"下拉（63 种动作），切换即带参
  重查；空结果提示"该类型暂无记录"。
- 门禁：ruff/mypy 绿 / pytest 220（含新筛选断言 + 契约快照）/ build 绿；
  重启后线上核验（login 筛选 50 条全前缀命中）；浏览器实测下拉筛选
  80 行全为 login*（截图留证）。

## 2026-09-11 — Web v3.46：总览页 45s 自动轮询（页签不可见即暂停）

v3.39 方向内已批准候选②：站长把总览挂在大屏/常开页签上也能看到新数据。
- DashboardView 改 45s 静默刷新（30~60s 区间取中）：存储/成员/照片/分享/
  回收站/会话/动态/公告全部随轮询更新，"数据截至"时间戳即刷新反馈。
- 省资源：`document.visibilityState` 不可见时跳过本轮 + visibilitychange
  切回立即补刷一轮；组件卸载清理定时器与监听。
- 静默失败保留旧数据（网络抖动不把工作台刷成错误页）；仅首载失败显示错误。
- 门禁：vue-tsc+build 绿、线上 hash 更新；浏览器实测：首载 02:42 →
  一个周期后 02:43 自动更新（截图留证）。

## 2026-09-11 — Web v3.45：admin 全量分享一览页（/share-ops）

v3.39 方向内已批准候选①：站长一眼看全全家分享。**零新增服务端端点**——
GET /api/shares 对 admin 本就返回全量（带创建者），只补工作台页面：
- 新页 ShareOpsView（/share-ops，meta.admin）：创建者/路径/大小/状态
  （有效·已用完·已过期）/带提取码徽章/定向标记/取件次数，操作=复制链接/
  取件记录/撤销（站内确认框）。
- 按 v3.39 定的候选口径支持创建者下拉 + 状态页签筛选；风格沿 notify-ops
  既有 card+frow 语言，零新视觉。
- 入口：侧栏"分享"组与用户菜单各加"🌐 全部分享"（仅 admin 渲染）。
- 备注（待主人拍板的取舍）：提取码明文自 R1-P2-5 起不随接口下发
  （一次性下发不落库），页面以"带提取码"徽章呈现；如需明文可见列
  需另开端点，等拍板再动。
- 门禁：vue-tsc+build 绿；线上 dist hash 已更新；浏览器实测（debug 会话）：
  空态/自建带码分享刷新出现/状态计数与创建者下拉正确/撤销确认框→
  列表回空全链路截图留证；smoke 文件与回收站记录已自清。

## 2026-09-11 — 服务端 v3.43：auth.py 按域拆路由（纯重构，契约零变更）

07 审查报告重构小尾巴第 1 项：793 行的 auth.py 按端点自然域拆成五个
APIRouter 模块，挂载方式对齐 api/ 既有结构（main.py 统一 include）：
- **auth.py（会话域）**：state/setup/login/logout/authcheck/me/verify-password
  + 会话助手（set_session_cookie、login_gate、uid 公共化供兄弟模块引用）。
- **totp.py**：/api/login/totp + /api/auth/totp/*（开通/恢复码/关闭）。
- **profile.py**：昵称/头像三端点（.avatars 目录管理随行）。
- **account.py**：改密/改用户名/WebDAV 独立密码（防爆破锁定策略原样保留）。
- **devices.py**：设备 Token 三端点 + 配对码两端点（reset_pair_codes 随迁）。
- 顺手归位：SharePrepareBody 从 auth.py 移回真正的主人 share.py
  （模型 docstring 会进 OpenAPI description，用普通注释防契约漂移）。
- 路径/行为/契约快照 100% 不变；test_contract 钉死。测试引用同步：
  conftest reset_pair_codes→devices、test_paircode→devices、
  test_totprc→totp。
- 门禁：ruff/mypy 41 文件全绿 / pytest 220（SQLite）/ 契约快照一致；
  重启后 debug 会话线上冒烟 login/state/me/devices/webdav-password/
  totp setup/pair code 全通。

## 2026-09-11 — 服务端 v3.42：反馈看板（目录即状态，定位零通读）

主人提出：反馈多起来后，逐条通读才能分辨"做完/待做"太费时间。头脑风暴定稿：
**目录即状态 + 自动看板**。App 无变更（接口响应形态不变）。
- **目录分区**：data/反馈/ 拆 待处理/（已提交）、进行中/（处理中）、已完成/
  （细分 已修复/已实装/已拒绝/已暂缓 在 frontmatter）。Agent 领任务 = `ls 待处理/`；
  状态流转 = 移动文件 + 改 frontmatter 细状态/备注。存量 2 条已迁入 已完成/。
- **看板.md 自动生成**：列表端点被查询或新反馈提交时自动重生成（不靠手动维护），
  内容 = 三档统计 + 已完成细分统计 + 待处理/进行中全量清单 + 已完成最近 10 条。
  Agent 读写这一份文件即知全局。
- 实现：feedback.py 列表递归三目录、状态按目录派生（已完成目录用 frontmatter
  细状态）；看板.md 不进列表/治理视图；提交落位 待处理/ 并即时刷新看板。
- 门禁：ruff/mypy/反馈测试 4 条（更新为移动+改文件的真实工作流，钉看板生成）；
  双引擎全量见下。已重启上线，看板生成实测正确。

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

依据《docs/项目记忆/archive/11-独立工程复审》执行（该报告 P0/P1/P2/A 全部逐条落地，见其 §九
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

依据《docs/项目记忆/archive/09-项目建议报告》与《docs/项目记忆/archive/10-改进计划-v3.35.md》执行，**行为等价 + 纯增强**，
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
  docs/archive/CHANGELOG-archive-v0.4-v0.20.md
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

