// 更新记录页的数据渲染 —— 2026-11-08 v4.1.0 装机后暴露的那批问题的回归钉。
//
// 那次的教训很具体：模拟器冒烟只验了「装上了、进程活着、没崩」，而屏幕上
// 写着「服务端分发版本 v4.1.0 (0)」和满屏「最新」—— **数据错了界面不会崩**。
// 所以这类页面必须有断言实际字段值的测试，不能靠冒烟。
//
// 本文件钉住：
// 1. 条数不再被截断（v4.1.0 时 `take(20)` 让「看全部」只出 20 条）
// 2. build 为 0 时不打「最新」标签（否则 0 == 0 处处成立，整页挂满）
// 3. 「当前 / 最新」各只出现在正确的那一行
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/update_service.dart';
import 'package:lanshare_app/ui/update_history_page.dart';

UpdateHistoryItem _h(String v, int build) => UpdateHistoryItem(
    version: v, build: build, date: '2026-11-08', notes: '$v 的说明');

Future<void> _pump(WidgetTester t,
    {required List<UpdateHistoryItem> items,
    required int localBuild,
    int? remoteBuild}) async {
  await t.pumpWidget(MaterialApp(
      home: UpdateHistoryPage(
          items: items, localBuild: localBuild, remoteBuild: remoteBuild)));
  await t.pumpAndSettle();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('条数按传进来的全量算，不再截断到 20', (t) async {
    // v4.1.0 的毛病：清单 41 条，解析时 take(20)，页面只出 20 条，
    // 而关于页那句「查看全部 40 个版本」还写着 40 —— 用户一眼就看出对不上。
    final items = [for (var i = 0; i < 42; i++) _h('1.0.$i', 100 + i)];
    await _pump(t, items: items, localBuild: 139, remoteBuild: 139);
    expect(find.text('共 42 个版本'), findsOneWidget);
  });

  testWidgets('build 全是 0 时一个标签都不打', (t) async {
    // 这是 v4.1.0 满屏「最新」的根因：清单没写 build，客户端全填 0，
    // 而 remoteBuild 也是 0 → 每一条都满足 h.build == remoteBuild。
    final items = [_h('4.1.0', 0), _h('1.1.25', 0), _h('1.1.23', 0)];
    await _pump(t, items: items, localBuild: 0, remoteBuild: 0);
    expect(find.text('最新'), findsNothing);
    expect(find.text('当前'), findsNothing);
  });

  testWidgets('已经是最新版：只有「当前」，没有「最新」', (t) async {
    final items = [_h('4.1.1', 139), _h('4.1.0', 138)];
    await _pump(t, items: items, localBuild: 139, remoteBuild: 139);
    expect(find.text('当前'), findsOneWidget);
    expect(find.text('最新'), findsNothing);
  });

  testWidgets('装着旧版：顶部「最新」，本机那版「当前」', (t) async {
    final items = [_h('4.1.1', 139), _h('4.1.0', 138)];
    await _pump(t, items: items, localBuild: 138, remoteBuild: 139);
    expect(find.text('最新'), findsOneWidget);
    expect(find.text('当前'), findsOneWidget);
  });

  testWidgets('说明里的 markdown 标记不原样露出来', (t) async {
    final items = [
      UpdateHistoryItem(
          version: '4.1.1',
          build: 139,
          date: '2026-11-08',
          notes: '### 标题\n**加粗** 和 `代码`\n- 列表项')
    ];
    await _pump(t, items: items, localBuild: 139, remoteBuild: 139);
    expect(find.textContaining('**'), findsNothing);
    expect(find.textContaining('###'), findsNothing);
    expect(find.textContaining('`'), findsNothing);
  });
}
