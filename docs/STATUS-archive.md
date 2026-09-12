# LAN Share v3 交付状态归档（2026-09-09 及更早批次）

> 本文件是 `docs/STATUS-v3.md` 的历史归档（v0.21.4 及更早段落，2026-09-12
> 清理维护批切出）。完整变更史另有 CHANGELOG.md / CHANGELOG-archive；
> 本文保留的是各批次交付时的问题定位与决策细节。**内容为历史快照，
> 版本号/基线数字不代表当前状态**（现状见 STATUS-v3.md 与 HANDOVER.md）。

---

# v0.21.4（2026-09-09）：改进建议 9.9 全量修复批

《lanshare改进建议9.9》全量落地（P0+P1+P2，三端）：服务端——WebDAV PROPFIND
线程池化（大目录不再冻结全站）、DAV Basic 哑哈希等时（堵用户名时序枚举）、
start.bat 钉 hermes venv python（根治"守护用 base 环境拉起即死"）、APK sha256
按 mtime+size 缓存、_user_field 列名白名单、/api/auth/me 容忍损坏 totp_rc、
zip 打包票据上限 200、存量 mypy 三错清零（门禁恢复全绿）；Web——401 豁免补
/up、成员视角预览登出即清、大文件上传提示去硬编码地址、vite 显式
sourcemap:false；App——登录密码 try-finally 清理（失败路径不残留）、首页三路
轮询断网横幅（仅无响应类失败计入）、欢迎头插值除零守卫、SecureStore/备份引擎
静默吞错补 debugPrint（shrinkWrap 九处评估后保留）。安全收口：生产凭据两个
txt 挪出仓库（<仓库外的私有位置>\）+ .gitignore 兜底。
+6 回归测试（test_review_20260909.py）。基线 169→175。App v0.21.4+65 /
服务 v3.31。详见 CHANGELOG v0.21.4 条目。


# v0.21（2026-09-09）：改进建议 9.6 批次（五项 P0 修复 + 四项方案落地）

笔记保存副本根治（服务端 upload 端点 `on_dup=overwrite`——就地覆盖不进回收站，
默认 trash 行为不变；两个编辑器 30s+失焦自动保存）；通知推送按钮发白根治
（禁用态保渐变降饱和 + 状态文案）；首页"我的分享"直达分享管理、备份卡片整卡
可点；笔记标题"笔记·笔记"重复消除 + 编辑页"分类名 / 文件名"面包屑。
**笔记分类体系（方案 B）**：新端点 `GET /api/notes/list`（递归聚合 + 前 200 字
摘要 + 分类归属，2000 篇护栏），App 左侧可开合嵌套分类栏、卡片化列表、
置顶收藏（本地）、排序切换、拖拽/批量移动；**首页沉浸渐变欢迎头（方案 A）**：
SliverAppBar 铺满状态栏（问候+头像+三枚微数据胶囊），滚动收窄纯色工具条，
卡片拖拽迁移 SliverReorderableList；**通知管理（全做）**：broadcast_id 贯穿 +
admin_broadcasts 表（双引擎迁移）、发送记录/撤回未读/提醒未读/草稿定时
（startup 周期任务扫描）、按类型组通知偏好（写入统一出口尊重偏好）、App 通知
页分类过滤+单条已读/滑动删除+偏好面板；**备份智能对账（全部采纳）**：轮前
三步（空间预检 skip / 改名识别不重传 / 相册被删转 server_missing 待用户决定），
详情页提示卡 + 设置页「管理服务端相册」删副本；**复制服务器路径**（/api/files
与 /api/photos 条目加 abs_path，Web 右键菜单 + App 文件菜单/照片长按菜单）；
**照片查看器**：放大后拖动平移不切图（_zoomed 冻结 PageView）+ 长按菜单
（存相册/分享/复制路径/详情）；**照片点赞评论整体下线**（端点/建表/测试同步
移除，存量数据保留；基线 174→169）。服务端 v3.31。详见 CHANGELOG v0.21.0 条目。

# v0.20（2026-09-06）：九项体验批次（首页间距/相册保存/推送/授权/笔记/二维码）

首页品牌头与卡片间距 + edge-to-edge 状态栏铺满（ReorderableListView header
padding 坑根治）；「保存到相册」链路加固（DISPLAY_NAME 净化 + IS_PENDING 发布
复查 + 异常带类名透传）；管理「发送通知」广播（/api/admin/notify，站内逐人 +
webhook，全体或指定成员）；授权重构（管理只选共享一级目录、子目录继承，授权卡
续期 7/30 天/读写切换/过期红牌，Web 同步）；笔记多选批量删除；文件页新建
md/txt + 「📲 手机上传二维码」弹层（离屏 900px PNG 存相册，中心实心云朵 logo）；
照片"传了不见"根治——photos 加 added_at + sort=added 入库序（SQLite/PG 双引擎
迁移）+ 上传落盘清扫描节流；服务端二维码中心 logo 换实心品牌云朵；首页最近上传
非媒体类型色块。详见 CHANGELOG v0.20.0 条目。

# v0.19（2026-09-06）：文档预览兼容重构 + 富文本笔记

docx 带样式转 Markdown（标题/粗斜/列表/表格/内嵌图片）+ xlsx/csv 转表格 +
pptx 大纲 + rtf/doc 尽力抽取（doc_extract.dart 纯 Dart 解析）；「用其他应用
打开」兜底；富文本笔记存 .html（html_editor_enhanced，图片 base64 内嵌、Web 可
直开），新建三选一 md/txt/html。详见 CHANGELOG v0.19.0 条目。

# v0.18（2026-09-06）：首页卡片自定义 + 预览音频/文本扩面 + 笔记增强

首页卡片长按拖拽排序 + 管理卡片显隐/恢复默认（本机 prefs 持久化 + 脏数据
自愈）；AudioViewerPage 音频内播（mp3/m4a/…）+ 文本家族预览扩面（yaml…/sql）
txt 可进编辑器；笔记搜索/长按编辑重命名删除/源码渲染切换/本地草稿。详见
CHANGELOG v0.18.0 条目。

# v0.17（2026-09-06）：App 细节优化大版本

照片墙 PG 500 根因修复（`_pg_sql` 字面 `%` 翻倍全仓收口）；备份暂停开关 +
清空服务端备份密码复核（/api/auth/verify-password）+ 备份详情页重构（实时
进度条/正在上传文件名）；头像+昵称资料体系三端贯通（users 加列、.avatars/
目录、App 我的页资料编辑、Web 账号设置资料卡、全局昵称优先显示）；App 预览
大改（双击缩放/页码/视频双击快进+倍速+静音/文档预览器 md·pdf·docx·txt）；
MD 笔记新功能（选目录创建/编辑/保存）；开屏图标圆角加大；目录布局文档落库。
详见 CHANGELOG v0.17.0 条目。

# v3 实施状态（截至 2026-09-09，**v3.31 / App v0.21.3+64**）

> 本文档记录 v3 里程碑的实际交付状态。规划见 `PLAN-v3.md`，设计见 `docs/DESIGN-v3/`。
> 测试基线：**pytest 169 项全绿**（v0.21 下线照片互动 5 项；ruff 全绿，
> mypy 存量 3 项历史遗留）；Flutter App `dart analyze`
> 零问题、**144 项 Dart 单测**（v0.19 批次 +6：doc_extract 自构造 docx/xlsx/pptx
> 样本；v0.18 +1：预览路由契约；v0.16 批次 +19：回收站条目匹配/清理报告文案/安装
> 包判定/孤儿分片/类型分档；v0.15 +7：自动切换设置/地址归一/邀请码形状/信任归一）
> （v0.14 +5、v0.15 +2）（RFC 6238 官方向量 + 0.6.x 各批纯逻辑 + 0.7.0 落位计算 +
> 0.7.1 备份统计聚合 + 0.7.3 轮次状态文案）。
> 源码行数（2026-09-11 实测）：后端 `app/` ≈ 8.6K 行；前端 `web/src/` ≈ 6.5K 行（Vite+TS）；
> App `mobile/lib/` ≈ 24.4K 行（Dart）+ `mobile/android/.../kotlin/` ≈ 1.1K 行（薄桥）。

## 2026-09-06 — v0.16.3+54 资深 Reviewer 三轮审查 + 对抗审查（安全大版本）

- 服务端：invite_redeem 锁内 audit 自死锁 + 自助改名路径穿越双 P0 修复；"锁内
  raise 残留未提交写事务"全仓收口；/api/upload/init 未认证内存 DoS 根治（大
  body 白名单精确化 + chunked/缺 CL 413/411）；分享下载计数挪 prepare 原子递增 +
  提取码停存明文（存量清洗）+ 直传配额按真实占用；秒传同名目录 409；zip 磁盘
  余量预计价；App安装包写 admin-only；is_public_entry 源地址复核；WebDAV Basic
  IP 限速 + authcheck 600/min + 公网桶放大 + _login_fails 键清扫；grants TTL
  缓存 + photos SQL 下推（非 ASCII 降级）+ disk 扫描 TTL 缓存 + 异步端点同步
  DB 线程池化；PIL 解压炸弹 60MP 钳制；upload_finish 失败销会话；DAV MOVE
  同步 grants；HSTS 1 年。
- Web：邀请页双表单、上传队列泵孤儿竞态、请求序号守卫、分享下载弹窗拦截、
  确认框补齐、键盘可达、大目录截断+排序、确认框 Enter 误确认修复。
- App：服务器选择器信任链收口（entries 指纹校验/未验证标注/_applyAddress
  信任闸）；TOTP 脏 secret 入库校验；http 明文地址双闸拒绝；失败任务重入队；
  退后台轮询闸门；视频后台暂停；登出确认；并发闸持续到响应体读完；401 清
  Token 竞态；退避可即时暂停；文件页返回键上级目录。
- 质量：pytest 161 / Dart 136 / analyze 0；Caddy validate 通过；线上冒烟
  （chunked login 413、未认证 init 401、healthz 200）。

## 2026-09-06 — v0.16.2+53 全仓四维审查 2 轮 + 对抗审查（安全加固批次）

