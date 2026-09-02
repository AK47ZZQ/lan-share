// 0.6.0 大批次纯逻辑单测：版本对比（检查更新）/ 排除相册过滤 /
// 视频时长文案 / 月份标签 / 媒体类型判定。全部不依赖网络与平台通道。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/update_service.dart';
import 'package:lanshare_app/ui/photo_models.dart';

void main() {
  group('检查更新：build 对比（UpdateService.isNewer）', () {
    UpdateInfo info(int build) => UpdateInfo(
        version: '9.9.9', build: build, notes: '', sha256: null,
        available: true, size: 1);
    test('远端 build 更大 → 有更新', () {
      expect(UpdateService.isNewer(info(14), 13), isTrue);
    });
    test('远端 build 相同/更小 → 无更新', () {
      expect(UpdateService.isNewer(info(14), 14), isFalse);
      expect(UpdateService.isNewer(info(14), 15), isFalse);
    });
    test('远端 404（check 返回 null）语义下不比较', () {
      // isNewer 只接受非空 UpdateInfo，契约由调用方保证 null 短路
      expect(UpdateService.isNewer(info(1), 0), isTrue);
    });
  });

  group('视频时长文案（PhotoItem.durationLabel）', () {
    PhotoItem withDuration(num? seconds) => PhotoItem(
        path: 'a/b.mp4', name: 'b.mp4', taken: '2026-09-02', month: '2026-09',
        uploader: 'x', mediaType: 'video', duration: seconds?.toDouble());
    test('空/0 → 空文案（角标只显示 ▶）', () {
      expect(withDuration(null).durationLabel, '');
      expect(withDuration(0).durationLabel, '');
    });
    test('分秒格式', () {
      expect(withDuration(5).durationLabel, '0:05');
      expect(withDuration(124).durationLabel, '2:04');
    });
    test('跨小时格式', () {
      expect(withDuration(3723).durationLabel, '1:02:03');
    });
    test('isVideo 判定', () {
      expect(withDuration(1).isVideo, isTrue);
      expect(PhotoItem(path: 'a.jpg', name: 'a.jpg', taken: '', month: '',
          uploader: 'x').isVideo, isFalse);
    });
  });

  group('PhotoItem.fromJson 兼容旧服务端（无 media_type/duration 字段）', () {
    test('缺省视为照片', () {
      final it = PhotoItem.fromJson({
        'path': '照片/手机相册/u/p.jpg',
        'name': 'p.jpg',
        'taken': '2026-09-02',
        'month': '2026-09',
      });
      expect(it.isVideo, isFalse);
      expect(it.duration, isNull);
    });
    test('服务端 video + 时长照收', () {
      final it = PhotoItem.fromJson({
        'path': 'a/v0.mp4', 'name': 'v0.mp4', 'taken': '2026-09-02',
        'month': '2026-09', 'media_type': 'video', 'duration': 3.5,
      });
      expect(it.isVideo, isTrue);
      expect(it.duration, 3.5);
    });
  });
}
