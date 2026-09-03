// 0.9.0 更新历史单测：/api/app/history 条目解析（宽容契约——服务端
// 已过滤坏条目，端上只做兜底默认）+ 新旧判断复用。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/update_service.dart';

void main() {
  group('UpdateHistoryItem.fromJson', () {
    test('完整条目', () {
      final h = UpdateHistoryItem.fromJson({
        'version': '0.8.14',
        'build': 36,
        'date': '2026-09-04',
        'notes': '主页顶部铺满',
      });
      expect(h.version, '0.8.14');
      expect(h.build, 36);
      expect(h.date, '2026-09-04');
      expect(h.notes, '主页顶部铺满');
    });

    test('缺字段兜底为空/0（服务端已滤坏条目，端上不炸）', () {
      final h = UpdateHistoryItem.fromJson({'version': 'x'});
      expect(h.version, 'x');
      expect(h.build, 0);
      expect(h.date, '');
      expect(h.notes, '');
    });
  });
}
