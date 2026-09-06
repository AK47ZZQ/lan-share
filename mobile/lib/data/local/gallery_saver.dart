// 已下载媒体保存进系统相册（v0.13 传输页）：走 lanshare/media 通道的
// saveToGallery（MediaStoreBridge，IS_PENDING 无需存储权限，落
// Pictures|Movies/LAN Share/）。仅图片/视频有意义，其他类型不提供入口。
// v0.20：错误原样透出（含机型侧底层原因），不再统一吞成"保存失败"。
import 'dart:convert';

import 'package:flutter/services.dart';

class GallerySaver {
  static const _ch = MethodChannel('lanshare/media');

  /// 返回 null=成功；否则为面向用户的失败原因
  static Future<String?> save(String path, {required bool isVideo}) async {
    String raw;
    try {
      raw = await _ch.invokeMethod<String>('saveToGallery', {
        'path': path,
        'isVideo': isVideo,
      }) ??
          '';
    } on MissingPluginException {
      return '当前环境不支持';
    } on PlatformException catch (e) {
      return '保存失败（${e.code}）';
    }
    if (raw.contains('"ok":true') || raw.contains('"ok": true')) return null;
    // MediaStoreBridge 错误协议：{"error":"code[:Exception:msg]"}——
    // 已知错误给友好文案；未知的把底层原因带出来（机型差异排障靠它）
    var code = raw;
    try {
      code = (jsonDecode(raw) as Map)['error']?.toString() ?? raw;
    } catch (_) {}
    if (code.isEmpty) return '保存失败';
    const friendly = {
      'missing_path': '参数缺失',
      'file_missing': '本地文件已被清理',
      'requires_android_10': '系统版本过低（需 Android 10+）',
      'insert_failed': '写入相册失败',
      'open_output_failed': '写入相册失败',
      'copy_failed': '写入相册失败',
      'publish_failed': '发布到相册失败',
    };
    final base = code.split(':').first;
    final msg = friendly[base];
    if (msg != null && !code.contains(':')) return msg;
    // insert_failed/copy_failed 等带异常细节：友好文案 + 底层原因
    return msg != null ? '$msg（${_short(code)}）' : '保存失败（${_short(code)}）';
  }

  /// 异常类名+消息压成一行短摘要（去 MediaStore 前缀和长栈路径）
  static String _short(String code) {
    final parts = code.split(':');
    if (parts.length < 2) return code;
    final cls = parts[1];
    final msg = parts.length > 2 ? parts.sublist(2).join(':') : '';
    final m = msg.replaceAll(RegExp(r'\s+'), ' ').trim();
    return m.isEmpty ? cls : '$cls: ${m.length > 80 ? m.substring(0, 80) : m}';
  }
}
