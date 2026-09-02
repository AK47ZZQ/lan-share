// 全局设计令牌与主题（2026-09-04 UI 重设计批，方向 A：深紫蓝 + 玻璃质感）。
// 品牌语言来自登录页 v2 的品牌头：primary→tertiary 渐变底 + 白底云朵圆角块
// （用户拍板：云朵图案 + 它的背景颜色作为品牌）。所有颜色/圆角/阴影/字号
// 收拢为 tokens，页面不再散用 Color(0xFF...)。
import 'package:flutter/material.dart';

/// ── 品牌常量（云朵 + 渐变底）──────────────────────────────
abstract final class Brand {
  /// 云朵图标（登录页品牌头的核心图案，全 App 复用）
  static const IconData cloud = Icons.cloud_outlined;

  /// 品牌渐变底（primary → tertiary，登录页品牌头同款）
  static const LinearGradient gradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF5B6CFF), Color(0xFF9C6BFF)],
  );

  /// 品牌头圆角（底部大弧，登录页同款）
  static const double headerRadius = 32;

  /// 云朵块的圆角
  static const double cloudTileRadius = 20;

  /// 卡片圆角（App 级）
  static const double cardRadius = 20;

  /// 小号控件圆角
  static const double controlRadius = 12;

  /// 卡片阴影（浅色）/ 发光（暗色）
  static List<BoxShadow> shadow(Color color) => [
        BoxShadow(
            color: color.withValues(alpha: 0.06),
            blurRadius: 18,
            offset: const Offset(0, 6)),
      ];
}

/// 文件类型 → 品牌色块（文件页统一图标语义：圆角方块 + 类型色 + 白字形）
Color fileTypeColor(String ext) {
  switch (ext) {
    case '.mp4':
    case '.mkv':
    case '.mov':
    case '.webm':
    case '.avi':
      return const Color(0xFF3B9EFF); // 视频：晴蓝
    case '.mp3':
    case '.flac':
    case '.wav':
    case '.m4a':
      return const Color(0xFFF2994A); // 音频：琥珀
    case '.zip':
    case '.7z':
    case '.rar':
      return const Color(0xFF9C6BFF); // 压缩包：品牌紫
    case '.pdf':
      return const Color(0xFFFF6B6B); // PDF：珊瑚
    case '.doc':
    case '.docx':
      return const Color(0xFF4E8DE0); // 文档：钢蓝
    case '.xls':
    case '.xlsx':
      return const Color(0xFF34A853); // 表格：草绿
    case '.jpg':
    case '.jpeg':
    case '.png':
    case '.webp':
    case '.gif':
    case '.bmp':
    case '.heic':
    case '.heif':
      return const Color(0xFF5B6CFF); // 图片：品牌蓝
    default:
      return const Color(0xFF8A93A6); // 其它：中性
  }
}

/// ── 主题组装 ──────────────────────────────────────────────
abstract final class AppTheme {
  /// 亮色：品牌蓝种子（延续 0xFF5B6CFF，加深为 SystemMaterial3 演进）
  static ThemeData light() => _build(Brightness.light, amoled: false);

  /// 暗色：会话专用深色分层，卡片降对比
  static ThemeData dark() => _build(Brightness.dark, amoled: false);

  /// AMOLED 纯黑：真正的 #000 底（照片墙/夜间观影友好），表面用 #12141F 系
  static ThemeData amoled() => _build(Brightness.dark, amoled: true);

