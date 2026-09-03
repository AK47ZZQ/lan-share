// 全局设计令牌与主题（0.8.4 方向 A：深紫蓝 + 玻璃质感；0.8.5 主题引擎）。
// 品牌语言来自登录页 v2 的品牌头：渐变底 + 白底云朵圆角块（用户拍板：
// 云朵图案 + 它的背景颜色作为品牌）。0.8.5 起配色收拢为 Palette 色板：
// 6 套预置（种子色 + 品牌渐变 + 深色底）+ Material You 跟随壁纸，
// 圆角/阴影/字号仍是全局 tokens，页面不再散用 Color(0xFF...)。
import 'package:flutter/material.dart';

/// ── 主题色板（0.8.5 主题引擎）────────────────────────────
/// 每套色板 = M3 种子色 + 品牌渐变（云朵块所在的品牌头）+ 深色底。
/// 深色不再是 M3 中性灰：用带色板色相的深底（默认云朵紫蓝对应方向三
/// 的深海军 #0B1020），暗色层次统一由 [AppTheme._build] 从底色派生。
class Palette {
  final String id;
  final String name;
  final Color seed;
  final List<Color> gradient; // [起, 止]：品牌头渐变
  final Color darkBg; // 非 AMOLED 暗色的页面底色
  const Palette({
    required this.id,
    required this.name,
    required this.seed,
    required this.gradient,
    required this.darkBg,
  });
}

abstract final class Palettes {
  /// 云朵紫蓝（默认，v0.8.4 起的品牌色；暗底=深海军）
  static const violet = Palette(
      id: 'violet',
      name: '云朵紫蓝',
      seed: Color(0xFF5B6CFF),
      gradient: [Color(0xFF5B6CFF), Color(0xFF9C6BFF)],
      darkBg: Color(0xFF0B1020));
  static const teal = Palette(
      id: 'teal',
      name: '晨雾青',
      seed: Color(0xFF14B8A6),
      gradient: [Color(0xFF0D9488), Color(0xFF34D399)],
      darkBg: Color(0xFF0A1512));
  static const warm = Palette(
      id: 'warm',
      name: '暖沙陶土',
      seed: Color(0xFFE07A5F),
      gradient: [Color(0xFFE07A5F), Color(0xFFF2B662)],
      darkBg: Color(0xFF181009));
  static const rose = Palette(
      id: 'rose',
      name: '玫瑰落日',
      seed: Color(0xFFE85D8A),
      gradient: [Color(0xFFE85D8A), Color(0xFFF2994A)],
      darkBg: Color(0xFF170D12));
  static const forest = Palette(
      id: 'forest',
      name: '墨绿',
      seed: Color(0xFF2F7D5D),
      gradient: [Color(0xFF2F7D5D), Color(0xFF6FBF9F)],
      darkBg: Color(0xFF0A130E));
  static const slate = Palette(
      id: 'slate',
      name: '极简灰',
      seed: Color(0xFF64748B),
      gradient: [Color(0xFF475569), Color(0xFF94A3B8)],
      darkBg: Color(0xFF0E1013));

  static const all = [violet, teal, warm, rose, forest, slate];

  /// "跟随壁纸"（Material You，Android 12+）——不是色板 id，是 accent 取值
  static const auto = 'auto';
}

/// accent 字符串 → 色板；未知值回落云朵紫蓝（默认品牌，防脏数据）。
Palette paletteById(String? id) =>
    Palettes.all.firstWhere((p) => p.id == id, orElse: () => Palettes.violet);

/// 当前生效色板的作用域（MaterialApp.builder 挂载）：品牌头/云朵块等
/// 组件从这里取渐变，切色板即全 App 生效；作用域外回落云朵紫蓝。
class PaletteScope extends InheritedWidget {
  final Palette palette;
  const PaletteScope({super.key, required this.palette, required super.child});

  static Palette of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<PaletteScope>()?.palette ??
      Palettes.violet;

  @override
  bool updateShouldNotify(PaletteScope oldWidget) =>
      oldWidget.palette != palette;
}

