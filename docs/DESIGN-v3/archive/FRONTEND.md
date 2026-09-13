# v3 设计 · 前端升级（FRONTEND）

> 实际落地与本文略有偏差（2026-09 状态）：
> - **Pinia 未引入**——M3 拆包时 Pinia 在桌面上"全家桶"过重、且本项目跨 session/跨 tab
>   的全局状态只有登录态一处，用 `web/src/auth.ts` 单例 + Vue 内置 `provide/inject` 即足够
>   （如 `App.vue` 直接持 `me` ref）。需要时再迁 Pinia 不晚。
> - **Naive UI 改自封装玻璃拟态组件**（vue3 + CSS 变量主题，深浅色一键切换），主因
>   Naive UI 的样式主题与现有玻璃拟态色调有冲突、改造成本高于收益；自封装约 20 个
>   组件已够覆盖全部页面。
> - **Vitest 改 check.bat 后端门禁 + 真实浏览器点验**——前端单元测试维护成本高，
>   而 M3 权限边界由后端 18 项 pytest 覆盖；前端靠"改后真浏览器/手机视口点一遍"兜底。
> - `static/` 旧 HTML/JS 已删（`app/main.py` 的 SPA 回退直供 `web/dist` 真实文件）。
>
> 原生 HTML/JS（零构建）→ Vue 3 + Vite + TypeScript。CSP `script-src 'self'`
> **不放宽**。渐进替换四页，M2 完成后删除旧 `static/*.js`。

## 1. 技术选型

| 项 | 选择 | 理由 |
|----|------|------|
| 框架 | Vue 3（`<script setup>` SFC） | 用户点名；SFC 预编译 = 运行时不编译模板，CSP 无需 unsafe-eval |
| 构建 | Vite 5+ | 产物 ES module 自托管 |
| 语言 | TypeScript（strict） | openapi 生成类型，接口变更编译期发现 |
| 状态 | Pinia | 官方标配 |
| 路由 | Vue Router（history 模式） | Caddy `try_files` SPA 回退 |
| 组件库 | **Naive UI** | TS 原生、主题可定制到玻璃拟态、无全局样式污染、按需树摇 |
| 工具 | VueUse（拖拽/存储/断网感知）、axios 不引入（fetch 封装 60 行足够） | |
| 测试 | Vitest（stores/工具函数）+ Playwright（E2E：登录→上传→移动→分享→回收站） | M3 权限边界 E2E 的载体 |

## 2. 工程结构

```
web/                      ← 前端工程根（新目录，与旧 static/ 并存至 M2 末）
├── package.json  vite.config.ts  tsconfig.json
├── src/
│   ├── main.ts  App.vue  router/index.ts
│   ├── api/              ← fetch 封装：统一 401 跳登录/CSRF 头/错误归一/上传队列
│   │   ├── client.ts  types.gen.ts（openapi-typescript 生成）
│   │   └── files.ts upload.ts share.ts admin.ts
│   ├── stores/           ← auth.ts（用户/角色/授权缓存） files.ts（当前目录/
│   │                        选择/剪切板） ui.ts（弹窗栈/移动端判定） tasks.ts
│   ├── composables/      ← useDrag(useVueUse) useUploadQueue useLongPress
│   ├── components/       ← FileCard DirTree Drawer ContextMenu UploadPanel
│   │                        ShareDialog MoveDialog TrashPanel PhotoTimeline
│   │                        AdminPanel ActivityFeed …
│   ├── views/            ← HomeView LoginView UpView ShareView
│   └── styles/           ← 主题变量（沿用 v2 玻璃拟态/渐变/动效 token），
│                             Naive UI themeOverrides
└── dist/                 ← 构建产物（gitignore；部署脚本同步）
```

## 3. CSP 与构建产物约束

- 产物全部自托管于 Caddy `static/dist/`，哈希/长缓存文件名，`script-src 'self'` 原样。
- Vite 配置禁用 `eval` 类插件；不用运行时模板编译（Vite 的 SFC 流程天然满足）。
- index.html 由 Vite 生成，含 CSP meta 与 theme-color（对齐 v2 四页头部）。
- 内联样式：CSP 允许 `style-src 'self' 'unsafe-inline'`（v2 现状即如此，不放宽）。

## 4. Caddy 路由切换（M2）

