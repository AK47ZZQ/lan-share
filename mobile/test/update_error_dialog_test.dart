// 更新失败弹窗的两条硬规则（2026-11-20）。
//
// 为什么值得单测：这个弹窗是「自动更新彻底失败」时的最后一道门。里面两条
// 逻辑是安全相关的，坏了不会报错、只会静默把用户带走：
//
//   1. 直链来自清单（自定义更新源可被替换）—— 只认 https。
//      否则一个坏源就能让弹窗把一个明文 / file:// 地址摆到用户面前。
//   2. 没有直链时必须回退到公开发布页，绝不能只显示一个空地址。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/update_service.dart';
import 'package:lanshare_app/ui/widgets/update_error_dialog.dart';

/// 把弹窗挂到一个最小页面上并打开。
Future<void> _open(WidgetTester tester, {String? link}) async {
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: Builder(
        builder: (ctx) => TextButton(
          onPressed: () => showUpdateErrorDialog(ctx, '原因是测试', link: link),
          child: const Text('触发'),
        ),
      ),
    ),
  ));
  await tester.tap(find.text('触发'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('清单直链是 https 时优先显示它', (tester) async {
    const direct = 'https://dl.example.test/LANShare-App-9.9.9.apk';
    await _open(tester, link: direct);
    expect(find.text(direct), findsOneWidget);
    expect(find.text(UpdateService.manualDownloadUrl), findsNothing);
  });

  testWidgets('明文 http 直链被丢弃，回退到发布页', (tester) async {
    await _open(tester, link: 'http://evil.test/x.apk');
    expect(find.text(UpdateService.manualDownloadUrl), findsOneWidget);
    expect(find.textContaining('evil.test'), findsNothing);
  });

  testWidgets('没有直链时回退到发布页（不会是空地址）', (tester) async {
    await _open(tester);
    expect(find.text(UpdateService.manualDownloadUrl), findsOneWidget);
  });
}
