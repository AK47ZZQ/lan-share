# 更新历史索引

> 由 `scripts/changelog_index.py` 自动生成，**别手改**。改 CHANGELOG 之后重跑一次即可（`release.py` 收尾会自动调）。
>
> **要细节就去「位置」列那个文件里搜版本号**（`grep -n -A 30 "4.1.10+150" CHANGELOG.md`）。
> 这些文件合计 426KB / 188 条 —— 整份读进来就吃掉上下文，别干这事。

**188 条**（App 94 / 服务端 75 / 混合 19）· 最新 2026-11-19 · 最早 2026-09-01 · 来源 3 个文件

| 日期 | 线 | 版本 | 标题 | 位置 |
|---|---|---|---|---|
| 2026-11-19 | App | 4.1.11+151 | 更新历史补拉归档分片 | `CHANGELOG.md` |
| 2026-11-19 | App | 4.1.10+150 | 收起 WebDAV 入口 | `CHANGELOG.md` |
| 2026-11-18 | 服务端 | 4.0.17 | 公网域名栏目（Cloudflare Tunnel） | `CHANGELOG.md` |
| 2026-11-18 | 服务端 | 4.0.19 | 放行分片端点的 chunked 编码（CF 隧道下上传才可用） | `CHANGELOG.md` |
| 2026-11-18 | 服务端 | 4.0.18 | 公网访问面板的三处补完 | `CHANGELOG.md` |
| 2026-11-18 | 服务端 | 4.0.17 | 三路 review + 三轮对抗的修复 | `CHANGELOG.md` |
| 2026-11-18 | App | 4.1.9+149 | 分片下载/上传（大文件走 CF 隧道快 11 倍） | `CHANGELOG.md` |
| 2026-11-18 | App | 4.1.8+148 | 更新下载不再误走 GitHub | `CHANGELOG.md` |
| 2026-11-18 | App | 4.1.7+147 | 迁移到公网域名时的两处修正 | `CHANGELOG.md` |
| 2026-11-17 | 服务端 | 4.0.16 | 十轮审查修复（两条越权 + 整库外泄口 + 事务回滚） | `CHANGELOG.md` |
| 2026-11-16 | App | 4.1.2 | 掉线不再静默 | `CHANGELOG.md` |
| 2026-11-16 | 服务端 | 4.0.15 | 401 分型诊断 + LAN 候选只发真网卡 | `CHANGELOG.md` |
| 2026-11-09 | 服务端 | 4.0.14 | 配额口径统一 + 改名联动 + 搜索快 26 倍 | `CHANGELOG.md` |
| 2026-11-08 | 服务端 | 4.0.13 | 修一键更新「装完还是旧版」 | `CHANGELOG.md` |
| 2026-11-08 | 服务端 | 4.0.12 | 更新记录入口 + 性能 3.7 倍 + 证书链路修复 | `CHANGELOG.md` |
| 2026-11-08 | App | 4.1.1 | 修更新记录的三个实机问题 | `CHANGELOG.md` |
| 2026-11-08 | App | 4.1.0 | 更新记录页 + 关于页精简 | `CHANGELOG.md` |
| 2026-11-07 | 服务端 | 4.0.11 | 自主值守批（8 个修复 + 质量基建） | `CHANGELOG.md` |
| 2026-11-07 | 服务端 | 3.0 | 许可证从 AGPL-3.0 改为 PolyForm Noncommercial 1.0.0 | `CHANGELOG.md` |
| 2026-11-07 | 服务端 | — | 反馈通道加固 + 依赖清单源码对账（服务端修复批，未发版） | `CHANGELOG.md` |
| 2026-11-07 | 服务端 | 4.0.10 | httpx 没打进包，反馈提交一直 500 | `CHANGELOG.md` |
| 2026-11-06 | 服务端 | 4.0.9 | 反馈三端入口 + 公开到 GitHub | `CHANGELOG.md` |
| 2026-11-06 | 服务端 | 4.0.8 | 连接服务器列表里的僵尸地址 | `CHANGELOG.md` |
| 2026-11-05 | 服务端 | 4.0.7 | 对抗修复批（托盘看护 / 配置守卫 / 更新链路加固） | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 4.0.6 | 升级会把公网域名抹掉 | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 4.0.5 | 一键更新的 40 秒黑洞 | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 4.0.4 | Caddy 只听 8765，别人的隧道指 8767 | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 4.0.3 | 公网接入的自检原来是假的 | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 4.0.2 | P3 收尾（正确性 + 性能） | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 4.0.1 | 修 4.0.0 引入的 ACL bug | `CHANGELOG.md` |
| 2026-11-04 | 混合 | 4.0.0 | 一次彻底审查（服务端 + 安装器 + App） | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 3.100 | 安装器认升级 + 托盘一键更新 | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 3.99 | 一键更新（点一下就不用管了） | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 3.98 | 安装器认得出「这是一次升级」 | `CHANGELOG.md` |
| 2026-11-04 | 服务端 | 3.97 | 安全与数据安全修复 ×6**（自主值守批） | `CHANGELOG.md` |
| 2026-11-04 | 混合 | 3.96 | 自定义更新地址（可加多条，局域网优先） | `CHANGELOG.md` |
| 2026-11-03 | 服务端 | 3.95 | 数据库搬出用户文件区 + 重启服务不再漏掉 PG | `CHANGELOG.md` |
| 2026-11-03 | 服务端 | 3.94 | 公网访问一键接入（内网穿透） | `CHANGELOG.md` |
| 2026-11-03 | 服务端 | 3.93 | 端口枚举看不到 ≠ 服务没在跑 | `CHANGELOG.md` |
| 2026-11-02 | 混合 | 3.92 | 别拿「公用网络」和「手打 https」拦人 | `CHANGELOG.md` |
| 2026-11-02 | 服务端 | 3.91 | 局域网明明开着，界面偏说没开 | `CHANGELOG.md` |
| 2026-11-02 | 服务端 | 3.90 | 重装换过数据目录后 PG 永远起不来 | `CHANGELOG.md` |
| 2026-11-02 | 服务端 | 3.89 | 装完注册 403、备份找不到工具、面板假死 | `CHANGELOG.md` |
| 2026-11-02 | 服务端 | 3.88 | 白屏真因是 /assets 403、面板卡死、装完报 740 | `CHANGELOG.md` |
| 2026-11-02 | 服务端 | 3.87 | 装完打不开、服务控制失败、新用户无从登录 | `CHANGELOG.md` |
| 2026-11-01 | 服务端 | 3.86 | 托盘没编译进包、卸载删不掉数据 | `CHANGELOG.md` |
| 2026-11-01 | 服务端 | 3.84 | GUI 点开关没反应、中文乱码、打开就卡 | `CHANGELOG.md` |
| 2026-11-01 | 服务端 | 3.83 | 装到 D 盘却写 C 盘、卸载崩掉半途而废 | `CHANGELOG.md` |
| 2026-11-01 | 服务端 | 3.82 | 证书没签给 127.0.0.1、开局域网卡 UAC、检查更新报 SSL | `CHANGELOG.md` |
| 2026-11-01 | 服务端 | 3.80 | 修安装器崩溃、更新镜像落地、发版脚本加固 | `CHANGELOG.md` |
| 2026-11-01 | 服务端 | — | 服务端成为更新镜像源 | `CHANGELOG.md` |
| 2026-11-01 | App | 1.1.23 | 关于页去掉内网 IP、更新历史能滑到底、加 VPN 提示 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-11-01 | App | 3.79 | 修 GUI 检查更新、说明精简、历史可展开 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-11-01 | App | 3.78 | 发版固化、登录页不再卡、反馈脱敏、历史改走 GitHub | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-11-01 | App | 3.77 | 真机反馈两连修：VPN 下检测不到 + 下载太慢 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-11-01 | App | 3.77 | 更新源统一到 GitHub：修掉「检查更新没反应」 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-11-01 | 服务端 | 3.73 | 托盘没进包、控制面板换 WebView2、证书信任 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-10-31 | 服务端 | — | 修掉 6 个问题 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-10-31 | 服务端 | — | 让程序自己装自己 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-10-30 | App | 1.1.17+124 | 双更新源 + 登录页不再预填地址 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-10-30 | App | 1.1.16+123 | 依赖维护版（零功能改动） | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-10-24 | App | 1.1.15+122 | 复测基线版（零功能改动） | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-10-24 | 服务端 | 3.70 | 观测跨重启存活 + 预发环境 + 巡检 v2 + 依赖安全升级 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-10-24 | 服务端 | 3.69.1 | 修复「局域网看照片详情失败」+ 首次 Tier 2 恢复演练 + 巡检重建 | `docs/changelog/CHANGELOG-archive-2026Q4.md` |
| 2026-09-28 | 混合 | 3.69 | 取消上传即释放 + 限速观测收口 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-28 | 混合 | 3.68 | 省电轮询 + 资源闸收尾 + 待办勾选人回填 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-28 | 服务端 | 3.67.5 | 后台循环防 GC + sweep 注册去重（可靠性） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-27 | 服务端 | 3.67.4 | 8.3 短名越权（P0）+ 上传收口加固 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-27 | 服务端 | 3.67.3 | 访问日志迁出共享数据区（隐私修复） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-27 | 服务端 | 3.67.2 | 回收站加固 + 审计取证保留 + dav/media 限速（安全收口） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-27 | 服务端 | 3.67.1 | 回收站保留期时钟修复（P0 数据安全） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-25 | 混合 | 3.67 | 点单 1/3/4/5 全交付（三轮审查收口） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-25 | App | 1.1.11+116 | 待办「开始专注」直达 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-25 | 服务端 | 3.66 | .trash` 无主残留对账（D5 残余风险先收"可见性"） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-25 | 服务端 | 3.65 | M2 番茄钟服务端切片（效率舱主线） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-25 | App | 1.1.10+114 | 番茄钟 App 端随 v3.65 一起发 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-25 | 混合 | 1.1.9+113 | 局域网入口自动再发现（回应反馈 b8b3） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-25 | 服务端 | 3.63 | 01 上线）：修复迁移批遗留的每日备份断链（P1） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-25 | 服务端 | 3.62 | 分享提取码哈希升级为 scrypt（与登录口令同源） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-21 | 混合 | 1.1.8+112 | 随服务端回收站修复一起出包 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-21 | 服务端 | 3.61 | 36**）：回收站并发"恰好一次" + 依赖审计处置 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-20 | 服务端 | 3.60 | 21）：白天自主复审 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-20 | 服务端 | 3.59 | 深夜自主复审（6 项对抗修复，界面无变化） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-19 | App | 1.1.7+111 | 审查加固版（6 轮审查 + 2 轮对抗性审查） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-19 | App | 1.1.6+110 | 时间线无缝上滑 + 日历视图升级（反馈 20260919162717-5d2d / 202… | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-19 | App | 1.1.5+109 | 时间线无缝续流 + 年份分组轴 + 效率舱日历（反馈 0471/ddb0/7e15/cfd… | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-19 | App | 1.1.4+108 | 照片墙时间线整体重构 + 提示条自动消失修复 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-19 | App | 1.1.3+107 | 照片墙跳月真机根修 + 待办分组修复 + 多选重命名（反馈 20260919121012-… | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-19 | App | 1.1.2+106 | 修复照片墙时间线跳转（反馈 20260919004417-df72） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-19 | App | 1.1.1+105 | 修复 1.1.0 更新包未带新界面（反馈 20260918181005-df91） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-19 | 服务端 | 3.58 | 照片墙打开慢的两处根修（反馈 20260917234058-05a0） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-18 | 服务端 | 3.56 | 双引擎 SQL 可移植性 + 后台任务可见性（架构复查批） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-18 | 服务端 | 3.57 | 在途状态外置 + 读通道门控统一（架构复查批 2） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-17 | 混合 | 1.1.0+104 | 效率舱首秀（反馈 20260917224102-1a63） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-14 | 混合 | 1.0.3+103 | 照片墙性能根修（反馈 31cd） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-14 | App | 1.0.2+102 | 照片墙体验批（4 条反馈全量处理） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-14 | App | 1.0.1+101 | 反馈闭环完善（7 条积压反馈全量处理） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-13 | App | 1.0.0+100 | 首个正式版（三 bug 根修 + 全量终审） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-13 | App | 0.28.10+84 | 反馈三连修复（浅色 SnackBar / 更新历史排序 / 分类抽屉点空白） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-13 | 服务端 | — | 备份验证钉死 public.settings（测试 schema 残留假阳性，P1） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-13 | App | 0.28.9+83 | 文件页缩略图请求噪声治理 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-13 | 混合 | — | /preview 显式支持 HEAD（App 大小预检首次生效） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-13 | App | 0.28.8+82 | SSE 连接累积修复（429 风暴根因） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-13 | 混合 | 0.28.7+81 | bug·a6df 新建笔记卡死根修 + 服务端相册双重编码修复 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-12 | 服务端 | 3.53 | 兜底备份内联验证（restore drill） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-12 | App | 0.28.6+80 | bug·0663 修复分发 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-12 | 服务端 | 3.52 | 服务端 v3.52（存储层/SSE 实时通知/全文搜索/备份与指标/反馈闭环） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 混合 | 3.51 | App 批次 B + 大扫除 / 服务端 v3.51 / Web 测试护栏 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | App | 0.28.3+77 | 结构整改批次 A + 视觉规范 v1.0 发版（纯加固，无新功能） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | App | 0.28.3+77 | App 整改批次 A 复查（4 轮 review + 3 轮对抗性审查，随 v0.28.3… | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | App | 0.28.3+77 | App 结构与护栏整改（批次 A，随 v0.28.3+77 发版） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | App | 1.0 | 设计令牌 + 响应式适配（随 v0.28.3+77 发版） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | App | 0.28.2+76 | 修分类侧边栏透明叠影（反馈 7063） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 服务端 | 3.49 | AdminView 按 tab 拆分（纯重构） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 服务端 | 3.48 | api.ts 按域拆模块（门面保兼容，纯重构） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 服务端 | 3.47 | 操作审计按类型筛选（/api/admin/audit + 运行状态页） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 服务端 | 3.46 | 总览页 45s 自动轮询（页签不可见即暂停） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 服务端 | 3.45 | admin 全量分享一览页（/share-ops） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 服务端 | 3.43 | auth.py 按域拆路由（纯重构，契约零变更） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 服务端 | 3.42 | 反馈看板（目录即状态，定位零通读） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | App | 0.28.1+75 | 反馈闭环首次实战（修历史报错 + 管理分区提前） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 混合 | 3.41 | 反馈通道（想法/Bug 直达开发端） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | App | 0.27.1+73 | 文件页多选新增"复制路径" | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-11 | 服务端 | 3.40 | 通知管理（公告+站内通知）+ 会话治理增强 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | 混合 | 3.39 | 产品定位更新——Web 后台化（App 主攻内容入口） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | App | 0.27.0+72 | 需求文件《改.txt》头脑风暴落地批 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | 混合 | 3.38 | 知识库/图谱整体下线 + 宫格溢出修复 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | 混合 | 3.37 | 笔记主功能化 & 知识库完善 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | 服务端 | 3.36 | 独立复审修复批（工程流程 + 生产等价性 + 架构收口） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | 服务端 | 3.35 | 安全加固与"约定收口"第一批 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | App | 0.24.0+69 | 知识库与图谱（双链/图谱/agent 三层，全端上线） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | App | 0.23.1+68 | 重构批三轮审查收口 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | App | 0.23.0+67 | 架构审查重构第二批（消循环 + 拆上帝模块 + 协议单实现） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | App | 0.22.0+66 | 架构审查重构第一批（P0 分片契约 + 事实源五件套） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-10 | 服务端 | 9.9 | 9.9 复审收尾批（P3 清尾 + 备份链路加固） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-09 | App | 0.21.4+65 | 改进建议 9.9 全量修复批 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-09 | App | 0.21.3+64 | 欢迎头色带根治（真机反馈第三轮） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-09 | App | 0.21.2+63 | 首页欢迎头减法瘦身（真机反馈第二轮） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-09 | App | 0.21.1+62 | 首页欢迎头热修（真机反馈） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-09 | App | 0.21.0+61 | 改进建议 9.6 批次（五项 P0 修复 + 四项方案落地） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-06 | App | 0.20.0+60 | 九项体验批次（首页/相册/推送/授权/笔记/二维码） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-06 | App | 0.19.0+59 | 文档预览兼容重构 + 富文本笔记 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-06 | App | 0.18.0+58 | 首页卡片自定义 + 预览音频/文本扩面 + 笔记增强 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-06 | App | 0.17.1+56 | v0.17.0 实测问题修复 + 双路资深 Review 审查闭环 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-06 | App | 0.17.0+55 | App 细节优化大版本（照片墙修复 + 备份体验 + 资料 + 预览 + 笔记） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-06 | App | 0.16.3+54 | 资深 Reviewer 三轮审查 + 对抗审查（安全大版本） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-06 | App | 0.16.2+53 | 全仓四维审查 2 轮 + 对抗审查（安全加固批次） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-06 | 服务端 | 0.16.1+52 | 开屏圆角回退 + 启动深浅色跟随设置 + 清空服务端备份 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-05 | App | 0.16.0+51 | 清理与本地文件管理大批次 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-05 | App | 0.15.2+50 | 自动切换实测修复提速 + 邀请码注册进 App | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-05 | App | 0.15.1+49 | 品牌开屏修复（"开屏 logo 未替换"根因） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-05 | App | 0.15.0+48 | 主页/我的页重设计 + 局域网/公网自动切换（纯 App 批次） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-05 | App | 0.14.1+47 | 备份白名单语义调整（一个不勾 = 全部不备份） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-05 | App | 0.14.0+46 | 精选专辑"文件夹式"重构 + 备份白名单 + 时间栏实时（照片页全面梳理批次） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-05 | App | 0.13.0+45 | 照片墙滑动根修 + 专辑共创体验 + 传输闭环（模拟器全交互实测批次） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-05 | App | 0.12.0+44 | App 文件页 Web 同款双布局 + 缩略图 + 点进预览 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-04 | App | 0.11.0+43 | 可运营 + 家庭共创大批次（A2/A4/A5/A6 + B1-B6） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.10.4+42 | 审查补丁三件（并发反转/照片墙截断/上传调度时序） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.10.3+41 | 弱网收尾四件套 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.10.2+40 | 深色通知框对比度 + 查看器美化 + 公网三件 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.10.1+39 | 公网弱网加固四件套 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.10.0+38 | 公网主/备双隧道 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.9.0+37 | 检查更新「更新历史」栏目 + 品牌收尾三件套（网页端/二维码） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.14+36 | 主页头部铺满适配状态栏 + 品牌资产全家桶（通知图标/favicon） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.11+33 | 云朵回原比例 + 半透明磨砂样式 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.9+31 | 启动图标对齐登录页品牌（圆角块 + 描边云） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.8+30 | 云朵图标缩小 + 更新描述恢复 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.7+29 | 主页字体对比度热修（v0.8.4 潜伏缺陷） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.5+27 | 主题引擎（6 色板 + Material You）+ 云朵品牌启动图标 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.4+26 | UI 重设计与美化（方向 A 全批一次做完） | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.2+24 | 二次亲审（5 轮逐行）补 3 项加固 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.1+23 | 6 轮 review + 对抗性审查——修管理面板删除失效等 5 个 P0 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.8.0+22 | 功能对齐网页版——管理面板 + 账号安全 + 打包下载 + 专辑 + 通知中心 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | App | 0.7.3+21 | 备份触发根修——手动立即备份不再被充电约束吞掉 + 全链路可观测 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-03 | 混合 | 0.7.2+20 | 照片墙实时更新扩面——脉搏轮询 + 强刷节流独立 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-02 | App | 0.7.1+19 | 备份状态卡三项修复——database_closed 根修 + 排除感知统计 + 照片/视… | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-02 | 混合 | 0.7.0+18 | 备份落位重构——类型/用户名/相册三级目录 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-02 | App | 0.6.3+17 | 动态更名 + 清空回收站（admin）+ 排除相册页加固；备份落位重构规划定稿待拍板 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-02 | App | 0.6.2+16 | 排除相册恶性 bug 根修 + 首页家里动态 + 备份完善三件 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-02 | App | 0.6.1+15 | 应用锁恶性锁死（开启即锁/解锁被弹回/登出逃不掉），v0.6.1+15 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-02 | App | 0.6.0+14 | 7 板块 35 项，v0.6.0+14 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-02 | App | 0.5.0+11 | 登录页方向 A 重构 + 全家桶功能（预检/生物识别/扫码/配对码），v0.5.0+11 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-02 | App | 0.4.6+10 | 模拟器九模块真连 + 4 bug 根修，v0.4.6+10 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-01 | App | 0.4.5+9 | 时间线日期头去悬浮，v0.4.5+9 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-01 | App | 0.4.4+8 | 照片时间线 Sliver 虚拟化重构 + "划走就消失"根修，v0.4.4+8 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-01 | App | 0.4.3+7 | 两轮评审 + 两轮对抗审查 + 图片提速 + 长按多选 + 照片墙准实时，v0.4.3+7 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-01 | App | 0.4.2+6 | 备份约束收口 + 照片统一落位 + 首页面板 + 时间线分组，v0.4.2+6 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
| 2026-09-01 | App | 0.4.1+5 | 与 PWA 一致的"紫渐变 + 白 L"，v0.4.1+5 | `docs/changelog/CHANGELOG-archive-2026Q3.md` |
