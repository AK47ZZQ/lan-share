// 照片时间线数据模型：photos_page 与 photo_viewer 共用（避免循环导入）。
// 0.6.0 起带 media_type（photo|video）与 duration（秒，视频特有，服务端
// ffprobe 提供，取不到为 null）。
class PhotoItem {
  final String path;
  final String name;
  final String taken;
  final String month;
  final String uploader;
  final String mediaType; // photo | video
  final double? duration; // 秒
  final int? width;       // 原始宽（瀑布流宽高比；null/0=未知→方格回退，v0.11）
  final int? height;      // 原始高
  /// 缓存击穿键（0.12.0）：文件页入口传文件 modified——同名覆盖后
  /// 预览 URL 随之变化，绕开客户端 180 天磁盘缓存陈旧。照片墙不传
  /// （无 mtime 字段，维持既有取舍）。
  final String? cacheKey;
  PhotoItem({required this.path, required this.name, required this.taken,
      required this.month, required this.uploader,
      this.mediaType = 'photo', this.duration, this.width, this.height,
      this.cacheKey});

  bool get isVideo => mediaType == 'video';

  /// 瀑布流格子宽高比（w/h）；未知或极端比例回退 1.0 方格（v0.11）。
  double get aspect {
    final w = width, h = height;
    if (w == null || h == null || w <= 0 || h <= 0) return 1.0;
    final a = w / h;
    if (a < 0.4 || a > 2.8) return 1.0;   // 全景图/长截图压扁整列，限制在合理区间
    return a;
  }

  factory PhotoItem.fromJson(Map<String, dynamic> j) => PhotoItem(
      path: j['path'] as String,
      name: j['name'] as String,
      taken: j['taken'] as String? ?? '',
      month: j['month'] as String? ?? '',
      uploader: j['uploader'] as String? ?? '共享空间',
      mediaType: j['media_type'] as String? ?? 'photo',
      duration: (j['duration'] as num?)?.toDouble(),
      width: (j['width'] as num?)?.toInt(),
      height: (j['height'] as num?)?.toInt());

  /// 视频角标时长文案（124 → "2:04"）
  String get durationLabel {
    final d = duration;
    if (d == null || d <= 0) return '';
    final s = d.round();
    final h = s ~/ 3600;
    final m = (s % 3600) ~/ 60;
    final sec = s % 60;
    return h > 0
        ? '$h:${m.toString().padLeft(2, '0')}:${sec.toString().padLeft(2, '0')}'
        : '$m:${sec.toString().padLeft(2, '0')}';
  }
}

String encPath(String p) => p.split('/').map(Uri.encodeComponent).join('/');
