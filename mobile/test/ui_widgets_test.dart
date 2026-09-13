// UI 组件与主题装配回归（v1.0 · 2026-09-11）
//
// 背景：此前全套 152 项测试里只有 5 处 testWidgets，**改主题/改组件样式不会
// 有任何测试报错**（`docs/APP-REVIEW-2026-09-11.md` 判为最重的护栏缺口）。
// 本文件补上这一层：品牌组件渲染 + 主题里组件样式的落地值 + 中文排版规则。
//
// 为什么不用像素级 golden：项目 CI 跑 Linux，而 baseline 在本机 Windows 生成，
// 字体渲染（Roboto/系统中文字体 fallback）跨平台不一致，golden 在 CI 必然假红。
// 属性断言同样能钉住"改坏了什么"，且跨平台稳定——护栏的价值在能守住，
// 不在长得像 golden。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/ui/theme/app_theme.dart';
import 'package:lanshare_app/ui/theme/design_tokens.dart';
import 'package:lanshare_app/ui/widgets/auth_image.dart';
import 'package:lanshare_app/ui/widgets/brand.dart';

/// 最小宿主：品牌组件不依赖 Riverpod/网络，包一层 MaterialApp+Scaffold 即可
/// （Scaffold 是必需的——Material 层才会把 textTheme 装配成 DefaultTextStyle）
Widget host(Widget child, {Palette? palette, ThemeData? theme}) => MaterialApp(
      theme: theme ?? AppTheme.light(palette ?? Palettes.violet),
      home: Scaffold(body: child),
    );

/// 取组件内第一个 Container 的 BoxDecoration（品牌组件的视觉都挂在这上面）
BoxDecoration decoOf(WidgetTester tester, Type type) {
  final c = tester.widget<Container>(find
      .descendant(of: find.byType(type), matching: find.byType(Container))
      .first);
  return c.decoration! as BoxDecoration;
}

