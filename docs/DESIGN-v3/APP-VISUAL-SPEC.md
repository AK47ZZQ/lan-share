# LAN Share App · 视觉设计规范 v1.0

> 适用范围：Flutter 移动端 App（`mobile/`，当前 v0.28.2+76，Flutter 3.47 / Dart 3.13）
> 关联文档：`APP-DESIGN.md` §7（UI/品牌，设计来源）、§8.21（v0.8.4 UI 重设计）、§8.22（v0.8.5 主题引擎）
> 代码落点：`mobile/lib/ui/theme/design_tokens.dart`、`responsive.dart`、`app_theme.dart`、`widgets/brand.dart`
> 本文是**可执行规范**：每条规则都指向真实代码，改视觉先改令牌，不要在页面里写字面量。

---

## 1. 设计风格定位

### 1.1 风格结论：Material Design 3，不做 Cupertino 双轨

| 维度 | 决策 | 理由 |
|------|------|------|
| 设计语言 | **Material 3**（`useMaterial3: true`） | 与 Web 端 PWA 品牌语言同源（同主色、同渐变、同圆角基调），三端保持一致认知 |
| iOS 适配 | **不切换 Cupertino**，只做细节让位 | 家庭网盘的核心用户是全家人（Android 为主），双轨会带来两套组件与两份维护成本；iOS 侧靠 `pageTransitionsTheme` 与安全区适配已足够自然 |
| 主题能力 | 6 套预置色板 + Material You 动态取色 | 见 §2.1；用户可"跟随壁纸"，是 Android 12+ 的体验加分项 |
| 暗色 | 亮 / 暗 / **AMOLED 纯黑** 三态 | 照片墙与夜间观影场景，纯黑底省电且观感高级 |

### 1.2 品牌语言

品牌由两件事定义（用户 2026-09-03 拍板）：**云朵图案** 与 **它背后的渐变底色**。

- 云朵：`Brand.cloud = Icons.cloud_outlined`，出现在品牌头、空态、二维码中心、启动图标
- 渐变：默认 `#5B6CFF → #9C6BFF`（云朵紫蓝），随色板联动
- 品牌头：底部 32 圆角的大弧 + 8% 黑罩膜（保证渐变浅色端也能压住白字）

> 实现：`widgets/brand.dart` 的 `BrandCloud` / `BrandHeader` / `EmptyState` / `FileTypeTile` / `SectionTitle`

### 1.3 视觉基调：**克制的扁平 + 低对比描边**

这是本 App 与"卡片堆阴影"式设计最本质的区别：

- 卡片 `elevation: 0`，靠 **0.6px 低对比描边** 与表面色阶分界
- 只有"真正浮在内容之上"的层（FAB、拖动中的卡片、底部弹层、对话框）才用阴影
- 结果：浅色下清爽、AMOLED 纯黑下层级依然干净，滚动照片墙时不出现大片灰投影

---

## 2. 配色体系

### 2.1 色板（`Palettes`，app_theme.dart）

每套色板 = `{种子色 seed, 品牌渐变 gradient[2], 深色底 darkBg}`。

| id | 名称 | 种子色 | 渐变 | 深色底 | 场景 |
|----|------|--------|------|--------|------|
| `violet` | 云朵紫蓝（**默认**） | `#5B6CFF` | `#5B6CFF → #9C6BFF` | `#0B1020` | 品牌主推 |
| `teal` | 晨雾青 | `#14B8A6` | `#0D9488 → #34D399` | `#0A1512` | 清凉感 |
| `warm` | 暖沙陶土 | `#E07A5F` | `#E07A5F → #F2B662` | `#181009` | 温暖家庭感 |
| `rose` | 玫瑰落日 | `#E85D8A` | `#E85D8A → #F2994A` | `#170D12` | 柔和 |
| `forest` | 墨绿 | `#2F7D5D` | `#2F7D5D → #6FBF9F` | `#0A130E` | 沉静 |
| `slate` | 极简灰 | `#64748B` | `#475569 → #94A3B8` | `#0E1013` | 无彩色 |
| `auto` | 跟随壁纸 | 系统动态 primary | dynamic primary → tertiary | darkDynamic.surface | Android 12+ |

**取值规则**：`accent` 存 prefs（`ui_accent`），未知值由 `paletteById()` 回落 `violet`（防脏数据）。`auto` 是 accent 取值而**不是**色板 id。

