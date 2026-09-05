## 2026-09-05 — v0.14.0+46：精选专辑"文件夹式"重构 + 备份白名单 + 时间栏实时（照片页全面梳理批次）

用户反馈四点（备份排除相册不方便改白名单/传输"删除"是否真删文件/时间栏数目不
实时更新/精选专辑应是自己勾选照片的文件夹），服务端+Web+App 三端批次，Agent
模拟器全链路实测后发版。1 轮完整 code review（2×P1+5×P2+6×P3 全修）：

- **[P0-体验] 精选专辑重构为"文件夹式"**（服务端 social.py/system.py 重写）：
  专辑模型从"月份书签 {title,month}"升级为"策展集 {title, items[照片路径]}"——
  建专辑后自己勾选照片/视频进来，也支持按月全选批量加入。新端点：
  GET /api/albums/{id}（条目按请求者可见性过滤后下发）、
  PUT /api/albums/{id}/items（add/remove，上限 500，条目入队校验可见性）、
  GET /api/photos?album=<id>（时间线按专辑过滤）；列表瘦身只回 count/cover
  不带全量条目。旧月份书签型专辑全端兼容（App 详情页自动退化为按月浏览、
  Web 按月过滤）。权限：guest 只读；编辑/删除 = admin 或创建者。
- **[App] 专辑详情页 + 选照片页**（新 album_detail_page.dart）：详情页瀑布流
  展示、长按多选移出/下载、溢出菜单删专辑；选照片页分页瀑布流点选即加
  （"已在专辑"灰显防重复）、AppBar 日历"按月全选"（facets 月份表→整月拉全→
  分批 200 项 PUT，服务端 500 上限不再整体 400）。新建流程：起名即建空专辑
  →自动弹选照片页。照片页专辑从"月过滤 chip"升级为封面缩略图迷你卡
  （count/cover），内容有变 pop(true) 实时刷新计数。
- **[App] 备份白名单**：0.6.0 的"排除相册"黑名单改为"选择备份相册"白名单
  （selectedBuckets，空=全部备份；旧排除表继续生效、保存白名单时清除迁移）。
  引擎扫描双条件过滤，账本统计 stats(included:) 口径与引擎一致（含"未分类"
  空串相册）。设置页入口/统计卡文案同步改写。
- **[P1-根修] 时间栏/月份侧栏实时**：_months facets 首拉后永久缓存是"照片墙
  不同步"第二根源——任何来源刷新（20s 脉搏/备份完成/上传完成/删除）现在同步
  重拉 facets。模拟器实证：服务端新增 2025-01 照片，30s 内月栏自动长出
  "25-01·1"，删除后自动收回；新增/删除照片的月份数目同步刷新。
- **[App] 传输删除语义澄清**：确认 removeTask 只清记录、从不删手机文件（用户
  担心数据丢失，实为相反）。文案改"移除记录（不删除文件）"+副标题说明；新增
  "删除本地文件并移除记录"（确认框说明只删 App 目录副本、服务端原件不受
  影响，删除失败降级为仅移除记录）。
- **[App] 手动上传支持视频**：FileType.image → FileType.media，按扩展名分流
  照片|视频/<用户名>/手机上传/（此前只能选图片）。
- **[服务端加固]**（review 修复）：专辑 settings-JSON 读改写全程持 _db_lock
  （_mutate_albums 原子化，并发勾选/删除不再整表互相覆盖）；GET /api/albums
  列表 cover 与详情 items 均按请求者可见性过滤（private 路径名不再泄露给
  guest/无权成员）；专辑满 50 本显式 400 而非静默截断最旧一本；create 响应
  瘦身只回 {ok,id}。
- 测试：服务端 pytest 158→161（专辑条目 CRUD/ACL/可见性/月份书签兼容；
  同月 409 查重随旧语义废弃）；Dart 101→106（白名单配置往返/账本白名单
  扣减）；dart analyze 零问题；web build 过。
- 模拟器 E2E 实测：专辑新建→自动弹选照片→勾选 3 张→按月全选 10 张→移出
  2 项→删除专辑全链路 + 迷你卡计数实时刷新；备份白名单勾选/保存/副标题；
  月份实时增减；传输"删除本地文件"实测文件真删且服务端原件不动。

## 2026-09-05 — v0.13.0+45：照片墙滑动根修 + 专辑共创体验 + 传输闭环（模拟器全交互实测批次）

用户实测反馈四问题（照片墙无法滑动/专辑逻辑乱且操作不便/传输页无法选择打开方式/
不知道文件下载到哪），Agent 上安卓模拟器全页面交互实测后逐一根修，纯 App 批次
（服务端零改动，专辑新端点 v0.11 已具备）：

- **[P0] 照片墙滑动卡死根修**：v0.11 的 flutter_staggered_grid_view 有上游致命
  bug——同一 CustomScrollView 存在多个 SliverMasonryGrid 时滚动修正失效
  （issue #265/#286/#338），照片墙按日分组=每天一个=真机必卡死；E2E 复现
  后换 waterfall_flow 的 SliverWaterfallFlow（API 同型、社区专为此修复），
  24+ 次跨组连滑不再卡死；新增多组瀑布流滚动回归测试（batch071）。
- **[P1] 精选专辑对齐 v0.11 共创语义**：端点从 /api/admin/albums（仅 admin）
  迁到 /api/albums + /api/albums/{id}（成员可建、创建者/admin 可删）；
  新建入口从"按月跳转弹窗最底部"提到照片页 AppBar；chip 长按改菜单
  （查看这个月 / 创建者信息 / 删除）；_Album 带 created_by。
- **[P1] 传输页下载闭环**：已下载任务点按弹操作单——打开（openLocalFile
  统一校验）/ 保存到手机相册（新 MediaStoreBridge.saveToGallery 通道，
  IS_PENDING 落 Pictures|Movies/LAN Share/，无需存储权限）/ 分享 /
  查看保存位置 / 移除记录；空态文案说明下载去向。
- **[P1] 传输记录重启失真修复**：入口拿不到大小的下载（查看器传 0）完成时
  物化 size=实际字节；恢复逻辑不再对已完成下载按 .part 重算 receivedBytes
  （文件已改名，会把 488.8 KB 打回 "0 B · 已下载"）。
- **[P0] 输入对话框退场崩溃修复**：showDialog 后立即 dispose TextEditingController
  在退场动画中触发 framework 断言 '_dependents.isEmpty' 红屏（E2E 点"下一步"
  必崩，存量雷）；新 widgets/PromptDialog 自持 controller 生命周期，
  photos_page/files_page 三处统一收口。
- **[美化] 月份快滚轴毛玻璃**：BackdropFilter 实时模糊替代半透明色块，
  贴右缘窄条，不再遮挡第三列照片观感。
- 测试：Dart 99→101（batch071 多组瀑布流滚动回归）；dart analyze 零问题；
  模拟器 E2E 实测滑动/专辑创建删除（服务端 created_by 落账）/传输操作单
  （MediaStore 落库验证 Pictures/LAN Share/）全过。

## 2026-09-05 — v0.12.0+44：App 文件页 Web 同款双布局 + 缩略图 + 点进预览

App 侧体验对齐 Web 文件页（用户指定：方形/横向双排布、Web 同款缩略图、
点击直进预览）。纯 App 批次，服务端零改动。经 3 轮完整 code review +
1 轮对抗性审查（红队 9 场景推演），P1×2/P2×9/P3×11 全部修复：

- **[App] 双布局**：文件页 AppBar 新增 ▦/☰ 切换（方形网格/横向列表），
  settings 持久化（files_grid）；网格 3/4/5 列按屏宽自适应，卡片双行
  文件名+大小，视频 ▶ 角标；两布局共用常用目录区与多选/长按交互；
  条目区改 CustomScrollView+SliverGrid/SliverList（虚拟化不回退 0.4.4 教训）。
- **[App] 缩略图**：图片/视频（扩展名集合与 /thumb/ 的 IMG|VID_EXTS 逐字
  对齐，集合外不发请求）走 AuthImage 鉴权通道，失败回退品牌类型图标；
  **失败负缓存**（会话级 10min TTL，仅 404/5xx 记——401/断网/超时不记，
  防 token 过期瞬间整屏坏图固化；登出/手动清缓存即放行；下拉刷新放行
  当前目录）。
- **[App] 点进预览**：可预览文件点击直接进全屏查看器（PhotoViewerPage
  复用：PageView 相邻翻页/幻灯片/视频代理播放/删除分享下载全套），
  翻页范围=当前目录全部可预览项（Web FileViewer navItems 同款）；
  不可预览类型保持操作菜单。查看器新增"用其他应用打开"（已下载副本，
  open_local 助手统一存在性校验+中文错误文案，三处接入）。
- **[App] 对抗审查修复**：① 服务器地址改进程级单一事实源
  Prefs.addressNotifier——会话内切换主/备隧道后文件页/照片墙的缩略图与
  预览不再永久打旧地址；② thumb/preview URL 追加 ?v=modified——同名
  覆盖文件后客户端 180 天磁盘缓存不再展示旧图（服务端忽略未知参数，
  零改动）；③ 错误态粘滞修复（刷新成功复位、重试指向真实失败路径、
  面包屑回退退出错误视图）；④ _VideoPage dispose 竞态不再误杀共享
  媒体代理；⑤ 地址尾斜杠归一；⑥ 移动/复制网络异常补 toast；
  ⑦ 网格文本区字号钳制 1.3x（iOS 辅助特大字号不溢出）。
- **[App] 其他**：查看器信息弹窗按入口切"拍摄日期/修改时间"标签、
  空段不渲染；ext 集合单一事实源（_iconOf 复用 Entry 常量）。
- 测试：Dart 93→99（batch070：previewable 判定对齐服务端集合 + 布局
  设置持久化回读）；dart analyze 零问题。

## 2026-09-04 — v0.11.0+43：可运营 + 家庭共创大批次（A2/A4/A5/A6 + B1-B6）

经用户裁决的九件套（A2 健康日报、A4 视频可播性、A5 质量门禁、A6 设备管理；
B1 相册共创、B2 照片互动、B3 数据导出、B4 PC 备份入口、B5 瀑布流+快滚轴、
B6 站内通知轮询推送；A1 备份演练与 A3 流量守门待第二块盘/后续排期）：

- **[服务端] 每日健康日报（A2）**：新模块 `app/api/healthreport.py`，每日定时
  采集磁盘水位/备份产物新鲜度/证书剩余天数/公网隧道探活/成员照片统计推
  Webhook；管理面板可配置+立即采集。按日去重防重启重发；探活是唯一出网
  动作且与采集拆分（单测不出网）。
- **[服务端] 视频可播性（A4）**：photos 表加 codec 列（三引擎迁移），ffprobe
  补扫第一视频流编码（NSSM PATH 常缺 ffmpeg → 回退 C:/env/ffmpeg/bin）；
  Web 查看器播放报错转"编码无法播放→下载兜底"卡片（小米 HEVC 场景）。
- **[服务端] 质量门禁（A5）**：check.bat 增补 ruff/mypy/pip-audit 三段
  （M0 欠账清偿）；pyproject.toml 务实规则集（FastAPI B008/裸 except
  BLE001 豁免，风格留给 review）；存量 mypy 20 条归零——顺带修掉
  resolve 返回注解缺失、share 局部变量撞 Form 参数名等真实瑕疵；
  运行 venv 依赖漏洞清零（pip/tornado/cryptography 升级，cryptography
  升级中断致 dist-info 损坏的坑已记录）。
- **[Web] 设备管理（A6）**：账号设置补"登录设备"列表+按台吊销（API/App
  端早已有，Web 缺位）；丢手机只吊销单台不再牵连全家（改密码仍全吊）。
- **[服务端] 照片互动（B2）**：新模块 `app/api/social.py`——photo_comments /
  photo_reactions 两表（三引擎，表情五枚白名单、UNIQUE 兜底 toggle 幂等）；
  可见性复用 spaces.visible（能看见照片的人才互动得了，404 不暴露存在性）；
  评论/回应触发拍摄者站内通知（自己互动自己不通知）。
- **[服务端+Web] 相册共创（B1）**：精选专辑从管理员专属放开为家庭共创——
  POST /api/albums 成员可建（记录 created_by），admin/创建者可删；
  Web 照片页按钮对全员开放，专辑卡显示创建者。
- **[服务端+Web] 数据导出逃生门（B3）**：POST /api/admin/export 打包成员
  私人空间+直传子树 zip（arcname 保留空间前缀，恢复按原样落位）+ manifest
  （账号/分享/设备/统计，边界声明：共享空间文件无归属元数据不在包内）；
  管理面板成员行加"📦 导出"。走 /api/zip 同一套一次性票据。
- **[文档+脚本] PC 备份入口（B4）**：docs/PC-BACKUP.md 三姿势指南（脚本/
  WebDAV 挂载/网页直传）+ scripts/pc-sync.py 零依赖增量备份脚本
  （PROPFIND 对账 + PUT，同路径同大小同 mtime 跳过，落位 照片/PC备份/<本机名>/）。
- **[App] 瀑布流+年月快滚轴（B5）**：photos 表三引擎加 width/height，
  _probe_image_sizes 每轮 200 张 PIL 读头补扫（与视频补扫分账不抢配额，
  打不开写 0x0 占位防重扫）；/api/photos 返回宽高。照片墙 SliverGrid→
  SliverMasonryGrid（保留 Sliver 虚拟化——0.4.4 shrinkWrap 风暴教训不回退；
  新依赖 flutter_staggered_grid_view，pubspec 记录理由），真实宽高比出
  瀑布流、未知/极端比例回退方格；右缘年月快滚轴（facets 预取+直点跳月）。
- **[App] 站内通知轮询推送（B6）**：前台 60s 轮询 /api/notifications，
  未读增量发系统通知（新 inbox channel 可单独关闭；一次事件一条不轰炸；
  首轮只对齐基线不炸历史）。后台推送属厂商通道，阶段 2 评估。
- 验证：服务端 pytest 158 项全绿（148+10），ruff/mypy/pip-audit 归零；
  Web vue-tsc+构建通过；App analyze 零问题、Dart 93 全绿；
  APK 0.11.0+43 分发（三件套齐）。

## 2026-09-03（深夜）— v0.10.4+42：审查补丁三件（并发反转/照片墙截断/上传调度时序）

v0.10.0→0.10.3 批次代码审查发现的三个 P1/P2 缺陷修复：

- **[App] 缩略图并发档位写反**：`auth_cache.dart` 中 `isPublicAddress ? 8 : 3` 与
  注释意图（局域网 8 路/公网 3 路）相反——恰把要避免的"公网弱网 8 路互相拖累"
  制造出来，恢复设计值。
- **[App] 照片墙分页竞态**：`_limit` 依赖异步赋值的 `_base`，首屏请求发出时 `_base`
  未到位被判成公网（limit=60），响应回来后 `_done` 又按 120 比较 → 翻页永久关闭，
  局域网照片墙静默截断在 60 张。limit 在请求前物化为局部变量，请求与 `_done`
  判断用同一值。
- **[App] 上传入口档位刷新时序**：`onLogin`/`enqueueDownload` 调度前先 `await
  _refreshPublic()`；`enqueueUpload` 保持同步签名，改走 `_refreshAndKick()` 包装
  （刷新完档位再调度）——公网冷启动首批任务不再按局域网档 2 路抢并发。
- 验证：analyze 零问题、Dart 93 全绿；APK 0.10.4+42 分发（sha256 aa448129…），
  `/api/app/latest` 与 `/api/app/history` 线上核对通过。

## 2026-09-03（深夜）— v0.10.3+41：弱网收尾四件套

公网弱网加固批次收尾（v0.10.1 → v0.10.3），四项剩余待办全部落地：

- **[App] 上传并发自适应**：公网入口 1 路串行 / 局域网 2 路（`_maxActive` 动态化，
  登录与入队时按入口刷新）——10Mbps/蜂窝下并发任务互相抢带宽导致双双掉速触发重试，
  单路反而稳定吃满。
- **[App] 上传 sha256 后台 isolate**：秒传指纹改用 `compute()` 在独立 isolate 计算
  （200MB 上限内同步读盘喂摘要，不再占 UI 线程），失败只影响秒传探测不阻塞上传。
- **[App] 照片墙分页自适应**：公网每页 60 条 / 局域网 120——弱网首屏少拉半屏、
  滚动到顶请求突发减半。
- **[App] 幻灯片节奏自适应**：公网 8 秒/张 / 局域网 3 秒——蜂窝下大图加载 5-8s，
  3 秒节奏会让播放永远追不上加载。
- 验证：analyze 零问题、Dart 93 全绿；APK 0.10.3+41 分发（sha256 e0e36f12…）。

## 2026-09-03（夜）— v0.10.2+40：深色通知框对比度 + 查看器美化 + 公网三件

- **[App] SnackBar 深色模式深字深底修复**：自定义 snackBg（深海军）却沿用 M3 默认
  `onInverseSurface` 文字色（深色模式=深色）——文字显式压白，像素实测对比 19:1。
- **[App] 照片查看器防黑屏**：缩略图磁盘垫底（Hero 飞入不闪黑）+ 深海渐变影棚底 +
  小号柔光 spinner（去掉"加载中…"大字）+ 失败可重试（换 key 重解析）。
- **[App] 公网入口大图预览 w=1280→960**：蜂窝加载快近一倍，手机屏仍清晰。
- **[App] 照片墙缩略图并发按入口自适应**：局域网 8 路 / 公网 3 路（10Mbps 下
  8 路互相拖累拥塞控制）。
- **[App] 视频代理失败自动重置**：重试前重建回源 HttpClient 与本地代理，
  不再复用僵死 socket。
- 验证：Dart 93 全绿、analyze 零问题；深色 SnackBar 模拟器像素验证通过；
  APK 0.10.2+40 分发（sha256 a6e25886…）。

## 2026-09-03（傍晚）— v0.10.1+39：公网弱网加固四件套

用户真机反馈"公网照片加载不出来、上传很慢会失败"。多轮实测（本机视角：缩略图
0.05s、直传 1.38MB/s 满速、50 路并发零失败）证明服务端/隧道健康，问题集中在
手机蜂窝路径质量 + 客户端弱网韧性不足：

- **[App] 出站 HttpClient 显式超时**：connectionTimeout 12s / idleTimeout 5min
  （Dart 默认连接超时极长、keep-alive 15s 的蜂窝踩坑）。
- **[App] 图片加载 45s 总超时 + 5xx/429 自动重试一次**：不再无限转圈，
  下轮网格重建自动再拉。
- **[App] 上传分片 sendTimeout 10→30min**；网络错误重试 5→8 次（最长 8 分钟退避）
  后才放弃。
- **[服务端] 限速桶按入口分账**：Caddy 反代使内外网 client_ip 同为 127.0.0.1，
  原先全家共享 120 次/分——现分 `api:lan`(120) / `api:pub`(240) 独立桶 +
  429 携带 `Retry-After: 30`（test_zz_rate_limit.py 3 例锁定）。
- 验证：pytest 142 全绿（+3）、Dart 93 全绿；APK 0.10.1+39 分发（sha256 a38ef118…）；
  公网冒烟（主页/缩略图/动态）通过。

## 2026-09-03（午后）— v0.10.0+38：公网主/备双隧道

樱花FRP 开通后 App 对齐公网访问：登录页可选主/备隧道、我的页一键切换、同源指纹
自动信任。

- **[服务端] `public_base_url_alt` 备用公网地址** + `is_public_entry()` 主/备双 Host
  同时命中（防爆破分账、healthz 收敛对两条入口同样生效，test_public_alt.py 6 例）。
- **[服务端] `GET /api/app/entries`（免登录）**：下发主/备地址 + 证书指纹
  （PEM→DER→SHA256 纯标准库，与 App TOFU 弹框逐位可比）。
- **[网络] Caddy `:8801` 监听**：备用隧道 frpc 探测口；用户随后在面板关闭自动
  HTTPS → 备用隧道（your-frp-domain.com:xxxxx）为纯 TCP 端到端透传，出示本机自签证书
  （与主隧道指纹一致，同源静默信任实测生效）。
- **[App] 登录页主/备隧道快捷入口**（服务端下发，共用 `server_picker.dart`）；
  **我的页「当前入口」切换**（不退出登录，Bearer 跨入口有效）；**指纹分级信任**
  （同源静默+陌生弹框明示不一致）；**公网徽标**与公网大文件/更新包流量确认；
  **管理面板主/备两栏**（App + Web）。
