// 0.8.5 主题引擎单测：Palette 预置完整性 / paletteById 脏数据回落 /
// PaletteScope 作用域外回落云朵紫蓝 / BrandHeader 渐变随作用域色板走。
// 色板是全 App 配色的单一事实源，断言错了每套主题都会歪。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:lanshare_app/state/settings.dart';
import 'package:lanshare_app/ui/theme/app_theme.dart';
import 'package:lanshare_app/ui/widgets/brand.dart';

void main() {
  group('Palettes 预置', () {
    test('6 套预置，id 唯一，渐变两段', () {
      expect(Palettes.all.length, 6);
      expect(Palettes.all.map((p) => p.id).toSet().length, 6);
      for (final p in Palettes.all) {
        expect(p.gradient.length, 2, reason: '${p.id} 渐变必须两段');
        expect(p.name, isNotEmpty);
      }
    });

    test('默认色板是云朵紫蓝，暗底为深海军', () {
      expect(Palettes.violet.id, 'violet');
      expect(Palettes.violet.darkBg, const Color(0xFF0B1020));
      expect(paletteById('violet'), same(Palettes.violet));
    });
  });

  group('paletteById 脏数据回落', () {
    test('未知 id / null 都回落云朵紫蓝', () {
      expect(paletteById('nope'), same(Palettes.violet));
      expect(paletteById(null), same(Palettes.violet));
      // 'auto'（跟随壁纸）不是色板 id：动态色拿不到时也回落紫蓝
      expect(paletteById(Palettes.auto), same(Palettes.violet));
    });
  });

  group('AppSettings.accent', () {
    test('默认 violet；setAccent 更新并持久化', () async {
      SharedPreferences.setMockInitialValues({});
      final c = SettingsController();
      await Future<void>.delayed(Duration.zero); // 等异步 _load
      expect(c.state.accent, 'violet');

      await c.setAccent('teal');
      expect(c.state.accent, 'teal');
      final sp = await SharedPreferences.getInstance();
      expect(sp.getString('ui_accent'), 'teal');
    });

    test('启动时读回已存 accent', () async {
      SharedPreferences.setMockInitialValues({'ui_accent': 'rose'});
      final c = SettingsController();
      await Future<void>.delayed(Duration.zero);
      expect(c.state.accent, 'rose');
    });
  });

  group('PaletteScope', () {
    testWidgets('作用域外回落云朵紫蓝；作用域内取到所选色板', (tester) async {
      late Palette grabbed;
      await tester.pumpWidget(MaterialApp(
          home: Builder(
              builder: (context) {
                grabbed = PaletteScope.of(context);
                return const SizedBox.shrink();
              })));
      expect(grabbed, same(Palettes.violet));

      await tester.pumpWidget(PaletteScope(
          palette: Palettes.teal,
          child: Builder(builder: (context) {
            grabbed = PaletteScope.of(context);
            return const SizedBox.shrink();
          })));
      expect(grabbed, same(Palettes.teal));
    });

    testWidgets('BrandHeader 渐变随作用域色板', (tester) async {
      await tester.pumpWidget(PaletteScope(
          palette: Palettes.warm,
          child: const MaterialApp(home: BrandHeader(child: SizedBox()))));
      final header = tester.widget<Container>(
          find.descendant(
              of: find.byType(BrandHeader), matching: find.byType(Container)).first);
      final deco = header.decoration as BoxDecoration;
      expect((deco.gradient! as LinearGradient).colors,
          Palettes.warm.gradient);
    });
  });

  group('AppTheme 挂色板', () {
    test('深色底 = 色板 darkBg 向上派生（云朵紫蓝=深海军系）', () {
      final dark = AppTheme.dark(Palettes.violet);
      expect(dark.scaffoldBackgroundColor, Palettes.violet.darkBg);
      // 表面阶梯单调不降
      final cs = dark.colorScheme;
      expect(cs.surfaceContainerLow.computeLuminance(),
          greaterThan(cs.surfaceContainerLowest.computeLuminance()));
      expect(cs.surfaceContainerHigh.computeLuminance(),
          greaterThan(cs.surfaceContainer.computeLuminance()));
    });

    test('AMOLED 底纯黑；亮色种子随色板', () {
      final amoled = AppTheme.amoled(Palettes.violet);
      expect(amoled.scaffoldBackgroundColor, Colors.black);
      final light = AppTheme.light(Palettes.teal);
      expect(light.colorScheme.primary, isNot(AppTheme.light(Palettes.violet).colorScheme.primary));
    });

    testWidgets('默认墨水色方向正确（0.8.4 曾把 Typography 墨水映射写反：'
        '亮色白字画浅卡=不可见）', (tester) async {
      late Color lightInk, darkInk;
      // 必须包 Scaffold：Material 层才会把 theme.textTheme 装配成
      // DefaultTextStyle（真实页面的环境墨水色）；裸 MaterialApp 拿到的是
      // 框架 fallback 样式，不代表真实渲染
      await tester.pumpWidget(MaterialApp(
          theme: AppTheme.light(Palettes.violet),
          home: Scaffold(
              body: Builder(builder: (c) {
            lightInk = DefaultTextStyle.of(c).style.color!;
            return const SizedBox.shrink();
          }))));
      // Material 用 AnimatedDefaultTextStyle 装配墨色，不等动画结束会采到旧值
      await tester.pumpAndSettle();
      await tester.pumpWidget(MaterialApp(
          theme: AppTheme.dark(Palettes.violet),
          home: Scaffold(
              body: Builder(builder: (c) {
            darkInk = DefaultTextStyle.of(c).style.color!;
            return const SizedBox.shrink();
          }))));
      await tester.pumpAndSettle();
      // 亮色墨水必须是深的（低亮度），暗色墨水必须是浅的（高亮度）
      expect(lightInk.computeLuminance(), lessThan(0.3),
          reason: '亮色主题默认文字必须是深墨');
      expect(darkInk.computeLuminance(), greaterThan(0.6),
          reason: '暗色主题默认文字必须是浅墨');
    });
  });
}
