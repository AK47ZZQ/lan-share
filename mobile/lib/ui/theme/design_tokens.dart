// 设计令牌（Design Tokens）· 视觉规范 v1.0（2026-09-11）
//
// 定位：把"页面里散落的魔法数字"收拢为可复用的语义令牌。此前项目只有
// 圆角与阴影在 Brand（app_theme.dart）里有名字，间距、动效时长、图标
// 尺寸仍是各页手写（8/12/16/20 混用），响应式断点完全缺失——同一层次的
// 两个卡片可能一个用 12 一个用 16，视觉节奏无法统一收敛。
//
// 依赖方向：本文件只依赖 Flutter 基础库，**不 import 任何项目文件**；
// app_theme.dart 反向依赖本文件（Brand 的圆角常量改指向 Radii）。
// 单向依赖保证令牌是单一事实源，且不留循环 import。
//
// 用法：间距/圆角/时长一律从令牌取，禁止在页面里再写字面量。
import 'package:flutter/material.dart';

/// ── 间距（4pt 基准网格）────────────────────────────────────
/// 所有 padding / margin / 组件间隙只从这里取。命名按"语义"而非数值，
/// 数值只是当前实现；将来整体放大 1.1 倍只需改这一处。
abstract final class Gap {
  /// 4 · 图标与文字的紧凑间隙、徽标内边距、紧跟的辅助说明
  static const double xxs = 4;

  /// 8 · 同一行元素间隙、小按钮内边距、列表项图标与标题
  static const double xs = 8;

  /// 12 · 列表项内部、卡片内小节间隙、表单字段间距
  static const double sm = 12;

  /// 16 · 页面左右边距（手机）、卡片内边距（默认）
  static const double md = 16;

  /// 20 · 品牌头左右边距、卡片与卡片之间的间距
  static const double lg = 20;

  /// 24 · 页面左右边距（平板）、区块与区块之间
  static const double xl = 24;

  /// 32 · 大区块间距、空态上下留白
  static const double xxl = 32;

  /// 48 · 首屏级留白、骨架占位高度
  static const double xxxl = 48;

  // 注：这里曾提供 pageH / card / vXs… 等 EdgeInsets 与 SizedBox 便利常量，
  // 但核查后发现**全部没有调用点**（页面直接写 SizedBox(height: 8) 更直观），
  // 故删除——令牌层只保留"数值单一事实源"，便利包装等真有复用再加。
}

/// ── 圆角 ──────────────────────────────────────────────────
/// 单一事实源：Brand.cardRadius / controlRadius / headerRadius /
/// cloudTileRadius 全部指向本类，页面既可用 Brand（旧）也可用 Radii（新）。
abstract final class Radii {
  /// 8 · 小徽标、标签、紧凑色块
  static const double xs = 8;

  /// 10 · 输入框内的附属控件、极紧凑容器
  static const double sm = 10;

  /// 12 · 按钮 / 输入框 / 列表项（= Brand.controlRadius）
  static const double control = 12;

  /// 14 · 中等容器、导航指示胶囊
  static const double md = 14;

  /// 16 · 悬浮动作按钮（对齐 M3 FAB 的 squircle 形状）
  static const double fab = 16;

  /// 20 · 卡片 / 云朵块（= Brand.cardRadius / cloudTileRadius）
  static const double card = 20;

  /// 24 · 底部弹层上圆角
  static const double sheet = 24;

  /// 32 · 品牌头下弧（= Brand.headerRadius）
  static const double header = 32;

  /// 全圆胶囊（Chip / 状态 pill）
  static const double pill = 999;

  static const BorderRadius bXs = BorderRadius.all(Radius.circular(xs));
  static const BorderRadius bSm = BorderRadius.all(Radius.circular(sm));
  static const BorderRadius bControl =
      BorderRadius.all(Radius.circular(control));
  static const BorderRadius bMd = BorderRadius.all(Radius.circular(md));
  static const BorderRadius bCard = BorderRadius.all(Radius.circular(card));
  static const BorderRadius bPill = BorderRadius.all(Radius.circular(pill));

  /// 底部弹层上圆角
  static const BorderRadius bSheet = BorderRadius.vertical(
      top: Radius.circular(sheet));

  static BorderRadius all(double r) => BorderRadius.circular(r);
}