- 验证：pytest 139 全绿、analyze 零问题、Dart 93 全绿；模拟器全流程 E2E
  （选择器→主隧道 TOFU→公网徽标→切备用→切换成功）；APK 0.10.0+38 分发
  （sha256 2a42c596…）。

# 更新日志

## 2026-09-03（午后）— v0.9.0+37：检查更新「更新历史」栏目 + 品牌收尾三件套（网页端/二维码）

用户拍板：头脑风暴的 1/2/3 全做、顺手的做，并在检查更新界面下新增"每一次
版本的更新列举"栏目。本次跨三端（服务端 + Web + App）：

- **[服务端] `/api/app/history` 更新历史端点**：读 `App安装包/versions.json`
  （发版时随 notes 一起维护的版本列表），宽容解析（缺文件/坏条目→空列表，
  App 端隐藏栏目）；登录可用。pytest +6（history 契约 4 + QR logo 2），
  全量 133 绿。**发版纪律升级：发版 = APK + version.json + versions.json
  三件套都要动。**
- **[服务端] QR 中心云朵 logo**：`/api/qr` 与 `/api/share/{token}/qr` 收拢
  到同一 `qr_svg_response()`——ECC 升 Q（25% 冗余）+ 嵌套 `<svg>` 百分比
  定位注入白底圆角块 + 品牌云朵（parity 与启动图标同源路径）。
- **[Web]** 登录页与侧栏的旧 `◈` 菱形 logo 全部换成云朵品牌图
  （`/static/favicon.svg`，单源）；`index.html` 补 og:type/title/description/
  image 分享卡片 meta（此前缺失；theme-color 已有）。`vite build` 重建 dist。
- **[App]** 「关于与检查更新」页新增**「更新历史」栏目**：版本号（品牌色）+
  日期 + 说明逐条列出，`当前`（绿）/`最新`（品牌色）徽章标注；拉取失败
  静默隐藏。分享二维码弹窗中心加云朵 chip（ECC 同步升 Q）。
- 验证：pytest 133 全绿、analyze 零问题、Dart 83 全绿（+2 history 解析）；
  NSSM 重启后线上验证 history 27 条/QR logo/og meta/favicon 全部 ✓；
  模拟器验收关于页历史栏目渲染；APK 0.9.0+37 分发（sha256 18ccadc1…）。

## 2026-09-03（晨）— App v0.8.14+36：主页头部铺满适配状态栏 + 品牌资产全家桶（通知图标/favicon）

用户三连需求：图标云朵改回"白边镂空、内部透底"；主页顶部颜色块铺满不留边框
并整体适配状态栏不重叠；状态栏通知图标更新——并要求头脑风暴其余待同步点。

- **主页头部铺满**：`ListView` 去侧边距，`BrandHeader` 铺满全宽（底部弧保留），
  其余卡片包回 12px 边距的 `Padding+Column`；新增 `BrandHeader.topInset`，
  传入 `MediaQuery.padding.top` 让渐变延伸进状态栏、内容从状态栏下方开始
  （edge-to-edge 下不再重叠）。登录页同步：去 SafeArea + topInset。
- **状态栏图标亮度**：`HomeShell` 用 `AnnotatedRegion` 按页签切换——首页
  （渐变头）白图标，其余浅底页深图标；登录页白图标。
- **通知小图标**：新增 `drawable-*dpi/ic_notification.png`（白云剪影 24dp
  基准五档）；替换 BackupWorker 三处 `android.R.drawable.stat_*` 系统图标 +
  `setColor(0xFF5B6CFF)`；flutter_local_notifications 初始化
  `'ic_launcher'`→`'ic_notification'` + details.icon/color。
- **[潜伏缺陷] 资源名优化咬人**：AGP `enableResourceOptimizations`（默认开，
  与 minify/shrink 无关）把 release 资源名混淆成 `res/-B.png`，运行时
  `getIdentifier("ic_notification")` 必失败——意味着 0.6.0 起状态栏小图标
  一直是糊的默认图。修：`gradle.properties` 关闭该优化（与 shrink=false
  同理，体积换稳定）；`res/raw/keep.xml` 兜底。APK 已验证五档 ic_notification
  真名保留。
- **图标云朵回描边镂空**：白边、内部透底色（evenodd 双子路径），比例保持
  35% 画布、全幅圆角块不变。
- **favicon**：`/static/favicon.svg` 此前 404（index.html 引用了不存在的
  文件），脚本现生成同款渐变+描边云 SVG（public+dist）。
- 验证：analyze 零问题、Dart 81 全绿；模拟器截图验收主页铺满/状态栏白图标
  无重叠/桌面图标；分发 0.8.14+36（sha256 8c8fe5b6…）+ notes。
  注：0.8.12+34/0.8.13+35 为本轮中间构建，资源名问题根治后由 36 覆盖。

**品牌同步头脑风暴结论**（已做=启动图标/PWA 三件/favicon/通知图标/通知色；
候选=Web 登录页 logo 检查、`theme-color` meta、外链分享 og:image 预览、
二维码中心 logo、Android 12+ splash 背景（自动跟启动图标）、桌面小部件）。

## 2026-09-03（凌晨 V）— App v0.8.11+33：云朵回原比例 + 半透明磨砂样式

用户定版两处：① 云朵比例回到原大小（35% 画布，v0.8.9 时的大小——铺满后的
55% 偏大）；② 云朵本身改**半透明磨砂样式**（白 57% alpha，与圆角块同一
质感语言，透出底渐变）。

- 实现注意：半透明云须在独立透明层绘制后 `Image.alpha_composite`——
  ImageDraw 直接写在有底色的画布上会**替换**像素而非叠加（云区域会
  变成"镂空露壁纸"）。
- monochrome 层维持实心不透明云（主题图标需要完整 alpha 剪影）。
- 验证：512 母版 + 模拟器桌面截图目检；release 0.8.11+33 分发
  （sha256 c5b48f53…）+ notes。


## 2026-09-03（凌晨 IV）— App v0.8.9+31：启动图标对齐登录页品牌（圆角块 + 描边云）

用户给登录页品牌头截图拍板："图标跟这个圆角框里的图标对齐，颜色和样式我很
喜欢"。启动图标从"渐变 + 实心云"升级为登录页同款结构：

- **三件套结构**：渐变底 + 半透明白圆角块（25% ≈ 登录页 white24）+ 描边
  云朵（cloud_outlined 外轮廓+内孔 evenodd 挖孔）。几何：块占画布 62%、
  块圆角 28%、云占块 56%；monochrome 层改用实心云专用图（半透明块进单色
  通道会糊成方块）。
- **脚本升级**：mini SVG 解析器补 `S/s` 平滑贝塞尔（内孔需要）；修 tokenizer
  **无前导零小数 bug**（`.5`/`.71` 被丢点读成 5/71，内孔整个飞出——SVG 路径
  解析必须支持 `\.d+` 形式）；圆角块走 `Image.alpha_composite`（直接画会替换
  底色渐变）。
- PWA 图标（512/192/apple-touch）同源重绘，全端品牌一致。
- 验证：512 母版 + 模拟器真实桌面截图目检（描边在 48dp 桌面图标下清晰）；
  release 0.8.9+31 分发（sha256 0d9d5503…）+ notes。


## 2026-09-03（凌晨 III）— App v0.8.8+30：云朵图标缩小 + 更新描述恢复

用户反馈两点：

- **检查更新的"更新描述"没了**：0.8.5 起发版时写的 version.json 漏了
  `notes` 字段（服务端 `notes = data.get("notes") or ""`，App 端
  `remote.notes.isNotEmpty` 才渲染说明区）——字段一丢说明区整体消失。
  0.8.8 起恢复，并把发版纪律固化：**version.json 必须带 notes**。
- **App 图标云朵缩小**：`make-app-icon.py` 的 `CLOUD_FRAC` 0.58→0.50，
  云朵留白更舒适（自适应安全区 66% 内余量更足），安卓三套 + PWA 同源重绘。
- 验证：analyze 零问题；release 0.8.8+30 分发（sha256 d22396c2…）；
  线上 /api/app/latest 返回 0.8.8 (30) + notes 文案 ✓；512 母版目检云朵
  占比合适。


## 2026-09-03（凌晨 II）— App v0.8.7+29：主页字体对比度热修（v0.8.4 潜伏缺陷）

用户报"字体颜色要有对比不然看不清，主页就是"。逐像素采样定位出**两个叠加
根因**，均为 v0.8.4 UI 重设计潜伏：

- **[P0] Typography 墨水映射写反**：`_textTheme` 在亮色主题取了
  `Typography.white`（浅墨）、暗色取了 `.black`（深墨）——全 App 所有
  不写颜色、走默认文字色的文本（主页动态正文、备份状态、传输状态等）
  在亮色模式下渲染成**白字画浅卡，基本不可见**；动态页时间戳/通知中心/
  登录页提示等同理。修复：亮色用 `.black` 深墨、暗色用 `.white` 浅墨。
  新增 DefaultTextStyle 墨水方向回归测试（亮<0.3、暗>0.6 亮度钉死；
  测试须包 Scaffold 并 `pumpAndSettle`——Material 用 AnimatedDefaultTextStyle
  装配墨色，不 settle 会采到上一棵树的旧值）。
- **[P1] `cs.outline` 被当正文色用**：outline 是 M3 边框语义色（约 3.5:1），
  主页 8 处 + 动态/管理面板/登录/通知/验证器/空态提示共 15 处拿它画文字，
  暖色系色板下尤其发灰。全部改为 `onSurfaceVariant`（次级文字语义色）。
- **主题层加固**：亮色 `onSurfaceVariant` 向 `onSurface` 拉近 35%（约 8:1）；
  品牌头加 8% 暗色罩膜，暖沙等浅渐变端白字不至糊掉；主页问候副标题
  白字 0.8→0.92。
- 验证：analyze 零问题、Dart 81 全绿（+1 墨水方向）、release 0.8.7+29
  分发（sha256 c3e54d1a…）；模拟器像素采样：云朵紫蓝/暖沙亮色正文
  19:1、暖沙暗色 17.5:1（修复前正文对比 1.0~1.2:1 即不可见）。
  注：0.8.6+28 为本轮未宣布的中间版，已被 0.8.7+29 覆盖。


## 2026-09-03（凌晨）— App v0.8.5+27：主题引擎（6 色板 + Material You）+ 云朵品牌启动图标

用户拍板"组合拳"：方向四主题引擎为主体、方向一/二/三做成预置色板、深色默认
用方向三的深海军底；云朵 Logo 与渐变保留（随色板变色），并把云朵+渐变做成
App 启动图标。纯 App 侧改造，服务端零改动：

- **Palette 色板系统**（`app_theme.dart`）：每套色板 = M3 种子色 + 品牌渐变
  （登录/首页品牌头）+ 深色底。6 套预置：云朵紫蓝（默认，暗底=深海军
  #0B1020，即方向三"暗房"）、晨雾青、暖沙陶土、玫瑰落日、墨绿、极简灰。
  暗色表面阶梯不再用 M3 中性灰，统一从色板深底向上派生（`_tier`），
  AMOLED 底仍纯黑、表面仅余极淡种子色相提升。
- **PaletteScope**：MaterialApp.builder 挂载的 InheritedWidget，品牌头等
  组件从作用域取当前渐变；作用域外回落云朵紫蓝（防脏数据）。
- **Material You 跟随壁纸**（新依赖 `dynamic_color`，Android 12+）：主题色
  选"跟随壁纸"时从系统动态取色构造临时色板（渐变=primary→tertiary），
  拿不到动态色回落紫蓝。
- **设置持久化**：`AppSettings.accent`（`ui_accent`，默认 violet）；「我的」
  页深色模式下新增「主题色」行（渐变圆指示 + 弹窗色板九宫格选择）。
- **云朵启动图标**：`make-app-icon.py` 重写——内置 mini SVG 路径解析器
  （M/C/c/h/z + 隐式重复 + 贝塞尔拉直，不引 SVG 依赖），Material "cloud"
  填充外形白云 + 品牌渐变底；遗留/自适应前景/monochrome 三套 + PWA 图标
  （web/public/icons 与 web/dist/icons 的 512/192/apple-touch）同源重绘，
  全端品牌统一为"云朵 + 渐变"。
- 验证：analyze 零问题、Dart 80 全绿（+9：色板完整性/脏数据回落/作用域/
  深色阶梯单调/AMOLED 纯黑/accent 持久化）、release APK 分发（sha256
  5c3681c0…，version.json 0.8.5+27）、模拟器实测换色板（暖沙亮/暗）→
  深海军暗色 → AMOLED 像素级采样 (0,0,0) → 桌面图标云朵渐变 ✓。


## 2026-09-03（深夜 II）— App v0.8.4+26：UI 重设计与美化（方向 A 全批一次做完）

用户拍板：视觉方向 A（深紫蓝 + 玻璃质感）、要暗色增强、三批一次做完、
品牌图标用登录页云朵 + 其渐变背景色。本次为纯前端改造，服务端零改动：

- **Design Tokens + 三套主题**（新 `lib/ui/theme/app_theme.dart`）：亮/暗/
  AMOLED 纯黑三套；品牌色（#5B6CFF 主 → #9C6BFF 三级渐变）、统一圆角阶梯
  （卡片 20/控件 12）、低对比描边卡片、输入框/对话框/底部弹层/浮层按钮/
  NavigationBar/进度条全套组件主题；页面转场改 8ms 淡入 + 8px 上移。
- **暗色增强**：AMOLED 纯黑（真 #000 底，OLED 省电/夜间观影友好），「我的」
  页新增开关，暗色生效场合（dark 或 system 且系统为暗）自动替换。
- **品牌组件**（新 `lib/ui/widgets/brand.dart`）：云朵 Logo（登录页同款白底
  圆角块）、渐变品牌头、空态三件套（云朵+标题+引导）、品牌化文件类型图标
  （类型色圆角方块）。
- **逐屏**：首页顶部改品牌渐变头（云朵+问候），卡片圆角统一、快捷入口改
  品牌色块；登录/关于页用云朵组件；照片墙日期改主色胶囊；缩略图↔全屏查看器
  Hero 动画（tag=path）；查看器底部毛玻璃操作条；文件列表改品牌图标
  （目录=色块+文件夹、文件=类型色方块）；传输/分享/回收站/动态空态统一
  EmptyState；我的页加 AMOLED 开关。
- 验证：analyze 零问题、Dart 71 全绿、release APK v0.8.4+26 分发、重启探活。


## 2026-09-03（深夜）— 全仓十轮 review + 五轮对抗审查 v0.8.3+25：13 项修复（含授权前缀 Windows 失效 P1）

用户要求"亲自 10 轮完整代码 review + 5 轮对抗性审查，覆盖代码的所有模块与
功能"。本轮本人逐文件审查后端全 16 文件（≈4.6K 行）、Web 全 30 文件
（≈4.7K 行）、Mobile 全 43 Dart + 6 Kotlin 文件（≈13K 行）与 Caddyfile，
各轮不同视角（API 契约 → 认证安全 → 服务层 → Web 数据层 → Web 页面 →
Mobile 数据层/备份引擎 → Mobile 页面 → 原生桥 → 契约交叉 → 并发边界 →
对抗五视角：认证/穿越/注入/竞态/泄露），修复 13 项后全面回归：