- 服务端：DAV COPY/MOVE 与图片预览 PIL 进线程池；PG 断线自愈；上传临时名
  唯一化+同名目录 409+TOCTOU 收窄；孤儿回收锚定正则（防误删合法文件名）；
  中间件/SPA/DAV 会话查询进线程池；scrypt 移出全局锁；照片扫描 N+1 消除；
  分片会话双上限；zip 单用户并发 1；activity 改 visible() 判权；Caddyfile
  隧道端口注入 X-LanShare-Entry（Host 伪造分账绕过根治）。
- App：自动切换全面 https-only（LAN 候选也收口）+ 指纹门槛全 scheme + 空
  指纹拒绝 + 缓存/哨兵 TLS-only 写入；自更新 fail-closed（sha256 缺失拒装，
  服务端自算兜底）；传输引擎响应防御补齐（chunk_size 上限/path/sid/416 重
  做）；TOTP clamp+Keystore-only+启动清旧明文副本；对话框 dispose 时序统一
  收口 GC 方案；照片墙刷新竞态修复；主页 2s 轮询 diff 跳过重建。
- 质量：analyze 0 / Dart 136 / pytest 161；Caddy validate 通过，LAN healthz
  200 / 隧道端口 healthz 404 实测。

## 2026-09-06 — v0.16.1+52 开屏圆角回退 + 启动深浅色跟随设置 + 清空服务端备份

- **开屏圆角回退**：v0.16.0 全圆盘不合口味，恢复渐变圆角方块（四角透明）。
- **启动深浅色**：MainActivity attachBaseContext 读 flutter.ui_theme_mode，
  用户固定 light/dark 时覆写 uiMode 让 values-night 资源按选择解析；
  NormalTheme 底色改 @color/launch_bg（#FFFFFF / #0B1020）。系统开屏段
  （Android 12+ 进程前绘制）只能跟随系统——平台边界，如实告知。
- **清空服务端备份**：危险区两步确认+输入用户名；整目录 DELETE +
  快照差集彻底删除（absentOk 404=absent）；执行期间关停自动备份
  （wipe_backup_paused 标记 + 登录 _recoverWipePause 兜底）；账本默认
  保留、勾选才清；在途轮次拦截；配置控件执行期锁定。
- **E2E 红屏根修**：对话框退场动画期间 dispose TextEditingController →
  `_dependents.isEmpty` 断言；对话框重构后全链路实测通过。
- 质量：analyze 0 / Dart 136 / pytest 161；真删测试号全链路 E2E。

## 2026-09-05 — v0.16.0+51 清理与本地文件管理批次（纯 App）

- **彻底删除（物理删除）**：照片墙多选/查看器三选入口；purgeServerFiles
  两段式（进回收站→快照差集匹配→DELETE /api/trash/{id}）+ 快照防跨会话
  误删/同秒 tie-break 取大 id/3 连败熔断/会话漂移中止四重防御；查看器
  二次确认。普通删除语义不变。
- **传输页重做**：汇总条（进行中/总速度/暂停/失败聚合）、上传目标目录
  副标题、"本地文件"视图（列表/打开/存相册/分享/删除/清空全部/清理残留
  分片；.part 隐藏且失败任务分片受保护 isOrphanPart）。
- **更新安装包清运**：独立 Downloads/updates 子目录 + purgeInstallers
  （登录后/关于页/下载前触发，minAge 1h 保护在装包）。
- **清理应用缓存**：我的页通用组新条目（temp+cache 去重统计与清空，
  清理后两项统计同步刷新）；下载文件/账本/Token 不受影响。
- **开屏图标圆形化**：make-splash.py 圆形裁切（2x 超采样），四角真透明。
- 质量：dart analyze 0 / Dart 测试 136 / pytest 161；模拟器 E2E 实测
  （物理删除三重验证、本地文件删除沙盒验证、清缓存 391.6KB→0B 且登录态
  保持）；2 轮审查+对抗审查 P1×3/P2×4 闭环；发版三件套 0.16.0/51。

## 2026-09-05 — v0.15.1+49 品牌开屏；v0.15.2+50 自动切换实测修复 + 邀请码注册

- v0.15.1+49：flutter_native_splash 品牌开屏（旧 logo=Android 12+ 系统开屏
  图标缓存，与 App 侧无关）。
- v0.15.2+50：切公网死锁根修（活动传输闸门改"快照暂停+切回定向恢复"）、
  探测提速（2s 超时/并行/5s 快速复核/回前台即探/启动首探）、公网端点预
  信任（TrustStore.normHex 归一；http LAN 不预信任、公网候选 https-only）、
  allow() 指纹不匹配也记 pending（换证书可重确认，消除永久锁死）、邀请码
  注册进 App（主页卡片+登录页入口，登录页传地址栏底座）。详见 CHANGELOG。

## 2026-09-05 — v0.15.0+48 主页/我的页重设计 + 自动切换批次（纯 App）

- 局域网/公网自动切换（auto_switch.dart）：周期探测+LAN 优先+两次失败
  切公网+冷却/粘性/传输互斥/代数校验；安全三道门（响应体判定、http 候选
  指纹校验、缓存跨账号清空）。存储详情页（/api/disk/detail）。我的页分组
  重组+功能宫格。最近文件缩略图/筛选/直进查看器。详见 CHANGELOG。

## 2026-09-05 — v0.14.0+46 精选专辑"文件夹式"重构批次（服务端+Web+App）

- 精选专辑升级为策展集（items 自由勾选 + 按月全选）：新端点
  GET /api/albums/{id}、PUT /api/albums/{id}/items、GET /api/photos?album=；
  列表瘦身 count/cover；settings-JSON 读改写 _mutate_albums 原子化；
  条目可见性过滤（防路径名泄露）；满 50 显式拒绝。旧月份书签型全端兼容。
- App：专辑详情页+选照片页（album_detail_page.dart）、备份"排除相册"改
  白名单 selectedBuckets、时间栏 facets 随刷新重拉（月份实时增减根修）、
  传输"移除记录（不删文件）"文案澄清 + "删除本地文件并移除记录"、手动
  上传支持视频分流。模拟器 E2E 全链路实测。详见 CHANGELOG。

## 2026-09-04/05 — v0.11.0+43 可运营+家庭共创大批次；v0.12.0+44 App 文件页体验对齐 Web

- **v0.11.0+43**（服务端+Web+App，A2/A4/A5/A6+B1-B6 九件套）：健康日报、
  视频可播性/HEVC 兜底、check.bat 质量门禁（ruff/mypy/pip-audit）、Web 设备
  管理；照片互动（评论+表情）、相册共创、数据导出逃生门、PC 备份指南、
  照片墙瀑布流+年月快滚轴、App 通知轮询。详见 CHANGELOG。
- **v0.12.0+44**（纯 App 批次，服务端零改动）：文件页 ▦/☰ 双布局（持久化）、
  图/视频 /thumb/ 缩略图（失败负缓存：仅 404/5xx 记、TTL 10min、登出/刷新
  放行）、点可预览文件直进查看器+相邻翻页、查看器"用其他应用打开"。
  3 轮 review+1 轮对抗审查修复：切主/备隧道地址快照失效（Prefs.addressNotifier
  单一事实源）、同名覆盖缓存陈旧（?v=modified）、错误态粘滞、_VideoPage
  dispose 竞态误杀共享代理、地址尾斜杠、_transferTo 静默失败、网格特大
  字号溢出。

## 2026-09-03（夜）— v0.10.0→v0.10.4：公网主/备双隧道 + 弱网加固批次

- **v0.10.0+38**：樱花FRP 主/备双隧道（备用 Caddy :8801 纯 TCP 透传、同源
  证书 App 切换零弹框）；`/api/app/entries` 免登录下发主/备入口+证书指纹；
  App 登录页主/备快捷入口、我的页入口切换（不登出）、同源指纹静默信任、
  公网省流提醒。
- **v0.10.1+39**（用户真机反馈"公网照片加载不出/上传失败"）：本机实测公网
  链路健康（缩略图 0.05s/直传 1.38MB/s/50 并发零失败），根因在手机蜂窝
  路径——客户端韧性加固：图片 45s 超时+5xx/429 重试；分片超时 30min/
  重试 8 次长退避；限速桶入口分账（api:lan 120 / api:pub 240 独立桶 +
  Retry-After，`test_zz_rate_limit.py` 3 项）。
- **v0.10.2+40**：SnackBar 深色对比度（文字恒白，实测 19:1）；查看器防黑屏
  （缩略图垫底+深海渐变底+柔光 spinner+失败重试）；公网预览 1280→960；
  缩略图并发 LAN8/公网3；视频代理失败自动重置。
- **v0.10.3+41**：上传并发公网 1 路/局域网 2 路；sha256 后台 isolate；
  照片墙分页公网 60/局域网 120；幻灯片 8s/3s。
- **v0.10.4+42（审查补丁）**：批次代码审查修 3 处 P1/P2——缩略图并发档位
  写反；照片墙分页竞态（局域网静默截断 60 张）；上传调度前先定入口档位。
  验证：analyze 零问题、Dart 93 全绿、pytest 142 全绿、线上 latest/history ✓。

## 09-03（傍晚）：v0.8.1+23 六轮 review + 对抗性审查——管理面板删除失效等 5 个 P0 全修

用户报管理面板删除按钮失效。四专员并行审查（契约/生命周期/UI/安全/对抗）
+ 修复轮：**P0×3**——邀请吊销/授权撤销误用 POST→405（`_del` 统一走
DELETE，真跑验证）；清配额/清 WebDAV 密码被 `_prompt` 空值拦截（加
`allowEmpty`，配额发 0 清除）；通知未读字段 `read_at` 应为 `read`。
**P1×10+**：错误提示中文化（新 `errors.dart errDetail`，6 处英文堆栈
直出替换）；约 10 处 fromJson 强转兜底；`_busy` 防连点；目录路径逐段
编码；zip 临时名防覆盖；多选栏横向可滚防溢出；改用户名清缩略图缓存等。
analyze 零问题、pytest 122 全绿、模拟器 E2E 烟测（邀请删/授权撤）通过，
线上 /api/app/latest 实测 0.8.1/build 23。随后当晚本人亲自 5 轮逐行复审（五视角：契约/生命周期/空安全/交互/
跨文件+对抗）补 **v0.8.2+24** 三项：配额 0=不限与 <10GB 一位小数展示
（两处误清隐患）、webhook 测试按钮 ValueListenableBuilder 实时可用、
_loadActivity 异常收口。

