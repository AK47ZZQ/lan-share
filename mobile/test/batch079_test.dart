// 月视图游标推进纯函数矩阵（R4 审查要求）：nextOlderMonth/newerMonth 的
// facets 路径、日历回退路径与边界（跨年、当前月上限、facets 缺月）。
// 对照实现：photos_page.dart 的 @visibleForTesting 静态方法。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/ui/photos_page.dart';

void main() {
  group('nextOlderMonth（向下续流游标推进）', () {
    final facets = {
      '2026-09': 22, '2026-01': 33, '2025-12': 282, '2024-02': 5,
    };

    test('facets 命中：取下一个更早月份（跳过空月）', () {
      expect(PhotosPage.nextOlderMonth(facets, '2026-09'), '2026-01');
      expect(PhotosPage.nextOlderMonth(facets, '2026-01'), '2025-12');
    });

    test('facets 命中：已是最早月份 → null（续流到底）', () {
      expect(PhotosPage.nextOlderMonth(facets, '2024-02'), isNull);
    });

    test('facets 中找不到当前月 → 按日历回退', () {
      expect(PhotosPage.nextOlderMonth(facets, '2026-06'), '2026-05');
    });

    test('日历回退：跨年进位（1 月 → 去年 12 月）', () {
      expect(PhotosPage.nextOlderMonth(null, '2026-01'), '2025-12');
      expect(PhotosPage.nextOlderMonth(null, '2025-03'), '2025-02');
    });

    test('月序号补零', () {
      expect(PhotosPage.nextOlderMonth(null, '2026-10'), '2026-09');
    });
  });

  group('newerMonth（向上前插游标推进）', () {
    final facets = {
      '2026-09': 22, '2026-01': 33, '2025-12': 282,
    };

    test('facets 命中：取上一个更晚月份', () {
      expect(PhotosPage.newerMonth(facets, '2025-12', '2026-09'), '2026-01');
      expect(PhotosPage.newerMonth(facets, '2026-01', '2026-09'), '2026-09');
    });

    test('facets 命中：已是最新月份 → null（到顶）', () {
      expect(PhotosPage.newerMonth(facets, '2026-09', '2026-09'), isNull);
    });

    test('不前插未来月份：目标晚于当前自然月 → null', () {
      // 2025-12 的日历回退是 2026-01；若当前自然月是 2025-12，则到顶
      expect(PhotosPage.newerMonth(null, '2025-11', '2025-12'), '2025-12');
      expect(PhotosPage.newerMonth(null, '2025-12', '2025-12'), isNull);
    });

    test('日历回退：跨年进位（12 月 → 明年 1 月）', () {
      expect(PhotosPage.newerMonth(null, '2025-12', '2026-06'), '2026-01');
      expect(PhotosPage.newerMonth(null, '2025-03', '2026-06'), '2025-04');
    });

    test('日历回退：月序号补零', () {
      expect(PhotosPage.newerMonth(null, '2026-08', '2026-12'), '2026-09');
    });
  });
}