- **[P1] 授权前缀命中在 Windows 全盘失效（潜伏核心缺陷）**：`os.path.normcase`
  在 Windows 把 `/` 归一成 `\`，`_grant_hit` 与 `sync_grants_on_move` 的
  `gp + "/"` 段拼接永不命中——访客/成员只能访问被授权目录**本身**，子目录
  读写授权与移动/改名后的授权跟随全部形同虚设（存量测试只覆盖精确路径）。
  修复：`_normcase` 统一回正斜杠做规范化比较键；补 guest 子目录读 / member
  子目录写 / move / rename 四条回归测试。
- **[P1] `/api/move` 授权同步死代码**：`target.is_dir()` 在 `os.rename` 之后
  才判定（旧路径已不存在恒 False）→ 移动目录后 grants.path 全部失联。
  判定提前到 rename 之前。
- **[P2] `/api/upload` 直传配额可被绕过**：`enforce_quota` 信任 multipart
  声明 size，谎报 0 即逃逸 → 缺声明时按直传上限 2GB 悲观计价。
- **[P2] 定时清理任务会永久停摆**：`cleanup_loop` 无异常兜底，任一瞬时
  OSError（回收站条目并发删除等）逸出即杀死 while 循环 → 单轮异常吞掉
  记日志，`_cleanup_tmp/_cleanup_trash` 逐项 try 跳过。
- **[P3] copy 配额定价遍历裸越界路径**：`_tree_size(Path(src))` 拿客户端
  输入当本地路径 rglob → 改走 `resolve(read)` 之后计价。
- **[P3] WebDAV PUT 配额双计 + 失败残留**：tmp 已在 private 树内被扫描，
  incoming 传 size 双计误报；配额失败时 tmp 残留 → trash 后再按入=0 精确
  校验，失败先清 tmp。
- **[P3] 设备 Token 唯一约束异常只捕 sqlite3.IntegrityError**：PG 生产库
  抛 psycopg 异常会 500 → 改捕 Exception 重试一次。
- **[P1] Web 管理面板"留空=不限"配额无效**（与 App 0.8.1 同类契约错误）：
  清空发 null 被 `UserPatchBody` 当"未提供"跳过 → 显式发 0；输入校验 +
  现有配额 <10GB 一位小数展示（防误清）。
- **[P2] PhotosView 滚动监听跨路由泄漏**：onMounted 挂 scroll 无 unmount
  移除 → 补 onUnmounted。
- **[P3] uploader clearFinished 误清暂停项续传会话**：注释与过滤自相矛盾，
  暂停项留在队列却被删 sid → 保留。
- **[P3] App 传输引擎 429 判死**：改 5 次退避重试（与备份引擎语义对齐）。
- **[P2] Caddy `request_body 2GB` 掐断 WebDAV 20GB**：服务端 MAX_WEBDAV_SIZE
  20GB 从未真正可达 → 代理上限升 25GB（分块/直传不受影响）。
- **验证链**：pytest 129 全绿（+5 回归）、`npm run build` 通过、flutter
  analyze 零问题 + 71 Dart 单测全绿、NSSM 服务重启探活
  （healthz ok / debug 登录 / /api/app/latest 200）。


## 2026-09-03（晚）— App v0.8.2+24：二次亲审（5 轮逐行）补 3 项加固

0.8.1 发版后用户要求"亲自再进行 5 轮完整代码 review"（不再派专员，本人
逐文件逐行按五个视角各过一遍：契约/生命周期/空安全/交互/跨文件+对抗）。
新发现并修复 3 项：

- **配额弹窗两处误清隐患**：输入 `0` 被正数校验拒收（0 是用户表达"不限"
  的自然输入）→ 改为 0=不限；现有配额 <1GB 时初始值 `.round()` 显示成
  "0"，用户不动直接保存会误清 → <10GB 改保留一位小数展示。
- **webhook"发送测试"按钮不随输入刷新**：可用性在 build 时求值，
  TextEditingController 不触发整页重建，新输入 URL 后按钮仍灰 → 改
  `ValueListenableBuilder` 跟随文本实时启用。
- **首页动态刷新异常收口**：0.8.1 把 `dio()` 提到 try 外复用，但其内部
  有 await 且 `_loadActivity` 被 fire-and-forget 调用，可能产生未捕获
  异步异常 → 包回 try。
- 另修正一处 lint（`__`→`_`）；analyze 零问题、构建分发线上验证
  0.8.2/build 24、模拟器安装启动冒烟通过。

## 2026-09-03（傍晚）— App v0.8.1+23：6 轮 review + 对抗性审查——修管理面板删除失效等 5 个 P0

用户报"管理面板里的删除按钮失效"，要求全面 6 轮 review + 对抗性审查。
第 1 轮（API 契约核对）即找到根因：**邀请删除与授权撤销把 DELETE 端点
写成了 POST**（`_post('/api/admin/invites/…')`）→ 服务端 405 → 点了没反应。
第 2–5 轮由四个审查专员并行跑（状态生命周期 / UI 交互 / 安全权限 / 对抗性
边界），第 6 轮修复+验证。共修 5 个 P0、10+ 个 P1：

- **P0 管理面板删除失效**：新增 `_del` 助手走 `dio.delete`（邀请吊销、
  授权撤销、删成员统一）；模拟器真跑"建邀请码→删除""API 建授权→UI 撤销"
  均通过。
- **P0 清配额 / 清 WebDAV 密码不可达**：`_prompt` 把"留空提交"与"取消"
  都映射成 null，"留空=清除"流程永远走不到；且配额发 null 服务端按
  "未传"跳过（需发 0）。`_prompt` 加 `allowEmpty`，配额走 0 清除并校验
  正数（输错不再误清）。
- **P0 通知未读判断字段错**：服务端字段是 `read`（bool），App 读的
  `read_at` 恒 null → 所有通知永远显示未读。改为 `read != true`，标记
  已读后本地列表同步去红点。
- **P1 错误提示全中文化**：新 `data/api/errors.dart` 的 `errDetail()`——
  优先取服务端中文 `detail`，fallback HTTP 状态码；替换管理面板/通知页/
  打包下载/专辑管理里 6 处 `e.toString()` 英文堆栈直出。
- **P1 fromJson 强转兜底**：`_AdminUser/_Grant/_Invite/_Notif/_Album`、
  设备列表、配对码、恢复码、zip url 等约 10 处 `as int/as String` 强转
  改可空兜底——服务端脏数据不再整页崩。
- **P1 其它**：zip 临时文件加时间戳前缀防覆盖；管理面板保存类按钮
  `_busy` 防连点；`_loadDirs` 目录路径逐段 encode（中文/#/空格）；
  `_addAlbum` 控制器泄漏 + setState mounted 守卫；改用户名成功后清
  缩略图缓存（旧目录名不留本地）；文件多选栏改横向可滚（六键并排窄屏
  溢出）；专辑 chip 主色调与月份 chip 视觉区分；配对码 FittedBox 防溢出；
  邀请码弹窗补"复制"按钮；成员删除后清理授权表单里已选的幽灵 uid；
  删除成员回退文案"已删除(#id)"。
- 验证：`flutter analyze` 零问题；后端 pytest 122 全绿；构建 v0.8.1+23，
  线上 /api/app/latest 实测通过；模拟器 E2E 烟测（登录→管理面板→邀请
  创建/删除→授权创建/撤销）通过，临时目录已清理。

## 2026-09-03（午后）— App v0.8.0+22：功能对齐网页版——管理面板 + 账号安全 + 打包下载 + 专辑 + 通知中心

用户要求"对齐网页版功能全部迁移到 App 并做好排版优化、全面更新文档"。
盘点 Web 五视图（Home/Photos/Shares/Trash/Admin）与 App 现有页面后，差距
集中在三块：管理面板（App 完全没有）、账号安全设置（部分缺失）、文件
打包下载与照片精选专辑。全部迁移，服务端零改动（端点早已齐备）：

- **管理面板**（我的页 admin 可见入口，`admin_page.dart`）：三 Tab——
  **成员**（角色徽章/2FA/停用、改角色、改用户名、重置密码、解除两步
  验证、WebDAV 密码重置、配额、停用/启用、删除，底部卡=公网访问地址+
  事件推送 webhook 保存与测试）；**邀请**（创建成员/教访邀请码 72h、
  复制、吊销）；**授权**（批量新建：成员多选 × 共享目录多选（两层树，
  跳过 private/incoming）× 读写 × 有效期，按成员过滤，撤销）。
  Web 的"视角预览"未迁移（App 全局 dio header 改造大，记 §9 遗留）。
- **我的页"账号与安全"区块**：修改用户名（`applyRename` 同步本地会话，
  私人目录/incoming 服务端自动改名）；WebDAV 独立密码（设置/清除，
  实时读状态）；生成配对码（大字弹窗，180s 一码一用）；重新生成恢复码
  （旧码作废，新码仅本次明文展示）。
- **文件页**：多选"打包下载"——/api/zip 服务端打包 → 临时目录 → 系统分享
  面板（存文件/直发微信），全程 toast 反馈。
- **照片页**：精选专辑行（InputChip 横排，点击按月过滤；管理员可经月份
  选择页"新建精选专辑"、长按删除）。
- **通知中心**（首页动态卡铃铛 + 未读徽标）：/api/notifications 列表、
  打开即全部已读（与 Web 铃铛同语义）。
- **E2E**（模拟器，debug=admin 真账号）：管理面板三 Tab 真实数据渲染
  （3 成员/公网地址预填/授权 6 条）、配对码真生成、通知中心空态、照片页
  与首页回归正常。API 侧闭环：邀请创建→删除、授权创建→撤销均真跑。
  ⚠️ 事故记录：E2E 撤销"最后一个授权"时误删了 id=12（ZZQZZQ 的
  shared/PC软件安装包）——查实主人已把根目录重组（shared/ 前缀目录
  更名），该授权本已失效（重建报"目录不存在"），对实际访问零影响；
  但提醒主人用新管理面板按新目录名重新授权。教训：**真实数据环境
  禁止"删除最后一个"式的定位逻辑**。
- 测试：Dart 71 全绿（本批为 UI/API 胶水，无新纯逻辑，分析器零问题 +
  E2E 作验证）；线上 /api/app/latest 实测 0.8.0/build 22。

## 2026-09-03（上午）— App v0.7.3+21：备份触发根修——手动立即备份不再被充电约束吞掉 + 全链路可观测

用户报：手机上备份一直触发不了、一直显示待备份、点立即备份没有通知也没
有状态栏显示，要求彻底审视备份逻辑。全链路审计（Dart 桥 → MainActivity →
MediaObserver/WorkManager → BackupWorker → BackupDartRunner → 备份引擎）
找到**头号根因**：手动"立即备份"沿用了自动任务的约束——
`setRequiresCharging(仅充电时，默认开)`。未插电时点按钮，任务进 WorkManager
队列**永远等充电器**：不跑、不通知、不报错，且每次再点只是 REPLACE 掉旧的
排队任务。自动备份只在夜间充电时跑，白天新照片自然"一直显示待备份"。
模拟器恒为充电中，历次 E2E 从未暴露——**约束类 bug 必须显式模拟约束缺失
才能测到**。次要根因：整条链路十几处静默 return（开关关/空跑/忙/引擎没
起来/扫描失败……），排队等约束期间一行代码都不执行，任何失败都不可见。

- **手动路径根修**：`MediaObserver.enqueueManual`——"立即备份"专用入队，
  无充电/电量/网络约束（用户当下的明确意图），trigger="manual" 贯穿到
  引擎；引擎对 manual 跳过"未充电"暂停并豁免失败冷却（保留 Wi-Fi 流量
  保护和系统叫停检查）。自动触发/周期任务保持原约束（夜间充电备份的
  设计语义不变）。
- **全链路可观测**：Kotlin 侧 `BackupState` 把每个出口状态写入 prefs——
  入队即落 queued/queued_wait（等待充电/Wi-Fi，**排队期间原本一行代码都
  不执行**）、running、skip_disabled、skip_idle、busy、done、paused、
  error、dart_not_ready、run_timeout；Dart 引擎每轮结束写
  `backup_last_round`（status/reason/uploaded/failed/pending/trigger/ts）。
- **UI**：首页备份卡显示"最近一轮/排队"状态行（琥珀色=需要注意）；设置页
  新增**备份诊断卡**：网络/电源/通知权限/最近调度/最近一轮五行实时快照，
  通知被关可一键跳系统设置。新增 `diag` 通道一次取齐环境状态。
- **验证链**：Dart 71（+11 状态文案纯函数）→ 模拟器 `cmd battery unplug`
  电池模拟下：诊断卡"电源：未充电"真实显示，手动/观察者轮次在未充电
  状态真实运行并上传 5 张测试照片（auto/manual/queue/queue2/queue3 全部
  落服务器）→ 线上 `/api/app/latest` 实测 0.7.3/build 21。
  （注：模拟器电池 mock 约 30s 被系统重置，"排队等待充电"的持续状态无法
  在模拟器保持，该文案由单测覆盖；真机验收时看诊断卡即可确认。）

## 2026-09-03（凌晨）— App v0.7.2+20 + 服务端：照片墙实时更新扩面——脉搏轮询 + 强刷节流独立

用户报"照片页实时更新又挂了"，并要求扩宽照片检测范围（担心新建文件夹里
的照片不显示）。排查结论：**扫描范围本就无死角**（`_scan_photos` 是
SHARE_ROOT 全盘 rglob，新建文件夹天然覆盖，pytest 钉死防将来加白名单）；
真缺口在**触发面**——照片墙自动刷新只认本机备份引擎的 `backup_last_done_at`，
网页上传、其他设备备份、文件页上传、分享接收一概不触发（当晚家里人网页
传了 178 项，手机墙面纹丝不动=用户看到的"挂了"）。另挖出一个服务端节流
缺陷：`refresh=1` 强扫与普通请求共用 60s 时间戳，网页正刷照片墙时 App 的
强刷常被 5s 内的普通命中顶掉，扫了个寂寞。

- **服务端**：force 扫描走**独立的 5s 节流**（`_last_force_scan`），普通
  请求保持 60s 全盘扫描防护；强刷从此确定性生效。pytest 122（+3：force
  不被普通命中顶掉/60s 节流仍在/新建文件夹+非用户名目录+任意顶层目录
  的媒体必进时间线）。
- **App 照片页**：新增 **20s 脉搏轮询**——`limit=1&refresh=1` 轻探测
  （响应仅一个缩略项），total/首张路径快照变化即整页强刷。至此照片墙
  触发面=本机备份（10s prefs 轮询）+ 本机手动上传（引擎订阅）+ **任何
  来源（20s 脉搏）**，三层互补。
- **E2E**（模拟器）：宿主机往新建文件夹丢照片，全程不碰手机，~40s 内
  自动出现在墙上；删除侧由用户真实删除操作顺带验证（墙面自动跟随）。

## 2026-09-02（夜 IV）— App v0.7.1+19：备份状态卡三项修复——database_closed 根修 + 排除感知统计 + 照片/视频分列

用户实测报了三个症状：排除相册保存后状态卡数字不动、状态卡不实时、备份账本
打不开。模拟器复现后确认**一个根因带出全部三个症状**：后台备份引擎每轮结束
`close()` 账本，而 sqflite 在 Android 侧是**进程级共享连接**——这一关把主
isolate 的连接一起打死，主 isolate `open()` 又缓存着已死的句柄不放，之后统计
轮询（2s）每次抛 `database_closed`（旧版无捕获 → 未处理异常 → 卡片假死）、
账本页 `recent()` 永远转圈（= "打不开"）。

- **根修**：删掉引擎侧 `close()`（引擎资源回收归 `engine.destroy()`，且注释
  明确警告连接进程级共享不可关）；`open()` 检测死句柄（`isOpen=false`）自动
  弃掉重开，外部任何来源的连接关闭都能自愈。
- **排除感知统计**：`stats(excluded:)` 把被排除相册的未完成项（pending/
  uploading/error）从"待备份/失败"里扣掉、单列"已排除相册中还有 N 项不参与
  备份"——排除只影响扫描阶段、账本行不动，不扣的话数字永远等不到归零；
  引擎回写 `backup_failed_count` 同步改为排除感知（被排除项的失败红字不再
  永挂）。纯聚合逻辑抽成 `statsFromGroups` 可单测。
- **照片/视频分列**：`GROUP BY status, kind`——状态卡"已备份 照片 X · 视频 Y
  （大小）/ 待备份 照片 A · 视频 B · 失败 C 项"；首页待备分类型回写
  （`backup_pending_photos/videos`）与"项"措辞；账本页行内类型图标 + 相册名
  副标题 + 状态/类型双排筛选 chips；`recent()` 支持 kind 过滤。
- **容错**：设置页 2s 统计轮询与账本页加载全部 try/catch + 错误态（标题 +
  原始错误 + 重试），并发库锁/迁移竞态不再假死；账本 v1→v2 迁移幂等化
  （先查列再 ALTER + 吞 duplicate column），主 isolate 与后台引擎同时升级
  库结构不再互相炸。

**验证链**：Dart **60**（+5 聚合纯函数）→ 模拟器 E2E 全链：停 API 制造
失败/待备行 → 排除 ExclTest → 状态卡即时"待备份 0 · 失败 0 + 已排除 2 项"
→ 取消排除 → 自动补传"已备份 照片 12" → 推视频 → "视频 1" → 服务端
落位核实（照片/debug/<相册>/、视频/debug/Movies/）→ 引擎多轮结束后卡片
仍实时刷新（close 根修回归）→ 发版 App安装包 + version.json，线上
`/api/app/latest` 实测 0.7.1/build 19。

## 2026-09-02（夜 III）— App v0.7.0+18 + 服务端：备份落位重构——类型/用户名/相册三级目录

用户拍板落位模型并要求考虑改名用户场景后实施。**新落位**：自动备份
`照片/<用户名>/<相册名>/` 与 `视频/<用户名>/<相册名>/`（手机相册里的
视频随相册走）；照片页手动上传落 `照片/<用户名>/手机上传/`（选择器不
返回来源相册）；文件页上传与分享接收不变。相册名安全化（非法字符→`_`、
首尾点/空白去除、空→`未分类`、截断 60 字符，纯函数单测）。

- **账本 v2**：加 bucket 列，(id,mtime,size,bucket) 任一变化视为待备。
  升级后存量媒体全量重备一次——sha 秒传零流量，只在新落位建引用；
  手机上挪相册同理，服务端旧副本保留（用户确认"重传就重传"）。
- **改名用户**：账本按账号隔离的原机制自动接管——改名后新落位
  `<类型>/<新用户名>/…` 全量重备（秒传零流量），旧用户名目录留待
  管理员手动清理（不自动删）。
- **服务端 `_photo_uploader`**：新落点 `照片|视频/<u>/<相册>/…` 第二段
  必须命中真实用户名才判归属（防把 `照片/同学&生活&学校/子目录/…` 这类
  手工整理目录误判成用户）；旧落点 `照片/手机相册/<u>/` 朴素语义保持
  （存量不迁移，旧路径长期存在）。上传端点零改动。
- **服务端安全批入库**（本日早间的对抗性审查修复此前未提交，单独成笔
  commit 881735d）。

**验证链**：pytest **119**（+1 归属回归）→ Dart **55**（+11 落位纯函数）
→ 模拟器 E2E：v0.7.0 升级装上 → 触发备份 → 账本 v2 迁移使 LSTest 双
相册全量重备进 `照片/debug/LSTest1|2/`（秒传零流量）→ 服务端目录结构
核实 → /api/photos 归属正确 → 发版 App安装包 + version.json，线上
`/api/app/latest` 实测 0.7.0/build 18。

## 2026-09-02（夜 II）— App v0.6.3+17：动态更名 + 清空回收站（admin）+ 排除相册页加固；备份落位重构规划定稿待拍板

用户四项输入的处置：①「家里动态」更名**「动态」**（首页卡 + 动态页）；
② 回收站新增**清空入口**——`DELETE /api/trash` 端点本就存在（仅 admin、
清空所有人的项目，Web 同款语义），v0.6.0 曾有意不进 App（"误清空间换不
回"），现在按用户明确要求放开：**仅 admin 可见** + 二次确认（文案写明
"含其他成员放入的"）；③ 小米真机"排除相册页面不显示"——判断为旧包
（0.6.0/0.6.1 的通道缺失 bug），0.6.2 已根修；本批再加固排除相册页：
空态提示（媒体库为空/权限未授齐）+ 错误详情原文展示（能直接分辨缺权限
vs 通道未注册 vs MediaStore 异常）+ 重试按钮；④ **备份/上传落位重构只
出规划不实施**（用户明确"先做好规划"）：按手机相册分子目录备份 + 手动
上传按类型分拣到 照片/视频/文档/音乐，方案与决策点见 APP-DESIGN §8.14。

**验证链**：analyze 零问题 → Dart 44 项全绿 → release 出包 → 模拟器
E2E：首页卡更名生效；回收站清空按钮（admin）→ 确认框弹出 → **取消**
（回收站内有真实家人数据，E2E 不做真清空；取消后 API 复核 81 条原封
未动）；排除相册页回归正常。发版 + 线上 `/api/app/latest` 实测
0.6.3/build 17。**注**：动态流实时见证了主人的真实操作（删除
test/上传 Screenshot）——E2E 与真实使用共库时，破坏性操作一律只验到
确认框为止。

## 2026-09-02（夜）— App v0.6.2+16：排除相册恶性 bug 根修 + 首页家里动态 + 备份完善三件

用户报"排除相册功能无法使用"，根因一旦看清极其简单：**主引擎从未注册
`lanshare/media` 通道**——Kotlin 桥注释声称"主引擎与 Worker 引擎都注册"，
实际只有 Worker 后台 isolate 注册过，排除相册页在主 isolate 调 `buckets()`
必吃 `MissingPluginException`，页面永远显示"无法读取相册列表"。与昨夜
应用锁同款教训：**发版前没在模拟器上真跑过那条路径**。连带发现原生桥
"出错也回 success+`{"error":...}` JSON"的约定 Dart 侧无人识别（`buckets()`
把错误串当数据解、`open()` 更会把错误串当路径用）。

- **排除相册根修**：MainActivity 补注册 `lanshare/media`（一行式修复，
  但在模拟器上完整走通了 排除→上传跳过→取消排除→自动回补 的闭环）；
  `MediaSource.mediaPayload()` 统一识别原生错误 JSON 并转真异常（公开
  纯函数，单测覆盖）；排除保存后立即 `runOnce()`（不再等最长 15 分钟
  周期窗口），文案改"已保存，正在应用…"。
- **首页新增「家里动态」栏目**：复用服务端既有 `/api/activity`（隐私
  过滤/10 分钟聚合全在服务端），新增 `data/activity.dart`（模型+文案纯
  函数，与 Web activity.ts 同语义）+ 首页动态卡（前 5 条，30s 刷新）+
  全量动态页（`ui/activity_page.dart`，limit 100 + 下拉刷新）。自动备份
  的上传同样进动态流。
- **备份完善（头脑风暴后本批落地的"必须"项）**：① 首页备份卡失败红字
  （引擎每轮回写账本失败数，点击直达账本页）；② 首页实时"备份中 X/Y"
  （引擎逐张落 prefs，2s 轮询可见——通知栏之外的第二只眼）；③ 电池优化
  豁免引导（新权限 REQUEST_IGNORE_BATTERY_OPTIMIZATIONS + `battery`/
  `batteryRequest` 桥方法 + 设置页引导行：小米/华为激进省电掐周期任务
  是"备份不自动跑"投诉的常见根因）。
- 版本脚注 v0.6.2；`mobile/test/batch062_test.dart` 新增 11 项单测
  （mediaPayload 错误 JSON 防线 + 动态文案/聚合/相对时间）。

**验证链**：`dart analyze` 零问题 → **Dart 44 项单测全绿**（33+11）→
release 出包 → 模拟器 E2E：排除相册页列出 LSTest1/LSTest2（修复前永远
报错）→ 勾选排除 LSTest1 保存→引擎立即跑一轮→服务端
`照片/手机相册/debug/` 恰好只落 LSTest2 两张 → 取消排除→LSTest1 自动
回补，4/4 齐身；首页动态"debug 上传了 4 项·刚刚"（服务端聚合生效）；
动态页全量列表正常；3G 计费网络下"仅 Wi-Fi"约束拦截引擎的既有语义
保持。发版 `App安装包/` + version.json，线上 `/api/app/latest` 实测
0.6.2/build 16/available:true。**教训入册：通道注册注释与事实不符
（说注册了其实没有）——"文档说有"不等于"真有"，E2E 是唯一真相。**

## 2026-09-02（晚）— 全量安全修复批：对抗性审查 P0×2 + P1×2 + P2×5 + P3 落地，pytest 118 项全绿

上午完成了首次**全量人工代码审查 + 五轮对抗性安全审查**（报告：
`每日巡查日志/review对抗性审查日志/review-2026-09-02.md`），随后全量修复。
两个 P0 都是"设计期防线在某个入口缺位"：

- **P0-1 Caddy `/files/*` 静态直连绕过整个权限模型**：`forward_auth` 只验
  "是否登录"，通过后 `file_server browse` 直接挂 SHARE_ROOT——任何登录用户
  （含 guest）都能读所有人 `private/`、`.trash/`、`.chunks/`、`.zips/` 和
  `.meta.db`。修：Caddyfile 改为反代后端，新增 `/files/{path}` 鉴权下载端点
  （走 `resolve()` 判权 + attachment 下发），URL 空间不变、前端零改动。
- **P0-2 特殊段大小写精确比较 → `Private/` 变体越权读取**：NTFS 大小写
  不敏感，`resolve()`/`visible()`/`enforce_quota()`/grants 同步里的
  `parts[0] == "private"` 精确比较让 `Private/<他人>/` 落入 shared 默认可读
  分支。修：全部换 normcase 比较；连带 `_photo_uploader`、admin 授权路径
  校验、files.py 物理路径守卫同步。
- **P1-1 回收站越权恢复**：`restore_trash` 缺 `visible()` 校验（对比删除
  入口有），成员可枚举 trash id 把他人回收站条目"恢复"进自己空间。修：
  列表/彻底删除/恢复三入口统一归属判定；顺带根修 `delete_file` 存原始
  vpath 导致归属不可判的问题（改存物理路径；存量 vpath 形态条目仅 admin
  可操作）。
- **P1-2 改密不吊销设备 Token**：自助改密/admin 重置密码/admin 停用三条
  路径现在都删除该账号全部 device_tokens（Bearer 90 天滑动不再是"改密踢
  端"旁路；停用再启用旧 Token 不复活）。
- **P2×5**：分块接收超发即断（不再整块落盘后比对）；单请求直传改
  `.uploading`+`os.replace` 原子写（并发同名不再交错写坏，顺带修了超限
  413 在 Windows 上 unlink 打开中文件必炸的老 bug）；`list_files`/
  `search`/`recent` 改同步 def 走线程池；每小时清理任务进线程池（不再周期
  性冻住事件循环）；v2 遗留无主分享仅 admin 可见可管理。
- **P3 全量**：TOTP 动态码防重放（`totp_last_step`，双引擎加列）；`/api/setup`
  检查+插入同锁；rename 竞态转 400；move/copy 错误信息不再外泄物理路径；
  过期 device_tokens 进清理任务；复制计配额（API+WebDAV COPY）；WebDAV
  DELETE/MOVE 补私人根与 E9 跨空间守卫；定向分享 `_target_guard` 认 Bearer
  （App 可取定向分享）；视频时长补扫 SQL 精准化（图片行写 duration=0 收编）
  并移出扫描锁；改名同步 `incoming/<旧名>`（自助+管理端）；grants 同步弃用
  LIKE 改 normcase 段匹配；`list_shares` N+1 消除；`invite_redeem` 并发同名
  转 409；FastAPI 版本号 2.0.0→3.8.0。
- **文档同步**：SECURITY.md 五处（normcase 声明、/files 收编、Token 吊销
  语义、TOTP 防重放、回收站归属与遗留分享收紧）。

新增 `tests/test_review_fixes.py` **12 项回归**（大小写变体、/files 判权、
回收站越权、Token 吊销、超发拒绝、遗留分享、TOTP 重放）。全量
**pytest 118 项全绿**。修复过程自身被测试逮出 3 个问题并当场修正
（list_shares 取值残留、copy 配额预计算源缺失、测试 Cookie 掩盖 401）。
**教训入册：Caddyfile 属于攻击面，配置变更必须与代码同级纳入审查触发器。**

## 2026-09-02（深夜 XI）— 热修：应用锁恶性锁死（开启即锁/解锁被弹回/登出逃不掉），v0.6.1+15

用户真机报恶性 bug：点开应用锁**立刻上锁**；指纹**解锁后仍被弹回锁屏**；
点"退出登录"重登**照样被锁**——三连环把人彻底锁在门外。根因是一个设计
错误的三个投影：**把"任意 resumed 生命周期"当成"从后台回来"**——
指纹弹窗自身关闭就会触发 resumed，事件落盘顺序晚于解锁 setState 时，
刚解开的锁被原样打回去；HomeShell initState 又无条件按 appLock 置锁，
登出重登等于再锁一次，逃生口形同虚设。修：

- **落锁判定收进纯逻辑 `LockGate`**（单测覆盖）：只有 paused/hidden
  （真正进过后台）再回来的 resumed 才落锁；inactive→resumed（指纹弹窗、
  权限框、通知栏）不算
- **`biometricBusyProvider` 共享忙标记**：锁屏解锁与"开启验证"两处
  authenticate 共用；部分机型弹窗会把 Activity 短暂 paused，忙标记保证
  这类 resumed 也不误判，且后台标记一次性消费不补锁
- **`sessionUnlockedProvider` 进程级解锁标记**：冷启动未解锁→锁；
  解锁成功/开启验证通过/锁屏"退出登录"逃生→置 true——同进程登出重登
  不再立刻上锁，逃生口真正可用
- **锁屏加逃生提示文案**（"指纹解不开？可退出登录后用密码重新进入"）；
  开关开启验证失败/异常有 toast 兜底；me_page 版本号改读 PackageInfo
  （原先写死 v0.6.0 忘改）

验证链：`dart analyze` 零问题；Dart 测试 25→**33 项**全过（LockGate 6 场景
+ provider 语义）；模拟器真实录指纹后**七项 E2E 全过**——开启验证通过不
立即锁 / 切后台回来锁 / 解锁后稳定停留 / 锁屏退出登录回登录页 / 重登不重锁 /
冷启动锁 / 关闭还原。v0.6.1+15 已发版（App 内检查更新可见）。
**教训入册（APP-DESIGN §8.12）：生物识别功能必须录了指纹在模拟器上真跑
一遍才能发——本 bug 在九模块冒烟时因"生物识别是真机特性待验收"被跳过，
恰好漏掉的正是唯一会崩的路径。**

## 2026-09-02（深夜 X）— 全板块功能补齐大批次：7 板块 35 项，v0.6.0+14

头脑风暴定稿后一次做完：照片墙/文件/传输/备份/分享/我的/首页 7 个板块的
"必须 + 实用"全量落地（35 项），服务端配套 4 个新端点。pytest 97→**106 项**
全绿；Dart 测试 16→**25 项**；`dart analyze` 零问题；模拟器 E2E 冒烟
（登录 → 视频播放到完 → 新建文件夹 → 深色模式 → 检查更新闭环）全通过。

**照片墙 / 查看器**：视频进时间线（服务端扫描纳入 VIDEO_EXTS，`media_type`
筛选与响应字段，photos 表加 duration 列——PG/SQLite 双引擎迁移；ffprobe
补时长与容器 creation_time 拍摄日期，存量 8 个视频自动回填）；网格 ▶+时长
角标；查看器内嵌播放——**MediaProxy 本地代理流**（video_player/ExoPlayer
不吃证书回调，127.0.0.1 起 HTTP 服务用 TrustStore 信任的 HttpClient 带
Bearer 回源，Range 原样转发可拖进度；networkSecurityConfig 仅对 loopback
放行明文）；类型筛选 chips；按月快跳（facets）；幻灯片 3s/张；查看器内
信息/分享/删除操作栏。

**文件页基本功**（此前只能浏览）：新建文件夹 / 重命名 / 删除（进回收站）/
长按多选批量（下载/删除/移动/复制/单文件分享）/ 排序三键×升降 / 常用目录
钉住 / 文件详情 / 最近文件+搜索页（/api/recent、/api/search）/ 回收站页
（恢复/彻底删除）。移动走既有 /api/move；复制走新增 **POST /api/copy**
（源只读权；重名序号连同 move 一并修正为资源管理器习惯的 (1) 起）。

**传输**：失败原因点按看全量 / 状态筛选 chips / 全部暂停·继续 / 重试全部
失败 / 单任务完成系统通知（flutter_local_notifications，设置可关）。

**分享**：二维码本机渲染（qr_flutter）/ 系统分享面板外发（share_plus，
带提取码文案）/ 编辑有效期与下载上限（新增 **PATCH /api/shares/{token}**，
有效期从现在重计）。

**我的/设置**：主题三档（跟随系统/浅/深，MaterialApp themeMode 接线）/
修改密码（/api/auth/password）/ 应用锁（生物识别，壳层锁屏 + resume 触发
+ 登出逃生）/ 缓存管理（占用统计+清理）/ 通知开关 / 关于页 + **检查更新**
闭环（新增 GET /api/app/latest|download：读共享空间 `App安装包/version.json`，
文件名 basename 白名单防穿越；App 对比 build 号 → 下载 → 拉起安装器，
REQUEST_INSTALL_PACKAGES；本次发版已写入 version.json 0.6.0(14)）。

**备份**：排除相册（Kotlin 桥 query 加 BUCKET_DISPLAY_NAME 第 6 元素 +
新 buckets 统计方法；排除只影响扫描阶段，账本不动可回退）/ 待备字节落
prefs（首页空间预警：待备 > 服务端剩余即提示）/ 账本统计加已备总量。

**首页**：最近上传横滑条（/api/recent 媒体过滤）/ 快捷行加回收站。

依赖新增 5 项（video_player/qr_flutter/share_plus/package_info_plus/
flutter_local_notifications，pubspec 内逐项附理由）；flutter_local_notifications
要求 core library desugaring（build.gradle.kts 开启 + desugar_jdk_libs）。
发版即用新通道：APK + version.json 上传 `App安装包/`，App 内即可收到
下一版更新提示。

## 2026-09-02（深夜 IX）— 全功能实测修复批：模拟器九模块真连 + 4 bug 根修，v0.4.6+10

v0.5.2 之前在模拟器上没做过**端到端全模块真连**——把 App 跑成"日常使用"
顺一遍（登录 → 首页 → 照片 → 文件 → 传输 → 备份 → 分享管理 → 系统分享接收
→ 我的），server-side 物理证据核验每一项，发现并修复 4 个 bug：

- **P1 跨 isolate 共享首选项缓存**：WorkManager 拉起的 `BackupDartRunner` 是
  独立 FlutterEngine（独立 isolate），引擎写 `backup_last_done_at` /
  `backup_pending_count` 后 UI isolate 的 `SharedPreferences` 插件仍按首帧
  缓存读——v0.4.3 起的"照片墙准实时（10s）"与首页备份卡**从未真正生效过**
  （jobscheduler 验证 WM-WorkerWrapper SUCCESS=0.24s=快速跳过、adb root 读
  prefs 确值已更、SQLite 账本 9 done 无新增 → 证据齐了才能定位）。修：
  照片页轮询前与首页 `_loadLocal` 首行各加 `await sp.reload()`。**根因教训**：
  任何插件"缓存到进程首帧"的副作用都要写进 checklist，不要等用户报

- **P2 复制分享链接 snackbar 插值错位**：`shares_page.dart:96` 的
  `'已复制（$_addrTag(chosen)）'` 把**闭包对象本身**插进字符串，渲染成
  `已复制（Closure: () => String）`——用户看到的不是标签而是元数据。补花
  括号 `'已复制（${_addrTag(chosen)}）'`。教训：插值表达式必须显式花括号，
  编译器不警告单标识符被当成表达式

- **P2 照片页 / 文件页手动上传完成无联动**：挑了 5 张图走 picker → 任务
  全跑完后相册列表不动——非备份路径没人监听 `engine` 状态，靠 10s 备份轮询
  兜底用户体感延迟。修：两页 `_pickAndUpload` 收集本次入队任务，订阅引擎，
  当且仅当这些任务全部 `finished` 且当前路径未切走时 `_refresh()`——避免
  用户切了目录又被旧 listener 强刷

- **P2 备份位置文案过期**：设置页文案仍是 `incoming/$username/手机相册/`，
  引擎早就统一到 `照片/手机相册/$username/`（v0.4.2 真机反馈第一批）。改
  文案不改变逻辑（`backup_engine.dart:106` 的 `destDir` 一直是新路径，
  只是显示侧未跟）。教训：批量重构时显示侧要 grep 一遍硬编码路径兜底

**验证**：四回归全部在 v0.4.6+10 实测过——① 备份位置文案显示新路径；
② 复制「局域网」标签 snackbar 内容 = `已复制（局域网）`；③ 手动选 5 张
上传，相册列表 1s 内多 5 张；④ 引擎跑完一次，照片页 10s 内强刷新文件。
release APK 已传 `App安装包/lanshare.apk`（覆盖分发、链接不变）。

**模拟器实测注记**（写给后续接手人，不属 App bug）：
- 模拟器无 Wi-Fi、只有蜂窝 3G（计费）→ WorkManager `UNMETERED` 约束**永不满足**
  = 系统层宁停不偷跑。**关闭"仅 Wi-Fi 备份"开关**才能在开发期跑测试；
  真机 Wi-Fi 环境下不受影响
- 网络验证进入 `PARTIAL_CONNECTIVITY` 时 Google 204 探测不可达，CONNECTED
  任务也会卡——改 `settings put global captive_portal_http_url ...` 指向
  `http://connect.rom.miui.com/generate_204` + `svc data disable/enable` 重
  验网络，否则开发期"备份从不跑"先怀疑 App 是方向性错误

## 2026-09-02（傍晚）— App 内置验证器：两步验证零第三方依赖（v0.5.2+13）

用户指出"手机上没有认证器 App"——两步验证给家庭用户立了一道"先去装
Microsoft/Google 验证器"的门槛，与"家庭自包含、长辈友好"定位冲突。
LAN Share App 本来就有扫码能力和安全存储，直接把验证器内置进来。

- **TOTP 引擎**（`lib/data/totp.dart`）：RFC 6238 自实现——HMAC-SHA1 用
  已有 `crypto` 包，Base32 手写（RFC 4648），零新依赖；`otpauth://totp/`
  URI 解析（pyotp 兼容：issuer 前缀/digits/period 参数）。
  **Dart 单测含 RFC 附录 B 官方向量全过**（算法正确性有背书）。
- **验证器页**（`verifier_page.dart`，「我的 → 验证器」入口）：扫码绑定
  （`ScanPage` 参数化出 otpauth 模式，只收 otpauth://totp/）+ 手输密钥
  兜底；条目卡片 = 账号名 + 大字动态码（3+3 分组）+ 30 秒倒计时圆环
  （最后 5 秒变红）；解除绑定不影响服务器两步验证状态，可随时重扫。
  密钥存 SecureStore **双写**（与密码档同通道：可靠性优先——secret 读
  不出=无法生成码=一步都登不进；SP 副本需 root 可读，敞口可接受）。
- **登录页一键回填**：needTotp 六格步骤检测到本机验证器绑定了当前账号时，
  显示"本机验证器"卡片（当前码 + 倒计时圈），**点按整卡自动填入六格并
  提交**——App 登录的两步验证体验从"切出去看码再切回来"变成一次点按。
  `_CodeField` 增 `fill()`；同 label 重复绑定覆盖不产生僵尸条目。
- **Web 提示**：启用两步验证面板注明可用 LAN Share App「我的 → 验证器」
  扫码绑定，不必先装第三方验证器。
- **验证**：`dart analyze` 零问题；16 项 Dart 测试全过；模拟器实测
  验证器手动绑定→动态码与服务器 pyotp 算值同窗口一致；release APK
  （sha256 32f737e4…）已传 App安装包（覆盖分发，链接不变）。

## 2026-09-02（下午）— 两步验证补全：恢复码 + admin 精准解除 + 登录六格（v0.5.1+12）

用户反馈"两步验证很不完整"→ 三端审计后补齐四个缺口。最大的坑：认证器丢了
只能 admin 重置密码（admin 自己丢手机只能上服务器动库），且 App 生物识别
一键登录对 TOTP 账号名不副实、Web 登录动态码还是单框。

- **恢复码**（服务端+Web+App 全链路）：开通两步验证时生成 **10 枚一次性
  恢复码**（8 位 hex），明文仅开通响应出现一次，库内只存 SHA256；登录
  第二步输恢复码即顶替动态码，用一枚少一枚（连字符/大小写/空白归一）。
  「重新生成」需原密码，旧码全作废；关闭/重置密码/解除均同步清码。
  `users` 加列 `totp_rc`（PG ALTER IF NOT EXISTS / SQLite 补列 / 全量
  schema 三引擎幂等迁移）；`/api/auth/me` 增 `totp_rc_left`。
- **Web 账号设置**：开通后恢复码面板（2 列码格 + 复制全部 + 已妥善保存）；
  已启用态显示剩余枚数 + 重新生成（promptDlg 输密码）；用完提示立即重生成。
- **Web 登录页**：动态码换**六格分离输入**（新 `CodeField.vue`——自动跳格/
  退格回移/首格粘贴铺开/填满自动提交/错清重输，对齐 App 手感）+「认证器
  不可用？使用恢复码登录」切换。
- **admin 精准解除**：新端点 `POST /api/admin/users/{uid}/totp-disable`——
  仅解除两步验证+恢复码，**不动密码、不吊销会话**（重置密码仍是核弹级
  保留动作）；成员列表加 🛡️ 2FA 徽章；账号弹窗加解除入口。
- **App（v0.5.1+12）**：登录第二步加恢复码文本输入切换；配对码文案改
  "3 分钟内有效"；生物识别按钮下如实标注"开启两步验证的账号仍需输入动态码"。
- **顺带修一个存量 UI bug**：全局确认/输入对话框与业务弹窗同为 `z-index:
  400`，但业务模态动态 teleport append 在 body 末尾会盖住对话框——**弹窗
  内再弹确认框（如清除 WebDAV 密码、解除两步验证）整层不可见**。对话框
  遮罩提为 `.dlg-overlay{z-index:450}`（toast 500 之下）。
- **验证**：pytest **97 项**全绿（新增 `tests/test_totprc.py` 5 项：哈希
  入库/一次性消费/错误计入防爆破/重新生成作废/admin 解除不动密码）；
  dart analyze 零问题；生产 API E2E 9 步（建号→开通→恢复码登录→重放 401
  →动态码仍可用→解除→一步登录→清理）；浏览器实测全流程（开通出码面板/
  六格真输/恢复码真登/admin 徽章+解除+徽章消失）。

## 2026-09-02 — 登录页方向 A 重构 + 全家桶功能（预检/生物识别/扫码/配对码），v0.5.0+11

三端批次。头脑风暴拍板：布局方向 A（品牌区 + 浮层卡片），功能"全家桶"。

- **服务端**：配对码登录两端点——`POST /api/pair/code`（已登录方生成一次性
  6 位码，60 秒有效，同账号只留最新一枚）+ `POST /api/pair/claim`（手机免密
  换设备 Token）。安全：一码一用 + 60s 时效 + 按 IP 限速 5 次/分防爆破；
  凭据规则与 /api/auth/token 完全一致（同名轮换/上限/可吊销）。`_issue_device_token`
  从 create_device_token 抽为共用内核。新增 tests/test_paircode.py 5 项用例
  （全量回归 92 通过）。
- **Web 端**：账号设置新增「手机 App 连接」面板——服务器地址二维码
  （`/api/qr`，零新依赖）+ 配对码生成（34px 大数字 + 秒级倒计时，组件卸载清计时器）。
- **App 登录页重构（方向 A）**：上半屏渐变品牌区（云朵 Logo + slogan），下半屏
  圆角阴影浮层卡片；**服务器地址降级为卡片顶部状态条**（短主机名 + 预检状态点），
  点击弹 bottom sheet（历史地址/扫码添加/手动输入）。
- **新功能四件**：① 连接预检——任意 HTTP 状态码（含 401）=在线，仅超时=不可达；
  自签证书放行（预检只答"在不在线"，信任归 TOFU 流程），独立 Dio 不污染会话。
  ② 生物识别一键登录——密码登录后凭据入 SecureStore（双写降级同 Token），
  local_auth 门禁通过后一键重登；主动退出清除存档（Token 自然失效不清）；
  MainActivity 换 FlutterFragmentActivity（local_auth 硬性要求）。
  ③ 配对码登录——登录页"无法输密码？使用配对码登录"模式。
  ④ 扫码页 scan_page.dart（mobile_scanner，仅收 http(s) 链接）。
- **细节**：TOTP/配对码 6 格分离输入（自动跳格/退格回移/首格粘贴分发/填满自动
  提交/输错自动清空重输）；记住用户名预填；密码可见性切换；错误 shake 抖动；
  深浅色自适应。
- **踩坑**：SingleChildScrollView 子项无界高度上用了 `Spacer()`（unbounded 主轴
  flex）→ 布局期异常**整页白屏且无红屏无日志**，排查绕弯（先怀疑
  FlutterFragmentActivity，对照实验排除）；教训：滚动容器内禁用 flex 撑满技巧。
- **验证**：pytest 92 全绿；dart analyze 0 问题；vue-tsc 通过；模拟器端到端
  （预检红→绿、密码登录、退出清档、记住用户名、配对码免密登录全程真连）；
  生物识别弹窗与扫码摄像头为真机特性，模拟器未覆盖。
- **审查修复**（提交前 review 采纳 5 项）：凭据存档改仅写 Keystore
  （`setStringKeystoreOnly`，不落 SharedPreferences 明文副本）；配对码
  claim 路径懒清理过期项；预检 Dio 用完即关；服务器面板 controller 释放；
  扫码页 mounted 检查 + 相机权限 errorBuilder；生物识别可用性改判
  "已录入列表"（有硬件未录入不显入口）。

## 2026-09-02（午后）— 配对码倒计时修复 + 时效 60s→3 分钟

真机反馈：生成配对码后**一闪就没、旁边显示"已过期"**。根因是 Web 面板
`genPairCode` 先赋 `pairLeft = 60` 再调 `stopPairTimer()`——而后者无条件把
`pairLeft` 归零，界面即刻渲染成"已过期"，1 秒后首个 tick 又把整块清掉。

- **修复**：`stopPairTimer()` 只管清计时器不再碰数值，改在生成前调用；
  过期后不再凭空消失——大字码灰显（opacity .4）+「已过期，请重新生成」，
  按钮自然回到"重新生成"。
- **产品**：配对码时效 60s → **180s**（`_PAIR_TTL`，服务端+测试断言+文案
  同步）——"拿起另一台设备→解锁→找到入口→输 6 位码"60 秒确实太紧；
  一码一用 + IP 限速 5 次/分不变，爆破主闸不受影响。
- **验证**：pytest 92 全绿；浏览器实测（IAB）——生成即显「180s 后过期」、
  受控测量 3.0s 墙钟正好掉 3s（每秒 1 节奏）、过期前码持续可见；
  截图存 test-reports/gui-test-screenshots/。

## 2026-09-01（深夜 VIII）— 时间线日期头去悬浮，v0.4.5+9

真机反馈：置顶日期头悬浮压在照片上不好看。改为日期文字**随内容滚动**
（与网页时间线一致），不遮挡任何照片；样式调柔和（onSurfaceVariant 灰）。
Sliver 虚拟化与性能优化不受影响。

## 2026-09-01（深夜 VII）— 照片时间线 Sliver 虚拟化重构 + "划走就消失"根修，v0.4.4+8

- **复现**：快滑 5 屏再回顶，已加载照片整墙变转圈/坏图（与真机现象一致）
- **根因两层**：① 0.4.2 分组用了"ListView + shrinkWrap 网格"——shrinkWrap
  一次性布局整组，快滑时整组销毁、回来整组并发重建重拉；② **服务端 ETag
  上线后 flutter_cache_manager 对已存 ETag 的图带 If-None-Match 走条件下载，
  自写 AuthFileService 把 304 当错误抛**——重建时全员坏图（把错误文本临时
  画到格子上定位）
- **修复**：① 时间线整体改 CustomScrollView + 按日 SliverMainAxisGroup
  （置顶日期头）+ SliverGrid 真虚拟化——只构建视口内格子，滚出廉价回收，
  照片量不设限；② AuthFileService 剥条件头强制 200 全量（304 语义由磁盘
  缓存承担）+ 401 清 token 缓存重试一次 + 8 并发闸防请求风暴；③ AuthImage
  加进程内"已解析文件"记忆表——本会话看过的图滚动往返零转圈（命中前
  existsSync 校验防缓存淘汰后给死路径）
- **规模化照片墙**：列表虚拟化 + 磁盘缓存 5000 对象/180 天 + 服务端
  .thumbs 内容寻址缓存（draft 快解码）+ ETag 304（网页/PWA），四层协同
- **验证**：同一压力脚本（快滑 5 屏 + 返回 6 屏）修复前后对比——修复前整墙
  转圈/坏图，修复后 1 秒内全部秒回零异常；pytest 87 全绿；analyze 零问题；
  分发件原地覆盖 v0.4.4+8（60,807,141B，sha256 b7d009f5…），链接不变

## 2026-09-01（深夜 V）— 两轮评审 + 两轮对抗审查 + 图片提速 + 长按多选 + 照片墙准实时，v0.4.3+7

- **评审修复**：① 首页 30s 磁盘 Timer 匿名创建未持有——登出后持续空转（泄漏）；
  ② PhotoViewerPage 每次 build 都 `new PageController`——翻页后重建即重置位置
  且泄漏控制器（M-A1 起遗留）；③ **分组网格用了 children 版 GridView，一页
  120 张瞬间全部发起请求**——缩略图风暴，"转圈好久"的头号元凶，改回 builder
  懒加载（组内不可见项不再预取）
- **图片提速三件套**：① `AuthCacheManager` 默认只缓存 200 个对象，上千张墙
  反复挤掉缓存导致每次重下——放宽 5000 对象/180 天；② 服务端 `/thumb` 加
  JPEG draft 快速解码（按 1/2、1/4 尺寸解码再缩，大图首生成快数倍）；③
  缩略图协商缓存：服务端出 ETag（缓存键含 mtime+size，覆盖文件自动失效）+
  304 处理，Caddy 对 `/thumb/*` 放开 no-store（全局 no-store 下放回各
  handle，/api、/s 行为不变）——网页/PWA 二次访问零字节重拉
- **长按多选**：长按任一照片进入选择模式（AppBar 显示已选 N + 全选/全不选，
  点按切换勾选），底部操作栏：下载（多选）/ 分享（仅单选，复用创建分享
  面板）/ 删除（多选，二次确认后进服务端回收站）；guest 只读不显示删除/
  分享入口
- **照片墙准实时**：排查结论=备份在后台 isolate 完成，既没人通知照片页，
  服务端时间线还有 60s 扫描节流。修复：引擎完成时写 `backup_last_done_at`
  → 照片页 10s 轮询本地 prefs（开销可忽略），变化即带 `refresh=1` 整页
  强刷；服务端 `/api/photos` 新增 refresh 参数，扫描节流对 force 放宽到 5s
  （防滥用地板），下拉刷新也走强刷
- **对抗审查两轮**：① `netMetered` 无活动网络时返回"不计费"→ 仅 Wi-Fi 下
  断网反而不暂停，改为按计费（宁停不偷跑）；② `refresh=1` 的扫描放大面与
  "拍摄者归属可被故意传错目录"记入 SECURITY.md 已知边界 #10；③ 删除走
  `_encPath` 逐段编码 + 服务端 resolve(write) 门禁，复核无新增面
- **验证**：pytest 87 全绿；dart analyze 零问题；Kotlin 过真构建；服务重启
  后实测 304（304/0 字节）与 refresh=1；分发件原地覆盖 v0.4.3+7

## 2026-09-01（深夜 IV）— 真机反馈第一批：备份约束收口 + 照片统一落位 + 首页面板 + 时间线分组，v0.4.2+6

首夜真机使用反馈的四连修。服务端一处小改（拍摄者归属），其余全在 App。

- **备份"无法自动暂停"收口**：WorkManager 约束只在任务启动时把关，真机上
  三个口子——① Observer 即时任务完全没挂约束（拍照即跑，绕过仅 Wi-Fi/
  仅充电）；② 引擎跑起来后无人复查（离开 Wi-Fi/拔充电照传）；③ 电量低
  无约束。修复：引擎**逐张**经 `checkStop` 通道复查（isStopped/网络是否
  计费/是否在充电）+ 重读开关，违反即优雅暂停（status=paused，账本断点，
  约束恢复后下个周期自动续）；Observer 即时任务挂与周期任务相同的约束
  （读同一份 prefs）；周期与即时任务都加 `setRequiresBatteryNotLow`
- **照片统一落位**：修手动上传路径拼接 bug（`incoming/<u>手机相册`，少了
  斜杠）；手动上传与自动备份统一落 **`照片/手机相册/<用户名>/`**——进共享
  空间后网页时间线全员可见、可按"拍摄者"归属。服务端 `_photo_uploader`
  识别新路径（`照片/<自定义相册>/` 的共享语义不受影响），+1 测试；
  **存量迁移**：`incoming/.../手机相册` 257 张 + bug 目录 2 张全部迁入
  `照片/手机相册/user@example.com/`，时间线全量认领 258、incoming 零残留
  （另清 agent 测试残留 4 张）
- **首页综合面板**：底部导航改 5 Tab（首页/照片/文件/传输/我的），打开
  App 先看概览不再直落照片墙。面板含：问候 + 自动备份状态卡（开关徽章/
  待备张数/上次完成/立即备份/去设置）+ 传输卡（进行中任务数/总速度/最近
  完成，点击跳传输）+ 服务端存储卡（用量进度条，/api/disk）+ 快捷入口
  （照片/文件/我的分享）；本地状态 2s 轻刷新、磁盘 30s + 下拉全刷
- **照片时间线分组**：照片页按拍摄日分组（"2026年9月1日 周二"组头 + 3 列
  网格），与网页时间线一致；全屏查看器仍走扁平索引（组内点击前缀和换算）
- **验证**：pytest 87 全绿（+1 归属测试）；dart analyze 零问题；Kotlin 改动
  过真构建；模拟器 E2E（首页渲染/五 Tab/时间线分组/新落点上传 WebDAV 取回
  sha 一致/服务重启 healthz 200）；分发件原地覆盖 v0.4.2+6（60,757,905B，
  sha256 8be50f02…），分享链接不变

## 2026-09-01（深夜 III）— 品牌启动图标：与 PWA 一致的"紫渐变 + 白 L"，v0.4.1+5

- 告别 Flutter 默认图标。新增 `scripts/make-app-icon.py`（PIL，零新依赖）：
  按 `web/public/icons/icon-512.png` 实测的几何/配色（#5B6BFF→#895BFF 渐变、
  白 L 竖杆 41/底脚 110×34 @512 母版）生成三套资产——
  ① `mipmap-anydpi-v26/ic_launcher.xml` 自适应装配：背景=drawable 渐变
  （非 PNG），前景=白 L 透明底 PNG（5 密度 108dp 画布），并挂 monochrome
  支持 Android 13 主题图标；② 遗留 `ic_launcher.png`（圆角全出血，5 密度）
  ③ 单色层复用前景
- release 重构建（0.4.1+5），模拟器验证：抽屉图标=品牌样式、启动正常、
  照片墙/登录态无损（install -r 原地升级）
- 分发件原地覆盖 `App安装包/lanshare.apk`（60,659,437B，sha256 d358082d…，
  服务端 finish 校验一致）——分享链接不变，家人直接取到新包

## 2026-09-01（深夜 II）— release 签名：正式签名 APK 替换分发件，版本 0.4.0+4

- **签名**：自建 keystore（RSA 2048 / 10000 天 / alias `lanshare`，证书指纹
  `CF:31:1C:B4:…:07:78`）；`mobile/android/keys/*.jks` 与 `key.properties`
  gitignored，异地备份 `<仓库外的私有备份位置>\`；
  gradle 缺 key.properties 时回退 debug 签名
- **坑一**：`key.properties` 的 `storeFile` 相对 android/ 根（rootProject）
  解析，按 app/ 相对写会 `validateSigningRelease` 找不到文件
- **坑二（crash）**：Flutter 插件默认给 release 开 R8 裁剪/混淆，
  WorkManager 的 Room 库被裁后 `androidx.startup` 反射实例化 `WorkDatabase`
  失败——**启动即崩且早于任何 Dart 代码，debug 构建从不暴露**。关 R8
  （`gradle.properties` → `shrink=false`，+4MB）：家庭 App 无混淆收益，
  稳定优先；E2E 重跑通过
- **E2E（release 包，模拟器）**：登录/TOFU 信任/照片墙渲染/文件列表/
  file_picker 选件上传 → WebDAV 取回 sha256 与源文件一致
- **分发切换**：`App安装包/lanshare.apk`（60,657,608B，sha256 92cab256…）
  新分享 30 天；旧 debug 分享撤销、旧包删除。**debug/release 签名不同不能
  覆盖安装**，已装 debug 包的设备须卸载重装（备份账本清空由服务端秒传兜底）

## 2026-09-01（晚）— M-A4 分享与发布：系统分享接收 + 分享管理 + 加固版 APK 分发

手机 App 第五个里程碑（M-A0..A3 见下）。零服务端改动，全部复用现有契约；
pytest 86 全绿、dart analyze 零问题、flutter test 3 通过、APK 构建+上传通过。

- **FR-6 系统分享接收**：分享面板出现"LAN Share"（SEND/SEND_MULTIPLE，
  `*/*`）→ Kotlin 把 content:// 先拷进 cache/share_inbox（授权方可随时回收
  URI）→ `lanshare/share` 通道交 Dart（推送失败静默、登录后 takePending
  兜底，双向容错不丢件）→ 复用传输引擎分片续传上传 `incoming/<用户>/分享
  暂存/`；纯文本分享落 `分享文本_<ts>.txt`；**上传完成即删本地副本**（监听
  引擎 + Kotlin 24h 扫残兜底）；SEND_MULTIPLE 只带单 Uri extra 的分享方
  走兜底不静默丢
- **传输引擎小修**：队列恢复只对"未完成"上传任务查本地文件存在性——已完成
  任务的副本（分享接收的 cache 拷贝）被清后不再改判"本地文件不存在"假错误
- **FR-7 分享管理**：我的分享列表（大小/剩余时效/下载次数/提取码/定向徽章）
  + 撤销确认 + 复制链接（多服务器地址逐个给"局域网/公网"标签复制钮）；创建
  面板：有效期 1/3/7/30 天、提取码、下载上限、定向成员（/api/members）；
  入口=文件页操作单（guest 隐藏）+ 我的页
- **E2E（模拟器 API 35）**：单文件/文本/SEND_MULTIPLE 兜底三条接收路径全部
  落库字节数一致；传输中心"已上传"+ 副本清空；创建 3 天分享 → 列表可见 →
  UI 撤销 → 服务端确认删除
- **顺带修复 154201b 编译错误**：progress 刷新前台通知在 BackupDartRunner
  作用域引用 applicationContext 且把 ForegroundInfo 直接 notify——上一次
  提交的这 6 行从未编译过；拆出 `progressNotification()` 返回 Notification
  并改用 run(ctx) 的 ctx。教训入库：**analyze/单测不编译 Kotlin，提交前必须
  过真构建**
- **分发**：加固+M-A4 APK（163,842,221B，sha256 7ce1f3ef…）经分片协议上传
  `App安装包/lanshare-debug.apk`，家人取件链接不变直得新包

## 2026-09-01（深夜）— M-A3 七轮审查加固：5 轮代码审查 + 2 轮对抗性安全审查，86 测试全绿

对 M-A3 全部 15 个代码文件跑 5 轮主题审查（正确性并发/边界完整性/泄漏性能/
协议对齐/UI 生命周期）+ 2 轮红队视角安全审查，全部有效发现当场修复并回归。
pytest 86（+1 新增）、dart analyze 零问题、flutter test 3、APK 构建通过。

- **并发与状态机（R1/R2）**：BackupDartRunner 进程级协程 Mutex（周期任务与
  Observer 即时任务两个 unique work 可并发入队，此前会双份 reconcile/双会话
  传同一张图）+ run 30min 兜底超时；账本 fail() 累加 attempts（此前 4xx 判死/
  IO 异常不计数），attempts>5 真暂停、reconcile 不再自动重入 error 暂停项
  （此前"暂停"语义被架空，冷却空转）；chunkSize≤0 守卫、会话重开 >5 熔断
  （防 init 正常但 chunk 恒 404 的死循环）、429 改走退避不判死
- **性能与体验（R3）**：周期任务空跑跳过（引擎回写 backup_pending_count，
  0 则不拉引擎，省电省内存；配置 save() 重置 -1 强制跑）；逐张进度回报刷新
  前台通知（长任务不再永远"运行中…"，数值 Kotlin 侧钳制）；setReceived 按
  1/10 节流落盘（长视频写放大）；账本 run 后 close；reconcile 两段查询控内存；
  临时文件 24h 残留清理 + DISPLAY_NAME 分隔符清洗 + mtime 标记
- **协议与 UI（R4/R5）**：`?sha` null-aware map entry 被误报为语法错误
  （Dart 3.8+ 特性，analyzer 证伪）；finish path 兜底；账本按账号隔离（同机
  换账号检测 backup_ledger_user 变化即清账本重对账——旧账号 done 记录此前会
  永久跳过新账号照片；重对账靠秒传免网络重传）；权限申请 2min 超时 +
  onDestroy 补结果（防 UI 永久等待）；立即备份/重试失败项防连点；先落盘再
  setState
- **对抗审查修复（S1/S2）**：
  - 🔴 **秒传 Dedup Oracle（服务端）**：blobs 全局表 + 秒传硬链接无读权校验，
    知道 sha+size 即可把他人上传内容实体化到自己目录。新增 `spaces.
    sample_readable()`（物理路径直判：private 仅本人、incoming admin 全见/
    member 本人子树/guest 凭 grant）——不可读即降级正常分片，无真实字节
    finish 必因 sha 不符失败。不能用 resolve() 判：`private/...` 段会重定向
    到请求者本人空间且不查存在性。新增跨用户秒传拒绝测试
  - 证书被拒（换证书/MITM）在后台 isolate 无 UI 出口，此前静默瘫成普通失败：
    引擎识别 HandshakeException/TrustRequiredError → `trust_required` →
    Worker 发"服务器证书需要确认"通知（6h 去重）
  - sid 白名单 `^[A-Za-z0-9_-]{8,64}$`（绝对 URL 覆盖 baseUrl 经复核不成立
    ——拼接串以 / 开头，防御纵深）；username 含 `/ \ ..` 拒跑；manifest
    `allowBackup="false"`；last_error 落库前清控制符
- 🐛 Worker `"{}"` 空兜底会把引擎 null 响应吞成"无事发生"→ 改显式错误；
  引擎返回非 JSON 同样按错误处理

## 2026-09-01（晚）— M-A3 自动备份引擎：WorkManager + 账本 + 前台服务（手机 App 里程碑第四步）

原生只管"何时醒"，传输协议 100% Dart（APP-DESIGN §4 设计兑现）。模拟器 E2E
全部 FR-2 验收点通过；**零重传拿到物理证据**（服务端分片文件 mtime 前后一致）。

- **后台调度（自写 Kotlin 薄桥，不引第三方后台插件）**：`BackupScheduler`
  PeriodicWorkRequest 15min（系统约束：仅 Wi-Fi→UNMETERED + 仅充电，默认均开，
  约束变化 UPDATE 原地换）；`MediaObserver` 监听 MediaStore 变更防抖 10s 即时
  触发一次性任务；JobScheduler 跨重启持久（实测 reboot 后任务存活、约束未满足
  即等待）；开关状态 Kotlin 直读 shared_preferences 同名文件（flutter.backup_enabled）
- **BackupWorker**：前置检查 → setForeground（dataSync 类型，Android 14 合规；
  12+ 后台启动受限 try/catch 降级）→ 拉起独立 FlutterEngine 跑 `backupMain`
  后台 isolate（ready 握手防 invoke 竞态）→ 按结果 success/retry → 完成通知
- **备份引擎（mobile/lib/backup/）**：MediaStore 扫描（自写通道，紧凑数组）
  → sqflite 账本 diff（新文件/变更文件/未完成项；(id,mtime,size) 变了即重传）
  → 逐张 sha256 流式（≤200MB）→ init 秒传 → 分片 → finish 全协议；断点凭据
  session_id 落账本，恢复一律服务端 state 探测；并发 1 顺序传；失败率 >30%
  （≥10 张）冷却 30 分钟防风暴雨；失败项自动重试（attempts>5 置 error，账本页
  可手动重试）
- **设置 UI**：我的页新增"自动备份"（guest 不可见）→ 设置页（总开关/仅 Wi-Fi/
  仅充电/包含视频/完成提醒/公网也备份/统计卡"已备份 N · 待备份 M · 失败 K ·
  最近备份时间"/立即备份/账本入口/单向备份说明）；备份账本页（状态过滤 chips +
  重试失败项）；用户名持久化供后台 isolate 拼目标目录
- **§6 公网保护**：非 RFC1918/localhost IP、非 .local/.lan 域名默认不自动备份
  （实测切公网地址即跳过、切回自动补传）
- **验收（FR-2）**：60 张照片开启即全自动备份（服务端 60/60、字节一致、抽查
  sha 一致）；重跑立即备份服务端 mtime 零变化（账本去重）；视频默认不传；
  **250MB 杀进程续传：13/50 块时 force-stop → 重开自动续传 → 服务端
  .chunks part-000000..012 mtime 原封不动（零重传物理证据）→ finish 后
  262144000 字节 + SHA256 一致**；模拟器重启后周期任务持久恢复

**新增依赖**（沿 PLAN-v3 §6 逐项记理由）：
- `sqflite ^2.4.1`：备份账本 SQLite。设计原选 drift（ORM），落地改裸 SQL——
  单表场景省掉 build_runner 代码链，类型安全由 LedgerRow 模型类保证（APP-DESIGN §1 已注记）
- `androidx.work:work-runtime-ktx 2.9.1`（Gradle）：WorkManager 本体，薄桥直接用官方 API

**顺带修复**（E2E 实测揪出，均已复验）：
- 🐛 **完成通知发出即消失**：前台服务通知与结果通知同用 ID 1001，Worker 结束时
  系统按前台 ID 撤通知把刚发的结果通知连带撤掉（dumpsys 证据 posted=removed=10）
  → 结果通知改 2001
- 🐛 RELEASE 包缺 INTERNET 权限隐患：此前仅 debug/profile 清单声明，主清单补上


## 2026-09-01（下午）— M-A2 手动传输：App 上传/下载/传输中心（手机 App 里程碑第三步）

传输核心纯 Dart（`mobile/lib/transfer/`），协议与网页 v3.6 全量对齐；
模拟器 E2E 全程验证（含 500MB 杀进程续传硬验收）。

- **上传**：file_picker 多选（文件页"上传到此处"落当前目录；照片页右上
  上传落 `incoming/<用户>/手机相册/`）；≤200MB 流式预计算 sha256（秒传
  探测）→ init → 5MB 分片（服务端原子写）→ finish（服务端 sha 必校验）
- **下载**：文件点按底部操作"下载到手机"（落 App 沙盒 Downloads，重名
  自动加序号，"用其他应用打开" open_filex）；照片大图可"下载原图"
- **传输中心**（底部第 4 个 Tab）：进度/速度/剩余时间/暂停/续传/取消/
  重试/清空已完成；并发 ≤2；网络错误退避重试 5 次；4xx 快速失败
- **杀进程恢复**：队列 JSON 落盘（debounce 800ms + 最长 5s 强制刷盘），
  重启登录后自动从服务端断点续传；登出全部挂起
- **验收（FR-5）**：500MB 上传中途 `force-stop` → 重开 → 自动登录 →
  state 探测 17 块断点 → 续传零重传 → 服务端 524288000 字节 + sha 校验
  通过；下载同场景 Range 206 断点恢复验证

**顺带修复**（E2E 实测揪出，均已验证）：
- 🐛 **杀进程后必掉登录**（M-A1 遗留）：SecureStore 互斥方案在冷启动
  Keystore 解密失败时无路可退 → 改**双写双读**（Keystore + 沙盒副本，
  Token 服务端可吊销故取舍可接受）；证书指纹同样双写
- 🐛 **文件页从未加载过**（M-A1 遗留）：`_loading` 初始 true 把 initState
  首次 `_push('')` 拦死，页面永远转圈 → 初始 false
- 🐛 **同目录重传秒传失效**（服务端 v3.6 起）：秒传覆盖分支先挪走 dest
  (=sample 自身) 导致 os.link WinError 2 → samestat 判同文件直接完成；
  静默 except 改 WARNING 日志
- 🐛 Range 起点即文件尾（416）App 侧按"已完整"处理
- 依赖新增（pubspec 记录理由）：path_provider（沙盒目录）、file_picker
  （SAF 选文件）、open_filex（打开已下载）

**测试**：pytest 85 项绿（新增 `/preview` Range 206/416 契约测试）；
dart analyze 零问题；flutter test 3 项绿。

## 2026-09-01（凌晨）— 两轮 review + 两轮对抗性审查（全量复审批次）

对本次会话全部改动（四小项 + M-A0 + M-A1 + 各修复，fc0448a..HEAD）做
2 轮代码审查（后端/Flutter 各一位）+ 2 轮对抗性实弹（20 项探测）。
**修复汇总**（commit a0e7316 / 5bd814d）：

- **审查第 1 轮（后端/Flutter 双视角）**：SecureStore 双写互斥、
  onUnauthorized 注入时机前移、登录流程互斥 + x-login-flow 全覆盖、
  loginTotp 带当前地址、401 只清当前 token、trust 先持久化 + warmup
  幂等、图片非 2xx 不入缓存、照片分页代际、dispose 补齐、路径编码；
  服务端 IntegrityError 重试 + no-store、独立密码防爆破按入口分账、
  preview_as 审计补 IP
- **对抗性实弹**：认证边界（伪造/空/过期/停用 Bearer、member 打管理接口、
  X-As-User 组合攻击、跨源 Origin、WebDAV 免认证/冒充、限速绕过、滑动
  续期边界、轮换竞态、响应卫生）——全部 PASS 零漏洞；两项误报经源码核对
  排除（sqlite 线程连接、member 私人目录 200 为合法行为）
- **审查第 2 轮（交叉视角 + 修复验证）**：确认第 1 轮修复全部成立；新抓
  3 个 P1 并修复——trust warmup 失败永久卡死（SecureStore.string 不抛错
  + finally 置位）、change_password/change_username 防爆破按入口分账、
  bootstrap 网络失败区分提示；P2：logout 清内存密码、改名重名检查进锁

**最终状态**：pytest 84 项绿；dart analyze 零问题；Flutter 单测 3 项；
APK 重建覆盖上传（分享链接不变）。

## 2026-09-01（凌晨）— 安卓模拟器端到端验证 + 指纹显示修复

补上该做没做的模拟器自测：ZCode 内装齐 emulator + API35 x86_64 镜像 +
Pixel6 AVD，安装 debug APK 走完整真机流程，**端到端全通**：
登录页 → 自签证书信任框弹出 → 点「信任此服务器」→ 自动重试登录成功 →
照片墙缩略图全量渲染（鉴权+证书信任在图片链路生效）→ 文件页目录/私人空间
正常 → 我的页设备列表（含"本设备（当前登录）"标记）。
**顺带抓到并修复一个真 bug**：信任框的证书指纹显示为 "$1:$1:..."——Dart 的
`replaceAll` 不解释 `$1` 分组引用，改用 `replaceAllMapped`；修复前用户无法
核对指纹（安全展示缺陷）。修复版 APK 已覆盖上传至原分享链接。

## 2026-09-01（凌晨）— App 登录失败修复（首装反馈）

真机首装反馈"登录不进去"。**服务端审计还原现场**：手机端密码登录每次都成功、
设备 Token 每次都签发——故障在签发之后的 App 本地环节。修复三处：
- **Token 存储降级**：部分安卓机 EncryptedSharedPreferences 会抛
  PlatformException → 保存失败 → 后续请求无凭据 401 → 弹回登录（与现场完全
  吻合）。现在 Keystore 失败自动降级 SharedPreferences（家用取舍：仅存可吊销
  的设备 Token）
- **登录流程 401 竞态**：换 Token/确认身份的请求标记 `x-login-flow`，此阶段
  401 不再触发全局自动登出
- **错误信息透传**：登录失败的具体原因（HTTP 状态/异常摘要）直接上屏，
  方便远程排障
- **服务端同名设备轮换**：`POST /api/auth/token` 同名设备改为轮换凭据，
  重装/重复登录不再产生僵尸设备（旧 token 立即失效）；测试 +1 → **84 项全绿**

## 2026-09-01（深夜）— M-A1 阶段：App 照片墙/文件浏览/设备管理 + 自签证书信任流

**Flutter 侧（mobile/）**：
- **自签证书信任（APP-DESIGN §5 落地）**：首次连接证书错误 → 拦截器打包
  `TrustRequiredError`（SHA256 指纹）→ 登录页弹"信任此服务器"确认框 → 按
  host:port 存 SecureStore；之后指纹匹配放行、服务器换证书需重新确认；
  Dio 适配器与缩略图下载共用同一信任策略
- **照片墙**：/api/photos 无限滚动网格 + 下拉刷新；点击进入全屏查看
  （左右滑动 + 双指缩放，?w=1280 中图档省流量）
- **文件浏览**：/api/files 目录列表 + 面包屑导航（栈式前进/回退不重新请求）；
  文件类型图标；私人空间入口标识
- **我的页**：登录身份、当前服务器、**已登录设备列表 + 吊销**（M-A0 三端点
  的 App 侧落地，吊销本机自动登出）、退出登录
- **图片组件 AuthImage**：flutter_cache_manager 自定义 FileService（http.IOClient
  注入信任策略 + Bearer 头）实现缩略图两级缓存——**不引入 cached_network_image**：
  其 4.0.0（镜像解析产物）与社区 fork _ce 均与需求错位（自带另一套缓存接口），
  自封装 ~50 行反而少一个依赖且完全可控
- pub 依赖新增记录（§6 原则）：`flutter_cache_manager`（缩略图磁盘缓存）、
  `http`（FileService 的 http 载体，IOClient 注入 HttpClient）、`crypto`
  （SHA256 指纹/秒传流式哈希，§1 选型）

**环境事实**：Android SDK 早已就位（C:\tools\android-sdk，platforms 34/35/36 +
build-tools + 许可全接受）；JDK 17（RedHat）；flutter doctor Android 工具链 √。

## 2026-09-01（晚）— M-A0 设备 Token + Flutter 骨架（手机 App 里程碑第一步）

按 `APP-DESIGN §3` 契约实现并冻结（Flutter 阶段 1 的服务端前置）：
- **Token 三端点**：`POST /api/auth/token`（会话换设备 Token，明文仅返回一次）、
  `GET /api/auth/devices`（设备列表 + current 标记）、`DELETE /api/auth/devices/{id}`
  （吊销，审计留痕）
- **Bearer 中间件**：`require_user` 先 Cookie 后 `Authorization: Bearer`，App 全
  业务端点免改造直通；90 天滑动过期（续期节流 24h）、last_seen 节流 1h、停用
  账号即刻失效；审计中间件与 `/api/auth/me` 同等识别 Bearer 操作者
- 新表 `device_tokens`（双引擎）；单账号 20 台上限、签发限速 20 次/时
- 新增测试 4 项（tests/test_devicetoken.py），全量 **83 项 pytest 绿**
- 契约要点（App 侧实现依据）：401 → 清 Token → 登录页；TOTP 账号先走
  `/api/login/totp` 拿会话再换 Token

**Flutter 骨架（`mobile/`，M-A0 出口标准"App 骨架可登录"）**：
- Flutter 3.47.1 stable（`C:\flutter\flutter`，未入 PATH）；项目经
  `C:\lan-share\mobile`（ASCII junction）操作——**Dart 分析器 LSP 在含中文的
  路径上会崩**（JSON 消息多字节截断），中文路径下只能用 `dart analyze`
- 已实现：登录页（服务器地址管理 + 账号密码 + TOTP 按需动态码）→ 会话换
  设备 Token（Cookie 用完即弃）→ SecureStore 存储 → Dio Bearer 拦截器 +
  401 清 Token 回登录页；主页显示登录身份
- pub 依赖记录（沿 §6 原则）：`dio`（网络/拦截器/进度取消，§1 选型）、
  `flutter_riverpod`（状态管理，§1 选型）、`flutter_secure_storage`（Token
  Keystore/Keychain 加密，官方插件）、`shared_preferences`（非敏感设置）
- Dart 侧 3 项单测（Cookie 头解析起步）；`dart analyze` 零问题
- ⚠️ 待办（上真机前）：Android SDK/许可未装（无法出 APK）；自签证书 SPKI
  pinning 未接（§5，Android 默认不信任自签）；设备名暂手填（M-A1 换
  device_info_plus）

**同批杂项**：视角预览退出按钮配色修复（白底白字不可读）；窄屏成员行操作
按钮溢出裁切修复（flex-wrap）；备份计划任务改**每日 23:30** + 错过开机补跑
（机器 2 点自动关机，原 03:30 从不触发）；`.gitignore` 的 `data/` 规则锚定
根目录（此前误伤 mobile/lib/data，App 数据层源码已补入库）。

## 2026-09-01 — 四小项批次（挂账清单清空）

① **HEIC/HEIF 缩略图与预览**：iPhone「高效」照片格式全链路支持——缩略图/
中图档/照片时间线/EXIF 元数据（`ensure_heif_support()` 注册解码器，依赖缺失
静默降级）；顺带修复存量问题：缩放前 EXIF 方向矫正（iPhone 照片旋转不再横躺，
对新文件生效）。**依赖记录（PLAN §6 原则）**：新增 `pillow-heif`——纯 wheel
无系统级依赖，Pillow 官方生态的 HEIF 插件，解码 HEIC 的唯一可行方案；缺失
时仅损失 HEIC 预览，不影响其它格式。
② **WebDAV 独立密码**：与登录密码隔离的挂载凭据。设置后 DAV 入口只认独立密码
（登录密码对 /dav/ 无效），可单独轮换不碰主密码；自助设置/清除（需当前密码
确认、失败计入防爆破）+ admin 重置/清除（不动网页会话）。users 表加列
`webdav_password_hash`，三引擎幂等迁移（PG `ADD COLUMN IF NOT EXISTS` /
SQLite 补列 / 全量 schema）。
③ **事件推送（通知 bot）**：管理面板配置 GET 模板 Webhook（`{title}/{body}`
占位符 URL 编码替换），定向分享、新成员加入自动推站外；有界线程池（2 worker）
发送、失败只记审计；「发送测试」按钮同步回报结果。GET 模板格式通吃 ntfy /
Bark / Server酱 / Telegram。
④ **admin 成员视角预览**：成员列表「👁 视角」按钮，文件/相册/分享/照片元数据
四个只读接口以成员身份解析可见性（X-As-User 头，成员传头无效，命中记审计）；
顶栏横幅常驻提示 + 一键退出。用于核对授权矩阵的"能看到什么"。

新增测试 9 项（tests/test_extras.py），全量 79 项 pytest 绿；conftest 补
照片扫描 60s 节流的重置（白盒内存态，消除跨测试残留）。审查修复：webhook
线程池有界化、测试端点改线程池执行、preview 记审计、photo_meta 视角一致性。

## 四日总览（2026-08-29 ~ 09-01）

**第一天（08-29）— v3.5 四大件 + 数据库上生产**：
① 定向分享 + 站内通知；② 共享相册升级（月份/拍摄者双维度 + 精选专辑）；
③ TOTP 两步验证（可选启用）；④ **PostgreSQL 16 切换生产库**（317 行迁移，
SQLite 转测试）；每日 pg_dump 备份落地（后改为按日期命名、保留 2 天）。

**第二天（08-30）— 预览器 + 通知动态体系 + 手机适配**：
① **统一预览器**（图片缩放平移/文本/Markdown/CSV/docx/音频连播/视频倍速
画中画/全屏/信息面板 EXIF/胶片条/相邻导航/键盘手势，PC 与手机双端独立
配置）；② **通知与动态体系**（🔔 双 Tab：动态+通知、家庭动态流独立页
`/activity` 全员可见、审计日志补记操作者用户名、红点体系：铃铛圆点 +
Tab 红点 + 首次使用静默水位）；③ 三轮代码审查 + 对抗性实弹（全部修复）；
④ 备份保留策略、管理面板去冗余。

**第三天（08-31）— 大文件上传优化 + UI 重做 + 入口安全加固**：
① 大文件自适应分片（≤2GB→5MB/≤5GB→16MB/≤10GB→32MB/更大→64MB）+ 并发 3 路
+ 公网 2/5/10GB 弹窗提醒；② **上传前端全面重做**（队列组件化/平滑 XHR 进度/
取消重试/同名覆盖进回收站/拖拽+粘贴上传）；③ **暂停/恢复 + 跨刷新断点续传**
（localStorage 存会话号）+ 速度曲线 sparkline + 系统完成通知；
④ **多图预览布局修复**（grid item 被胶片条 min-content 顶破）+ 触屏居中
+ 导航栏进度条误伤恢复；⑤ **公网防护加固**（防爆破按入口分账、限速分桶、
healthz 公网 404——根治公网远程锁死全家的 DoS 攻击面）；⑥ 返回按钮改
回主页。

**第三天（08-31）— 大文件上传优化 + UI 重做 + 入口安全加固**：
① 大文件自适应分片（≤2GB→5MB/≤5GB→16MB/≤10GB→32MB/更大→64MB）+ 并发 3 路
+ 公网 2/5/10GB 弹窗提醒；② **上传前端全面重做**（队列组件化/平滑 XHR 进度/
取消重试/同名覆盖进回收站/拖拽+粘贴上传）；③ **暂停/恢复 + 跨刷新断点续传**
（localStorage 存会话号）+ 速度曲线 sparkline + 系统完成通知；
④ **多图预览布局修复**（grid item 被胶片条 min-content 顶破）+ 触屏居中
+ 导航栏进度条误伤恢复；⑤ **公网防护加固**（防爆破按入口分账、限速分桶、
healthz 公网 404——根治公网远程锁死全家的 DoS 攻击面）；⑥ 返回按钮改
回主页。

**第四天（09-01 凌晨）— 手机 PWA + 移动 App 规划定稿**：
① **PWA 阶段 0 上线**（manifest/Service Worker/图标，"添加到主屏幕"即全屏
独立应用）；② **移动 App 需求规格 + 技术设计方案成文**（Flutter 跨端选型
定稿：未来 iPhone 规划 + 上架路径；自动备份默认仅照片、视频开关）；
③ 远景规划入档 PLAN-v3 §7（CF Tunnel/域名试点制、App 三阶段、数据安全线等盘）。

逐条明细见下方按日条目。

## 2026-09-01（凌晨）— 手机 PWA 阶段 0 + 移动 App 规划定稿

- **PWA 上线**：manifest + Service Worker + 渐变 L 图标三尺寸；浏览器
  "添加到主屏幕"即得全屏独立应用（真机小米桌面已验证）。SW 策略保守：
  带哈希的 /assets//static/ 缓存优先、导航网络优先断网兜底、API/上传/
  下载绝不缓存；仅安全上下文注册，失败静默降级普通网页
- main.py SPA 回退直供 dist 内真实文件（MIME 正确 + 路径穿越守卫）；
  manifest/sw/icons 免登录可达（系统拉取不带会话）
- **移动 App 需求规格 + 技术设计方案成文**（docs/DESIGN-v3/APP-*.md）：
  Flutter 跨端选型定稿（推翻初版 Kotlin：未来 iPhone 规划 + 上架路径）；
  FR-1~9 带 P0-P2 分级与验收点；自动备份默认仅照片、视频做开关（用户拍板）；
  后台执行风险解法 = 原生薄层管"何时醒"、Dart 管"怎么传"；自签证书
  SPKI pinning 信任策略；后端前置 = /api/auth/token 设备 Token 三端点（M-A0）
- PLAN-v3 §7 远景规划入档：CF Tunnel/自有域名试点制、App 三阶段、数据安全线等盘

## 2026-08-31（深夜）— 导航修复 / 滚动条主题化 / 清洗缺口（对抗性审查二批）

- 🐛 **私人空间无法返回主页**：进入私人空间后 path 为 `private/<用户>`，
  返回按钮算出的父级 `private` 是自指别名（服务端又映射回本人私人根），
  原地打转。修：父级为 private 时回主页；面包屑按逻辑层级折叠为
  🏠 / 🔒 私人空间 / 子目录（不露技术路径段）
- 🐛 **手机列表模式长文件名按钮出屏**：`.frow` 作为 grid item 默认
  min-width:auto，被 nowrap 长名的 min-content 顶到 849px（视口 360），
  ⬇🔗✏️⋯ 整排出屏。修：`.flist` minmax(0,1fr) + `.frow` min-width:0，
  名字省略号生效（849→303px 实测）
- 操作按钮常显：桌面端卡片/列表行按钮去掉"悬浮才显示"，与触屏统一（用户偏好）
- **滚动条主题化**（三轮迭代定稿）：深色主题自定义（淡靛蓝拇指 0.62 +
  深于背景的藏青轨道，可见且融入主题）；浅色保持浏览器原生不动。
  踩坑记录：Chrome/Edge 121+ 的 fluent 悬浮滚动条无视 webkit 伪元素
  自定义、部分跟随 OS 主题（浅色系统=白条），只有标准属性
  `scrollbar-color` 能给它上色；transparent 轨道在 fluent 下可能回落
  系统白。最终按主题作用域双机制并存
- 缩略图懒加载体验：加载中微光占位 + 完成淡入（reduced-motion 静态）、
  error 自动重试一次。实测 253 张文件夹 0 失败——"图标不显示"实为
  lazy 占位观感 + 冷缓存生成时延
- 🔴 **对抗性实弹发现：特殊字符文件名 500**——`clean_filename` 漏洗
  Windows 非法字符 `? * " < > |`（只洗了 / \ : 与控制符），手机端
  （Linux FS 允许）传来的此类文件名落盘即 OSError。补全清洗 +
  回归测试；同类缺口一并堵上：WebDAV MKCOL/PUT/MOVE/COPY 路径入口
  逐段清洗（与界面 mkdir/rename 同规则）
