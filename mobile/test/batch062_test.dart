// 0.6.2 批次纯逻辑单测：
// 1) MediaSource.mediaPayload——原生桥错误 JSON 识别（排除相册"无法读取"根修的
//    Dart 侧防线：原生出错回 {"error":...} 而非抛异常，此前被当数据解）
// 2) 家里动态文案（data/activity.dart）——与 Web activity.ts 同语义
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/backup/media_source.dart';
import 'package:lanshare_app/data/activity.dart';

void main() {
  group('mediaPayload（原生错误 JSON 防线）', () {
    test('错误 JSON 抛出并带原因', () {
      expect(
          () => MediaSource.mediaPayload('{"error":"no permission"}', '读取相册列表'),
          throwsA(isA<PlatformException>()
              .having((e) => e.message, 'message', contains('no permission'))));
    });

    test('空/null 抛出', () {
      expect(() => MediaSource.mediaPayload(null, 'x'), throwsA(isA<PlatformException>()));
      expect(() => MediaSource.mediaPayload('', 'x'), throwsA(isA<PlatformException>()));
    });

    test('正常载荷原样通过：数组 / 对象 / 纯路径', () {
      expect(MediaSource.mediaPayload('[]', 'query'), '[]');
      expect(MediaSource.mediaPayload('{"Camera":3}', 'buckets'), '{"Camera":3}');
      expect(MediaSource.mediaPayload('/cache/backup_tmp/i_1.jpg', 'open'),
          '/cache/backup_tmp/i_1.jpg');
    });

    test('业务对象里恰好有 error 字段也不误伤（值为 null 才算错误）', () {
      // 服务端不会回这种形状；但语义上只有显式非空 error 才当错误
      expect(MediaSource.mediaPayload('{"error":null}', 'buckets'), '{"error":null}');
    });
  });

  group('动态文案', () {
    ActivityItem item(Map<String, dynamic> m) => ActivityItem.fromMap(m);

    test('fromMap 缺字段兜底', () {
      final n = item({'ts': 100});
      expect(n.username, '');
      expect(n.count, 1);
      expect(n.action, '');
    });

    test('普通上传：who + 动词 + 文件名', () {
      final n = item({
        'ts': 1, 'username': 'debug', 'action': 'upload',
        'target': '照片/手机相册/debug/IMG_1.jpg', 'detail': '', 'count': 1,
      });
      expect(activityText(n), 'debug 上传了 IMG_1.jpg');
    });

    test('聚合：count>1 只报项数', () {
      final n = item({
        'ts': 1, 'username': 'ma', 'action': 'upload',
        'target': '照片/手机相册/ma/a.jpg', 'detail': '', 'count': 5,
      });
      expect(activityText(n), 'ma 上传了 5 项');
    });

    test('定向分享带备注', () {
      final n = item({
        'ts': 1, 'username': 'pa', 'action': 'share_target',
        'target': '文档/成绩单.pdf', 'detail': '给妹妹', 'count': 1,
      });
      expect(activityText(n), 'pa 定向分享了 成绩单.pdf 给妹妹');
    });

    test('无用户名：链接下载=链接访客，其余=早期记录', () {
      final d = item({'ts': 1, 'username': '', 'action': 'share_download',
          'target': '照片/x.jpg', 'detail': '', 'count': 1});
      final old = item({'ts': 1, 'username': '', 'action': 'upload',
          'target': '照片/y.jpg', 'detail': '', 'count': 1});
      expect(activityText(d), startsWith('链接访客'));
      expect(activityText(old), startsWith('早期记录'));
    });

    test('相对时间（注入 now）', () {
      const now = 1_000_000_000 * 1000; // ms
      expect(activityRelTime(now ~/ 1000 - 5, nowMs: now), '刚刚');
      expect(activityRelTime(now ~/ 1000 - 120, nowMs: now), '2 分钟前');
      expect(activityRelTime(now ~/ 1000 - 7200, nowMs: now), '2 小时前');
      expect(activityRelTime(now ~/ 1000 - 3 * 86400, nowMs: now), '3 天前');
      // 超过 7 天落日期格式 M-DD HH:MM
      final old = activityRelTime(now ~/ 1000 - 10 * 86400, nowMs: now);
      expect(RegExp(r'^\d{1,2}-\d{1,2} \d{2}:\d{2}$').hasMatch(old), isTrue);
    });

    test('listFrom 过滤非 Map 元素', () {
      final list = ActivityItem.listFrom([
        {'ts': 5, 'username': 'a', 'action': 'upload', 'target': 't', 'detail': '', 'count': 1},
        'junk',
      ]);
      expect(list.length, 1);
      expect(list[0].username, 'a');
    });
  });
}
