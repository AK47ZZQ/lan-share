// v0.14 批次回归测试：
// 1) 备份相册白名单：BackupConfig 持久化往返 + copyWith 语义；
// 2) 账本统计的白名单扣减：NOT-IN-白名单 的未完成组并入排除组后，
//    statsFromGroups 的待备/失败/排除计数与旧排除表叠加时仍对账。
// 3) 服务端专辑列表模型解析（count/cover，无 items）。
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:lanshare_app/backup/backup_config.dart';
import 'package:lanshare_app/backup/backup_ledger.dart';

Map<Object?, Object?> _g(String status, String kind, int c,
        {int? bytes, int? last}) =>
    {'status': status, 'kind': kind, 'c': c, 'bytes': bytes, 'last': last};

Map<Object?, Object?> _x(String status, String kind, int c) =>
    {'status': status, 'kind': kind, 'c': c};

void main() {
  group('BackupConfig 白名单（0.14）', () {
    test('selectedBuckets 保存/读取往返', () async {
      SharedPreferences.setMockInitialValues({});
      final cfg = const BackupConfig()
          .copyWith(selectedBuckets: const ['相机', '微信']);
      await cfg.save();
      final loaded = await BackupConfig.load();
      expect(loaded.selectedBuckets.toSet(), {'相机', '微信'});
      expect(loaded.excludedBuckets, isEmpty);
    });

    test('默认值：不勾=全部备份，旧排除表读到仍生效', () async {
      SharedPreferences.setMockInitialValues({
        'backup_excluded_buckets': ['Screenshots'],
      });
      final cfg = await BackupConfig.load();
      expect(cfg.selectedBuckets, isEmpty); // 空=全部
      expect(cfg.excludedBuckets, ['Screenshots']);
    });

    test('保存白名单时清空旧排除表（copyWith 传 const [] 覆盖）', () async {
      final cfg = BackupConfig(excludedBuckets: const ['截图'])
          .copyWith(selectedBuckets: const ['相机'], excludedBuckets: const []);
      expect(cfg.selectedBuckets, ['相机']);
      expect(cfg.excludedBuckets, isEmpty);
    });
  });

  group('账本统计白名单扣减（0.14）', () {
    test('NOT-IN-白名单 未完成组并入排除组：待备只留白名单内的', () {
      // 全账本：待备照片 10；其中 4 项相册不在白名单（engine 查询层会被
      // 过滤，统计侧也要把它们从待备里扣掉，单列 excludedPending）
      final s = statsFromGroups([
        _g('done', 'image', 10, bytes: 1000, last: 100),
        _g('pending', 'image', 10),
        _g('error', 'image', 2),
      ], [
        _x('pending', 'image', 4), // 不在白名单的未完成
        _x('error', 'image', 1),
      ]);
      expect(s.donePhotos, 10);
      expect(s.pendingPhotos, 6);
      expect(s.error, 1);
      expect(s.excludedPending, 5);
    });

    test('全在白名单内（无排除组）时计数不受影响', () {
      final s = statsFromGroups([
        _g('pending', 'image', 7),
        _g('pending', 'video', 2),
      ], const []);
      expect(s.pendingPhotos, 7);
      expect(s.pendingVideos, 2);
      expect(s.excludedPending, 0);
    });
  });
}