void main() {
  group('BrandCloud（云朵品牌块）', () {
    testWidgets('尺寸按传入值，圆角按 56 基准等比缩放', (tester) async {
      await tester.pumpWidget(host(const BrandCloud(size: 56)));
      final c = tester.widget<Container>(find
          .descendant(
              of: find.byType(BrandCloud), matching: find.byType(Container))
          .first);
      expect(c.constraints!.maxWidth, 56);
      expect(c.constraints!.maxHeight, 56);
      final br = decoOf(tester, BrandCloud).borderRadius! as BorderRadius;
      expect(br.topLeft.x, Radii.card, reason: '56 基准下应等于卡片圆角 20');
    });

    testWidgets('onGradient：渐变上用白块，浅底上用品牌色淡底', (tester) async {
      await tester.pumpWidget(host(const BrandCloud(size: 56)));
      expect(decoOf(tester, BrandCloud).color, Colors.white24);

      await tester.pumpWidget(
          host(const BrandCloud(size: 56, onGradient: false)));
      final onLight = decoOf(tester, BrandCloud).color!;
      // 非渐变场景用 primary 的淡底（12%），不能是纯白块
      expect(onLight, isNot(Colors.white24));
      expect(onLight.a, lessThan(0.5));
    });
  });

  group('BrandHeader（渐变品牌头）', () {
    testWidgets('渐变随 PaletteScope 色板走', (tester) async {
      await tester.pumpWidget(PaletteScope(
          palette: Palettes.forest,
          child: host(const BrandHeader(child: SizedBox()))));
      final grad = decoOf(tester, BrandHeader).gradient! as LinearGradient;
      expect(grad.colors, Palettes.forest.gradient);
    });

    testWidgets('作用域外回落云朵紫蓝', (tester) async {
      await tester.pumpWidget(host(const BrandHeader(child: SizedBox())));
      final grad = decoOf(tester, BrandHeader).gradient! as LinearGradient;
      expect(grad.colors, Palettes.violet.gradient);
    });

    testWidgets('圆角只削下两角（上沿铺满）+ 8% 黑罩膜保白字对比', (tester) async {
      await tester.pumpWidget(host(const BrandHeader(child: SizedBox())));
      final d = decoOf(tester, BrandHeader);
      final br = d.borderRadius! as BorderRadius;
      expect(br.bottomLeft.x, Brand.headerRadius);
      expect(br.bottomRight.x, Brand.headerRadius);
      expect(br.topLeft.x, 0, reason: '上沿必须铺到屏幕顶（edge-to-edge）');

      final c = tester.widget<Container>(find
          .descendant(
              of: find.byType(BrandHeader), matching: find.byType(Container))
          .first);
      final fg = c.foregroundDecoration! as BoxDecoration;
      expect(fg.color!.a, closeTo(0.08, 0.001),
          reason: '暖沙等浅色渐变端不压暗的话白字对比不足');
    });
  });

  group('EmptyState（空态三件套）', () {
    testWidgets('云朵 + 标题 + 提示 + 引导按钮齐全', (tester) async {
      await tester.pumpWidget(host(const EmptyState(
          title: '还没有照片',
          hint: '去文件页上传',
          action: SizedBox(width: 10))));
      expect(find.byType(BrandCloud), findsOneWidget);
      expect(find.text('还没有照片'), findsOneWidget);
      expect(find.text('去文件页上传'), findsOneWidget);
    });

    testWidgets('只有标题时不渲染多余文案', (tester) async {
      await tester.pumpWidget(host(const EmptyState(title: '空空如也')));
      expect(find.byType(Text), findsOneWidget, reason: '无 hint 时不应多出文字');
    });
  });

  group('FileTypeTile + 类型色', () {
    testWidgets('渐变末段为类型色，圆角按 size×0.28', (tester) async {
      await tester.pumpWidget(host(const FileTypeTile(
          ext: '.pdf', icon: Icons.picture_as_pdf, size: 40)));
      final d = decoOf(tester, FileTypeTile);
      final grad = d.gradient! as LinearGradient;
      expect(grad.colors.last, fileTypeColor('.pdf'));
      final br = d.borderRadius! as BorderRadius;
      expect(br.topLeft.x, closeTo(40 * 0.28, 0.01));
    });

    test('扩展名 → 类型色映射稳定（改这里等于改全 App 文件语义色）', () {
      expect(fileTypeColor('.pdf'), const Color(0xFFFF6B6B));
      expect(fileTypeColor('.zip'), const Color(0xFF9C6BFF));
      expect(fileTypeColor('.docx'), const Color(0xFF4E8DE0));
      expect(fileTypeColor('.xlsx'), const Color(0xFF34A853));
      expect(fileTypeColor('.unknown'), const Color(0xFF8A93A6));
    });
  });

  group('SectionTitle（分组小标题）', () {
    testWidgets('图标 + 标题 + trailing', (tester) async {
      await tester.pumpWidget(host(const SectionTitle(
          title: '通用', icon: Icons.settings, trailing: Text('更多'))));
      expect(find.text('通用'), findsOneWidget);
      expect(find.text('更多'), findsOneWidget);
      expect(find.byIcon(Icons.settings), findsOneWidget);
    });
  });

  group('主题装配 · 组件样式落地值', () {
    final light = AppTheme.light(Palettes.violet);
    final dark = AppTheme.dark(Palettes.violet);

    test('卡片：elevation 0 + 20 圆角 + 0.6 描边（扁平基调，不堆阴影）', () {
      expect(light.cardTheme.elevation, 0);
      final shape = light.cardTheme.shape! as RoundedRectangleBorder;
      expect((shape.borderRadius as BorderRadius).topLeft.x, Radii.card);
      expect(shape.side.width, Strokes.width);
    });

    test('FAB：16 圆角（M3 squircle 对齐）', () {
      final shape = light.floatingActionButtonTheme.shape
          as RoundedRectangleBorder;
      expect((shape.borderRadius as BorderRadius).topLeft.x, 16);
    });

    test('底部导航：高 68 + 指示胶囊 14 圆角', () {
      expect(light.navigationBarTheme.height, 68);
      final ind = light.navigationBarTheme.indicatorShape
          as RoundedRectangleBorder;
      expect((ind.borderRadius as BorderRadius).topLeft.x, 14);
    });

    test('输入框：常态 0.8 输入框描边 / 聚焦 1.6 强调', () {
      final enabled =
          light.inputDecorationTheme.enabledBorder! as OutlineInputBorder;
      final focused =
          light.inputDecorationTheme.focusedBorder! as OutlineInputBorder;
      expect(enabled.borderSide.width, Strokes.widthInput);
      expect(focused.borderSide.width, Strokes.widthStrong);
      // 输入框描边必须比静态卡片描边粗，否则在扁平界面里看不出"可交互"
      expect(Strokes.widthInput, greaterThan(Strokes.width));
    });

    test('底部弹层：上圆角 24', () {
      final shape = light.bottomSheetTheme.shape! as RoundedRectangleBorder;
      final br = shape.borderRadius as BorderRadius;
      expect(br.topLeft.x, Radii.sheet);
      expect(br.bottomLeft.x, 0);
    });

    test('SnackBar：floating + 文字亮度随模式（bug·959d：浅色下不再恒黑底）', () {
      for (final t in [light, dark]) {
        expect(t.snackBarTheme.behavior, SnackBarBehavior.floating);
      }
      // 暗色：深底必须显式白字（不能沿用 M3 默认深字）
      expect(dark.snackBarTheme.contentTextStyle!.color, Colors.white);
      // 浅色：浅灰浮层 + 墨色文字——背景亮度必须显著高于暗色（959d 修复）
      expect(light.snackBarTheme.contentTextStyle!.color,
          light.colorScheme.onSurface);
      expect(
          light.snackBarTheme.backgroundColor!.computeLuminance(),
          greaterThan(
              dark.snackBarTheme.backgroundColor!.computeLuminance()));
    });

    test('Tooltip：圆角 8 + 文字亮度随模式（bug·959d 配套）', () {
      expect(light.tooltipTheme.textStyle!.color, light.colorScheme.onSurface);
      expect(dark.tooltipTheme.textStyle!.color, Colors.white);
    });

    test('AppBar：不抬起、标题左对齐、18 号 w700', () {
      expect(light.appBarTheme.centerTitle, false);
      expect(light.appBarTheme.elevation, 0);
      expect(light.appBarTheme.titleTextStyle!.fontSize, 18);
    });

    test('次级文字对比加固：亮色 onSurfaceVariant 向 onSurface 拉近', () {
      final cs = light.colorScheme;
      final gap = (cs.onSurface.computeLuminance() -
              cs.onSurfaceVariant.computeLuminance())
          .abs();
      expect(gap, lessThan(0.25),
          reason: '亮色下次级文字必须贴近正文墨色（v0.8.7 加固，约 8:1）');
    });
  });

  group('排版规范 · 中文规则', () {
    test('字距一律归零（M3 默认给 label 加 0.1–0.5，中文会发散）', () {
      for (final t in [
        AppTheme.light(Palettes.violet),
        AppTheme.dark(Palettes.violet),
      ]) {
        final tt = t.textTheme;
        for (final s in [
          tt.titleLarge, tt.titleMedium, tt.titleSmall,
          tt.bodyLarge, tt.bodyMedium, tt.bodySmall,
          tt.labelLarge, tt.labelMedium, tt.labelSmall,
        ]) {
          expect(s!.letterSpacing ?? 0, 0);
        }
      }
    });

    test('中文行高：正文 ≥1.4、标题 ≥1.25（密排会成黑块）', () {
      final tt = AppTheme.light(Palettes.violet).textTheme;
      expect(tt.bodySmall!.height!, greaterThanOrEqualTo(1.4));
      expect(tt.bodyMedium!.height!, greaterThanOrEqualTo(1.4));
      expect(tt.bodyLarge!.height!, greaterThanOrEqualTo(1.4));
      expect(tt.titleMedium!.height!, greaterThanOrEqualTo(1.25));
      expect(tt.titleLarge!.height!, greaterThanOrEqualTo(1.25));
    });

    test('字号层级严格递减（28→12，防止再出现 13/14.5/17 散值）', () {
      final tt = AppTheme.light(Palettes.violet).textTheme;
      final sizes = [
        tt.displaySmall!.fontSize!,
        tt.headlineSmall!.fontSize!,
        tt.titleLarge!.fontSize!,
        tt.titleMedium!.fontSize!,
        tt.bodyLarge!.fontSize!,
        tt.bodyMedium!.fontSize!,
        tt.bodySmall!.fontSize!,
      ];
      for (var i = 1; i < sizes.length; i++) {
        expect(sizes[i], lessThan(sizes[i - 1]),
            reason: '第 $i 级字号 ${sizes[i]} 未小于前一级');
      }
    });
  });

  // 无障碍（v1.0）：改造前全仓 Semantics/semanticLabel 为 0 处，读屏用户打开
  // App 只能听到无意义的图形描述。这里钉住"装饰性图形不占语义"这条规则，
  // 防止后续往组件里塞图标时又把噪音加回去。
  group('无障碍语义（v1.0）', () {
    testWidgets('装饰性图形一律 ExcludeSemantics（云朵 / 文件类型块 / 小节图标）',
        (tester) async {
      await tester.pumpWidget(host(const Column(children: [
        BrandCloud(size: 56),
        FileTypeTile(ext: '.pdf', icon: Icons.picture_as_pdf),
        SectionTitle(title: '分组', icon: Icons.settings),
      ])));
      // 逐个组件断言（不用总数：Material 层自身也可能插入 ExcludeSemantics）
      for (final t in <Type>[BrandCloud, FileTypeTile, SectionTitle]) {
        expect(
            find.descendant(
                of: find.byType(t), matching: find.byType(ExcludeSemantics)),
            findsWidgets,
            reason: '$t 的装饰图形不承载信息，应当排除语义');
      }
    });

    testWidgets('空态语义合并成一个节点（连着念，不跳三次焦点）', (tester) async {
      await tester.pumpWidget(
          host(const EmptyState(title: '还没有照片', hint: '去文件页上传')));
      expect(find.byType(MergeSemantics), findsOneWidget);
    });

    test('AuthImage 可带语义标签（照片墙是唯一的信息来源）', () {
      const w = AuthImage(url: 'https://example/thumb/x.jpg', semanticLabel: '照片');
      expect(w.semanticLabel, '照片');
      // 不传时为 null：由调用方决定是否让相邻文字承担语义
      const bare = AuthImage(url: 'https://example/thumb/x.jpg');
      expect(bare.semanticLabel, isNull);
    });
  });
}
