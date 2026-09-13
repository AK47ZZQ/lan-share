// 全局设计令牌与主题（0.8.4 方向 A：深紫蓝 + 玻璃质感；0.8.5 主题引擎）。
// 品牌语言来自登录页 v2 的品牌头：渐变底 + 白底云朵圆角块（用户拍板：
// 云朵图案 + 它的背景颜色作为品牌）。0.8.5 起配色收拢为 Palette 色板：
// 6 套预置（种子色 + 品牌渐变 + 深色底）+ Material You 跟随壁纸，
// 圆角/阴影/字号仍是全局 tokens，页面不再散用 Color(0xFF...)。
import 'package:flutter/material.dart';

import '../../data/media_kinds.dart';
import 'design_tokens.dart';

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
/// v1.0 起圆角/阴影改由 [Radii] / [Shadows] 供给（单一事实源），此处保留
/// Brand 作为语义别名——既有页面继续用 Brand.xxx 不受影响。
abstract final class Brand {
  /// 云朵图标（登录页品牌头的核心图案，全 App 复用）
  static const IconData cloud = Icons.cloud_outlined;

  /// 品牌头圆角（底部大弧，登录页同款）
  static const double headerRadius = Radii.header;

  /// 云朵块的圆角
  static const double cloudTileRadius = Radii.card;

  /// 卡片圆角（App 级）
  static const double cardRadius = Radii.card;

  /// 小号控件圆角
  static const double controlRadius = Radii.control;

  /// 底部弹层上圆角
  static const double sheetRadius = Radii.sheet;

  /// 卡片阴影（浅色）/ 发光（暗色）
  static List<BoxShadow> shadow(Color color) => Shadows.low(color);
}