  static ThemeData _build(Brightness brightness, {required bool amoled}) {
    final isDark = brightness == Brightness.dark;
    final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF5B6CFF),
      brightness: brightness,
      // 暗色下 surface 分层：非 AMOLED 用 M3 默认派生，AMOLED 压到近黑
      // （surface/表面层手动压低对比，卡片更"贴"底）
    );

    final baseSurface = amoled ? const Color(0xFF000000) : scheme.surface;
    final baseCanvas = amoled ? const Color(0xFF0B0D16) : scheme.surface;
    final cardColor =
        amoled ? const Color(0xFF12141F) : scheme.surfaceContainerLow;
    final dividerColor = scheme.outlineVariant.withValues(alpha: 0.5);
    final radius = Brand.cardRadius;

    final textTheme = _textTheme(brightness);

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme.copyWith(
        surface: baseSurface,
        surfaceContainerLowest: amoled ? const Color(0xFF000000) : scheme.surface,
        surfaceContainerLow: amoled ? const Color(0xFF10121B) : scheme.surfaceContainerLow,
        surfaceContainer: amoled ? const Color(0xFF151724) : scheme.surfaceContainer,
        surfaceContainerHigh: amoled ? const Color(0xFF1B1E2C) : scheme.surfaceContainerHigh,
        surfaceContainerHighest: amoled ? const Color(0xFF22253A) : scheme.surfaceContainerHighest,
      ),
      scaffoldBackgroundColor: baseSurface,
      canvasColor: baseCanvas,
      textTheme: textTheme,
      appBarTheme: AppBarTheme(
        backgroundColor: baseSurface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0.5,
        centerTitle: false,
        titleTextStyle: textTheme.titleLarge?.copyWith(
            fontWeight: FontWeight.w700, color: scheme.onSurface),
        iconTheme: IconThemeData(color: scheme.onSurfaceVariant),
      ),
      // 品牌圆角：全局基础形状阶梯
      cardTheme: CardThemeData(
        elevation: 0,
        color: cardColor,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radius),
          side: BorderSide(color: dividerColor, width: 0.6),
        ),
        margin: EdgeInsets.zero,
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: baseSurface,
        surfaceTintColor: Colors.transparent,
        indicatorColor: scheme.primary.withValues(alpha: isDark ? 0.22 : 0.14),
        indicatorShape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14)),
        labelTextStyle: WidgetStatePropertyAll(TextStyle(
            fontSize: 11.5,
            fontWeight: FontWeight.w600,
            color: scheme.onSurfaceVariant)),
        height: 68,
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(Brand.controlRadius)),
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 13),
          textStyle: const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(Brand.controlRadius)),
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
          side: BorderSide(color: dividerColor),
          textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(Brand.controlRadius))),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: isDark
            ? scheme.surfaceContainerHighest.withValues(alpha: 0.35)
            : scheme.surfaceContainerHighest.withValues(alpha: 0.45),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Brand.controlRadius),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Brand.controlRadius),
          borderSide: BorderSide(color: dividerColor, width: 0.8),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Brand.controlRadius),
          borderSide: BorderSide(color: scheme.primary, width: 1.6),
        ),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: amoled ? const Color(0xFF161827) : scheme.surfaceContainerHigh,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Brand.cardRadius)),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: amoled ? const Color(0xFF12141F) : scheme.surfaceContainerLow,
        surfaceTintColor: Colors.transparent,
        shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Brand.controlRadius)),
        backgroundColor: isDark ? const Color(0xFF2A2D3D) : const Color(0xFF23253A),
      ),
      listTileTheme: ListTileThemeData(
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Brand.controlRadius)),
        iconColor: scheme.onSurfaceVariant,
      ),
      chipTheme: ChipThemeData(
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(999)),
        side: BorderSide(color: dividerColor),
        backgroundColor: amoled ? const Color(0xFF1B1E2C) : scheme.surfaceContainerLow,
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: amoled ? const Color(0xFF161827) : scheme.surfaceContainerHigh,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Brand.controlRadius)),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: scheme.primary,
        linearTrackColor: scheme.primary.withValues(alpha: 0.12),
        circularTrackColor: scheme.primary.withValues(alpha: 0.12),
      ),
      dividerTheme: DividerThemeData(color: dividerColor, thickness: 0.6),
      // 页面转场：淡入 + 8px 上移（克制、全家桶统一；不碰相机/地图等）
      pageTransitionsTheme: PageTransitionsTheme(builders: {
        TargetPlatform.android: const _FadeSlideTransitionsBuilder(),
        TargetPlatform.iOS: const _FadeSlideTransitionsBuilder(),
      }),
    );
  }

  static TextTheme _textTheme(Brightness b) {
    final base = b == Brightness.dark
        ? Typography.material2021(platform: TargetPlatform.android).black
        : Typography.material2021(platform: TargetPlatform.android).white;
    return base.copyWith(
      titleLarge: base.titleLarge?.copyWith(
          fontSize: 18, fontWeight: FontWeight.w700, letterSpacing: 0),
      titleMedium: base.titleMedium?.copyWith(
          fontSize: 15.5, fontWeight: FontWeight.w600),
      bodyLarge: base.bodyLarge?.copyWith(fontSize: 15),
      bodyMedium: base.bodyMedium?.copyWith(fontSize: 13.5, height: 1.45),
      bodySmall: base.bodySmall?.copyWith(fontSize: 12, height: 1.4),
      labelLarge: base.labelLarge?.copyWith(fontSize: 14, fontWeight: FontWeight.w600),
    );
  }
}

/// 8ms 淡入 + 8px 上移的页面转场（方向 A 的克制动效；长度与系统
/// ZoomPageTransition 相仿，不拖慢图片墙等重度页面）
class _FadeSlideTransitionsBuilder extends PageTransitionsBuilder {
  const _FadeSlideTransitionsBuilder();

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    if (route.settings.name == '/no-animation') return child;
    final curved = CurvedAnimation(
        parent: animation, curve: Curves.easeOutCubic, reverseCurve: Curves.easeInCubic);
    return FadeTransition(
      opacity: curved,
      child: SlideTransition(
        position: Tween<Offset>(begin: const Offset(0, 0.012), end: Offset.zero)
            .animate(curved),
        child: child,
      ),
    );
  }
}