### 2.2 语义色（一律用 ColorScheme，不写死）

| 语义 | 令牌 | 用途 |
|------|------|------|
| 主色 | `scheme.primary` | 主按钮、选中指示、链接、品牌强调 |
| 正文 | `scheme.onSurface` | 标题与正文文字 |
| 次级文字 | `scheme.onSurfaceVariant` | 说明、副标题、未选中图标 |
| 边框 | `scheme.outlineVariant` @ 0.5 | 卡片/输入框描边、分割线 |
| 危险 | `scheme.error` | 删除、退出登录、断网横幅 |
| 主色之上 | `scheme.onPrimary` | 主色底上的文字/图标 |

> **红线（v0.8.7 教训）**：`scheme.outline` 是**边框语义**（对比度仅 ≈3.5:1），**禁止当正文色**。曾经 15 处误用导致"主页字看不清"。文字一律 `onSurface` / `onSurfaceVariant`。

### 2.3 对比度加固

- 亮色下 `onSurfaceVariant` 向 `onSurface` 拉近 **35%** → 实际约 **8:1**（远超 WCAG AA 4.5:1）
- 品牌头/渐变上文字：白字 + 8% 黑色罩膜（`foregroundDecoration`），保证暖沙等浅色渐变端也达标
- SnackBar 恒为深海军底（`#23253A` 系）→ 文字显式 `Colors.white`（不能依赖 M3 默认 `onInverseSurface`，暗色下会变深字深底）

### 2.4 文件类型色（`fileTypeColor`，单源 `data/media_kinds.dart`）

视频 `#3B9EFF` · 音频 `#F2994A` · 图片 `#5B6CFF` · 压缩包 `#9C6BFF` · PDF `#FF6B6B` · 文档 `#4E8DE0` · 表格 `#34A853` · 其它 `#8A93A6`

渲染为 **圆角方块 + 白字形**（`FileTypeTile`），不用裸露 Material 图标。

---

## 3. 字体排版规范

### 3.1 字体族

**不内嵌字体**，使用 `Typography.material2021(platform: android)` + 系统回退（Android 为 Roboto + 系统中文字体，iOS 为 SF + PingFang）。理由：中文字体内嵌动辄 5–10MB，对家庭网盘 APK 体积不划算；系统字体在各平台观感已达标。

> 墨水色方向：亮背景用 `.black`、暗背景用 `.white`——**方向写反会导致"字看不见"**，已有回归测试钉死（`Typography` 的 `.black/.white` 指墨水色，不是底色）。

### 3.2 层级表（`AppTheme._textTheme`）

| 层级 | 字号 | 字重 | 行高 | 用途 |
|------|------|------|------|------|
| `displaySmall` | 28 | w700 | 1.25 | 数字大字、存储占用 |
| `headlineSmall` | 22 | w700 | 1.3 | 页面级大标题 |
| `titleLarge` | 18 | w700 | 1.3 | AppBar 标题、卡片主标题 |
| `titleMedium` | 15.5 | w600 | 1.35 | 区块标题、列表主标题 |
| `titleSmall` | 13.5 | w600 | 1.4 | 小标题、卡片内小节 |
| `bodyLarge` | 15 | w400 | 1.5 | 正文主力 |
| `bodyMedium` | 13.5 | w400 | 1.45 | 次要正文、说明 |
| `bodySmall` | 12 | w400 | 1.4 | 辅助信息、时间戳 |
| `labelLarge` | 14 | w600 | — | 按钮文字 |
| `labelMedium` | 12.5 | w600 | — | 标签、Chip |
| `labelSmall` | 11.5 | w600 | — | 角标、导航栏标签 |

**三条中文排版规则**：

1. **行高必须大于英文**：正文 1.45–1.5，标题 1.3–1.4。中文是方块字，行高不足会密排成"黑块"
2. **`letterSpacing` 一律归零**：M3 默认给 label 加了 0.1–0.5 字距，中文下字符间会显得松散、不成词
3. **字号收敛为 8 级**：禁止再出现 13/14.5/17/19 这类散值；页面上优先用 `Theme.of(context).textTheme.xxx`

---

## 4. 布局与空间

### 4.1 间距（`Gap`，4pt 基准网格）

