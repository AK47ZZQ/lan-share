// 跳月偏移推算纯函数矩阵（df72 批：反馈 20260919004417-df72 的修复之一）。
// 仓库测试哲学：只测可纯算部分——_jumpToMonth 的竞态收尾（_jumping 盖住
// 落点校准）属状态机接线，由模拟器 E2E 覆盖；本文件钉 facets→偏移的口径。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/ui/photos_page.dart';

void main() {
  group('jumpTargetFromFacets（跳月偏移推算，新月份在顶）', () {
    final facets = {
      '2026-09': 7,
      '2026-05': 190,
      '2025-12': 81,
      '2025-05': 367,
      '2024-07': 62,
      '2018-04': 1,
    };

    test('目标月偏移 = 比它新的所有月份计数之和', () {
      expect(PhotosPage.jumpTargetFromFacets(facets, '2025-05'), 7 + 190 + 81);
      expect(PhotosPage.jumpTargetFromFacets(facets, '2024-07'),
          7 + 190 + 81 + 367);
    });

    test('最新月份偏移为 0（落点=墙顶）', () {
      expect(PhotosPage.jumpTargetFromFacets(facets, '2026-09'), 0);
    });

    test('最老月份偏移 = 除它之外全部之和', () {
      expect(PhotosPage.jumpTargetFromFacets(facets, '2018-04'),
          7 + 190 + 81 + 367 + 62);
    });

    test('目标月不在 facets → null（调用方静默返回，不跳）', () {
      expect(PhotosPage.jumpTargetFromFacets(facets, '2020-01'), isNull);
    });

    test('非 YYYY-MM 键（空串/异常长度）不参与累计', () {
      final dirty = <String, int>{
        '': 5,          // 全部按钮占位等空键
        '2026': 3,      // 异常长度键
        '2026-09': 7,
        '2026-05': 190,
      };
      expect(PhotosPage.jumpTargetFromFacets(dirty, '2026-05'), 7);
      expect(PhotosPage.jumpTargetFromFacets(dirty, '2026-09'), 0);
    });

    test('月份按字典序倒序累计（YYYY-MM 字典序=时间序）', () {
      final m = {'2025-01': 10, '2025-10': 20, '2025-11': 30};
      // 2025-11 > 2025-10 > 2025-01（字典序）；跳 2025-01 跳过 10 与 11 月
      expect(PhotosPage.jumpTargetFromFacets(m, '2025-01'), 50);
      expect(PhotosPage.jumpTargetFromFacets(m, '2025-10'), 30);
    });
  });
}
