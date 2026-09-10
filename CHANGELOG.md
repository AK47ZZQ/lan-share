> 更早版本（v0.4.1 ~ v0.20.0+60，2026-08-20 ~ 09-09，共 103 条）的完整变更史
> 见 [CHANGELOG-archive-v0.4-v0.20.md](CHANGELOG-archive-v0.4-v0.20.md)；
> 里程碑视角浓缩史见 README「演进」与 项目记忆/02-里程碑。

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