| 令牌 | 值 | 用途 |
|------|-----|------|
| `Gap.xxs` | 4 | 图标与文字紧凑间隙、徽标内边距 |
| `Gap.xs` | 8 | 同行元素间隙、小按钮内边距 |
| `Gap.sm` | 12 | 列表项内部、卡片内小节 |
| `Gap.md` | 16 | **页面左右边距（手机）**、卡片默认内边距 |
| `Gap.lg` | 20 | 品牌头左右边距、**卡片间距** |
| `Gap.xl` | 24 | 页面左右边距（平板）、区块间距 |
| `Gap.xxl` | 32 | 大区块间距、空态上下留白 |
| `Gap.xxxl` | 48 | 首屏级留白、骨架占位 |

### 4.2 圆角（`Radii`）

| 令牌 | 值 | 用途 |
|------|-----|------|
| `Radii.xs` | 8 | 小徽标、缩略图角 |
| `Radii.sm` | 10 | 输入框内附属控件 |
| `Radii.control` | 12 | **按钮 / 输入框 / 列表项** |
| `Radii.md` | 14 | 导航指示胶囊、中等容器 |
| `Radii.card` | 20 | **卡片 / 云朵块** |
| `Radii.sheet` | 24 | 底部弹层上圆角 |
| `Radii.header` | 32 | **品牌头下弧** |
| `Radii.pill` | 999 | Chip、状态胶囊、日期头 |

`Brand.cardRadius / controlRadius / headerRadius / cloudTileRadius / sheetRadius` 全部指向 `Radii`——**单一事实源**，旧代码用 `Brand.xxx` 不受影响（有单测钉死两者相等）。

### 4.3 阴影（`Shadows`）与描边（`Strokes`）

| 档位 | 令牌 | blur / offset / alpha | 用途 |
|------|------|----------------------|------|
| 0 | `Shadows.none` | — | **默认卡片**（靠描边分界） |
| 1 | `Shadows.low(c)` | 18 / (0,6) / 0.06 | 轻浮卡片 |
| 2 | `Shadows.overlay(c)` | 24 / (0,8) / 0.18 | FAB、拖动中卡片、悬浮条 |
| 3 | `Shadows.modal(c)` | 32 / (0,12) / 0.24 | 对话框、底部弹层 |

- 描边宽度固定 **0.6**（1px 在高 DPI 下显重）；强调描边（聚焦/选中）**1.6**
- 阴影色一律从 `scheme.shadow` 或 `Colors.black` 派生，禁止硬编码不透明黑

### 4.4 图标（`IconSizes`）

| 令牌 | 值 | 用途 |
|------|-----|------|
| `xs` | 14 | 行内状态点、辅助徽标 |
| `sm` | 16 | 列表 trailing 箭头、小节标题图标 |
| `md` | 20 | 列表 leading、按钮内图标 |
| `lg` | 24 | **导航栏、AppBar 动作（Material 默认）** |
| `xl` | 32 | 卡片强调图标、空态小图 |
| `display` | 48 | 空态主图、锁屏 |

---

## 5. 响应式适配规范

### 5.1 断点（`Breakpoints`）

| 档位 | 宽度 | 典型设备 | 页面边距 | 照片墙列数 |
|------|------|---------|---------|-----------|
| `compact` | **< 600** | 手机竖屏 | 16 | 3 |
| `medium` | **600–839** | 手机横屏 / 小平板竖屏 | 20 | 4 |
| `expanded` | **≥ 840** | 平板 / 折叠展开 / 桌面 | 24 | 6 |

取值方式：`context.windowSize`（内部用 `MediaQuery.sizeOf`，只订阅尺寸变化，避免无关重建）。

### 5.2 核心策略：**内容不无限拉伸**

比"平板双栏"更稳的全局改善——超过 **720** 逻辑像素就居中留白：

- 清单类页面（首页卡片流、我的、通知、关于）：限宽 720
- 网格类页面（照片墙、文件网格、专辑）：**不限宽**，改为增加列数（铺满更有沉浸感）
- 品牌头：始终铺满全宽（不限宽），保持 edge-to-edge 沉浸感

> 手机竖屏下这些约束**恒等于原值**（零视觉变化），是纯增量改善。

工具（`responsive.dart`）：

| API | 说明 |
|-----|------|
| `context.windowSize` | 当前档位 |
| `context.pagePadding` / `pagePaddingH` | 页面边距 16/20/24 |
| `context.gridColumns({compact, medium, expanded})` | 网格列数（默认 3/4/6） |
| `context.isLandscape` | 是否横屏 |
| `sliverGutter(context, base: 12)` | **Sliver 场景限宽水平边距**（折算进 `SliverPadding`） |
| `AdaptiveContent(child:)` | 单栏限宽容器（Center + maxWidth 720） |

