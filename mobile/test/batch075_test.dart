// 批次 075（v0.16.0+51）：物理删除两段式纯函数 / 安装包清运判定 / 本地文件
// 视图纯逻辑 / 传输汇总口径。涉及网络与插件的路径由 E2E 覆盖，这里只测
// 可纯算的部分。
import 'package:flutter_test/flutter_test.dart';
import 'package:lanshare_app/data/update_service.dart';
import 'package:lanshare_app/data/api/purge.dart';
import 'package:lanshare_app/transfer/local_files.dart';
import 'package:lanshare_app/transfer/transfer_task.dart';

void main() {
  group('matchNewestTrashIds（两段式物理删除的条目匹配）', () {
    final entries = [
      {'id': 11, 'orig_path': '照片/alice/IMG_1.jpg', 'deleted_at': '2026-09-01 10:00:00'},
      {'id': 12, 'orig_path': '照片/alice/IMG_2.jpg', 'deleted_at': '2026-09-05 09:00:00'},
      {'id': 13, 'orig_path': '照片/alice/IMG_1.jpg', 'deleted_at': '2026-09-05 10:00:00'},
      {'id': 14, 'orig_path': '照片/bob/IMG_3.jpg', 'deleted_at': '2026-09-05 10:00:00'},
      {'id': 0, 'orig_path': '照片/alice/IMG_4.jpg', 'deleted_at': '2026-09-05 10:00:00'},
      {'orig_path': '照片/alice/IMG_5.jpg', 'deleted_at': '2026-09-05 10:00:00'},
    ];

    test('同路径多条目取 deleted_at 最新的一条', () {
      final m = matchNewestTrashIds(['照片/alice/IMG_1.jpg'], entries);
      expect(m, {'照片/alice/IMG_1.jpg': 13});
    });

    test('不碰未删除路径的历史回收条目', () {
      final m = matchNewestTrashIds(['照片/alice/IMG_9.jpg'], entries);
      expect(m, isEmpty);
    });

    test('一次匹配多个路径，互不串扰', () {
      final m = matchNewestTrashIds(
          ['照片/alice/IMG_1.jpg', '照片/alice/IMG_2.jpg'], entries);
      expect(m.length, 2);
      expect(m['照片/alice/IMG_1.jpg'], 13);
      expect(m['照片/alice/IMG_2.jpg'], 12);
    });

    test('id 缺失/为 0 的条目不参与匹配', () {
      final m = matchNewestTrashIds(
          ['照片/alice/IMG_4.jpg', '照片/alice/IMG_5.jpg'], entries);
      expect(m, isEmpty);
    });

    test('非 Map 元素安全跳过', () {
      final m = matchNewestTrashIds(['照片/alice/IMG_1.jpg'], ['junk', null, ...entries]);
      expect(m, {'照片/alice/IMG_1.jpg': 13});
    });

    test('同秒并列取 id 更大者（回收站 id 自增，大者必是本次新条目）', () {
      const tie = [
        {'id': 21, 'orig_path': '照片/alice/X.jpg', 'deleted_at': '2026-09-05 10:00:00'},
        {'id': 34, 'orig_path': '照片/alice/X.jpg', 'deleted_at': '2026-09-05 10:00:00'},
      ];
      expect(matchNewestTrashIds(['照片/alice/X.jpg'], tie),
          {'照片/alice/X.jpg': 34});
    });
  });

  group('PurgeResult.report', () {
    test('全成功一句话', () {
      expect(
          PurgeResult(total: 3, purged: 3, staged: 0, failed: 0).report,
          '已彻底删除 3 项');
    });
    test('部分滞留回收站', () {
      expect(
          PurgeResult(total: 3, purged: 1, staged: 1, failed: 1).report,
          '彻底删除 1 项，1 项滞留回收站（可去回收站手动删除），1 项删除失败');
    });
    test('trashOnly 动词降级为"删除"', () {
      expect(
          PurgeResult(total: 1, purged: 1, staged: 0, failed: 0, verb: '删除')
              .report,
          '已删除 1 项');
    });
    test('全部滞留（回收站列表拉取失败的兼容路径）不谎报', () {
      expect(
          PurgeResult(total: 2, purged: 0, staged: 2, failed: 0).report,
          '2 项滞留回收站（可去回收站手动删除）');
    });
    test('全部失败（进回收站都没成）', () {
      expect(
          PurgeResult(total: 2, purged: 0, staged: 0, failed: 2).report,
          '2 项删除失败');
    });
  });

  group('isInstallerName（更新包清运判定）', () {
    test('分发命名命中', () {
      expect(UpdateService.isInstallerName('lanshare-0.16.0.apk'), isTrue);
      expect(UpdateService.isInstallerName('LanShare-0.16.0.APK'), isTrue);
    });
    test('非安装包不误伤', () {
      expect(UpdateService.isInstallerName('lanshare.apk'), isFalse); // 少连字符
      expect(UpdateService.isInstallerName('photo.jpg'), isFalse);
      expect(UpdateService.isInstallerName('backup-lanshare-1.apk'), isFalse);
      expect(UpdateService.isInstallerName('lanshare-0.16.0.apk.part'),
          isFalse);
      expect(UpdateService.isInstallerName('xlanshare-1.0.apk'), isFalse);
    });
  });

  group('本地文件视图纯逻辑', () {
    test('.part 分片从列表隐藏', () {
      expect(isHiddenLocalEntry('movie.mp4.part'), isTrue);
      expect(isHiddenLocalEntry('movie.part.mp4'), isFalse);
      expect(isHiddenLocalEntry('照片.jpg'), isFalse);
    });

    TransferTask task(String id, String localPath,
            {TransferStatus status = TransferStatus.pending,
            bool download = true}) =>
        TransferTask(
            id: id,
            kind: download ? TransferKind.download : TransferKind.upload,
            name: 'n',
            localPath: localPath,
            size: 1,
            status: status);

    test('孤儿分片：任务已移除 → 可清理', () {
      expect(isOrphanPart('/d/Downloads/a.jpg.part', const []), isTrue);
    });

    test('进行中/暂停/失败任务的分片受保护（失败可重试续传）', () {
      final tasks = [
        task('d1', '/d/Downloads/a.jpg', status: TransferStatus.downloading),
        task('d2', '/d/Downloads/b.jpg', status: TransferStatus.paused),
        task('d3', '/d/Downloads/c.jpg', status: TransferStatus.error),
      ];
      expect(isOrphanPart('/d/Downloads/a.jpg.part', tasks), isFalse);
      expect(isOrphanPart('/d/Downloads/b.jpg.part', tasks), isFalse);
      expect(isOrphanPart('/d/Downloads/c.jpg.part', tasks), isFalse);
    });

    test('已完成（done）任务的分片视为孤儿（.part 已改名，残留必是垃圾）', () {
      final tasks = [task('d1', '/d/Downloads/a.jpg', status: TransferStatus.done)];
      expect(isOrphanPart('/d/Downloads/a.jpg.part', tasks), isTrue);
    });

    test('上传任务名下没有分片语义', () {
      final tasks = [task('u1', '/d/Downloads/a.jpg', download: false)];
      expect(isOrphanPart('/d/Downloads/a.jpg.part', tasks), isTrue);
    });
  });

  group('localFileKind 类型分档', () {
    test('媒体/压缩/文档/兜底', () {
      expect(localFileKind('a.JPG'), 'image');
      expect(localFileKind('b.heic'), 'image');
      expect(localFileKind('c.MP4'), 'video');
      expect(localFileKind('d.mp3'), 'audio');
      expect(localFileKind('e.apk'), 'archive');
      expect(localFileKind('f.pdf'), 'doc');
      expect(localFileKind('g'), 'other');
      expect(localFileKind('h.unknown'), 'other');
    });
  });
}
