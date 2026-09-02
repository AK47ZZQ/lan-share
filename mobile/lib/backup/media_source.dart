// 媒体库访问（自写薄通道，APP-DESIGN §4"媒体扫描器（平台通道）"）：
// query 拉全量 [id,kind,name,size,mtime] 紧凑数组（账本 diff 用）；
// open 把单张媒体拷到缓存临时文件（哈希/分片上传都走真实文件路径）。
// 主引擎与后台 isolate 都可能调（MainActivity 与 Worker 各自注册同名通道）。
import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';

class MediaItem {
  final String mediaId;
  final String kind; // image | video
  final String name;
  final int size;
  final int mtime;
  final String bucket; // 相册目录名（BUCKET_DISPLAY_NAME，0.6.0 排除相册用）
  const MediaItem(this.mediaId, this.kind, this.name, this.size, this.mtime,
      {this.bucket = ''});
}

class MediaSource {
  static const _ch = MethodChannel('lanshare/media');

  /// 原生桥约定"永不抛 PlatformException"：出错也回 success，正文是
  /// {"error": ...} JSON。这里统一识别并转成真异常——此前 buckets() 拿到
  /// 错误 JSON 会按数据解出古怪的 TypeError，open() 更会把错误串当路径用。
  /// 公开为纯函数便于单测（0.6.2）。
  static String mediaPayload(String? raw, String method) {
    if (raw == null || raw.isEmpty) {
      throw PlatformException(code: 'media_channel', message: '$method：原生无返回');
    }
    if (raw.startsWith('{')) {
      final obj = jsonDecode(raw);
      if (obj is Map && obj['error'] != null) {
        throw PlatformException(
            code: 'media_channel', message: '$method 失败：${obj['error']}');
      }
    }
    return raw;
  }

  /// 全量媒体清单（0 字节的脏记录由原生侧过滤）
  static Future<List<MediaItem>> query({required bool includeVideos}) async {
    final raw =
        mediaPayload(await _ch.invokeMethod<String>('query', {'videos': includeVideos}), '扫描媒体库');
    final arr = jsonDecode(raw) as List;
    return [
      for (final e in arr)
        MediaItem(e[0] as String, e[1] as String, e[2] as String,
            (e[3] as num).toInt(), (e[4] as num).toInt(),
            bucket: e.length > 5 ? (e[5] as String? ?? '') : ''),
    ];
  }

  /// 相册名 → 媒体数（排除设置页候选；0.6.0 新增原生 buckets 方法）
  static Future<Map<String, int>> buckets() async {
    final raw = mediaPayload(await _ch.invokeMethod<String>('buckets'), '读取相册列表');
    if (raw.isEmpty) return const {};
    final obj = jsonDecode(raw) as Map<String, dynamic>;
    return obj.map((k, v) => MapEntry(k, (v as num).toInt()));
  }

  /// 拷贝到缓存临时文件，返回路径（用完由引擎删除）
  static Future<String> openTemp(String mediaId, String kind) async {
    final raw = mediaPayload(
        await _ch.invokeMethod<String>('open', {'id': mediaId, 'kind': kind}),
        '读取媒体文件');
    return raw;
  }

  /// 删除临时文件（失败忽略）
  static void cleanup(String path) {
    try {
      final f = File(path);
      if (f.existsSync()) f.deleteSync();
    } catch (_) {}
  }
}