### 5.3 各页面适配策略

| 页面 | 手机竖屏 | 横屏 / 平板 |
|------|---------|------------|
| 首页（卡片流） | 单栏 · 12 边距 | 内容居中限宽 720 · 品牌头仍铺满 |
| 照片墙 | 3 列瀑布流 | 4 / 6 列（列数自适应，Sliver 结构不变） |
| 专辑详情 / 选照片 | 3 列瀑布流 | 4 / 6 列 |
| 文件页（网格） | 3 列 | 4 / 5 列（带文件名标签，不用 6 列） |
| 文件页（列表） | 单栏 | 单栏 |
| 我的 / 设置 / 通知 / 关于 | 单栏 · 12 边距 | 内容居中限宽 720 |
| 品牌头（AppBar 收起态） | 高 48 + 状态栏 | 不变（高度与屏高无关） |

---

## 6. 核心页面规范

### 6.1 首页（`home_page.dart`）

**布局结构（自上而下）**

```
┌──────────────────────────────────┐
│ 渐变品牌头（SliverPersistentHeader）│  ← 铺满全宽，含状态栏，仅首页白色状态栏图标
│  「晚上好，XX」+ 右上角头像         │    展开 78 高 → 收起 48 高（自绘交叉淡入）
├──────────────────────────────────┤
│ 断网横幅（条件渲染）                │  ← error @12% 底 + error 字 + wifi_off 图标
├──────────────────────────────────┤
│ 卡片流（SliverReorderableList）    │  ← 长按拖动排序；限宽 720 居中
│  · 最近上传（横向 92×92 缩略图）    │
│  · 动态 / 自动备份 / 传输           │
│  · 服务端存储 / 快捷入口 / 邀请码    │
│  · 管理卡片（常驻入口，不可拖）      │
└──────────────────────────────────┘
```

**视觉层级**：品牌头（最强，渐变+白字）> 卡片标题（`titleMedium`）> 卡片内容（`bodyMedium`）> 辅助（`bodySmall` + `onSurfaceVariant`）

**卡片规范**：圆角 20 · 描边 0.6 · 内边距 16 · 卡片间距 8–12 · 无阴影

**交互元素**：
- 头像：40×40 圆形，`Colors.white24` 底；有头像走 `AuthImage`，无头像取显示名首字
- 卡片标题行：左侧 `SectionTitle`（品牌色小图标 + `titleSmall`），右侧 `TextButton`（"全部 ›" @12）
- 拖动中：`Material(elevation: 6)` + 圆角 12 代理装饰

### 6.2 详情页（照片查看器 `photo_viewer.dart` / 文档查看器 `doc_viewer.dart`）

**照片查看器**
```
┌──────────────────────────────────┐
│              ←  全屏图片（Hero 转场）│  ← tag = 'ph-<path>'，与网格一一对应防重
│                                  │
│                                  │
├──────────────────────────────────┤
│ 毛玻璃操作条（BackdropFilter）      │  ← 半透明底 + 圆角按钮组
│  下载 · 分享 · 删除 · 信息         │     仅此条做模糊，不碰图片层（性能）
└──────────────────────────────────┘
```

**规范要点**
- 沉浸：状态栏透明 + 深色图标（内容为亮图时）
- 操作条是**唯一**的浮层，用 `Shadows.overlay`；图片层不加阴影
- 加载态：`CircularProgressIndicator`（primary 色，轨道 primary @12%）
- 失败态：类型色块 + `FileTypeTile` 回退（不显示破碎图标）

**文件详情 / 文档预览**：卡片分组呈现元信息（名称/大小/类型/修改时间），值靠右 `onSurfaceVariant`，长文本可选中复制。

### 6.3 个人中心（`me_page.dart`）

**布局结构（自上而下，限宽 720 居中）**