```
# 过渡期（新旧并存）
/static/*            → static/            # 旧页（login/up/share 殿后期间仍服务）
/assets/* /*.html    → static/dist        # Vue 产物
/                    → static/dist/index.html
try_files {path} /index.html             # SPA history 回退
```

**M3 审查补充——现网 Caddy 的三个 HTML 页面路由必须显式迁移**（审查时核对
了 Caddyfile：`/s/*` 与默认 handle 当前都 `reverse_proxy localhost:8766`
出 FastAPI HTML，不迁移 SPA 无法接管）：

| 路由 | 现状（v2） | 切换后 |
|------|-----------|--------|
| `/s/{token}` | Caddy `handle /s/*` → FastAPI 出 share.html | 改为直发 `dist/index.html`（SPA 内路由 `/s/:token` 接管）；**必须保持免认证**，不得误挂 forward_auth，否则分享链接全灭 |
| `/login`、`/up` | 默认 handle → FastAPI 出 HTML | 同上直发 dist；`/up` 免认证语义保持（页面本身有登录态判断） |
| `/api/*` | → FastAPI | 不变（页面走 SPA，接口仍 FastAPI） |

切换顺序：每页一个 Caddy `handle` 块 + settings 灰度开关，出问题一键回退
到 reverse_proxy；旧页在四页全灰度验证一周后才删除 FastAPI 的页面路由与
`static/*.js`。SPA 回退 `try_files` 只作用于 HTML 请求，不得拦截
`/api`、`/dav`、`/files`、`/s`（`/s` 交 dist 前置于回退规则）。

## 5. 关键交互的迁移映射（index 页 = 80% 工作量）

| v2 (index.js 区块) | v3 归宿 |
|---|---|
| 状态 + 渲染 + ACTIONS 事件分发表 | Pinia stores + 组件事件；事件分发表消亡，回归声明式 |
| 上传队列（分块/秒传/重试） | `useUploadQueue` composable（逻辑保留，仅换壳），并发/重试参数不变 |
| 移动三入口（选择器/剪切粘贴/拖拽） | MoveDialog / files store clipboard / VueUse useDragTo；internalDrag 隔离标记语义保留 |
| 右键/长按/⋯ 菜单 | 通用 ContextMenu 组件（触屏长按 = VueUse useLongPress，44px 触区保留） |
| 弹窗栈（含 z-index 修复教训） | Naive UI Modal 体系（v2 的 100→120 遮挡问题此类消失） |
| 磁盘用量弹窗（两段条 + inode 去重） | 组件化重写，接口对齐 stats.py |
| 照片时间线 | PhotoTimeline 虚拟滚动（v2"加载更多"升级） |
| 返回按钮拖动记忆 | VueUse useStorage + usePointer 30 行替换 |
| esc() 转义全覆盖 | Vue 模板自动转义，esc 退役；v-html 列入 lint 禁用清单 |

## 6. 移动端与可达性

- 保留 v2 断点体系（≤480px 紧凑化、输入 ≥16px 防 iOS 缩放、
  viewport-fit=cover、prefers-reduced-motion）。
- Naive UI 主题变量映射到 v2 设计 token，避免两套视觉语言。
- M4 起 PWA：manifest + 图标 + 离线壳（仅壳，数据不缓存），安装到主屏
  代替原生 App（PLAN-v3 已裁决不做原生）。

## 7. 构建与发布流程

```bash
cd web && npm ci && npm run build      # tsc --noEmit + vite build
# 产物同步到 static/dist（脚本 npm run deploy = build + robocopy /MIR static/dist）
```

- `dist/` 不入 git；发布 = 打 tag 时构建并部署到 static/dist（本机部署模型，
  CI 构建产物作为 release 附件备用）。
- CI 前端任务：`vue-tsc` 类型检查、Vitest、ESLint(vue)——与后端同一
  Actions 工作流。

## 8. 迁移顺序与里程碑对齐

1. M2 第一周：脚手架 + 路由 + api 层 + 主题 + **HomeView 只读浏览**
   （列表/导航/排序/搜索）跑通全链路 ← 架构验证窗口，不搬交互。
2. 第二、三周：上传队列、多选/批量、移动三入口、分享、回收站、预览。
3. 第四周：照片时间线、磁盘弹窗、移动端走查（390×844）、`vue_index` 灰度。
4. login → up → share 三小页殿后（各半天~一天），全量灰度一周后删旧 JS。
