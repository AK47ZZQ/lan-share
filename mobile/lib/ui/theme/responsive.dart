// 自适应布局（响应式）· 视觉规范 v1.0（2026-09-11）
//
// 问题：此前 App 是"手机单栏"假设——卡片在平板上被拉满整屏（一行正文
// 超 1000 逻辑像素，阅读时眼睛要来回扫）；照片墙固定 3 列，平板上缩略图
// 被撑得过大；横屏时品牌头占掉近半屏高。断点与最大宽度此前完全没有。
//
// 断点取 Material 3 window size class 的通俗三分（与 Web 端保持一致）：
//   compact  < 600   手机竖屏          —— 单栏，页边距 16
//   medium   600–839 手机横屏/小平板  —— 单栏居中，页边距 20，多列
//   expanded ≥ 840   平板/折叠展开/桌面 —— 单栏居中 720，页边距 24，更多列
//
// 核心策略是"**内容不无限拉伸**"：超过 720 逻辑像素就居中留白。这比
// "平板双栏"更稳——双栏要重新组织每个页面的信息层级，而限宽是零风险的
// 全局改善，且立刻让平板上的观感从"拉伸的手机界面"变成"克制的卡片流"。
import 'package:flutter/material.dart';

import 'design_tokens.dart';

/// 断点阈值（逻辑像素，宽度）
abstract final class Breakpoints {
  /// 600 · 手机竖屏 → 横屏/小平板
  static const double medium = 600;

  /// 840 · 平板及以上
  static const double expanded = 840;

  /// 单栏内容最大宽度。超出即居中留白，保证行长在舒适区
  /// （中文约 40 字/行，英文约 90 字符/行）
  static const double contentMaxWidth = 720;

  /// 对话框 / 底部弹层在宽屏下的最大宽度（避免贴满整屏）
  static const double dialogMaxWidth = 560;
}

/// 窗口尺寸档位
enum WindowSize {
  /// < 600：手机竖屏
  compact,

  /// 600–839：手机横屏 / 小平板竖屏
  medium,

  /// ≥ 840：平板 / 折叠展开 / 桌面
  expanded;

  /// 手机竖屏
  bool get isCompact => this == WindowSize.compact;

  /// 平板及以上
  bool get isExpanded => this == WindowSize.expanded;

  /// 非手机竖屏（横屏或平板）——放宽边距、增加列数
  bool get isWide => this != WindowSize.compact;
}

/// BuildContext 扩展：在任意 widget 里读当前窗口档位与适配值
extension ResponsiveContext on BuildContext {
  /// 当前窗口档位。用 [MediaQuery.sizeOf]（只订阅 width/height 变化，
  /// 比 `MediaQuery.of(context).size` 少触发无关重建）
  WindowSize get windowSize {
    final w = MediaQuery.sizeOf(this).width;
    if (w >= Breakpoints.expanded) return WindowSize.expanded;
    if (w >= Breakpoints.medium) return WindowSize.medium;
    return WindowSize.compact;
  }

  /// 页面横向内边距：手机 16 / 横屏 20 / 平板 24
  double get pagePadding => switch (windowSize) {
        WindowSize.compact => Gap.md,
        WindowSize.medium => Gap.lg,
        WindowSize.expanded => Gap.xl,
      };

  /// 页面横向内边距（EdgeInsets 形式）
  EdgeInsets get pagePaddingH => EdgeInsets.symmetric(horizontal: pagePadding);

  /// 是否横屏（宽 > 高）。横屏下品牌头应收紧高度
  bool get isLandscape => MediaQuery.orientationOf(this) == Orientation.landscape;

  /// 卡片网格列数：照片墙/相册的缩略图列数按窗口档位自适应
  int gridColumns({int compact = 3, int medium = 4, int expanded = 6}) =>
      switch (windowSize) {
        WindowSize.compact => compact,
        WindowSize.medium => medium,
        WindowSize.expanded => expanded,
      };

  /// 底部安全区 + 导航栏高度，供自定义滚动页留白用
  double get bottomInset => MediaQuery.paddingOf(this).bottom;
}

/// 单栏内容限宽容器：宽屏下自动居中并限制最大宽度。
///
/// 用法（包裹页面主体的 Column/ListView）：
/// ```dart
/// AdaptiveContent(child: Column(children: [...]))
/// ```
/// 手机上它退化为纯 Padding（无额外层级、无布局开销）。
class AdaptiveContent extends StatelessWidget {
  final Widget child;

  /// 最大宽度，默认 [Breakpoints.contentMaxWidth]（720）
  final double maxWidth;

  /// 自定义内边距；不传则用 [ResponsiveContext.pagePaddingH]
  final EdgeInsetsGeometry? padding;

  const AdaptiveContent({
    super.key,
    required this.child,
    this.maxWidth = Breakpoints.contentMaxWidth,
    this.padding,
  });

  @override
  Widget build(BuildContext context) {
    final pad = padding ?? context.pagePaddingH;
    // 手机上 maxWidth 不生效（屏宽已 < 720），Align 也不产生偏移，
    // 因此不做分支，保持 widget 树形状稳定（利于 element 复用）
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: Padding(padding: pad, child: child),
      ),
    );
  }
}

/// Sliver 场景的限宽水平边距：算出"内容居中且不超过 maxWidth"所需的
/// 左右内边距。
///
/// Sliver 层没有 Center 可用，标准做法是把留白折算进 SliverPadding。
/// 手机竖屏下返回值恒等于 [base]（不产生任何视觉变化），宽屏下自动
/// 加宽两侧留白使内容居中。
///
/// 用法：
/// ```dart
/// final g = sliverGutter(context, base: 12);
/// SliverPadding(padding: EdgeInsets.fromLTRB(g, 12, g, 8), sliver: ...)
/// ```
double sliverGutter(
  BuildContext context, {
  double base = Gap.sm,
  double maxWidth = Breakpoints.contentMaxWidth,
}) {
  final w = MediaQuery.sizeOf(context).width;
  final usable = w - base * 2;
  if (usable <= maxWidth) return base;
  return base + (usable - maxWidth) / 2;
}