## 09-03：v0.8.4+26 App UI 重设计与美化（方向 A 全批一次做完）

用户拍板：视觉方向 A（深紫蓝 + 玻璃质感）/ 暗色增强（AMOLED）/ 三批一次
做完 / 品牌图标用登录页云朵 + 渐变背景色。纯前端改造服务端零改动：design
tokens + 亮/暗/AMOLED 三套主题（`app_theme.dart`）、云朵品牌组件库
（`widgets/brand.dart`：云朵 Logo/渐变品牌头/空态三件套/类型色文件图标）、
首页品牌渐变头、照片墙日期胶囊 + 查看器 Hero、底部毛玻璃操作条、传输/分享/
回收站/动态空态统一、我的页 AMOLED 开关、页面转场淡入上移。验证：analyze
零问题、Dart 71 全绿、release APK 分发。

## 09-03（深夜）：v0.8.3+25 全仓十轮 review + 五轮对抗审查——授权前缀 Windows 失效 P1 等 13 项修复

用户要求"亲自 10 轮完整代码 review + 5 轮对抗性审查，覆盖所有模块"，
随后全面修复。审查覆盖后端全 16 文件、Web 全 30 文件、Mobile 全 43
Dart + 6 Kotlin、Caddyfile。**头号发现（P1，潜伏已久）**：`os.path.normcase`
在 Windows 下把 `/` 归一成 `\`，授权前缀命中（`gp + "/"`）与移动同步的
段拼接全部失效——成员/访客只能访问"被授权目录本身"，子目录读写授权形同
虚设；存量测试只覆盖精确路径所以从未暴露。修复：`_normcase` 统一回正斜杠
规范化；**授权前缀语义**（E10，含 guest 只读子目录、member 写子目录、
move/rename 后 grants 跟随）补 4 项回归测试锁定。

同批修复：`/api/move` rename 后 `target.is_dir()` 恒 False 的授权同步死代码
（P1）；定时清理循环无容错（一次瞬时 OSError 永久杀死 hourly 清理，P2）；
直传 `/api/upload` 配额信任 multipart 谎报 size（P2）；copy 配额定价遍历
裸越界路径（P3）；WebDAV PUT 配额把 tmp 双计 + 失败残留 tmp（P3）；
Web 管理面板配额"留空=不限"发 null 被服务端当"未提供"跳过（P1，与 App
0.8.1 同类契约错误）；PhotosView 滚动监听跨路由泄漏（P2）；uploader
clearFinished 误清暂停项续传会话（P3）；App 传输引擎 429 判死改退避
（与备份引擎对齐）；Caddy `request_body 2GB` 掐断 WebDAV 20GB 大文件
（P2 契约不一致，升 25GB）。验收：pytest 129 全绿、web build 通过、
flutter analyze 零问题 + 71 单测全绿、服务重启探活通过。

## 09-03（午后）：v0.8.0+22 App 功能对齐网页版——管理面板 + 账号安全 + 打包下载 + 专辑 + 通知

App 侧补齐 Web 全部能力，服务端零改动：**管理面板**（成员/邀请/授权三
Tab + 公网地址 + webhook 事件推送；视角预览留 §9 遗留）；我的页**账号与
安全**（改用户名/WebDAV 独立密码/生成配对码/恢复码重生成）；文件多选
**打包下载**（zip→分享面板）；照片**精选专辑**行（admin 建删）；首页
**通知中心**（铃铛+未读徽标，打开即已读）。E2E 真账号跑通三 Tab/配对码/
通知；⚠️ 事故：E2E 误撤 id=12 授权（查实主人已重组根目录，授权本已
失效，零实际影响）；教训入册：真实数据环境禁止"删最后一个"式定位。

## 09-03（上午）：v0.7.3+21 备份触发根修——手动立即备份不再被充电约束吞掉 + 全链路可观测

全链路审计找到"点了没反应"头号根因：手动立即备份沿用自动约束
（仅充电时默认开），未插电时任务永远排队等充电器且零可见。修复：
手动走 `enqueueManual` 专用入队（无充电/电量/网络约束，trigger 贯穿
引擎，manual 跳过未充电暂停并豁免冷却）；`BackupState` 把 Worker/
队列每个出口状态写入 prefs（含入队时的"等待充电/Wi-Fi"）；引擎每轮
结束写 `backup_last_round`；首页备份卡加最近一轮状态行、设置页新增
备份诊断卡（网络/电源/通知/最近调度/最近一轮 + `diag` 通道）。
Dart 71（+11）。

## 09-03（凌晨）：v0.7.2+20 照片墙实时更新扩面——脉搏轮询 + 强刷节流独立

照片墙触发面从"只认本机备份引擎"扩为三层：本机备份（10s prefs）+ 本机
手动上传（引擎订阅）+ **20s 服务端脉搏**（`limit=1&refresh=1` 轻探测，
total/首张变化即整页强刷）——网页/其他设备/文件页/分享接收的新照片
自动上墙。服务端 `_scan_photos` 的 force 走独立 5s 节流，不再被普通
浏览的 60s 时间戳顶掉；扫描范围确认为全盘 rglob（新文件夹天然覆盖，
pytest 钉死）。pytest 122（+3）。

## 09-02（夜 IV）：v0.7.1+19 备份状态卡三项修复——database_closed 根修 + 排除感知统计 + 照片/视频分列

用户实测三个症状（排除后状态卡不动/不实时/账本打不开），复现定位为**同一
根因**：引擎每轮 `close()` 账本，sqflite Android 侧连接进程级共享，主
isolate 缓存句柄死后全部操作抛 `database_closed`。详见 APP-DESIGN §8.15：

- **根修**：删引擎侧 `close()`（回收归 `engine.destroy()`）+ `open()` 死句柄
  自动重开；统计轮询/账本页 try/catch + 错误态重试；账本 v1→v2 迁移幂等化。
- **排除感知统计**：被排除相册的未完成项从待备/失败中扣掉、单列"已排除 N 项"；
  首页失败红字同步排除感知（不再永挂）。
- **照片/视频分列**：状态卡分类型计数；账本页类型图标 + 相册名 + 类型筛选；
  首页待备分类型回写。Dart 60（+5 聚合纯函数）。

## 09-02（夜 III）：v0.7.0+18 备份落位重构——照片/视频/<用户名>/<相册名>/ 三级目录

用户拍板（类型/用户名/相册三级 + 存量不迁移 + 重传可接受 + 改名场景要
交代）后实施，详见 APP-DESIGN §8.14：

- **引擎**：`backup_dest.dart` 落位计算（图片→`照片/<u>/<相册>/`、
  视频→`视频/<u>/<相册>/`、相册名安全化）；账本 v2 加 bucket 列，
  (id,mtime,size,bucket) 变化即重备——升级后存量经 sha 秒传零流量
  全量迁入新落位；改名用户由账本账号隔离机制自动接管。
- **照片页手动上传**改道 `照片/<u>/手机上传/`；文件页/分享接收不变。
- **服务端 `_photo_uploader`**：新落点第二段命中真实用户名才判归属
  （防手工整理二级目录误判成用户）；旧落点 `照片/手机相册/<u>/` 朴素
  语义保持。上传端点零改动。pytest 119（+1 归属回归）、Dart 55
  （+11 落位纯函数）。

## 09-02（夜 II）：App v0.6.3+17——动态更名 + 清空回收站（admin）+ 排除页加固；落位重构规划待拍板

- 「家里动态」更名「动态」（首页卡+动态页）；回收站新增清空入口
  （`DELETE /api/trash` 本就存在且仅 admin——App 端补 admin 可见按钮 +
  二次确认"含其他成员放入的"；E2E 只验到确认框，取消收场，回收站
  81 条真实条目 API 复核原封未动）；排除相册页空态/错误详情/重试加固
  （小米"页面不显示"判断为旧包通道 bug，0.6.2 已根修，升级后复测）。
- **备份/上传落位重构出规划未实施**（APP-DESIGN §8.14）：按手机相册
  分子目录 + 手动上传类型分拣到 照片/视频/文档/音乐；三大决策点待
  用户确认（手动上传子夹命名/存量不迁移/账本加 bucket 列）。
- 验证链：analyze 零问题 → Dart 44 全绿 → 出包 → E2E → 线上 latest
  0.6.3/build 17。

## 09-02（夜）：App v0.6.2+16——排除相册恶性 bug 根修 + 首页家里动态 + 备份完善

用户报"排除相册功能无法使用"。根因：`MainActivity` 从未注册
`lanshare/media` 通道（桥注释声称"主引擎与 Worker 都注册"，实际只有
Worker 后台 isolate 有），排除相册页在主 isolate 调 `buckets()` 必吃
`MissingPluginException`，页面永远显示"无法读取相册列表"。与昨夜应用锁
同款教训：发版前没在模拟器上真跑过那条路径。

- **根修**：MainActivity 补注册 `lanshare/media`；`MediaSource.mediaPayload()`
  统一识别原生"出错回 success+error JSON"约定并转真异常；排除保存后立即
  `runOnce()` 即刻生效。
- **首页「家里动态」**：复用 `/api/activity`，`data/activity.dart` 纯函数 +
  首页动态卡（5 条/30s）+ 全量动态页；备份上传同样进动态流。
- **备份完善**：首页失败红字（点击进账本）+ 实时"备份中 X/Y"（引擎逐张
  落 prefs）+ 电池优化豁免引导（新权限 + `battery`/`batteryRequest` 桥 +
  设置页引导行，小米/华为掐周期任务的对症药）。
- 备份头脑风暴其余项（相册子目录映射/HEIC 转码/备份统计微图/通知深链）
  记录于 APP-DESIGN §8.13"后续候选"，待主人拍板。

**验证链**：`dart analyze` 零问题 → **Dart 44 项全绿**（+11：mediaPayload
错误 JSON 防线/动态文案聚合相对时间）→ release 出包 → 模拟器 E2E 七步
闭环：排除页列相册（修复前必报错）→ 排除 LSTest1 → 服务端只落 LSTest2
两张 → 取消排除 → 4/4 回补 → 首页动态"上传了 4 项·刚刚"+ 备份卡
"0 张待备·刚刚完成" → 动态页全量正常；"仅 Wi-Fi"在 3G 计费网络下拦截
引擎的语义保持。发版 + 线上 `/api/app/latest` 实测 0.6.2/build 16。
**教训入册：注释说"注册了"不等于真注册了——E2E 是唯一真相；通道/桥
类改动直接进发版 checklist。**

## 09-02（晚）：全量安全修复批——首次对抗性审查 P0×2 / P1×2 / P2×5 + P3 全量落地

上午完成首次**全量人工代码审查 + 五轮对抗性安全审查**（报告：
`每日巡查日志/review对抗性审查日志/review-2026-09-02.md`），随后全量修复，
安全健康度 critical → 修复后全绿：

- **P0-1**：Caddy `/files/*` 此前是 `forward_auth` + 裸 `file_server` 直挂
  SHARE_ROOT，任何登录用户（含 guest）可越权读所有人 `private/`、`.trash/`、
  `.chunks/`、`.meta.db`——改为反代后端 `/files/{path}` 鉴权端点
  （`resolve()` 判权 + attachment 下发），URL 空间不变、前端零改动。
- **P0-2**：`resolve()`/`visible()` 等对 `private`/`incoming` 特殊段用精确
  比较，NTFS 大小写不敏感下 `Private/<他人>/` 变体落入 shared 默认可读
  分支——全部换 normcase，连带授权校验/照片归属/grants 同步。
- **P1**：回收站恢复补归属判定（成员不能再枚举 trash id 把他人条目恢复进
  自己空间；顺带根修 delete 登记原始 vpath 导致归属不可判的问题）；改密/
  重置/停用三条路径吊销全部 device_tokens（Bearer 90 天滑动不再是"改密踢
  端"旁路）。
- **P2×5 + P3 全量**：分块超发即断、直传 `.uploading`+replace 原子写
  （顺带修 413 unlink 打开中文件的老 bug）、rglob 类操作移出事件循环、
  遗留无主分享仅 admin、TOTP 动态码防重放（`totp_last_step` 双引擎加列）、
  复制计配额、WebDAV 补 E9 守卫、定向分享认 Bearer 等 16 项（逐条见
  CHANGELOG 09-02 晚批）。

**验证链**：新增 `tests/test_review_fixes.py` 12 项回归（大小写变体/
files 判权/回收站越权/Token 吊销/超发/遗留分享/TOTP 重放）→ 全量
**pytest 118 项全绿** → 重启 api+caddy → 线上实测：未认证 401、
`.meta.db` 403、已登录 `/files` 下载 200（attachment）。SECURITY.md 五处
声明同步。**教训入册：Caddyfile 属于攻击面，配置变更必须与代码同级纳入
审查触发器。**

## 09-02（深夜 XI）：热修应用锁恶性锁死——三连环根修 + 录指纹 E2E 七项全过（v0.6.1+15）

用户真机报恶性 bug：开启应用锁**立即上锁**、指纹**解锁后被弹回**、
**退出登录重登照样锁**——彻底锁死。根因：生命周期判定把"指纹弹窗自身
关闭的 resumed"当成"从后台回来"，加上 HomeShell initState 无条件按
appLock 置锁（登出重登即再锁）。修：落锁判定收进纯逻辑 `LockGate`
（只有 paused/hidden 算真后台，单测覆盖）+ `biometricBusyProvider`
忙标记（弹窗在屏不判定）+ `sessionUnlockedProvider` 进程级解锁标记
（逃生口可用）。详见 CHANGELOG 深夜 XI 与 APP-DESIGN §8.12。

**验证链**：`dart analyze` 零问题 → Dart 测试 25→**33 项**全过 →
模拟器实录指纹（Settings 录入流程 + `adb emu finger touch`）→ **七项
E2E 全过**（开锁不立即锁/切后台锁/解锁稳定停留/锁屏登出逃生/重登不重锁/
冷启动锁/关闭还原）→ v0.6.1+15 签名发版，线上 `/api/app/latest` 已指向
0.6.1。**教训：生物识别功能必须录了指纹真跑一遍才能发**（§8.12）。

## 09-02（深夜 X）：全板块功能补齐大批次——7 板块 35 项一次做完（v0.6.0+14）

头脑风暴（每个板块"必须+实用"清单）定稿后一次性实施完成，服务端配套
4 个新端点，当前 APK v0.6.0+14 已签名发版（`App安装包/` + version.json）。

**验证链**：pytest 106 全绿（新增 test_appbatch.py 9 项：分享编辑 PATCH/
复制/版本端点/时间线视频）→ `dart analyze` 零问题 → Dart 测试 25 项
（新增版本对比/时长文案/fromJson 兼容）→ 生产 PG 实测 8 个存量视频自动
进时间线（ffprobe 时长 + creation_time 拍摄日期 + ffmpeg 封面）→ 模拟器
E2E 冒烟：TOFU 登录 → 视频播放到完（0:02/0:02，MediaProxy 代理流+控制条
+操作栏）→ 视频筛选全带 ▶ 时长角标 → 文件页新建文件夹成功 → 深色模式
即时切换 → 检查更新闭环（build 14 对比，"已是最新版本"）。

**服务端**：`PATCH /api/shares/{token}`（编辑有效期/上限）· `POST /api/copy`
（批量复制，重名序号连同 move 修正为 (1) 起）· `GET /api/app/latest|download`
（version.json 发版约定，basename 防穿越）· 照片时间线纳入视频
（VIDEO_EXTS + media_type 筛选 + photos.duration 双引擎迁移 + ffprobe 探针）。
**踩坑**：探针 CASE WHEN ? IS NOT NULL 悬空参数让 psycopg 报
IndeterminateDatatype（/api/photos 500）——改两段式 executemany；
flutter_local_notifications 要求 core library desugaring 出包才过。

**App 侧七板块**：①照片墙/查看器：视频角标时长/内嵌播放（MediaProxy
127.0.0.1 代理流解决 ExoPlayer 不吃证书回调；networkSecurityConfig 仅放行
loopback 明文）/类型筛选/按月快跳/幻灯片/信息操作栏；②文件页基本功：
新建夹/重命名/删除/多选批量/移动复制/排序/钉住/详情/最近+搜索/回收站页；
③传输：失败详情/筛选/全部暂停继续/一键重试/完成系统通知；④分享：二维码/
系统外发/编辑；⑤我的：主题三档/改密/应用锁（壳层锁屏+登出逃生）/缓存
管理/检查更新；⑥备份：排除相册（Kotlin bucket）/待备字节空间预警/统计
聚合；⑦首页：最近上传横滑条/快捷行。

## 09-02（深夜 IX）：全功能实测修复批——模拟器九模块真连 + 4 bug 根修（v0.4.6+10）

v0.5.2 之前的 App 改动都按"修改→单测→真构建→小切片模拟器回归"的
轻流程过，但**没在模拟器上做过一次"按用户日常路径走一遍"的端到端真连**。
本次按"打开 App → 登录 → 首页 → 照片 → 文件 → 传输 → 备份 → 分享管理 →
系统分享接收 → 我的"九模块顺序跑，每步都用 server-side 物理证据（API
响应、jobscheduler dumps、adb root 读 prefs、SQLite 账本）核对，挖出 4 个
存量 bug：

- **P1 跨 isolate `SharedPreferences` 缓存**：`BackupDartRunner` 是
  WorkManager 拉起的独立 FlutterEngine（独立 isolate），引擎完成一次备份
  后写 `backup_last_done_at` / `backup_pending_count`——但 UI isolate 的
  `shared_preferences` 插件**只在首次 `getInstance()` 时缓存到首帧**，后续
  `getXxx` 都读旧值。**v0.4.3 起的"照片墙准实时 10s 强刷"与首页备份卡从
  未真正生效过**——证据链：jobscheduler WM-WorkerWrapper SUCCESS=0.24s（快速
  跳过，确认引擎跑过）；adb root 直接读 `/data/data/.../shared_prefs/FlutterSharedPreferences.xml`
  确认 `backup_last_done_at` 已更新；pull `databases/backup_ledger.db` 看
  `ledger` 表 9 条 done 无新增（确认没有新文件进来）。**真正的 bug 不是
  备份不跑，是 UI 永远看不到备份跑了**。修：照片页 10s 轮询前与首页
  `_loadLocal` 首行加 `await sp.reload()`——任何插件"首帧缓存"副作用都
  要进 checklist，不靠用户报
- **P2 复制分享链接 snackbar 插值错位**：`shares_page.dart:96`
  `'已复制（$_addrTag(chosen)）'` 把**闭包对象本身**当字符串插值——
  Dart 单标识符 `$x` 是合法语法且编译器不警告，运行时 `toString()` 给
  `Closure: () => String`，用户看到的是元数据不是标签。补花括号
  `'已复制（${_addrTag(chosen)}）'`。教训：插值必须显式花括号
- **P2 手动上传完成无联动**：照片页/文件页 `_pickAndUpload` 选 5 张图 → 任
  务全 finished → 相册列表不动——非备份路径没人监听 `engine` 状态，靠
  10s 备份轮询兜底，体感延迟。修：两页收集本次入队任务，订阅引擎，所
  有任务 finished 且当前路径未切走时 `_refresh()`；订阅前快照目录是为了
  防用户切走目录被旧 listener 强刷
- **P2 备份位置文案过期**：设置页文案 `incoming/$username/手机相册/`，
  引擎实际 `destDir = 照片/手机相册/$username/`（v0.4.2 真机反馈第一批已
  统一）。改文案不改变逻辑，纯显示侧遗漏。教训：批量重构时显示侧要
  grep 一遍硬编码路径兜底

**验证**：四回归在 v0.4.6+10 模拟器实测过——① 备份位置文案显示新路径；
② 复制「局域网」标签 snackbar = `已复制（局域网）`；③ 手动上传 5 张，
相册列表 1s 内多 5 张；④ 引擎跑一次，照片页 10s 内强刷新。release APK
已传 `App安装包/lanshare.apk`（覆盖分发、分享链接不变）。

**模拟器实测注记**（不是 App bug，是开发环境事实）：
- 模拟器无 Wi-Fi、只有蜂窝 3G（计费）→ WorkManager `UNMETERED` 约束
  **永不满足** = 系统层宁停不偷跑。**开发期关掉"仅 Wi-Fi 备份"开关**
  才能跑测试；真机 Wi-Fi 环境下不受影响
- 网络验证 `PARTIAL_CONNECTIVITY` 时 Google 204 探测不可达，CONNECTED 任
  务也会卡——`settings put global captive_portal_http_url/https_url/...`
  指向 `http(s)://connect.rom.miui.com/generate_204` + `svc data disable/
  enable` 重验网络即可

## 09-02（傍晚）：App 内置验证器——两步验证零第三方依赖（v0.5.2+13）

"手机上没有认证器 App"暴露的门槛：两步验证要求用户先装 Microsoft/Google
验证器，与家庭自包含定位冲突。LAN Share App 自带扫码与安全存储，直接内置：

- **TOTP 引擎自实现**（RFC 6238）：HMAC-SHA1 用已有 crypto 包、Base32
  手写，零新依赖；**RFC 附录 B 官方向量单测全过**（16 项 Dart 测试）。
- **验证器页**（我的 → 验证器）：扫码绑定（ScanPage 参数化 otpauth 模式）
  + 手输密钥兜底；动态码卡片带 30 秒倒计时圆环（末 5 秒变红）；解除绑定
  不影响服务器状态。密钥 SecureStore 双写（可靠性优先，同密码档通道）。
- **登录页一键回填**：needTotp 时检测本机验证器绑定当前账号 → 显示当前码
  卡片，点按自动填入六格并提交——App 登录两步验证一次点按完成。

## 09-02（下午）：两步验证补全——恢复码 + admin 精准解除（v0.5.1+12）

审计发现四个缺口：认证器丢失无自助恢复（admin 自己丢手机只能动库）、
App 生物识别对 TOTP 账号名不副实、admin 无"仅解除 TOTP"动作、Web 登录
动态码是单框。全家桶补齐：

- **一次性恢复码**：开通时生成 10 枚（8 位 hex），明文仅响应一次、库内只存
  SHA256；登录第二步可顶替动态码（用一枚少一枚）；重新生成需原密码；
  `users.totp_rc` 三引擎迁移；`/api/auth/me` 增 `totp_rc_left`。
- **admin 精准解除**：`POST /api/admin/users/{uid}/totp-disable` 只清两步
  验证+恢复码，不动密码不吊销会话；成员列表 🛡️ 2FA 徽章。
- **Web**：开通后恢复码面板（复制全部/已妥善保存）+ 剩余枚数 + 重新生成；
  登录页动态码换六格分离输入（`CodeField.vue`）+ 恢复码切换。
- **顺带修存量 bug**：全局确认框与业务弹窗同 z-index 400，动态 teleport
  的弹窗会盖住确认框——`.dlg-overlay` 提到 450。
- **App**：登录第二步恢复码切换；配对码"3 分钟"文案；生物识别如实标注。
- 另修：**配对码倒计时归零副作用**（生成即显"已过期"一闪而没）+ 配对码
  时效 60s→180s（真机反馈"很快消失"的根因，独立提交 ed117b0）。

## 09-02：登录页方向 A 重构 + 全家桶（v0.5.0+11，三端批次）

头脑风暴拍板：布局方向 A（渐变品牌区 + 浮层卡片），功能"全家桶"（预检/
生物识别/扫码/配对码），服务端 + Web + App 一起动。

- **布局重构**：上半屏渐变品牌区（云朵 Logo + slogan），下半屏圆角阴影
  浮层卡片；**服务器地址从表单首位降级为卡片顶部状态条**（短主机名 +
  预检状态点），点击弹 bottom sheet（历史地址/扫码添加/手动输入）
- **连接预检**：任意 HTTP 状态码（含 401）=在线，仅超时=不可达——无需
  服务端健康检查端点；自签证书放行（预检只答"在不在线"，信任归 TOFU
  流程）；独立 Dio 不带凭据、用完即关
- **生物识别一键登录**：登录成功后凭据存档**仅写 Keystore**（不落
  SharedPreferences 明文副本——密码比可吊销 Token 敏感，审查采纳项）；
  local_auth 门禁（biometricOnly=false 兼容锁屏 PIN）；主动退出清档、
  Token 自然失效不清（掉线正需一键重登）；MainActivity 换
  **FlutterFragmentActivity**（local_auth 硬性要求）
- **配对码免密登录**：`POST /api/pair/code`（已登录方生成一次性 6 位码，
  3 分钟有效，同账号单码）+ `POST /api/pair/claim`（手机免密换设备 Token，
  凭据规则与 `/api/auth/token` 完全一致）；安全 = 180s 时效 + 一码一用 +
  IP 限速 5 次/分 + claim 路径懒清理；`_issue_device_token` 抽为共用内核；
  **长辈手机不碰密码**；Web 端「账号设置 → 手机 App 连接」出地址二维码
  （`/api/qr`，零新依赖）+ 配对码倒计时
- **扫码填地址**：mobile_scanner（仅收 http(s) 链接），权限拒绝走
  errorBuilder 友好提示
- **细节**：TOTP/配对码共用六格分离输入（跳格/粘贴分发/填满自动提交/
  错清重输）；记住用户名预填；密码可见性；错误 shake；深浅色自适应
- **踩坑**：SingleChildScrollView 子项用 `Spacer()`（unbounded 主轴 flex）
  → 布局期异常**整页白屏且无红屏无日志**，曾误判 FragmentActivity，
  对照构建（FlutterActivity 也白屏）才锁定；教训：滚动容器内禁用 flex
  撑满技巧。Gradle transform 缓存被连续构建互踩（immutable workspace
  modified）→ 杀 daemon 清 `~/.gradle/caches/9.3.1/transforms` 重建
- **验证**：pytest 87 → **92** 全绿（+5 配对码：全流程/过期/单码/校验
  限速审计/同名轮换）；`dart analyze` 0 问题；`vue-tsc` 通过；模拟器
  端到端真连（预检红→绿/密码登录/退出清档/记住用户名/**配对码免密登录
  全程**）；release 包冒烟 + sha256 一致上传 `App安装包/lanshare.apk`；
  生物识别弹窗与扫码取景为真机特性待真机验收

## 09-01（第四天）：四小项批次 + App 五连跳（M-A0~A4）+ 七轮审查加固 + 发布收尾

- **M-A0 设备 Token（App 里程碑第一步，契约冻结）**：Token 三端点 +
  Bearer 中间件（90 天滑动/SHA256 存储/吊销审计/停用即失效/同名设备轮换）；
  测试 +5
- **M-A1 只读可用**：照片墙/文件浏览/我的页（设备管理）+ 自签证书 TOFU
  信任流；**安卓模拟器端到端验证通过**；debug APK 经自家分享链接分发
- **首装排障**：真机"登录不进去"定位为 Keystore 存储异常 → Token 降级存储；
  证书指纹显示乱码修复（replaceAll→replaceAllMapped）
- **四小项批次**：HEIC/HEIF（pillow-heif）、WebDAV 独立密码、事件推送
  Webhook、admin 成员视角预览
- **运维**：备份计划任务改**每日 23:30** + 错过开机补跑（机器 2 点自动关机，
  原 03:30 从不触发）；测试 70 → **84 项全绿**
- **M-A2 手动传输**：上传（分片续传+秒传）/下载（Range 断点）/传输中心第 4 Tab；
  **500MB 杀进程续传验收**（FR-5，模拟器 E2E）；顺带修杀进程掉登录（SecureStore
  双写）、文件页永不加载、同目录重传秒传失效三个 bug；测试 84 → 85（Range 契约）
- **M-A3 自动备份**：自写 Kotlin 薄桥（WorkManager 15min 周期+MediaStore Observer
  即时触发+前台服务 dataSync，不引第三方后台插件）+ sqflite 账本去重 + 后台
  isolate 引擎（协议与网页全对齐）；设置页+账本页；**FR-2 验收点模拟器全通过**
  ——60 张全自动备份 sha 一致、账本去重 mtime 零变化、视频默认不传、250MB
  杀进程续传（**part-mtime 物理证据零重传**）、重启手机 JobScheduler 持久恢复、
  公网地址默认跳过；修通知同 ID 冲突（结果通知发出即被撤）
- **七轮审查加固**（commit 154201b）：对 M-A3 全部代码 5 轮主题审查（正确性
  并发/边界完整性/泄漏性能/协议对齐/UI 生命周期）+ 2 轮对抗性安全审查；
  核心修复：进程级协程 Mutex 防周期/Observer 双引擎并发、账本 attempts 暂停
  语义、**服务端秒传 Dedup Oracle 封堵**（spaces.sample_readable 物理路径直判
  +跨用户拒绝测试，resolve() 不可用作权限判定）、换账号清账本、trust_required
  证书通知、周期空跑跳过；测试 85 → 86
- **M-A4 分享与发布**（commit f9c0a60）：**系统分享接收**——SEND/SEND_MULTIPLE
  intent-filter → Kotlin 先拷 content:// 进 cache/share_inbox（授权方可随时回收
  URI）→ lanshare/share 通道 Dart 拉取/推送双向容错（handler 未就绪静默，登录
  后拉取兜底）→ 复用传输引擎上传 `incoming/<用户>/分享暂存/`，文本落 txt，
  **上传完成即删本地副本**；**分享管理**——列表/撤销/复制链接（多地址 局域网/
  公网 标签复制钮）/创建面板（1-30 天/提取码/下载上限/定向成员）；**零服务端
  改动**。E2E：单文件/文本/MULTIPLE 兜底三路径落库字节一致、创建 3 天分享 →
  UI 撤销 → 服务端确认；顺带修复 154201b 遗留 Kotlin 编译错误（progress 刷新
  在 Runner 作用域引用 applicationContext，该 6 行从未编译过）——教训：analyze/
  单测不编译 Kotlin，改完必须过真构建；加固版+M-A4 APK 已传 App安装包
  （sha256 与本地构建一致）
- **release 签名**：自建 keystore（10000 天，指纹 `CF:31:1C:B4:…:07:78`，
  gitignored + 异地备份）；gradle 接 `key.properties` 签名配置，版本 0.1.0+1 →
  **0.4.0+4**。踩两个坑：① `storeFile` 相对 android/ 根而非 app/ 解析；
  ② **Flutter 插件默认给 release 开 R8，WorkManager 的 Room 库反射实例化
  被裁剪破坏，启动即崩（debug 从不暴露）**——`shrink=false` 关掉（+4MB，
  稳定优先）。release 包模拟器 E2E 通过（登录/TOFU/照片墙/file_picker 上传
  sha 一致）；分发件换正装 `App安装包/lanshare.apk`（旧 debug 分享撤销删除；
  签名不同不能覆盖安装，旧设备须卸载重装）
- **品牌启动图标**：告别 Flutter 默认图标，与 PWA 同款"紫渐变 + 白 L"
  （几何/配色按 web/public/icons/icon-512.png 实测）。新增
  `scripts/make-app-icon.py`（PIL，零新依赖）产出自适应 anydpi-v26
  （背景=drawable 渐变、前景白 L、monochrome 主题图标）+ 遗留圆角 × 5 密度；
  版本 0.4.0+4 → **0.4.1+5**，分发件原地覆盖（sha256 d358082d… 校验一致，
  分享链接不变），模拟器验证抽屉图标/启动/登录态无损
- **真机反馈第一批（v0.4.2+6）**：首夜真机使用四连修——① 备份约束收口：
  WorkManager 约束只管启动，Observer 即时任务无约束 + 引擎跑后无人复查 =
  "无法自动暂停"；引擎逐张 `checkStop` 复查（叫停/计费网络/未充电/开关被关
  → 优雅暂停自动续）+ Observer 同约束 + 电量约束；② 照片统一落位：修手动
  上传少斜杠 bug，手动+备份统一 `照片/手机相册/<用户名>/`（服务端归属识别
  +1 测试，存量 259 张已迁移、时间线全量认领）；③ 首页综合面板（5 Tab，
  备份/传输/存储/最近上传 + 快捷入口）；④ 照片页按拍摄日分组。pytest
  85→86→**87** 全绿
- **评审+对抗审查+体验批次（v0.4.3+7）**：四轮自查修 3 个泄漏/状态机 bug
  （首页 Timer、查看器 PageController、**分组网格 children 版致缩略图风暴**）
  ；图片提速三件套（缓存上限 200→5000 对象、thumb JPEG draft 快解码、
  ETag 协商缓存 + Caddy /thumb 放开 no-store）；长按多选（下载/删除进回收站
  /单选分享）；照片墙准实时（备份完成 → 10s 内 refresh=1 强刷，服务端扫描
  节流对 force 放宽 5s）；对抗审查修 netMetered 断网语义，两处家庭信任边界
  入 SECURITY #10
- **时间线 Sliver 重构（v0.4.4+8）**：修复"已加载图片划走再回来全变转圈/
  坏图"——根因 shrinkWrap 网格整组销毁重建 + 服务端 ETag 上线后
  flutter_cache_manager 条件下载被自写 FileService 当 304 错误抛；重构为
  CustomScrollView + 按日 SliverGrid 真虚拟化（置顶日期头，只构建视口内
  格子），AuthFileService 剥条件头/401 自愈/8 并发闸，AuthImage 加进程内
  已解析文件记忆表（本会话往返零转圈）；快滑压力测试修复前后对比通过；
  照片墙规模化不设限（虚拟化 + 5000 对象磁盘缓存 + 服务端内容寻址缓存）

## 三日交付总览（2026-08-29 ~ 08-31）

### 08-29（第一天）：v3.5 四大件 + PG 上生产

- **定向分享 + 站内通知**：分享弹窗选成员 → 对方登录即取（免提取码）、
  站内通知；无关者 404 不泄露存在性
- **共享相册升级**：月份/拍摄者双维度筛选 + 精选专辑（admin 收藏月份）
- **TOTP 两步验证**：扫码/密钥自助开通，登录两步动态码，admin 重置密码
  同步解除；WebDAV 维持仅密码（取舍记录于 SECURITY.md）
- **PostgreSQL 16 切换生产**：双引擎 db.py（`DATABASE_URL`），317 行迁移，
  5433 原生服务；SQLite 转测试专用；`/preview?w=` 中图档、`/api/photo/meta`
  EXIF 元数据接口随之上线
- **每日备份落地**：pg_dump + 文件增量，同盘 C:\lan-share-backup，
  schtasks 每日 03:30

### 08-30（第二天）：预览器 + 通知动态体系 + 手机适配

- **统一预览器 FileViewer**（文件页/照片页合并）：沉浸外壳、相邻导航、
  键盘/触屏手势、图片缩放平移旋转（光标焦点 + 边界钳制 + 倍率气泡）、
  大屏原图/小屏 1280 档、加载失败重试、⛶ 全屏、信息面板（EXIF 尺寸/相机，
  预览内直接分享/重命名/删除）、文本/代码高亮、Markdown/CSV/docx 渲染
  （按需 chunk 懒加载）、音频连播、视频倍速/画中画、胶片条、空白点击退出
- **手机端适配（与 PC 配置隔离）**：响应式 isMobile 双轨——手机精简顶栏
  （ℹ⬇✕）、底部大按钮操作条、信息面板全宽单列、捏合/拖动互不干扰、
  下滑跟手关闭、安全区、箭头/胶片条隐藏；桌面端零改动
- **通知与动态体系**：🔔 面板双 Tab（动态/通知，默认动态）；家庭动态流
  独立页 `/activity` 全员可见（聚合/相对时间/去查看直达/高亮定位/清空）；
  **审计日志补记操作者用户名**（中间件 contextvar 注入 + 存量路径反推回填，
  "有人"→ 真实用户名，无法归因标"链接访客/早期记录"）；红点体系——铃铛
  小圆点（仅新动态）/数字徽章（通知未读）、Tab 级红点、首次使用静默水位
- **管理面板**：动态 Tab 移除（动态页独立后去冗余）
- **运维**：备份改按日期命名 + 仅保留 2 天；`check.bat` 测试 55 项全绿
- **质量**：三轮代码审查 + 两轮对抗性实弹（limit 钳制/XSS 转义/越权矩阵/
  w 洪水量化/缓存泄漏回收/过期响应竞态/拖拽卡死），全部修复并回归

## v3.5（2026-08-29 深夜）— 定向分享 / 共享相册 / TOTP / PostgreSQL

- **定向分享 + 站内通知 ✅**：分享弹窗可选目标成员；收件人登录即取（免提取码），
  通知铃铛（未读徽章/自动已读）；分享管理页 🎯 徽章；落地页"去登录"引导。
- **共享相册升级 ✅**：照片页月份/拍摄者双维度筛选 + 精选专辑（admin ⭐ 收藏月份）。
- **TOTP 两步验证 ✅**：账号设置自助开通（扫码/密钥），登录两步动态码；
  admin 重置成员密码同步清除 TOTP；WebDAV 维持仅密码（取舍记录于 SECURITY.md）。
- **PostgreSQL 切换 ✅**：生产库 PostgreSQL 16（Zonky 二进制，`C:\pginstall`，
  数据 `C:\pginstall\data`，仅本机 127.0.0.1:5433——5432 被既有 EDB 17.4 实例占用）；
  原生 Windows 服务 `lan-share-postgresql`（`pg_ctl register`，NetworkService，自启）；
  `DATABASE_URL` 配在 lan-share-api 服务环境变量（NSSM AppEnvironmentExtra）；
  2026-08-29 深夜已完成 317 行全量迁移并切流（API/WebDAV/公网 FRP 全链路验证通过，
  浏览器双视口实测通过）。不设 DATABASE_URL 自动回退 SQLite（测试默认）。
- **每日备份 ✅**（v2 决策推翻落地）：`backup.bat` = pg_dump -Fc + robocopy 增量，
  默认目标 `C:\lan-share-backup`（本机无 D 盘，同盘备份防误删/库损坏，盘故障防护
  仍待第二块盘），schtasks 每日 03:30（任务名 lan-share-backup），已实测 BACKUP OK。

## 已交付

### M0 工程地基 ✅
- `server.py`（1799 行单文件）拆包为 `app/`：`config / db / security / filetools /
  api/{auth,files,share,webdav,system,admin}`；`main.py` 组装保持 v2 路由注册顺序；
  `server.py` 变为兼容 shim，NSSM 入口 `server:app` 不变。
- pytest 基线 25 项 + 多用户权限 18 项 = **43 项测试**；`check.bat` 本地门禁
  （批处理文件必须纯 ASCII——cmd 按 GBK 解析批处理，UTF-8 中文注释会被撕碎）。

### M1 数据库 ⚠️ 按环境裁剪
- **PostgreSQL 未安装于本机**，已按"双引擎准备"落地：全部 SQL 参数化、
  schema 迁移集中 `_migrate_v3()`（幂等 ALTER + 新表）。安装 PG 后把
  `DATABASE_URL` 接入 `app/db.py` 即切换（repository 收拢留待切换时一并做）。
- 备份：`backup.bat`（SQLite 在线备份 + robocopy 增量到第二块盘，排除
  .chunks/.zips/tmp；含 schtasks 注册注释）。**D 盘不存在**，运行前先设
  `BACKUP_DEST` 环境变量。

### M2 前端 ✅ 全站
- 旧 HTML/JS 四页已删除（git 历史可回滚），`/` 即 Vue SPA；
- 七个视图：文件（网格/列表/搜索/最近/多选/操作菜单/灯箱预览/上传队列）、
  照片、回收站、分享管理、管理面板、分享落地页、手机直传页；
- 主题三选（浅/深/跟随系统）本地记忆；深色模式完整；
- 移动端专项：行布局两行化、触屏操作常显、安全区适配、顶栏紧凑化；
- `web/` Vite + Vue 3 + TypeScript：登录/目录浏览/面包屑/上传/新建文件夹，
  角色显示。`npm run build` → `web/dist`；`app/main.py` 探测后挂 `/app`
  （旧四页原样保留，灰度并存）。CSP 仍 `script-src 'self'`。
- 环境注意：本机 Node 22 的 `fs.cpSync` **recursive + 中文路径会静默 exit 127**
  （单文件/纯 ASCII 路径正常），因此不需要部署复制步骤，服务直接读
  `web/dist`。

### M3 多用户 ✅ 核心
- 角色 admin/member/guest；私人空间 `private:/shared:` 虚拟路径（裸路径=shared，
  兼容 v2 客户端与旧 UI）；`private/` 别名段永远重定向本人空间（无任何寻址
  方式触达他人私人目录，admin 亦然——E8 显式管理动作暂缺）。
- 目录授权 grants（按路径段前缀命中、过期失效、大小写归一）；邀请制建号
  （一次性 code 原子认领、保留词表、密码 ≥8）；配额（private 逻辑字节，
  上传/DAV/分块收尾全路径校验）；停用账号会话即时失效（Cookie + WebDAV Basic）。
- 全部文件入口过 `resolve()`；search/recent/trash/photos 按身份过滤；
  `/api/disk/detail` 仅 admin；跨空间移动拦截（E9）；目录改名/移动同步 grants（E6）。
- 直传 `incoming/` 按用户分桶：写入落 `incoming/<username>/`，成员只读本人子树。

### M4 团队体验 ✅ 大部分
- Vue 管理面板（成员/邀请/授权/动态流）、家庭动态流、分享管理页（含取件
  记录）均已上线；通知 bot 与共享相册升级未做。

### M5 可靠性 ⚠️ 部分
- `/healthz`（免认证：DB/数据目录/磁盘水位）；`backup.bat`；测试即回归网。
- **未做**：第二块盘实际接入（本机无 D 盘）、恢复演练、水位告警推送。

## 对抗式审查记录（本轮）

第一轮修复 3 项：
1. 🔴 `move` 源路径要求写权（此前只有读权的成员可移走共享文件）。
2. 🔴 `move_items` 目录移动同步 grants.path（E6 此前只覆盖 rename）。
3. 🔴 WebDAV Basic 认证未查 `is_active`（停用账号仍可挂载）。

第二轮（授权/邀请/配额攻击面走查）确认无恙：授权大小写归一、按段前缀、
邀请码原子认领、保留用户名、`..`/反斜杠/点前缀拒绝、跨空间拦截。

**已知残余风险（家庭场景接受，记录在案）：**
- zip 一次性 URL 未绑定用户（票据持有者均可在 TTL 内下载，链接不外泄即无风险）。
- `/healthz` 免认证暴露磁盘水位（公网可达）。
- WebDAV/根列表可见 `private`、`incoming/<用户名>` 目录名（仅名字，无内容）。

### 生产部署与线上验证 ✅（2026-08-29 深夜）
- 第四轮审查补刀：**server.py shim 忘了 `__main__` uvicorn 启动块**——服务
  重启后秒退（NSSM 显示 PAUSED、502），补上后恢复；
- 迁移补 `audit_log.username` 列（/api/activity 曾 500）；debug 账号提为 admin；
- 线上验证通过：/healthz ok（DB/数据目录/磁盘 83.5%）、旧页 200、/app 200、
  登录/me/文件列表/disk/activity/admin users 全通。
- 已知限制：audit 存量行无 username（动态流暂不显示"谁"，M4 补上下文写入）。

### M2 全站切换 ✅（同夜完成）
- 旧 HTML/JS 四页已删除（git 历史可回滚），`/` 即 Vue SPA；
- SPA 功能面：登录+邀请 redeem、文件（搜索/最近/多选批量/移动/分享/打包/
  分块续传+秒传/缩略图）、照片时间线、回收站、管理面板（成员/邀请/授权/动态）、
  分享落地页、手机直传页；深链回退 /photos /trash /admin → SPA；
- 设计：玻璃拟态 tokens、深色模式、reduced-motion、移动端断点；
- web/dist 缺失时自动回退旧页渲染逻辑仍保留（文件已删，仅作保底）。

## 部署

```
net stop lan-share-caddy && net stop lan-share-api
net start lan-share-api && net start lan-share-caddy
```
首次重启会自动执行 `_migrate_v3()`（幂等；首个用户自动晋升 admin）。
新入口：`/app`（Vue 新版）、`/healthz`。前端更新：`cd web && npm run build`，
无需重启。

## 2026-08-30（晚）追加

- 分享复制拆分：链接 / 公网链接 / 提取码三个独立按钮
- 分享落地页失效状态友好化（区分已撤销 / 已过期 + 指引）

## 2026-08-30 第六/七轮前端审查 + 弹窗 Vue 化

- 15 处原生 confirm/prompt 清零（Promise 化 Vue 对话框系统）
- 列表视图紧凑化、私人空间专属视觉、根目录面包屑隐藏
- 手机文件行单行化（按 .file-row 与其他页两行化规则区分作用域）

## 2026-08-30 第五轮全面代码审查

- 🔴 私人空间物理路径二次拼接修复（虚拟/物理双形态统一解析）+ 回归测试
- 全库模式扫描通过（XSS/路径拼接/危险函数/resolve 全覆盖）
- 备忘：guest 照片列表 N+1 小查询（家用无感）

## 2026-08-30 追加（迭代批次）

- 分享链接管理页（/shares）：状态徽章/进度条/筛选/取件记录/撤销；
  🔴 /api/shares 越权修复（owner_id 按创建者隔离）
- 双地址分享（局域网/公网，公网地址在管理面板配置）+ 手机直传双二维码
- 🔴 分享公开访问 401 误弹修复（未登录直开 /s/ 实测正常取件）
- 🔴 下载管理器重复计数修复（按票据去重）+ 🔴 中文文件名 500 修复
- 成员账号管理：管理员改成员用户名（私人目录同步）/重置密码（全端登出）
- 授权批量矩阵：成员多选 × 目录多选（目录下拉自动浏览共享树）、
  按成员筛选、有效期（永久/1/7/30 天）与到期显示
- 手机端专项：行布局两行化、触屏操作常显、iOS 安全区；取件记录 NaN 修复
- 主题三选开关；账号设置弹窗回归（改用户名/改密码）；顶栏品牌回主页；
  存储详情弹窗恢复（成员看自己配额、admin 看目录构成）
- 私人空间入口：可进入（自动建目录）、根目录防误删、显示名『🔒 私人空间』

## 2026-08-29 深夜 II 追加（全站切换 + 审查修复）

- Vue 全站切换（旧页删除）；对抗式审查修复：分享公开访问 401 误弹、
  中文文件名 500、下载管理器重复计数、二维码 500、/api/shares 越权、
  登录不跳转、取件记录 NaN、卡片长名溢出、触屏粘滞 hover 等
- 双地址分享（公网地址设置于管理面板，已配 your-frp-domain.com:xxxxx）
- 生产服务全程在线验证（healthz / 各页面 / 分享公开访问登出态实测）

## 2026-08-31（第三天）：大文件上传体系 + 预览精修 + 公网安全加固

### 大文件上传优化（v3.6 上线）
- **自适应分片**（`chunk_size_for()` 纯函数，init/state/chunk/finish 四处一致）：
  ≤2GB→5MB / ≤5GB→16MB / ≤10GB→32MB / 更大→64MB；10GB 请求从 2048 降到 320
- **WebDAV PUT 上限 2GB→20GB**（流式写盘，PC 互传挂盘可拖大文件）；
  分片路径硬上限 64GB（单请求 `/api/upload` 维持 2GB 不变）
- **≥256MB 并发 3 路**上传 + 进度条 XHR `upload.onprogress`（fetch 不支持
  上传进度）平滑化 + 实时速度与剩余时间

### 上传前端重做（队列化）
- `uploader.ts` 引擎 + `UploadQueue.vue` 组件（主页/直传页共用）：
  浮动卡片，汇总 N 个 · 总进度%；整体进度条 + 状态色 + 条纹流动动画
- **取消 / 重试 / 暂停 / 恢复**逐行按钮；完成 2.5s 自动消失
- **同名覆盖保护**：前端弹窗列明（"将覆盖，旧版本进回收站"）；后端三条
  上传路径（分片 finish / 秒传 / 单请求）补齐覆盖进回收站——修复 SECURITY.md
  既定声明的存量缺口（旧版本被静默丢失）
- **公网 ≥2GB 弹窗提醒**（2/5/10GB 三档递进，按上行 30Mbps 估算耗时 + 建议
  回局域网），局域网不打扰
- **拖拽上传 / 粘贴上传 / 整批入队**（截图自动命名 `截图-时间戳.png`）；
  全屏拖拽覆盖层 + dragover 心跳兜底；上传中 beforeunload 拦截

### 暂停/续传 + 系统通知
- **暂停/恢复**：AbordController 贯穿；琥珀色"已暂停·可续传"态
- **跨刷新断点续传**：会话号持久化 localStorage（key=`dir|name|size|mtime`，
  3 天 TTL 与服务端对齐）；重选同一文件自动从已收位置继续；队列空时显示
  "未传完 N 个"提示条
- **速度曲线 sparkline**（滚动 60s 窗口，无依赖）+ 当前速度数字
- **系统完成通知**：批次完成且后台 / >15s 时发系统通知（含失败计数）

### 对抗性审查修复（上传 9 项：1 🔴 / 4 🟠 / 4 🟢）
- 🐛 并发分片任一失败后其余 worker 不再后台空跑（内部 AbortController 联动）
- 🐛 取消/丢弃暂停项时同步清 localStorage 续传记录
- 🐛 回调随 item 走（`uopts`）——全局泵不再串用旧批次回调
- 🐛 合并校验阶段隐藏暂停/取消按钮（防状态与落盘脱节）
- 🟢 sparkline 颜色改实色、删死变量、空闲清速度采样残影、finish 带 signal
- 🐛 顺带修复分片中断健壮性缺陷：客户端中止在途分片时，旧版本会留下截断
  半截文件，续传按"文件存在=完整"误信导致 finish Size mismatch——
  现用 `_tmp_*` 临时文件原子落盘 + 整块尺寸校验，中断不残留

### 预览精修 + UI 修复
- 🐛 多图预览布局撑破（用户实测复现：照片/同学&生活&学校 253 张图，预览
  壳被胶片条 min-content 撑到 1820px，图片整体偏右 + 右箭头定位出屏）——
  修：`.fv-shell`/`.fv-strip` 加 `min-width: 0`，胶片条超宽部分内部横向滚动
- 🐛 预览触屏居中修复：stage 只绑 `touchend` 没绑 `touchcancel`，手势被
  系统打断时 `pullY` 滞留，`resetZoom` 也不清，导致图片持续偏下+变暗——
  补 `@touchcancel` + resetZoom 清零
- 🐛 共享 CSS 收窄误伤：上传队列样式重构时 `.bar` 全局基础样式被收窄成
  `.upl .bar`，导致导航栏存储徽标/DiskModal/SharesView 进度条高度归 0
  不可见——恢复全局块
- 🐛 私人空间无法返回主页：`private` 别名自指（返回按钮算出的父级又映射
  回本人私人根）；修：父级为 private 回主页，面包屑折叠为
  🏠 / 🔒 私人空间 / 子目录
- 🐛 手机列表模式长文件名按钮出屏：`.frow` grid item min-width:auto 被
  nowrap 长名 min-content 顶破（849px/视口 360）——`minmax(0,1fr)` +
  `min-width:0`，名字省略号生效
- 🟢 返回按钮语义定稿为"返回上一级"（回主页走面包屑 🏠）
- 🟢 操作按钮全端常显（桌面端去掉 hover 才显示，与触屏统一）
- **滚动条主题化**：深色自定义（淡靛蓝拇指 + 深藏青轨道），浅色保持原生；
  踩坑：fluent 悬浮滚动条（Chrome/Edge 121+）无视 webkit 伪元素、部分跟随
  OS 主题，须标准属性 `scrollbar-color` 上色，transparent 轨道可能回落系统白
- 缩略图懒加载体验：微光占位 + 完成淡入 + 失败自动重试一次（实测 253 张
  0 失败，"不显示"为 lazy 占位观感 + 冷缓存生成时延）

### 对抗性审查第二批（清洗缺口 + 健壮性）
- 🔴 **clean_filename 漏洗 Windows 非法字符 `? * " < > |`**——手机端
  （Linux FS 允许）传来的此类文件名，三条上传路径落盘即 OSError 500
  （对抗性实弹构造文件名复现）。补全字符集 + 回归测试
- 🐛 同类缺口：WebDAV MKCOL/PUT/MOVE/COPY 路径未清洗——dav_handler 入口
  与 _dav_destination 逐段 clean_filename（与界面 mkdir/rename 同规则）
- 🟠 分块 XHR 加 120s 超时（半开连接不再卡死并发 worker）
- 🟠 thumbOf/download 的 encodeURI 不编码 `# ? %`——改逐段 encodeURIComponent
- 🟢 sparkline 配色接 `--accent` 主题变量；公网提醒弹窗 LAN IP 纠正 (.12)

### 公网防护审计 + 入口隔离加固
- **审计结论**：认证/防爆破/限速/令牌熵/安全头/路径安全均在位；两处 🟠 弱点
  ① 锁定按用户名全局计数——公网攻击者 5 次错密可远程锁死全家（DoS）；
  ② FRP TCP 隧道使公网流量 IP 恒为 127.0.0.1，公网用户共享限速桶/审计盲
- **加固**：`is_public_entry()`（Host 与管理面板公网地址 netloc 比对，60s 缓存
  + 改地址即时失效）→ 防爆破按入口分账（公网侧只锁公网侧，LAN 永不波及）；
  登录限速分桶（`login@pub`）；`/healthz` 公网入口 404（磁盘水位不外泄）；
  登录 API + TOTP + WebDAV 三处全部接入
- **结构性限制记录入 SECURITY.md**：自签证书（樱花自动证书亦为私有 CA，
  访客需点告警）；TCP 隧道拿不到访客真实 IP——根除需自有域名（樱花支持绑定）

### 配套测试与质量
- 新增 `tests/test_largefile.py` 10 项（自适应分片 / 64GB 拒绝 / 末块尺寸
  / 覆盖进回收站 / 整块尺寸校验）+ `tests/test_public_guard.py` 4 项（healthz
  拦截 / 公网侧锁定 / LAN 侧不受波及 / LAN 成功不解除公网侧锁定）
- 全量 **70 项 pytest 全绿**（含 v3.5 回归 5 项 + 多用户权限 18 项 + 基线
  36 项 + 大文件/清洗 11 项 + 公网防护 4 项）
- 浏览器实测覆盖：2.5GB 真实文件 API E2E（160 块 16MB）、上传队列拖拽/取消/
  暂停/恢复/重试/同名覆盖/系统通知、253 张多图预览双箭头与居中、跨刷新续传
  21%→38%→落盘、真实 FRP 公网 healthz 404

## 2026-09-01（第四天）— 手机 PWA + 移动 App 规划定稿

- **PWA 阶段 0 上线**：manifest + Service Worker（资产缓存优先/导航网络优先/
  API 绝不缓存）+ 渐变 L 图标三尺寸；main.py SPA 回退直供 dist 真实文件
  （MIME + 穿越守卫，manifest/sw/icons 免登录）；真机小米"添加到主屏幕"
  已验证。仅安全上下文注册，自签环境静默降级
- **移动 App 需求规格 + 技术设计方案成文**（`docs/DESIGN-v3/APP-REQUIREMENTS.md`、
  `APP-DESIGN.md`）：Flutter 跨端选型定稿（用户决策：未来 iPhone + 上架路径）；
  FR-1~9 带 P0-P2 验收点；自动备份默认仅照片、视频开关；后台执行职责拆分
  （原生管"何时醒"、Dart 管传输）；自签证书 SPKI pinning；后端前置 =
  `/api/auth/token` 设备 Token 三端点（M-A0 待启动）
- **PLAN-v3 §7 远景规划入档**：自有域名 + CF Tunnel 试点制（含代价与混合
  策略）、App 三阶段、数据安全线等物理盘

## 路线图当前状态（v3 家庭团队版）

| 里程碑 | 状态 | 关键交付 |
|--------|------|---------|
| M0 工程地基 | ✅ | `server.py` → `app/` 拆包；兼容 shim；`check.bat` |
| M1 数据层 | ✅ | **PostgreSQL 16 生产库**（5433 Zonky + icacls）；双引擎 `app/db.py`；317 行迁移；SQLite 仅测试 |
| M2 前端 | ✅ | Vue3+Vite+TS SPA；旧 HTML/JS 删除；CSP；深色模式 |
| M3 多用户 | ✅ | 三角色 + private/shared 虚拟路径 + grants + 邀请制 + 配额 + 停用踢出 |
| M4 团队体验 | ✅ | 定向分享 / 站内通知 / 动态流 / 共享相册双维度 / 大文件上传 / 暂停续传 / 移动 PWA（通知 bot 已随 M6 事件推送 Webhook 落地） |
| M5 可靠性 | ✅ | 每日 pg_dump + 2 天保留（23:30 + 错过开机补跑）；公网入口隔离（防爆破分账/healthz 404/限速分桶）；第二块备份盘与恢复演练挂账 |
| M6 增量 | ✅ | HEIC/HEIF 缩略图与预览（pillow-heif，EXIF 方向矫正）；WebDAV 独立密码；事件推送 Webhook（GET 模板通吃 ntfy/Bark/Server酱/Telegram）；admin 成员视角预览（X-As-User 只读） |
| M-A0 App 契约 | ✅ | 设备 Token 三端点（`/api/auth/token` 明文仅返一次 / 同名设备轮换）+ Bearer 中间件（90 天滑动 / 停用即失效）；与 Cookie 会话并存；5 项测试 |
| M-A1 App 只读 | ✅ | Flutter 骨架（mobile/）+ 照片墙（无限滚动 / 全屏 / 缩放）/ 文件浏览（面包屑栈导航）/ 我的（设备管理 + 吊销）；自签证书 TOFU 信任流（指纹确认 + SPKI pinning 留 Keystore）；**安卓模拟器端到端验证通过**；debug APK 经自家分享链接分发；dart analyze 零问题 |
| M-A2 手动传输 | ✅ | 上传（分片续传+秒传）/ 下载（Range 断点）/ 传输中心；500MB 杀进程续传验收通过（模拟器 E2E）；顺带修复：杀进程掉登录（SecureStore 双写）、文件页永不加载、同目录重传秒传失效 |
| M-A3 自动备份 | ✅ | WorkManager 周期(15min,仅Wi-Fi+仅充电默认开)+ContentObserver 即时触发（自写 Kotlin 薄桥，不引第三方后台插件）；sqflite 账本 diff 去重；后台 isolate 备份引擎（init→分片→finish 全协议+断点续传+秒传）；>10min 前台服务通知保护（dataSync）；公网地址默认跳过；完成通知可关；备份设置页+账本页。E2E：60 张全自动备份 sha 一致；250MB 杀进程续传 part-mtime 证明零重传；重启手机后 JobScheduler 持久恢复 |
| M-A4 分享与发布 | ✅ | 系统分享接收（SEND/SEND_MULTIPLE → Kotlin 拷落地 cache/share_inbox → Dart 拉取/推送双向容错 → 复用传输引擎上传 incoming/<用户>/分享暂存，文本落 txt，传完删副本）；分享管理（列表/创建 1-30 天+提取码+上限+定向成员/撤销/多地址 局域网·公网 复制链接）；零服务端改动；E2E 三接收路径字节一致+创建撤销闭环；加固版 APK 已传 App安装包（sha256 一致） |
| M-A5 登录与连接体验 | ✅ | 登录页方向 A（品牌区+浮层卡片，服务器地址降级状态条+bottom sheet）；连接预检（任意状态码=在线/自签放行）；生物识别一键登录（凭据仅 Keystore，主动退出清档，FlutterFragmentActivity）；配对码免密登录（/api/pair/code+claim，180s 一次性+IP 限速，与 /api/auth/token 共用签发内核，Web 端出码+地址二维码）；扫码填地址（mobile_scanner）；六格验证码/记住用户名/密码可见性/shake；模拟器端到端全通过（配对码免密真连） |
| CF Tunnel | ⏳ | 自有域名 + Cloudflare Tunnel 试点（待域名到位） |