/// ── 动效（时长与曲线）──────────────────────────────────────
/// 克制原则：本 App 是工具型（浏览/传输/备份），动效只用于"解释状态
/// 变化"，不做装饰性弹跳。时长与系统 Material 规范对齐。
abstract final class Motion {
  /// 100ms · 状态反馈（按下、开关、勾选）
  static const Duration instant = Duration(milliseconds: 100);

  /// 150ms · 小范围展开/收起、淡入淡出
  static const Duration fast = Duration(milliseconds: 150);

  /// 250ms · 页面内元素位移、卡片展开（默认值）
  static const Duration normal = Duration(milliseconds: 250);

  /// 400ms · 大块区域进出、底部弹层
  static const Duration slow = Duration(milliseconds: 400);

  /// 进场 / 位移（减速，快起慢收）
  static const Curve enter = Curves.easeOutCubic;

  /// 退场（加速）
  static const Curve exit = Curves.easeInCubic;

  /// 双向平滑（颜色、尺寸、透明度的对称变化）
  static const Curve standard = Curves.easeInOutCubic;

  /// 强调（弹性收尾，仅用于品牌级动作，如备份完成勾选）
  static const Curve emphasized = Curves.easeOutBack;
}

/// ── 图标尺寸 ──────────────────────────────────────────────
/// Material 默认图标 24（[lg]）。层级内取小值放"辅助信息"，取大值放
/// "可点击的主操作"，避免 18/17/19 这类无规律的散值。
abstract final class IconSizes {
  /// 14 · 辅助徽标、行内状态点
  static const double xs = 14;

  /// 16 · 列表项 trailing 箭头、小节标题图标
  static const double sm = 16;

  /// 20 · 列表项 leading、按钮内图标（默认）
  static const double md = 20;

  /// 24 · 导航栏图标、AppBar 动作（Material 默认）
  static const double lg = 24;

  /// 32 · 卡片内强调图标、空态小图
  static const double xl = 32;

  /// 48 · 空态主图、锁屏
  static const double display = 48;
}

/// ── 阴影 / 海拔 ───────────────────────────────────────────
/// 设计基调：**扁平 + 低对比描边**（卡片 elevation 0，靠 0.6px 描边
/// 分界）。只有"浮在内容之上"的层才用阴影表达层级——避免卡片堆阴影
/// 的廉价感，也让 AMOLED 纯黑底下的层级依然干净。
abstract final class Shadows {
  /// 档 0：无阴影。默认卡片/列表用这个（配描边即可）
  static const List<BoxShadow> none = [];

  /// 档 1：卡片级微浮（= Brand.shadow，向后兼容）
  static List<BoxShadow> low(Color c) => [
        BoxShadow(
            color: c.withValues(alpha: 0.06),
            blurRadius: 18,
            offset: const Offset(0, 6)),
      ];

  /// 档 2：浮层（FAB、拖动中的卡片、悬浮操作条）
  static List<BoxShadow> overlay(Color c) => [
        BoxShadow(
            color: c.withValues(alpha: 0.18),
            blurRadius: 24,
            offset: const Offset(0, 8)),
      ];

  /// 档 3：模态（对话框、底部弹层在暗色下靠它脱离背景）
  static List<BoxShadow> modal(Color c) => [
        BoxShadow(
            color: c.withValues(alpha: 0.24),
            blurRadius: 32,
            offset: const Offset(0, 12)),
      ];
}

/// ── 描边 ──────────────────────────────────────────────────
/// 扁平基调用描边替代阴影分界，宽度固定挡位（1px 在高 DPI 下显重）。
abstract final class Strokes {
  /// 卡片/容器描边（静态分区用）
  static const double width = 0.6;

  /// 输入框等可交互控件描边：比静态卡片略粗 0.2，
  /// 在扁平无阴影的界面里给"这里可以点/可以输入"一个视觉暗示。
  /// 此前该值是散落在 app_theme 里的字面量 0.8，收进令牌避免继续漂移。
  static const double widthInput = 0.8;

  /// 强调描边（选中态、聚焦）
  static const double widthStrong = 1.6;

  static BorderSide side(Color c) => BorderSide(color: c, width: width);
  static BorderSide strong(Color c) => BorderSide(color: c, width: widthStrong);
}