/// 文件类型 → 品牌色块（文件页统一图标语义：圆角方块 + 类型色 + 白字形）
/// 媒体分组取单一事实源 data/media_kinds.dart（07 审查 §4.1-2：原 switch
/// 手写 case 已漂移，.m4v/.3gp/.ogg 等落默认灰）；压缩包/文档是图标专属
/// 分类，留在本地
Color fileTypeColor(String ext) {
  if (kVidExts.contains(ext)) return const Color(0xFF3B9EFF); // 视频：晴蓝
  if (kAudExts.contains(ext)) return const Color(0xFFF2994A); // 音频：琥珀
  if (kImgExts.contains(ext)) return const Color(0xFF5B6CFF); // 图片：品牌蓝
  switch (ext) {
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
      // 浅色模式 SnackBar 用浅灰浮层（bug·959d：此前硬编码深海军，浅色下
      // 「欢迎，debug」等所有 SnackBar 恒黑色不随主题变）；深色分支保持深底
      snackBg = cHighest;
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
        // 次级文字对比加固：M3 默认 onSurfaceVariant 偏浅（暖色系色板更明显），
        // 亮色下向 onSurface 拉近 35%（约 8:1 对比）；暗色默认已达标不动
        onSurfaceVariant: isDark
            ? scheme.onSurfaceVariant
            : Color.lerp(scheme.onSurfaceVariant, scheme.onSurface, 0.35)!,
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
        iconTheme: IconThemeData(
            color: scheme.onSurfaceVariant, size: IconSizes.lg),
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
            borderRadius: Radii.all(Radii.md)),
        labelTextStyle: WidgetStatePropertyAll(TextStyle(
            fontSize: 11.5,
            fontWeight: FontWeight.w600,
            color: scheme.onSurfaceVariant)),
        height: 68,
      ),
      // 悬浮动作按钮（文件页"上传"）：品牌色 + 16 圆角 + 浮层阴影。
      // M3 默认 FAB 是 16 squircle，这里显式对齐并收窄阴影范围，避免
      // 在浅色卡片流里出现一块过重的色斑
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: scheme.primary,
        foregroundColor: scheme.onPrimary,
        elevation: 2,
        focusElevation: 3,
        hoverElevation: 3,
        highlightElevation: 1,
        shape: RoundedRectangleBorder(borderRadius: Radii.all(Radii.fab)),
        extendedTextStyle:
            const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w600),
      ),
      // 图标按钮：品牌圆角水波纹（尺寸不动，避免打乱 AppBar/列表行布局）
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          shape:
              RoundedRectangleBorder(borderRadius: Radii.bControl),
        ),
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
          borderSide: BorderSide(color: dividerColor, width: Strokes.widthInput),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(Brand.controlRadius),
          borderSide:
              BorderSide(color: scheme.primary, width: Strokes.widthStrong),
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
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.vertical(
                top: Radius.circular(Radii.sheet))),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Brand.controlRadius)),
        backgroundColor: snackBg,
        // 文字亮度随模式：暗色深底压白字（否则 M3 默认 onInverseSurface 深
        // 字深底看不清）；浅色浅底用 onSurface 墨色（bug·959d 配套）
        contentTextStyle: TextStyle(
            color: isDark ? Colors.white : scheme.onSurface, fontSize: 14),
        actionTextColor:
            isDark ? const Color(0xFF8F9BFF) : scheme.primary,
      ),
      listTileTheme: ListTileThemeData(
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Brand.controlRadius)),
        iconColor: scheme.onSurfaceVariant,
      ),
      chipTheme: ChipThemeData(
        shape: RoundedRectangleBorder(
            borderRadius: Radii.all(Radii.pill)),
        side: BorderSide(color: dividerColor),
        backgroundColor: chipBg,
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: dialogBg,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Brand.controlRadius)),
      ),
      // 长按提示（桌面端 hover 也走它）：与 SnackBar 同语言（底色随模式，
      // bug·959d 配套——浅色下 tooltip 也换浅底墨字）
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
            color: snackBg, borderRadius: Radii.bXs),
        textStyle: TextStyle(
            color: isDark ? Colors.white : scheme.onSurface,
            fontSize: 12.5),
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
    // 墨水色按"背景亮度"取反：亮背景用黑墨（.black），暗背景用白墨（.white）。
    // 0.8.4 曾映射写反——亮色主题全 App 默认文字变白字画浅卡（主页正文不可见），
    // 已有 DefaultTextStyle 墨水方向的回归测试钉死。
    final base = b == Brightness.dark
        ? Typography.material2021(platform: TargetPlatform.android).white
        : Typography.material2021(platform: TargetPlatform.android).black;
    // v1.0 排版规范：字号收敛为 8 级（28→11.5），标题行高 1.25~1.4、
    // 正文 1.45~1.5——中文行高必须大于英文，否则密排成"黑块"；
    // letterSpacing 一律归零，M3 默认给 label 加了 0.1~0.5 字距，
    // 中文字符间会显得松散。
    return base.copyWith(
      displaySmall: base.displaySmall?.copyWith(
          fontSize: 28,
          fontWeight: FontWeight.w700,
          height: 1.25,
          letterSpacing: 0),
      headlineSmall: base.headlineSmall?.copyWith(
          fontSize: 22,
          fontWeight: FontWeight.w700,
          height: 1.3,
          letterSpacing: 0),
      titleLarge: base.titleLarge?.copyWith(
          fontSize: 18,
          fontWeight: FontWeight.w700,
          height: 1.3,
          letterSpacing: 0),
      titleMedium: base.titleMedium?.copyWith(
          fontSize: 15.5,
          fontWeight: FontWeight.w600,
          height: 1.35,
          letterSpacing: 0),
      titleSmall: base.titleSmall?.copyWith(
          fontSize: 13.5,
          fontWeight: FontWeight.w600,
          height: 1.4,
          letterSpacing: 0),
      bodyLarge: base.bodyLarge?.copyWith(
          fontSize: 15, height: 1.5, letterSpacing: 0),
      bodyMedium: base.bodyMedium?.copyWith(
          fontSize: 13.5, height: 1.45, letterSpacing: 0),
      bodySmall: base.bodySmall?.copyWith(
          fontSize: 12, height: 1.4, letterSpacing: 0),
      labelLarge: base.labelLarge?.copyWith(
          fontSize: 14, fontWeight: FontWeight.w600, letterSpacing: 0),
      labelMedium: base.labelMedium?.copyWith(
          fontSize: 12.5, fontWeight: FontWeight.w600, letterSpacing: 0),
      labelSmall: base.labelSmall?.copyWith(
          fontSize: 11.5, fontWeight: FontWeight.w600, letterSpacing: 0),
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
    // 页面转场曲线取自 Motion 令牌（enter/exit），改令牌即全站转场统一变
    final curved = CurvedAnimation(
        parent: animation, curve: Motion.enter, reverseCurve: Motion.exit);
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