- 🟠 分块 XHR 加 120s 超时（半开连接不再永久卡死并发 worker）；
  🟠 thumbOf/download 的 encodeURI 不编码 # ? %——改逐段
  encodeURIComponent；🟢 sparkline 配色接主题变量；🟢 公网提醒弹窗
  硬编码 LAN IP 纠正（.11→.12）
- 门禁 70 项 pytest 全绿

## 2026-08-31（深夜）— 返回按钮语义定稿

- 🟢 文件页"← 返回"保持**返回上一级**语义（曾按反馈临时改为"返回主页"，
  用户最终拍板恢复上一级——与常见文件管理器一致，回主页走面包屑 🏠）

## 2026-08-31（深夜）— 公网防护审计 + 入口隔离加固

- **审计结论**：认证/防爆破/限速/令牌熵/安全头/路径安全均在位（及格偏上）；
  两处 🟠 弱点——① 锁定按用户名全局计数，公网攻击者 5 次错密即可把全家
  （含局域网）锁在门外（远程 DoS）；② FRP TCP 隧道使公网流量 IP 恒为
  127.0.0.1，公网用户共享限速桶、审计无法溯源公网来源
- **加固（入口隔离）**：新增 `is_public_entry()`（Host 与管理面板公网地址
  netloc 比对，60s 缓存 + 改地址即时失效）。防爆破按入口分账（公网侧失败
  只锁公网侧，LAN 家人登录永不因公网攻击被锁；LAN 登录成功也不解除公网
  锁定）；登录限速按入口分桶；`/healthz` 公网入口 404（磁盘水位不外泄）。
  登录 API + TOTP + WebDAV 三处全部接入