```
┌──────────────────────────────────┐
│ AppBar「我的」（18/w700，左对齐）   │
├──────────────────────────────────┤
│ ① 身份 + 连接（Card）              │  ← 常驻置顶，最高优先级
│   头像 + 昵称 + 角色  ▸编辑         │
│   ────────────────────────        │
│   当前入口（局域网/公网）+ 网络徽标   │
│   自动切换局域网/公网（SwitchTile）  │
├──────────────────────────────────┤
│ ② 功能宫格（Wrap，大图标 46×46）    │  ← 传输/分享/通知/回收站/相册等高频
├──────────────────────────────────┤
│ ③ 分组卡 × N（小标题 + Card）       │  ← 「外观」「账号与安全」「通用」
│   ListTile / SwitchListTile       │
├──────────────────────────────────┤
│ 退出登录（error 色，二次确认）       │
│ 版本号（11px 灰，居中）             │
└──────────────────────────────────┘
```

**视觉层级**
- 分组小标题：`12.5 / w600 / onSurfaceVariant`，与卡片间距 6
- 分组卡片：圆角 20 · 描边 0.6 · 内边距 6（列表项自身有内边距）
- 列表项：高 ≥ 56 · leading 图标 `md`(20) · title `titleMedium` · subtitle `bodySmall` + `onSurfaceVariant` · trailing 箭头 `sm`(16)
- 危险项（退出登录）：图标与文字 `scheme.error`，**必须二次确认**

**宫格规范**：图标容器 46×46 · 圆角 14 · primary @12% 底 · 图标 `20–22` primary 色 · 标签 12 号居中 · `Wrap` + `spaceAround`（自动换行不溢出）

---

## 7. 交互元素外观定义

| 元素 | 圆角 | 尺寸 / 内边距 | 色彩 | 备注 |
|------|------|--------------|------|------|
| **FilledButton**（主操作） | 12 | h20 / v13 | `primary` 底 / `onPrimary` 字 | 字 14.5 / w600 |
| **OutlinedButton** | 12 | h18 / v12 | 0.6 描边 / 主色字 | 字 14 / w600 |
| **TextButton** | 12 | 默认 | 主色字 | 用于"全部 ›"等轻操作 |
| **FloatingActionButton** | **16** | M3 默认 | `primary` 底 / `onPrimary` | elevation 2（按压缩到 1）· 扩展文字 14.5 / w600 |
| **IconButton** | 12 | 默认触摸区 | `onSurfaceVariant` | 只改水波纹形状，**不动尺寸**（避免打乱 AppBar 布局） |
| **输入框** | 12 | h14 / v13 | 表面最高阶 @35–45% 填充 | 聚焦态 primary 1.6（`widthStrong`）· 常态 0.8（`widthInput`，可交互控件比静态卡片略粗） |
| **卡片** | 20 | 16 | `surfaceContainerLow` | 0.6 描边 · **elevation 0** |
| **列表项** | 12 | 高 ≥ 56 | 透明 | leading `md` / trailing `sm` |
| **Chip / 状态胶囊** | 999 | h12 / v5 | 描边 + `chipBg` | 照片墙日期头用 primary @10% 底 + primary 字 |
| **底部弹层** | 上 24 | `showDragHandle: true` | `sheetBg` | 与材质色阶一致 |
| **对话框** | 20 | 默认 | `dialogBg` | 宽屏下建议 `ConstrainedBox(maxWidth: 560)` |
| **PopupMenu** | 12 | 默认 | `dialogBg` | — |
| **SnackBar** | 12 | floating | 深海军底 `#23253A` | **文字恒为白色** · action 色 `#8F9BFF` |
| **Tooltip** | 8 | 默认 | 深海军底 | 12.5 白字 |
| **NavigationBar** | 指示胶囊 14 | 高 68 | `baseSurface` 底 | 指示器 primary @14%（亮）/ @22%（暗）· 标签 11.5 / w600 |
| **进度指示器** | — | — | `primary` | 轨道 primary @12% |
| **分割线** | — | 0.6 | `outlineVariant` @0.5 | — |
| **空态** | — | 上下 72 | — | 云朵 64 + 标题 14.5/w600 + 提示 12.5 + 引导按钮（`EmptyState`） |
| **文件类型块** | size×0.28 | 默认 40 | 类型色渐变（0.85→1.0） | 白字形 · 图标 size×0.52 |

---

## 8. 动效规范

| 令牌 | 时长 | 曲线 | 用途 |
|------|------|------|------|
| `Motion.instant` | 100ms | — | 按下、开关、勾选反馈 |
| `Motion.fast` | 150ms | `enter` = easeOutCubic | 小范围展开、淡入淡出 |
| `Motion.normal` | 250ms | `standard` = easeInOutCubic | 元素位移、卡片展开（默认） |
| `Motion.slow` | 400ms | `exit` = easeInCubic | 大块区域进出、底部弹层 |
| `Motion.emphasized` | — | easeOutBack | 品牌级动作（备份完成勾选） |

