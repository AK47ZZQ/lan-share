// 品牌组件库（0.8.4 UI 重设计批）：云朵 Logo、渐变品牌头、空态三件套、
// 品牌化文件类型图标。全部无状态纯展示组件，focus 在复用与一致性。
//
// v1.0 无障碍改造（2026-09-11）：此前全仓 `Semantics`/`semanticLabel` 为 0 处，
// 读屏用户打开 App 只能听到一串无意义的图形与拼读的路径。这里按
// 「装饰性图形一律 ExcludeSemantics、信息性文字交给原文字」的原则处理共享
// 组件——改这一处，全部使用方一起受益。
import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// 云朵 Logo：白底圆角块（登录页品牌头同款玻璃感）
///
/// 纯装饰元素：语义由相邻文字承担（品牌头有问候语、空态有标题），
/// 读屏跳过它，否则会多念一个无信息的图形。
class BrandCloud extends StatelessWidget {
  final double size;
  final bool onGradient; // 是否置于品牌渐变上（白块+白云）——浅色背景场景
  const BrandCloud({super.key, this.size = 56, this.onGradient = true});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tile = onGradient
        ? Colors.white24
        : cs.primary.withValues(alpha: 0.12);
    final iconColor =
        onGradient ? Colors.white : cs.primary;
    return ExcludeSemantics(
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: tile,
          borderRadius:
              BorderRadius.circular(Brand.cloudTileRadius * size / 56),
        ),
        child: Icon(Brand.cloud, color: iconColor, size: size * 0.55),
      ),
    );
  }
}

/// 渐变品牌头（首页顶部 / 关于页 / 登录页共用）。bottomRadius 出头弧，
/// [extra] 放入头内容（标题等）。
class BrandHeader extends StatelessWidget {
  final Widget child;
  final double bottomRadius;

  /// 状态栏高度内嵌（edge-to-edge 下渐变延伸到屏幕顶，内容再从状态栏
  /// 下方开始——主页/登录传 MediaQuery.padding.top，避免与状态栏重叠）
  final double topInset;
  /// 底部内边距（默认 32）：沉浸欢迎头等内容贴底场景传小值收紧空白
  final double bottomPadding;
  const BrandHeader({
    super.key,
    required this.child,
    this.bottomRadius = Brand.headerRadius,
    this.topInset = 0,
    this.bottomPadding = 32,
  });

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.only(
        bottomLeft: Radius.circular(bottomRadius),
        bottomRight: Radius.circular(bottomRadius));
    return Container(
      width: double.infinity,
      padding: EdgeInsets.fromLTRB(20, 20 + topInset, 20, bottomPadding),
      decoration: BoxDecoration(
        // 渐变随主题色板走（PaletteScope 由 MaterialApp.builder 挂载）
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: PaletteScope.of(context).gradient,
        ),
        borderRadius: radius,
      ),
      // 8% 暗色罩膜：暖沙等浅色系渐变端不压暗的话，白字对比不足
      foregroundDecoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.08),
        borderRadius: radius,
      ),
      child: child,
    );
  }
}

/// 空态三件套：品牌云朵 + 一句标题 + 引导按钮（替代散落的"还没xx"灰字）
///
/// 语义合并成一个节点：读屏连着念"还没有照片，去文件页上传"，
/// 而不是分三次跳焦点；`action` 按钮保持独立可点。
class EmptyState extends StatelessWidget {
  final String title;
  final String? hint;
  final Widget? action;
  final double padding;
  const EmptyState({
    super.key,
    required this.title,
    this.hint,
    this.action,
    this.padding = 72,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return MergeSemantics(
      child: Padding(
        padding: EdgeInsets.symmetric(vertical: padding, horizontal: 24),
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          const BrandCloud(size: 64, onGradient: false),
          const SizedBox(height: 14),
          Text(title,
              textAlign: TextAlign.center,
              style: TextStyle(
                  fontSize: 14.5,
                  fontWeight: FontWeight.w600,
                  color: cs.onSurfaceVariant)),
          if (hint != null) ...[
            const SizedBox(height: 6),
            Text(hint!,
                textAlign: TextAlign.center,
                style: TextStyle(
                    fontSize: 12.5, color: cs.onSurfaceVariant)),
          ],
          const SizedBox(height: 14),
          ?action,
        ]),
      ),
    );
  }
}

/// 品牌化文件类型图标：圆角方块 + 类型色 + 白字形（替代裸露 Material 图标）
///
/// 纯装饰：它总是紧邻文件名或扩展名文字出现（文件列表 / 最近上传），
/// 语义由那些文字承担，读屏跳过可避免"PDF"被念两遍。
class FileTypeTile extends StatelessWidget {
  final String ext; // 带点小写扩展名，如 .pdf
  final IconData icon;
  final double size;
  const FileTypeTile({
    super.key,
    required this.ext,
    required this.icon,
    this.size = 40,
  });

  @override
  Widget build(BuildContext context) {
    final color = fileTypeColor(ext);
    return ExcludeSemantics(
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [color.withValues(alpha: 0.85), color]),
          borderRadius: BorderRadius.circular(size * 0.28),
        ),
        child: Icon(icon, color: Colors.white, size: size * 0.52),
      ),
    );
  }
}

/// 分组卡片小标题（带品牌色小图标）
class SectionTitle extends StatelessWidget {
  final String title;
  final IconData? icon;
  final Widget? trailing;
  const SectionTitle({super.key, required this.title, this.icon, this.trailing});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(children: [
      if (icon != null) ...[
        // 小图标是语气强调，信息在标题文字里——不额外占用语义
        ExcludeSemantics(child: Icon(icon, size: 17, color: cs.primary)),
        const SizedBox(width: 6),
      ],
      Expanded(
        child: Text(title,
            style: TextStyle(
                fontSize: 13.5,
                fontWeight: FontWeight.w700,
                color: cs.onSurfaceVariant)),
      ),
      ?trailing,
    ]);
  }
}