- **结构性限制记录入 SECURITY.md**：自签证书（樱花自动证书亦为私有 CA，
  访客需点告警）；TCP 隧道拿不到访客真实 IP
- 新增 test_public_guard.py 4 项（共 69 绿）；真实 FRP 隧道实测：LAN
  healthz 200 / 公网 404
- 修测试隔离疏漏：session 级共享 client 的 cookie 罐被成功登录污染（新
  测试改用独立子客户端做成功登录断言）

## 2026-08-31（深夜）— 多图预览布局撑破修复（用户实测复现）

- 🐛 "照片/同学&生活&学校"（253 张）预览：图片整体偏右不居中 + 只有左箭头
  没有右箭头。根因：`.fv-shell` 是 grid item 且 `min-width:auto`（默认等于
  min-content），被窗口化胶片条的最小内容宽度（约 1820px）顶破视口——
  stage 跟着变 1820 宽，图片中心=910 偏右，右箭头定位在 x=1764 屏幕外
- 修复：`.fv-shell`/`.fv-strip` 加 `min-width: 0`，胶片条超宽部分内部横向
  滚动。实测 1/253 与 2/253 均图片居中（640=640）、左右箭头齐全

## 2026-08-31（深夜）— 预览器触屏居中修复

- 🐛 图片预览"不居中"真因：预览舞台只绑了 `touchend` 没绑 `touchcancel`——
  手机上手势被系统打断（通知下拉/来电/手掌拒识）时下拉偏移 `pullY` 停在
  中间值，图片**持续偏下且变暗**，且 `resetZoom` 不清 `pullY`，切图/重开
  也不恢复。补 `@touchcancel` + resetZoom 清零
