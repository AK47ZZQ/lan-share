> 更早版本（v0.4.1 ~ v0.20.0+60，2026-08-20 ~ 09-09，共 103 条）的完整变更史
> 见 [CHANGELOG-archive-v0.4-v0.20.md](CHANGELOG-archive-v0.4-v0.20.md)；
> 里程碑视角浓缩史见 README「演进」与 项目记忆/02-里程碑。

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

