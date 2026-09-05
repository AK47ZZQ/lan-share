// v0.13 回归测试：照片墙滑动卡死根修。
// v0.11 用的 flutter_staggered_grid_view SliverMasonryGrid 有上游致命 bug：
// 同一 CustomScrollView 里多个瀑布流 sliver（按日分组=每天一个）滚动修正
// 失效，真机快滑几屏后 maxScrollExtent 冻结、页面卡死（上游 issue
// #265/#286/#338）。换 waterfall_flow 后本测试钉住该行为：
// 多组 SliverWaterfallFlow 结构下连续拖动必须持续推进滚动位置。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:waterfall_flow/waterfall_flow.dart';

Widget _buildApp({required int groups, required int perGroup}) => MaterialApp(
      home: Scaffold(
        body: CustomScrollView(slivers: [
          for (var g = 0; g < groups; g++) ...[
            SliverToBoxAdapter(
                child: SizedBox(
                    height: 40,
                    child: Text('day-$g'))),   // 日期头
            SliverWaterfallFlow(
                gridDelegate:
                    const SliverWaterfallFlowDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 3,
                        mainAxisSpacing: 2,
                        crossAxisSpacing: 2),
                delegate: SliverChildBuilderDelegate((context, j) {
                  // 变高格子模拟真实照片宽高比差异
                  final h = 80.0 + ((g * 7 + j * 13) % 5) * 60;
                  return SizedBox(height: h, child: Text('g${g}i$j'));
                }, childCount: perGroup)),
          ],
        ]),
      ),
    );

void main() {
  testWidgets('多组瀑布流连续滚动持续推进（卡死回归）', (tester) async {
    await tester.binding.setSurfaceSize(const Size(1080, 2400));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(_buildApp(groups: 12, perGroup: 21));

    ScrollableState state() =>
        tester.state<ScrollableState>(find.byType(Scrollable).first);
    final first = state().position.pixels;

    // 连续 5 次大幅拖动（模拟真机快滑），每次后位置必须前进
    var last = first;
    for (var i = 0; i < 5; i++) {
      await tester.drag(find.byType(CustomScrollView), const Offset(0, -800));
      await tester.pumpAndSettle();
      final now = state().position.pixels;
      expect(now, greaterThan(last),
          reason: '第 ${i + 1} 次拖动后滚动位置未前进（$last→$now），'
              '多组瀑布流滚动卡死回归');
      last = now;
    }
    // 首屏之外至少推进数屏
    expect(last - first, greaterThan(2000));
  });

  testWidgets('向上滚动可回退到顶部', (tester) async {
    await tester.binding.setSurfaceSize(const Size(1080, 2400));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(_buildApp(groups: 6, perGroup: 9));
    await tester.drag(find.byType(CustomScrollView), const Offset(0, -3000));
    await tester.pumpAndSettle();
    await tester.drag(find.byType(CustomScrollView), const Offset(0, 5000));
    await tester.pumpAndSettle();
    expect(
        tester.state<ScrollableState>(find.byType(Scrollable).first)
            .position
            .pixels,
        0);
  });
}
