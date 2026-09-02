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
  PhotoItem({required this.path, required this.name, required this.taken,
      required this.month, required this.uploader,
      this.mediaType = 'photo', this.duration});

  bool get isVideo => mediaType == 'video';

  factory PhotoItem.fromJson(Map<String, dynamic> j) => PhotoItem(
      path: j['path'] as String,
      name: j['name'] as String,
      taken: j['taken'] as String? ?? '',
      month: j['month'] as String? ?? '',
      uploader: j['uploader'] as String? ?? '共享空间',
      mediaType: j['media_type'] as String? ?? 'photo',
      duration: (j['duration'] as num?)?.toDouble());

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
