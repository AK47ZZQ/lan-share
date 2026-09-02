// 0.7.1 备份状态卡修复纯逻辑单测（backup_ledger.dart statsFromGroups）：
// 照片/视频分开计数 + 排除相册感知（被排除项不进待备/失败，单列 excludedPending）。
// 真库聚合走 sqflite（单测环境要 ffi），抽纯函数保证这段扣减逻辑可测。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/backup/backup_ledger.dart';

Map<Object?, Object?> _g(String status, String kind, int c,
        {int? bytes, int? last}) =>
    {'status': status, 'kind': kind, 'c': c, 'bytes': bytes, 'last': last};

Map<Object?, Object?> _x(String status, String kind, int c) =>
    {'status': status, 'kind': kind, 'c': c};

void main() {
  group('statsFromGroups（状态卡聚合）', () {
    test('空账本全零', () {
      final s = statsFromGroups(const [], const []);
      expect(s.done, 0);
      expect(s.donePhotos, 0);
      expect(s.doneVideos, 0);
      expect(s.pending, 0);
      expect(s.error, 0);
      expect(s.excludedPending, 0);
      expect(s.doneBytes, 0);
      expect(s.lastUploadedAt, isNull);
    });

    test('照片/视频分开计数，uploading 算待备', () {
      final s = statsFromGroups([
        _g('done', 'image', 10, bytes: 1000, last: 100),
        _g('done', 'video', 3, bytes: 900, last: 300),
        _g('pending', 'image', 4),
        _g('uploading', 'video', 2),
        _g('error', 'image', 1),
      ], const []);
      expect(s.donePhotos, 10);
      expect(s.doneVideos, 3);
      expect(s.pendingPhotos, 4);
      expect(s.pendingVideos, 2);
      expect(s.pending, 6);
      expect(s.errorPhotos, 1);
      expect(s.errorVideos, 0);
      expect(s.doneBytes, 1900);
      expect(s.lastUploadedAt, 300); // 取各分组 MAX 的最大值
    });

    test('排除相册：未完成项从待备/失败中扣掉并单列，done 不受影响', () {
      final s = statsFromGroups([
        _g('done', 'image', 10, bytes: 1000, last: 100),
        _g('pending', 'image', 4),
        _g('pending', 'video', 2),
        _g('uploading', 'image', 1),
        _g('error', 'image', 3),
      ], [
        _x('pending', 'image', 4), // 被排除相册里的待备
        _x('error', 'image', 2), // 被排除相册里的失败（不重试，不该占红字）
      ]);
      expect(s.pendingPhotos, 1); // 4+1(UPLOADING) - 4
      expect(s.pendingVideos, 2);
      expect(s.pending, 3);
      expect(s.errorPhotos, 1); // 3 - 2
      expect(s.excludedPending, 6); // 只累计真实查到的排除项
      expect(s.donePhotos, 10); // done 恒不被排除扣减
      expect(s.doneBytes, 1000);
    });

    test('lastUploadedAt 为空组时不炸', () {
      final s = statsFromGroups([_g('pending', 'image', 1)], const []);
      expect(s.lastUploadedAt, isNull);
      expect(s.pendingPhotos, 1);
    });
  });

  group('LedgerRow.bucket（v2 列）', () {
    test('fromMap 兼容缺 bucket 的旧行（迁移中途读取）', () {
      final r = LedgerRow.fromMap({
        'media_id': 'img_1',
        'kind': 'image',
        'name': 'a.jpg',
        'size': 1,
        'mtime': 2,
      });
      expect(r.bucket, '');
      expect(r.status, 'pending');
    });
  });
}