- 桌面/手机/放大态实测均为像素级居中（图中心=舞台中心），fit/缩放/平移
  边界钳制逻辑复核无误

## 2026-08-31（深夜）— 暂停/续传 + 速度曲线 + 系统通知

- **暂停/恢复**：队列行级 ❚❚/▶ 按钮；暂停=中止在途传输但保留服务端会话，
  恢复从已收分块继续；暂停当前项即整队停（后续保持排队）；琥珀色暂停态
- **跨刷新断点续传**：会话号持久化 localStorage（key=目录|文件名|大小|mtime，
  TTL 3 天与服务端一致）；刷新后重新选择同一文件自动从已传位置继续；
  队列空时显示"未传完 N 个"提示条（列出文件名+大小，可一键清除记录）；
  完成/取消自动清持久化，会话失效自动回落新会话
- **速度曲线 sparkline**：引擎每 500ms 聚合采样（滚动 60s 窗口），队列头部
  内联 SVG 曲线 + 当前速度数字（无图表库依赖）
- **系统级完成通知**：首次上传时（用户手势内）申请 Notification 权限；
  批次完成时若页面在后台或总耗时 >15s 发系统通知（含失败计数）
- 🐛 **修复分片上传中断健壮性缺陷**（暂停功能暴露的存量问题）：客户端中止
  在途分片会在服务端留下截断的半截 part 文件，续传按"文件存在=完整"误信，
  finish 报 Size mismatch。现改为 `_tmp_*` 临时文件原子落盘 + 收完后校验
  分片字节数（不符即拒并清理），中断只产生不参与续传的临时文件
