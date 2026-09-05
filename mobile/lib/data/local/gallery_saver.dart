// 已下载媒体保存进系统相册（v0.13 传输页）：走 lanshare/media 通道的
// saveToGallery（MediaStoreBridge，IS_PENDING 无需存储权限，落
// Pictures|Movies/LAN Share/）。仅图片/视频有意义，其他类型不提供入口。
import 'package:flutter/services.dart';

class GallerySaver {
  static const _ch = MethodChannel('lanshare/media');

  /// 返回 null=成功；否则为面向用户的失败原因
  static Future<String?> save(String path, {required bool isVideo}) async {
    try {
      final r = await _ch.invokeMethod<String>('saveToGallery', {
        'path': path,
        'isVideo': isVideo,
      });
      if (r == null) return '保存失败';
      if (r.contains('"ok":true') || r.contains('"ok": true')) return null;
      // MediaStoreBridge 错误协议：{"error":"..."}
      final msg = switch (r) {
        String s when s.contains('requires_android_10') => '系统版本过低（需 Android 10+）',
        String s when s.contains('file_missing') => '本地文件已被清理',
        String s when s.contains('insert_failed') || s.contains('open_output') ||
            s.contains('copy_failed') || s.contains('publish_failed') =>
          '写入相册失败',
        _ => '保存失败',
      };
      return msg;
    } on MissingPluginException {
      return '当前环境不支持';
    } on PlatformException {
      return '保存失败';
    }
  }
}
