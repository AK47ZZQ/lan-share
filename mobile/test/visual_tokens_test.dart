// 视觉规范单测（v1.0）：设计令牌取值阶梯 + 响应式断点行为。
//
// 为什么要钉：令牌是"全局视觉一致"的地基，值被无意改动会静默影响全部
// 页面（类似 v0.8.4 墨水方向写反的教训——没有测试就没人发现）。这里把
// 关键阶梯与断点边界写死，改值必须是有意识的决定；测试即规范文档。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/ui/theme/app_theme.dart';
import 'package:lanshare_app/ui/theme/design_tokens.dart';
import 'package:lanshare_app/ui/theme/responsive.dart';

void main() {
  // ── 设计令牌 ────────────────────────────────────────────
  group('间距令牌', () {
    test('阶梯严格递增', () {
      final ladder = [
        Gap.xxs, Gap.xs, Gap.sm, Gap.md,
        Gap.lg, Gap.xl, Gap.xxl, Gap.xxxl,
      ];
      for (var i = 1; i < ladder.length; i++) {
        expect(ladder[i], greaterThan(ladder[i - 1]),
            reason: '第 $i 档必须大于前一档');
      }
    });

    test('全部落在 4pt 基准网格上', () {
      for (final v in [
        Gap.xxs, Gap.xs, Gap.sm, Gap.md,
        Gap.lg, Gap.xl, Gap.xxl, Gap.xxxl,
      ]) {
        expect(v % 4, 0, reason: '$v 不是 4 的整数倍');
      }
    });
  });

  group('圆角令牌', () {
    test('层级：小控件 < 卡片 < 品牌头', () {
      expect(Radii.control, lessThan(Radii.card));
      expect(Radii.card, lessThan(Radii.header));
      expect(Radii.xs, lessThan(Radii.control));
    });

    test('Brand 常量与 Radii 同源（单一事实源不漂移）', () {
      expect(Brand.cardRadius, Radii.card);
      expect(Brand.controlRadius, Radii.control);
      expect(Brand.headerRadius, Radii.header);
      expect(Brand.cloudTileRadius, Radii.card);
      expect(Brand.sheetRadius, Radii.sheet);
    });
  });

  group('动效令牌', () {
    test('时长阶梯递增', () {
      expect(Motion.instant.inMilliseconds,
          lessThan(Motion.fast.inMilliseconds));
      expect(
          Motion.fast.inMilliseconds, lessThan(Motion.normal.inMilliseconds));
      expect(
          Motion.normal.inMilliseconds, lessThan(Motion.slow.inMilliseconds));
    });
  });

  group('图标令牌', () {
    test('尺寸阶梯递增且默认档为 Material 标准 24', () {
      final ladder = [
        IconSizes.xs, IconSizes.sm, IconSizes.md,
        IconSizes.lg, IconSizes.xl, IconSizes.display,
      ];
      for (var i = 1; i < ladder.length; i++) {
        expect(ladder[i], greaterThan(ladder[i - 1]));
      }
      expect(IconSizes.lg, 24);
    });
  });

  // ── 响应式断点 ──────────────────────────────────────────
  // 边界值必须精确：599/600 与 839/840 是"手机↔平板"的分水岭，
  // 差一个像素就换布局，写错会在大屏上悄悄退化成手机版
  group('窗口分档', () {
    Future<void> expectSize(
        WidgetTester tester, double width, WindowSize expected) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      late WindowSize got;
      await tester.pumpWidget(MaterialApp(
        home: Builder(builder: (ctx) {
          got = ctx.windowSize;
          return const SizedBox.shrink();
        }),
      ));
      expect(got, expected, reason: '宽度 $width 的档位判定错误');
    }

    testWidgets('599 → 手机竖屏（compact）', (tester) async {
      await expectSize(tester, 599, WindowSize.compact);
    });

    testWidgets('600 → 横屏/小平板（medium，边界含）', (tester) async {
      await expectSize(tester, 600, WindowSize.medium);
    });

    testWidgets('839 → 仍是 medium', (tester) async {
      await expectSize(tester, 839, WindowSize.medium);
    });

    testWidgets('840 → 平板（expanded，边界含）', (tester) async {
      await expectSize(tester, 840, WindowSize.expanded);
    });
  });

  group('页面边距与列数', () {
    Future<void> pumpAt(WidgetTester tester, double width) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
    }

    testWidgets('页面边距 16 / 20 / 24 三档', (tester) async {
      for (final (w, pad) in [(390.0, Gap.md), (700.0, Gap.lg), (1024.0, Gap.xl)]) {
        await pumpAt(tester, w);
        late double got;
        await tester.pumpWidget(MaterialApp(
          home: Builder(builder: (ctx) {
            got = ctx.pagePadding;
            return const SizedBox.shrink();
          }),
        ));
        expect(got, pad, reason: '宽度 $w 的页边距错误');
      }
    });

    testWidgets('照片墙列数 3 / 4 / 6', (tester) async {
      for (final (w, cols) in [(390.0, 3), (700.0, 4), (1024.0, 6)]) {
        await pumpAt(tester, w);
        late int got;
        await tester.pumpWidget(MaterialApp(
          home: Builder(builder: (ctx) {
            got = ctx.gridColumns();
            return const SizedBox.shrink();
          }),
        ));
        expect(got, cols, reason: '宽度 $w 的列数错误');
      }
    });
  });

  group('内容限宽', () {
    Future<double> gutterAt(WidgetTester tester, double width) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      late double got;
      await tester.pumpWidget(MaterialApp(
        home: Builder(builder: (ctx) {
          got = sliverGutter(ctx, base: 12);
          return const SizedBox.shrink();
        }),
      ));
      return got;
    }

    testWidgets('手机竖屏不限宽（保持基础边距）', (tester) async {
      expect(await gutterAt(tester, 390), 12);
    });

    testWidgets('平板下内容宽度收敛到 720', (tester) async {
      final g = await gutterAt(tester, 1024);
      expect(1024 - g * 2, closeTo(Breakpoints.contentMaxWidth, 0.01));
    });
  });
}