- 新增 1 项测试（共 65 绿）；浏览器实测：暂停 26%→恢复→落盘（审计 upload/
  会话归零/chunks 清空）、暂停 21%→刷新→提示条→重选文件 38% 续传→落盘、
  sparkline 119.8 MB/s 实速渲染、通知权限 granted

## 2026-08-31（晚）— 上传前端全面重做

- **上传队列组件化**（`uploader.ts` 引擎 + `UploadQueue.vue`，主页/直传页共用）：
  汇总头（N 个 · 总进度 % + 整体进度条 + 全部取消 ⏹ / 清空记录 ✕ / 点击折叠，
  折叠状态 localStorage 记住）；每行文件类型图标 + 名称 + 百分比 + 分条进度 +
  状态（上传中条纹流动动画 / 失败红 / 已取消灰 + 逐行取消 ✕ / 重试 ↻；
  完成行 2.5s 自动消失，失败行保留待重试）
- **进度平滑化**：分块发送 fetch→XHR `upload.onprogress`，逐字节回报（fetch 不支持
  上传进度）——64MB 大分片在公网慢速链路上进度条不再长时间停滞，速度/剩余实时准确
- **取消上传**：AbortController 贯穿（排队即取消 / 传输中止断 XHR），全部取消一键批量
- **同名覆盖保护（前后端）**：前端对照当前目录列表，同名先弹窗列明"将覆盖（旧版本
  自动移入回收站）"；后端补齐三条上传路径（分片 finish/秒传/单请求）覆盖时旧版本
  进回收站——此前 `os.replace`/`unlink`/就地写会直接丢失旧版，违反 SECURITY.md
  "所有覆盖路径统一进回收站"的既定声明（真实缺口，已修复 + 2 项测试）
- **粘贴上传**：Ctrl+V 直接传文件/截图（输入框聚焦时不抢）；截图 `image.png`
  自动改名 `截图-YYYYMMDD-HHmmss.png`
- **全屏拖拽上传**：拖文件入窗口出现覆盖层"松开上传到当前目录"，drop 即传
- **上传中关页拦截**：beforeunload（队列进行中才触发）
- HomeView/UpView 旧内联队列移除；`move_to_trash` 下沉 filetools 三处共用
- 新增 2 项测试（共 64 绿）；浏览器实测：拖拽批量入队/取消/重试/覆盖弹窗/
  粘贴命名/折叠全链路 + 截图证据

## 2026-08-31 — 大文件上传优化（v3.6）：2/5/10GB 档 + 公网提醒

- **自适应分片**：按文件大小自动加大分片（≤2GB→5MB、≤5GB→16MB、≤10GB→32MB、
  更大→64MB），`chunk_size_for()` 纯函数供 init/state/chunk/finish 四处一致，
  断点续传不受影响。10GB 文件从 2048 个请求降到 320 个
- **分片路径硬上限 64GB**（此前无上限）；**WebDAV PUT 上限 2GB→20GB**（流式写盘，
  电脑互传挂盘可拖大文件）；单请求 `/api/upload` 维持 2GB 不变
- **≥256MB 并发 3 路上传**（worker 池），减少高延迟公网链路空转；小文件保持顺序
- **公网上传大文件提醒**：非私网地址访问时，≥2GB 弹窗提示耗时（按上行 30Mbps
  估算）并建议回局域网，2/5/10GB 三档措辞递进；局域网不提醒。Vue 弹窗（遵守
  全站禁原生弹窗约定），取消则跳过该文件继续下一个
- **上传进度显示实时速度与剩余时间**（滑窗 3s 采样，每 400ms 刷新）；收尾阶段
  显示"合并校验…"（服务端重组+SHA256 大文件需数秒到数十秒）
- 全局对话框支持 `okText` 自定义确认按钮文案（补齐 confirmDlg 既有参数）
- 测试钩子：`?net=public|lan` 强制网络类型、`?warnmb=N` 覆盖提醒阈值（MB）
- 新增 tests/test_largefile.py 7 项（共 62 绿）；真实 2.5GB 文件 API 级 E2E
  通过（160×16MB 分块+SHA256 校验落盘）；浏览器实测弹窗/取消/继续/LAN 直传

## 2026-08-30（深夜）— 红点体系：动态/通知未读状态

- 🔔 面板 Tab 调序：**动态在左（默认）、通知在右**
- 铃铛红点双态语义：有未读通知 → 数字徽章；仅新动态 → 小圆点
- Tab 级红点：通知（未读数）/ 动态（新内容）；"看到即清"——打开面板落
  动态即刷新水位
- 首次使用（无水位）静默记录最新时间戳，历史动态不触发红点
- 新动态探测并入 60s 轮询（`activity?limit=1` 轻量取最新时间戳）

## 2026-08-30（深夜）— 动态流真实用户名 + 独立动态页 /activity

- **动态显示"谁"了**（M4 挂账补齐）：请求中间件把登录用户名注入
  contextvar，`audit()` 全部 30+ 调用点自动带上——新记录都有真实用户名；
  存量行按路径反推回填（`incoming/<u>/`、`private/<u>/` 开头，启动时幂等执行）
- **诚实标注**：无法归因的历史行不再笼统显示"有人"——公网链接下载显示
  "链接访客"，其余早期操作显示"早期记录"
- **动态流独立页 `/activity`（全员可访问）**：完整列表 + 聚合徽章 + 相对
  时间 + 每条"去查看 →"直达对应目录/页面；支持 `/activity?ts=` 定位高亮
  （通知面板动态 Tab 点击即跳此页）；admin 可在此清空动态
- 管理面板"动态"Tab 改为入口卡（跳转 /activity + 清空动态）；用户菜单
  新增"家庭动态"入口（全员）；通知动态 Tab 底部"查看全部动态 →"
- 修复：动态行时间/按钮被全局手机规则 `.frow .meta{display:none}` 误伤隐藏
- 管理面板移除"动态"Tab（动态页已全员独立，避免双入口冗余；清空动态在动态页）

## 2026-08-30（夜）— 三轮审查 + UI 走查（动态 Tab / 铃铛 / 手机适配批次）

- 🟠 通知 Tab 重开（切换回来）不再保留未读——与首次打开一致自动清除
- 🟠 对抗性实弹：activity 极端 limit（负数/超大→钳制、非数字→422）、专辑标题
  存储型 XSS（前端转义验证）、notifications/read 幂等重放——均符合预期
- 🟢 动态跳转 `?dir=` 空参不再出现（根目录省略 query）
- 🟢 定向分享动态标题带收件人（"XX 定向分享了 Y → ZZ"）
- 🟢 UI：手机端分享卡撤销按钮改为红色文字钮（原 ✕ 图标 flex 换行后孤行）；
  照片页长用户名筛选 chip 截断；预览信息面板提示文案对比度提升
- 清理未使用 import；pytest 55 项全绿

## 2026-08-30（晚 II）— 预览器手机端适配（与 PC 配置隔离）

- **配置隔离**：响应式 `isMobile`（matchMedia 640px）+ CSS 媒体查询双轨驱动，
  桌面端 UI/按钮/布局零改动
- **顶栏手机精简**：仅 ℹ ⬇ ✕ 三个 40px 大按钮（缩放/旋转/1:1/全屏交给手势
  与信息面板），文件名不再被挤没；iPhone 安全区（safe-area-inset）补齐
- **底部大按钮操作条**（手机）：⬇ 下载 / 🔗 分享 / ℹ 信息，拇指友好
- **信息面板手机全宽** + 操作按钮单列大按钮；图片新增 ↻ 旋转入口
- **手势修复与升级**：双指捏合不再与单指拖动互相干扰；`touch-action: none`
  防浏览器页面级缩放抢手势；**下滑关闭跟手反馈**（图随手下移渐隐，未过
  阈值弹回）
- 手机端隐藏左右箭头与胶片条（滑动已覆盖切换）；Markdown/docx 排版收窄

## 2026-08-30（晚）— 通知面板双 Tab：通知 / 家庭动态

- 🔔 面板改为「通知 / 动态」双标签：通知仍是个人收件箱（定向分享）；
  **动态 Tab 让家庭动态流首次对全员可见**（此前只有管理面板里 admin 能看）
- **动态流聚合**：同人同类动作 10 分钟窗口合并一条（"XX 上传了 8 项"），
  后端 `/api/activity` 直接聚合（count 字段）；share_target（定向分享）入流
- **相对时间**（刚刚 / N 分钟前 / N 小时前 / 日期回退）；
  **新动态蓝点**（localStorage 水位，打开动态 Tab 即清）
- **点击动态跳转**：上传/新建/移动/重命名 → 打开对应目录（HomeView 支持
  `?dir=` 深链）；删除/恢复 → 回收站；分享类 → 分享管理；成员加入 →
  管理面板（仅 admin 可点）

## 2026-08-30 — 预览/v3.5 全面代码审查（三轮，含对抗性）

- 🟠 **过期响应竞态**：预览器快速切文件时，旧文件的文本/docx/meta 响应可能
  后到并覆盖当前内容——三处 fetch 均加 `item === 当前项` 守卫丢弃过期响应
- 🟠 **图片拖拽卡死**：指针移出舞台后松开，dragging 状态不释放（图粘在鼠标上）
  ——`setPointerCapture` 修复；平移时实时钳制边界
- 🟠 **`?w=` 缓存洪水**（对抗性发现）：任意整数 w 都会生成独立缩略图缓存文件，
  登录用户可写满磁盘——宽度量化到十档阶梯（320~3840），实测 50 个随机 w
  只产生有界文件数
- 🟠 **`.thumbs` 慢性磁盘泄漏**（对抗性发现）：缓存从不清理，w=1280 大图档
  放大泄漏速度——定时清理回收 30 天未使用的档位
- 🟢 缩略图生成临时文件唯一命名（并发请求不互相覆盖，含旧 320px 路径）
- 🟢 EXIF 相机字段 bytes 解码；⛶ 全屏按钮激活态；拖拽中实时钳制
- 对抗性实弹验证：photo/meta 路径穿越 Forbidden、非图片 404、albums member
  403、通知未登录 401/按人隔离、定向分享 target 不存在 404、w 量化一致
- 测试 55 项全绿；CSP/取舍记录更新至 SECURITY.md（blob:、docx-preview 渲染面）

## 2026-08-30 — 预览精修批次（图片尺寸处理）

- **缩放精修**：双击/滚轮缩放以光标为中心（看细节不再跑偏）；放大后平移
  边界钳制（图边缘不出舞台）；操作时底部显示缩放倍率气泡（~1s 淡出）
- **档位策略**：视口物理像素（宽 × DPR）超过 1280 的大屏直接加载原图，
  小屏/手机保持 `?w=1280` 中图档；图片加载失败显示 ↻ 重试卡片
- **⛶ 全屏模式**：Fullscreen API 沉浸看图（自动隐藏顶栏/胶片条），
  空白点击/Esc 先退全屏再退预览
- **EXIF 元数据**：新接口 `GET /api/photo/meta`（原始尺寸 + 拍摄设备），
  预览信息面板展示"原始尺寸 W × H / 拍摄设备"（无 EXIF 自动隐藏）
- **resize 重适配**：窗口尺寸变化时实时钳制平移边界
- 测试 54 → **55 项**（photo/meta 接口）

## 2026-08-29（深夜 II）— 全新统一文件预览器（FileViewer）

文件页与照片页的旧灯箱合并为一个预览器组件，观感与能力全面重做：

- **沉浸外壳**：近黑磨砂遮罩 + 弹出动效；顶栏文件名 + 大小/格式徽章 +
  操作区（缩放/旋转/信息/下载/分享/关闭）；尊重 reduced-motion
- **空白点击退出**：点预览区空白处先收信息面板、再点即退出预览
  （信息面板另有 ✕ 收起按钮）；点在图片/正文/控件上不误触
- **相邻导航**：←/→ 键与屏幕箭头在同目录（或照片流）内连续翻页，计数
  "3/8"；照片页预览可跨已加载照片滑动
- **键盘**：Esc 关闭、←/→ 切换、空格播放/暂停、i 信息面板、+/− 缩放
- **触屏手势**：单指左右滑切换、下滑关闭、双指捏合缩放（图片）
- **图片**：滚轮/按钮缩放（1–8x）、拖拽平移、双击放大、旋转；默认加载
  `?w=1280` 中图档（新后端能力，Pillow 缩放缓存于 .thumbs/），放大后自动
  换原图——公网/移动端省流量
- **信息面板**（i 键）：路径/大小/时间/上传者 + 预览内直接下载/分享/
  重命名/删除（操作后刷新列表）
- **文本/代码**：语法高亮（highlight.js 按需 chunk）+ 大文件截断提示；
  **Markdown** 排版渲染（marked + DOMPurify 消毒）；**CSV** 表格化
- **docx**：docx-preview 前端渲染（按需 chunk），Word 文档开箱可读
- **音频**：自定义播放条（播放/暂停/拖动/计时），目录内音频自动连播
- **视频**：0.5–2x 倍速、画中画（⧉）
- **胶片条**（桌面端）：底部缩略图横滑条，点选跳转
- **不支持格式**：美化卡片（大图标 + 格式说明 + 一键下载）
- 新依赖 highlight.js/marked/dompurify/docx-preview 均按需 chunk 懒加载
  （首屏仍 ~206KB/gzip 73KB）；CSP 不放宽

## 2026-08-29（深夜）— v3.5：定向分享 / 共享相册 / TOTP / PostgreSQL

- **定向分享 + 站内通知**：
  - `POST /api/share` 新增 `target` 表单字段（成员用户名）——定向分享仅目标
    成员（与 admin）登录后可取，免提取码；对无关者 404 不泄露存在性
  - 新增 `notifications` 表与 `app/api/notify.py`：`GET /api/notifications`
    （未读数+最近 50 条）、`POST /api/notifications/read`（全部已读）；
    定向分享自动给收件人发通知；定时清理已读 30 天后的通知
  - 前端：顶栏通知铃铛（未读徽章/点击跳转/自动已读）、分享弹窗新增
    "分享给指定成员"下拉（新接口 `GET /api/members`）、分享管理页 🎯 定向
    徽章、分享落地页对定向分享给出"去登录"引导
- **共享相册升级**：照片时间线新增月份/拍摄者双维度——
  - `GET /api/photos` 条目带 `month`/`uploader`（按路径归属推导：
    private/<u>、incoming/<u> 归本人，其余共享），支持 `month=`/`uploader=` 过滤
  - 新增 `GET /api/photos/facets`（月份/拍摄者分布）、精选专辑
    `GET /api/albums` + `POST/DELETE /api/admin/albums`（settings 存 JSON）
  - 前端照片页：月份/拍摄者筛选 chips、精选专辑卡片行、admin ⭐ 一键收藏月份