**页面转场**：淡入 + 8px 上移（`_FadeSlideTransitionsBuilder`，easeOutCubic 进 / easeInCubic 退）。Android/iOS 统一，长度与系统 `ZoomPageTransition` 相仿，不拖慢图片墙。

**克制原则**：本 App 是工具型（浏览/传输/备份），动效只用于**解释状态变化**，不做装饰性弹跳；照片墙的 Sliver 路径、`AuthImage` 缓存管线**不加动效**（性能红线）。

---

## 9. 可访问性

- **对比度**：正文 ≥ 4.5:1（实际约 8:1）；重要文字不用 `outline` 语义色
- **触摸区**：可点元素 ≥ 44×44（列表项 ≥ 56 高）
- **文字缩放**：遵循系统 `textScaleFactor`，不使用固定高度容器裁剪文字（用 `maxLines` + `ellipsis`）
- **状态栏**：首页（渐变头）白图标，其余页深图标，由 `AnnotatedRegion` 按页签切换
- **安全区**：edge-to-edge 下品牌头用 `topInset` 让位状态栏；底部留白用 `MediaQuery.padding.bottom`

---

## 10. 落地清单（v1.0 本次实施）

**新增**

| 文件 | 内容 |
|------|------|
| `lib/ui/theme/design_tokens.dart` | `Gap` / `Radii` / `Motion` / `IconSizes` / `Shadows` / `Strokes` |
| `lib/ui/theme/responsive.dart` | `Breakpoints` / `WindowSize` / `ResponsiveContext` / `AdaptiveContent` / `sliverGutter` |
| `test/visual_tokens_test.dart` | 令牌阶梯 + 断点边界 + 限宽行为的回归测试 |
| `docs/DESIGN-v3/APP-VISUAL-SPEC.md` | 本文档 |

**改造**

| 文件 | 改动 |
|------|------|
| `theme/app_theme.dart` | `Brand` 圆角指向 `Radii`（单一事实源）；排版补全 8 级层级 + 中文行高/字距；新增 FAB / IconButton / Tooltip 主题 |
| `home_page.dart` | 内容流限宽（`sliverGutter`），品牌头保持铺满 |
| `photos_page.dart` | 瀑布流列数 3 → 3/4/6 自适应 |
| `album_detail_page.dart` | 两处瀑布流列数自适应 |
| `files_page.dart` | 网格列数统一到全局断点（原 440/640 自定义阈值） |
| `me_page.dart` | 内容限宽 720 居中 |

**后续可选项**（未做，按需评估）

1. **双栏布局**：平板下"列表 + 详情"并排（需新建组件）——需重新组织各页信息层级，建议先看真机反馈
2. **横屏品牌头收紧**：当前品牌头高度与屏高无关（78），横屏下占比可接受，暂无必要
3. **动态字号**：跟随系统字号做整体缩放系数（当前遵循系统 `textScaleFactor`，未做额外适配层）
4. **桌面端 hover / focus 态**：`Strokes.strong` 已备，暂无桌面端发布计划

---

## 11. 检查清单（改 UI 前自问）

- [ ] 颜色是否来自 `ColorScheme`？文字是否用了 `onSurface` / `onSurfaceVariant`（**不是** `outline`）？
- [ ] 间距是否用 `Gap.xxx`？有没有又写 12/16/20 字面量？
- [ ] 圆角是否用 `Radii.xxx`？
- [ ] 字号是否用 `textTheme.xxx` 或已定义的层级？有没有出现 13/14.5/17 这类散值？
- [ ] 新卡片是否 `elevation: 0` + 0.6 描边？（不要堆阴影）
- [ ] 清单页是否限宽？网格页是否随 `gridColumns()` 增列？
- [ ] 空态是否用 `EmptyState`？（不要裸"还没 xx"灰字）
- [ ] 文件类型是否用 `FileTypeTile` + `fileTypeColor`？
- [ ] 是否跑了 `dart analyze lib` 与 `flutter test`？

---

*规范版本 v1.0 · 2026-09-11 · 与 App v0.28.2+76 代码同步。改视觉先改令牌，再改本文档。*