/// ── 品牌常量（云朵 + 渐变底）──────────────────────────────
abstract final class Brand {
  /// 云朵图标（登录页品牌头的核心图案，全 App 复用）
  static const IconData cloud = Icons.cloud_outlined;

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
  /// 亮色：种子色派生 M3 scheme（中性表面带一点色板色相）
  static ThemeData light(Palette p) =>
      _build(p, Brightness.light, amoled: false);

  /// 暗色：色板深底（云朵紫蓝=深海军）向上派生表面阶梯
  static ThemeData dark(Palette p) => _build(p, Brightness.dark, amoled: false);

  /// AMOLED 纯黑：真正的 #000 底（照片墙/夜间观影友好），表面仅余极淡
  /// 的种子色相提升，任何色板下都和谐
  static ThemeData amoled(Palette p) =>
      _build(p, Brightness.dark, amoled: true);

  static Color _mix(Color a, Color b, double t) => Color.lerp(a, b, t)!;

  /// 从底色向上抬升的表面阶梯；AMOLED 下再掺 6% 种子色相去死灰
  static Color _tier(Palette p, Color base, double lift, bool amoled) {
    final c = _mix(base, Colors.white, lift);
    return amoled ? _mix(c, p.seed, 0.06) : c;
  }

  static ThemeData _build(Palette p, Brightness brightness,
      {required bool amoled}) {
    final isDark = brightness == Brightness.dark;
    final scheme =
        ColorScheme.fromSeed(seedColor: p.seed, brightness: brightness);

    // 表面分层：亮色走 M3 派生；暗色从色板深底向上派生（AMOLED 底=纯黑）
    final Color baseSurface, baseCanvas, cardColor, dialogBg, sheetBg, chipBg, snackBg;
    final Color cLowest, cLow, cContainer, cHigh, cHighest;
    if (!isDark) {
      baseSurface = scheme.surface;
      baseCanvas = scheme.surface;
      cardColor = scheme.surfaceContainerLow;
      cLowest = scheme.surface;
      cLow = scheme.surfaceContainerLow;
      cContainer = scheme.surfaceContainer;
      cHigh = scheme.surfaceContainerHigh;
      cHighest = scheme.surfaceContainerHighest;
      dialogBg = scheme.surfaceContainerHigh;
      sheetBg = scheme.surfaceContainerLow;
      chipBg = scheme.surfaceContainerLow;
      snackBg = const Color(0xFF23253A);
    } else if (amoled) {
      const black = Colors.black;
      baseSurface = black;
      baseCanvas = black;
      cLowest = black;
      cLow = _tier(p, black, 0.055, true);
      cContainer = _tier(p, black, 0.085, true);
      cHigh = _tier(p, black, 0.12, true);
      cHighest = _tier(p, black, 0.17, true);
      cardColor = _tier(p, black, 0.07, true);
      dialogBg = _tier(p, black, 0.09, true);
      sheetBg = _tier(p, black, 0.07, true);
      chipBg = _tier(p, black, 0.11, true);
      snackBg = _tier(p, black, 0.15, true);
    } else {
      final bg = p.darkBg;
      baseSurface = bg;
      baseCanvas = bg;
      cLowest = bg;
      cLow = _tier(p, bg, 0.035, false);
      cContainer = _tier(p, bg, 0.06, false);
      cHigh = _tier(p, bg, 0.09, false);
      cHighest = _tier(p, bg, 0.13, false);
      cardColor = cLow;
      dialogBg = _tier(p, bg, 0.08, false);
      sheetBg = cLow;
      chipBg = cContainer;
      snackBg = _tier(p, bg, 0.16, false);
    }
    final dividerColor = scheme.outlineVariant.withValues(alpha: 0.5);
    final radius = Brand.cardRadius;

    final textTheme = _textTheme(brightness);

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme.copyWith(
        surface: baseSurface,
        surfaceContainerLowest: cLowest,
        surfaceContainerLow: cLow,
        surfaceContainer: cContainer,
        surfaceContainerHigh: cHigh,
        surfaceContainerHighest: cHighest,
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
        backgroundColor: dialogBg,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Brand.cardRadius)),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: sheetBg,
        surfaceTintColor: Colors.transparent,
        shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Brand.controlRadius)),
        backgroundColor: snackBg,
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
        backgroundColor: chipBg,
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: dialogBg,
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