- **TOTP 两步验证**（可选启用，pyotp）：
  - users 表加 totp_secret/totp_enabled；`POST /api/auth/totp/setup|enable|disable`
  - 登录改为两步：`/api/login` 密码正确且已启用 TOTP 时返回 `{"totp_required": true}`
    不发会话；`/api/login/totp` 用密码+动态码换会话（验证码错误计入按账号防爆破）
  - admin 重置成员密码同步清除其 TOTP（手机丢失恢复通道）；关闭 TOTP 需原密码
  - 前端：登录页动态码步骤、账号设置弹窗 TOTP 管理（二维码+密钥录入）
  - ⚠️ 取舍：WebDAV Basic 维持仅密码（客户端不支持动态码），记录于 SECURITY.md
- **PostgreSQL 切换（生产库）**：
  - `app/db.py` 双引擎化：`DATABASE_URL` 指向 PG 时走 psycopg 适配层
    （`?`→`%s` 占位符转换、dict 行、autocommit）；SQLite 保持原路径，测试默认
  - `scripts/migrate_sqlite_to_pg.py`：建表+全量重灌+序列拨号
  - 全库 SQL 引擎中立化：`COLLATE NOCASE`→`LOWER()=LOWER()`、
    `lastrowid`→`RETURNING id`、`rollback`→补偿 DELETE
- 新增依赖：`pyotp`（TOTP）、`psycopg[binary]`（PG 驱动）；测试 49→**54 项**
  （新增 test_v35.py：定向分享全流程/相册维度/精选专辑/TOTP 全流程/大小写查重）

## 2026-08-30（晚）— 分享复制拆分 + 失效页友好化

- 分享管理页复制拆分为三个独立按钮：📋 复制链接 / 🌐 公网链接 /
  🔑 复制提取码（取消组合复制；提取码徽章点击仍可单独复制）
- 分享落地页失效状态友好化：`Share not found` 英文裸报错改为图标化
  卡片，区分 🔍 不存在或已被撤销 / ⏰ 已过期 + 指引文案
  （修复大小写匹配：接口返回小写 not found）

## 2026-08-30 — 前端审查（第六、七轮）+ 弹窗 Vue 化

- **全站弹窗 Vue 化**：15 处原生 confirm/prompt 清零——ui.ts 新增
  Promise 化 confirmDlg/promptDlg（危险操作红钮/输入回显/回车确认/
  点遮罩取消），App 根级渲染；浏览器实测新建文件夹全流程 ✓
- 第六轮 UI 审查（桌面 1280 + 手机 390 双视口逐页走查）：
  - 列表视图紧凑化（短日期/『目录』标签/三段对齐）
  - 私人空间专属视觉（紫渐变图标砖 + 🔒，网格/列表一致）
  - 根目录隐藏孤零零 🏠 面包屑
  - 手机文件行重构为单行（图标+名称+日期+操作），分享/管理页保持
    两行化（按 .file-row 区分作用域）
- 第五轮全面代码审查：🔴 私人空间物理路径二次拼接修复（虚拟/物理
  双形态统一解析）+ 6 断言回归；全库扫描通过（XSS/路径拼接/危险
  函数/resolve 全覆盖）；备忘 guest 照片 N+1 小查询

## 2026-08-30 — 全面代码审查（第五轮）

全库审查：安全核心推演 + 模式扫描（XSS/路径拼接/危险函数/resolve 覆盖）。

- 🔴 **私人空间物理路径二次拼接**：目录列表返回 `private/<用户名>/x`，
  别名解析再补一次用户名 → `private/<用户名>/<用户名>/x`。私人空间
  有文件后：进子目录/删除/重命名/预览全部 404。修复：统一虚拟/物理
  双形态解析（`_own_private_rel`），新增 6 断言回归测试（含跨用户隔离）
- ✅ 扫描确认：前端无 v-html/innerHTML（Vue 转义全覆盖）；SHARE_ROOT
  直拼仅存于 storage 层与 admin 校验路径；无 eval/os.system；
  18 处文件端点全部经 resolve 权限解析
- 📝 登记备忘：guest 照片列表存在 N+1 小查询（本地 SQLite 家用无感）

## 2026-08-30 — 文档基准（a11c4bd）之后的迭代

- **分享链接管理页 /shares**：状态徽章（有效/已用完/过期）+ 下载次数进度条 +
  全部/有效/已用完筛选 + 取件记录（含来源 IP）+ 撤销 + 提取码明文显示与
  『复制链接+提取码』组合复制
- 🔴 **/api/shares 越权**（v2 遗留）：任何人可见/可撤销所有人的分享——
  迁移补 shares.owner_id，列表/撤销/取件记录按创建者隔离，admin 全见
- 🔴 **分享公开访问被误弹登录页**：401 全局跳登录未豁免公开页；
  refreshAuth 改裸 fetch + 兜底跳过 /s/ 与 /login + 守卫等路由解析
- 🔴 **下载管理器重复计数**：小米等并发 Range 分段每段计数，一次 APK
  下载被记 4 次误触上限——按票据去重（同一 tkt 只计 1 次）+ 回归测试
- **双地址分享**：管理面板可配公网访问地址（settings.public_base_url，
  已配 your-frp-domain.com:xxxxx）；分享弹窗/管理页提供 📋 局域网 / 🌐 公网 两键
- **手机直传双二维码**：📲 弹窗并排 局域网/公网(FRP) 两码；/api/public-url
  开放全员可读；/up 未登录改为页内引导登录（登录后回跳直传页）
- **成员账号管理**：管理员可直接修改成员用户名（私人目录同步改名）与
  重置密码（成员全端登出）；成员行新增 ✏️ 账号 弹窗
- **授权批量矩阵**：成员多选 × 目录多选，一次生成 N×M 条授权；目录
  下拉自动浏览共享树；按成员筛选视图；有效期（永久/1/7/30 天）与到期显示
- 修复：取件记录时间 NaN（ISO/数字双兼容）；网格卡片长文件名撑破轨道；
  触屏粘滞 hover；私人空间入口 404（自动建目录）与误删保护（根目录
  禁删/禁改名/禁移动，显示名改『🔒 私人空间』）

## 2026-08-29（深夜 III）— 交互细化批次

- 顶栏品牌（◈ LAN Share）可点击回主页
- 账号设置回归：头像菜单 → 🔑 账号设置 → 修改用户名/修改密码
  （Vue 换代时丢失的功能补齐；错误路径实测有提示）
- 磁盘徽标按真实已用百分比填充（原字段错位恒为 0）+ 存储详情弹窗恢复
  （成员看自己配额、admin 看目录构成），入口同时收进头像菜单
- 私人空间入口卡片：显示『🔒 私人空间』、可正常进入（自动建目录）、
  根目录禁止删除/重命名/移动（原会误删整个私人空间）
- 网格卡片操作按钮移到卡片底部（不再叠压图标）；触屏粘滞 hover 禁用
- 分享管理页升级：状态徽章（有效/已用完/过期）+ 下载次数进度条 +
  全部/有效/已用完筛选；提取码明文显示与『复制链接+提取码』
- 双地址分享：管理面板配置公网访问地址（your-frp-domain.com:xxxxx），分享
  弹窗/管理页提供局域网/公网两个复制按钮
- 🔴 分享公开访问被误弹登录页（401 全局跳登录误伤公开页）——
  未登录直开 /s/ 链接已实测正常取件
- 🔴 取件记录时间 NaN（ISO 字符串按数字格式化）；🔴 /api/shares 越权
  （补 owner_id 按创建者隔离）
- 手机端专项：行布局两行化（信息行+操作行，消除横向溢出）、iOS
  底部安全区、触屏操作按钮常显

## 2026-08-29（深夜 II）— 全站 Vue 切换 + 多轮对抗式审查修复

旧 HTML/JS 四页删除，`/` 即 Vue3 SPA（web/ 工程，CSP 不放宽）。

- 🔴 **分享公开访问被误弹登录页**：401 全局跳登录未豁免公开页——分享页的
  登录态探测（/api/auth/me 401）被当作会话过期。refreshAuth 改裸 fetch +
  401 兜底跳过 /s/ 与 /login + 壳层守卫等待路由解析
- 🔴 **中文文件名点击预览/分享下载必 500**：Content-Disposition 拼文件名炸
  latin-1，三处改走 starlette filename 参数（RFC5987）；含回归测试
- 🔴 **下载管理器重复计数**：小米等并发 Range 分段每次都计数，一次 APK
  下载被记 4 次误触上限。改为按票据去重（同一 tkt 只计 1 次）+ 回归测试
- 🔴 分享二维码与通用 /api/qr 全 500：拆包丢失 qrcode.image.svg 导入
- 🔴 /api/shares 越权：任何人可见/可撤销所有人的分享（v2 遗留）——
  补 shares.owner_id 并按创建者隔离
- 登录成功不跳转：抽共享 auth store（壳层状态脱节根因）
- 交互重构：⋯/右键操作菜单（替代误入多选）、页内灯箱预览（替代被弹窗
  拦截的 window.open）、多选分享按钮单项才显示、返回按钮归位按钮行
- 新页面：分享管理 /shares（状态徽章/次数进度条/筛选/取件记录/撤销/
  提取码明文显示与组合复制）；深链回退误排除 /shares 已修
- 体验：主题三选（浅色/深色/跟随系统）、头像下拉菜单（导航集成）、
  存储详情弹窗恢复（徽标百分比真实填充）、管理面板内联控件（角色下拉/
  配额弹窗/邀请任意删/动态清空）、下载次数上限美化提示、搜索按钮、
  卡片底部操作按钮、触屏粘滞 hover 禁用、手机行布局两行化、iOS 安全区
- Caddy：:80→HTTPS 跳转（裸地址进不去兜底）、:443 监听
- 测试 45 项全绿；全过程浏览器双视口（1280/390）实测

## 2026-08-29（深夜）— v3 家庭团队版首批交付（M0/M1/M2/M3/M5）

单管理员网盘 → 家庭团队网盘的第一夜。状态详情见 docs/STATUS-v3.md。

- **M0**：server.py（1799 行）拆包为 app/ 包（config/db/security/filetools/api/*），
  server.py 变兼容 shim，NSSM 入口不变；pytest 基线 + 本地门禁 check.bat
- **M3**：多用户核心——admin/member/guest 三角色、private:/shared: 空间模型、
  目录授权 grants、邀请制建号、私人空间配额、直传按用户分桶、
  search/recent/trash/photos 按身份过滤、停用账号即时踢出、跨空间移动拦截
- **M2**：Vue3 + Vite + TS 新前端首版挂 /app（登录/浏览/上传），旧页灰度并存
- **M1/M5**：/healthz、backup.bat（SQLite 在线备份 + robocopy 增量）；
  PG 未装，双引擎准备（迁移集中 _migrate_v3，幂等）
- **审查**：第一轮修复 3 项——move 源需写权、目录移动同步 grants、
  WebDAV Basic 查 is_active；残余风险记录于 STATUS-v3.md
- 测试 43 项全绿（基线 25 + 多用户权限 18）

## 2026-08-29（晚）— 全面对抗式审查（第四轮）

覆盖当日全部新增：/api/move、改名/改密、basic_auth 移除、调试账号、前端移动交互。修复：

- 🔴 改密/改用户名的**密码确认失败不计入防爆破**——会话被劫持后可在限速窗口内无限试密码。
  现在确认失败记入账号锁定（5 次锁 15 分钟），且锁定期内登录也一并 429
- 🔴 防爆破锁定 key 大小写变体可绕过——`record_login_fail`/`login_locked_remaining` 统一小写化
- 🟠 `/api/move` 重名改名的预检+`os.replace` 存在并发覆盖窗口——改为 `os.rename`（Windows 下
  目标存在必抛错）原子防覆盖；`sources` 增加 ≤500 项上限
- 🟠 改名重名检查 SQLite 默认大小写敏感，"admin"/"Admin" 可共存——`COLLATE NOCASE`
- 🟡 磁盘统计端点同步 rglob 阻塞事件循环——改 `def` 线程池执行
- 🟢 已确认无需修：`blobs.sample_path` 在源文件被移动后自愈（登记新源），硬链接关系随 rename 保留

审查确认无恙：`/files/*` forward_auth、CSRF Origin 中间件、XFF 剥离、限速分桶、分享票据一次性、
zip/缩略图路径校验、前端 `esc()` 转义全覆盖。文档同步：README/SECURITY/API/DEVELOPMENT。

## 2026-08-29（晚）— 修复磁盘用量显示

- 🐞 磁盘详情弹窗两处计算错误：分类占比误用「整个 C 盘已用」作分母导致条形图全空、
  "已用 N%"分子分母相同恒为 100%
- 弹窗重构为两段：**C 盘用量条**（已用/总容量）+ **网盘内容构成条**（各文件夹占网盘合计的
  比例），图例与构成条对齐
- 后端：`/api/disk/detail` 新增 `share_size`（网盘内容合计）；统计按 NTFS 文件索引（inode）
  **去重**——硬链接去重的同一份内容物理只计一次；嵌套隐藏目录排除
- 顶部徽标含义不变：显示 C 盘剩余可用空间

## 2026-08-29（晚）— 文件卡片 ⋯ 操作菜单 + 桌面右键

- 文件卡片右侧的 `›` 箭头替换为 **⋯ 按钮**：点击弹出与触屏长按相同的操作菜单
  （打开/下载/预览/分享/重命名/移动到…/删除），菜单贴按钮下缘弹出
- 桌面端首次拥有操作菜单入口（此前长按仅触屏可用）；**右键**也接入同一菜单
- ⋯ 按钮点击 stopPropagation，不误触发卡片默认动作（预览/下载/进入文件夹）；
  多选模式下自动隐藏（还原 `›`）；触屏设备触控区加大到 44px

## 2026-08-29（晚）— 文件移动（Web UI + API）

- 新增 `POST /api/move` 批量移动：重名自动 `(2)` 改名（不覆盖任何文件）、拒绝移入自身子目录、
  无意义移动（已在目标目录）逐项提示；同盘 rename，硬链接去重与秒传指纹不受影响；审计记 `move`
- Web UI 三种移动方式：
  - **移动到…**：右键菜单（文件/文件夹）与多选批量栏入口，弹窗目录选择器（可上下导航、
    新建文件夹），单选/批量通吃
  - **剪切/粘贴**：多选栏"剪切"→ 底部悬浮粘贴条跨目录保持 → 目标目录"粘贴到此处"；
    剪切项半透明高亮
  - **拖拽**（仅桌面指针环境）：文件卡片拖到文件夹卡片，目标高亮虚线框；与外部文件
    上传的拖放互不干扰（internalDrag 标记隔离）
- 修复过程中发现并处理：目录选择器导航时弹窗叠层（重渲染前清旧 overlay）

## 2026-08-29（晚）— 返回按钮可拖动

- 返回按钮默认位置从左下改为**右下**（right:16px / bottom:96px）
- 支持拖动：Pointer Events 实现，移动 ≥6px 视为拖动并拦截 click（不误触返回），
  边缘自动夹紧；位置存 localStorage（`backBtnPos`），刷新后恢复
- 按钮加 `touch-action:none`（触屏拖动不滚动页面）

## 2026-08-29（晚）— 调试账号

- 新建 `debug` 调试账号（权限与管理员一致，供 AI Agent 调试接口用），审计记 `debug_account_created`
- 凭据放 `docs/DEBUG-ACCOUNT.md`，该文件已加入 .gitignore 不入库；admin 账号密码未变更

## 2026-08-29（晚）— 移除公网 basic_auth + 账号设置

- 新增修改用户名：`POST /api/auth/username`（需当前密码确认）；头部 🔑 图标改为"账号设置"弹窗，
  整合改名与改密两个入口
- 改名不吊销会话（会话按 user_id 关联，所有设备登录态保持）；审计记 `username_change`（旧→新）
  与密码错误的 `username_change_fail`；用户名清洗：去首尾空白、拒绝空白/控制字符、≤64 字符
- 移除公网 basic_auth（同日晚，见下条）

## 2026-08-29（晚）— 移除公网 basic_auth

- `:8767` 公网入口不再弹浏览器 basic_auth 登录框，与 `:8765` 一样直接进 Web UI 登录页
- 两个端口配置合并为同一 Caddy 站点块（`certs/san.cnf` 不变，需重启 lan-share-caddy 生效）
- 未登录行为：页面 307 → `/login`，`/api/*` 401，`/files/*` 由 forward_auth 拦截 401；
  扫描器/爆破由应用层登录防爆破（按账号 5 次锁 15 分钟）+ 限速（登录 10/分）抵御
- 文档同步：README 架构图/入口表、SECURITY.md 网络层、PLAN.md 加修订注记

## 2026-08-29 — UI/UX 精修

- 新增 SVG favicon 与 theme-color，四页统一品牌标识
- 全站视觉升级：SVG 图标体系替换 emoji、文件类型彩色徽章（图片/视频带真实缩略图）、
  玻璃拟态顶栏/弹层、蓝紫渐变主按钮、卡片悬停微交互
- 动效体系：列表错峰入场、弹窗弹性上滑、右键菜单弹出、进度条流光、骨架屏呼吸、
  照片渐显悬停放大；尊重系统"减少动态效果"
- 修复：批量操作栏 SVG 图标失控撑爆布局（加 `.btn svg` 全局尺寸兜底）
- 修复：返回按钮箭头不居中（文字箭头换 SVG，量化验证偏移 0px）
- 修复：预览层内点分享被预览遮挡（弹窗 z-index 100→120）
- 交互：全选改为切换式（再点全不选），批量栏"取消"移到"全选"右侧
- 移动端适配：≤480px 媒体查询（头部/卡片/操作栏紧凑化）、输入框 ≥16px 防 iOS 聚焦缩放、
  viewport-fit=cover、touch-action 优化、小屏隐藏头部徽标

## 2026-08-28（晚）— 对抗性审查修复

- 🔴 XFF 伪造可绕过全部限速：后端改取最后一个 XFF 值 + Caddy 剥离入站 XFF
- 🔴 登录 `?next=` 开放重定向：排除 `//` 开头
- 🟠 分享提取码按 token 锁定（5 次/15 分钟），密码错误审计带 IP
- 🟠 WebDAV 覆盖写/MOVE/COPY 覆盖的旧目标移入回收站（不再直接丢失）
- 🟠 限速桶按类别拆分（login/share/api），authcheck 豁免
- 🟠 文件名清洗拦截 NTFS 备用数据流（冒号）、Windows 保留名、结尾点/空格
- 🟡 分享下载票据 TTL 内可复用（支持大文件续传）；escJs 修单引号注入；
  孤儿临时文件清理；SQLite 每线程连接；scrypt 移入线程池；硬链接去重原子化；
  下载计数改为发送完成后；WebDAV 假锁行为写入文档

## 2026-08-28 — v2 重构 + P1/P2/P3

### P0 安全地基
- 单管理员认证（scrypt）、30 天滑动会话（SQLite 持久化，重启不丢）
- 登录防爆破（按账号 5 次/15 分钟）、应用层限速、CSRF Origin 校验
- 分享入库（重启不丢），密码 POST body + 一次性下载票据（不再进访问日志）
- Caddy `forward_auth` 保护 `/files/*` 静态直连；`safe_path` 边界修复；QR 完整 URL

### P1 网盘核心
- 分块上传（5MB/块）+ 断点续传 + 秒传（≤100MB 客户端预检）
- NTFS 硬链接内容去重（blobs 表）；回收站（30 天，可恢复/清空）
- 图片/视频缩略图缓存；批量 zip 打包下载；重命名

### P2 分享与移动端
- 分享下载次数上限、取件记录页
- 手机直传页 `/up`（扫码打开，登录支持 `?next=` 回跳）
- 修改密码（吊销全部会话）

### P3 扩展
- WebDAV 服务器（`/dav/`，可挂载网络磁盘，Basic 认证复用防爆破）
- 照片时间线（EXIF 拍摄日期倒序，增量扫描缓存）

## 2026-08-28 — v1（初始版本）

- FastAPI + Caddy + 原生前端的局域网文件分发：目录浏览、上传、分享链接、QR、
  磁盘监控、tmp 自动清理；NSSM 服务常驻；樱花FRP 公网入口
- v1 原版备份见 `backup/v1/`（含回滚说明）
