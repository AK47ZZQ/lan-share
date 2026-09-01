// 系统分享接收桥（M-A4 / APP-REQUIREMENTS FR-6）。
// Kotlin 侧（ShareReceiver.kt）把 content:// 流拷进 cache/share_inbox/ 并排队；
// 本桥负责：取走排队文件 → 入上传队列（incoming/<用户>/分享暂存/）→
// 上传完成后删除本地副本（分享件是拷贝，源文件在分享方 App 里）。
import 'dart:io';

import 'package:flutter/services.dart';

import '../data/local/prefs.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';

class ShareBridge {
  ShareBridge._();
  static const _ch = MethodChannel('lanshare/share');
  static const inboxMarker = '/share_inbox/';

  static bool _wired = false;
  static bool _cleanupWired = false;
  static final _deleted = <String>{};

  /// Kotlin 推送"有新分享"时的回调（HomeShell 注入，用于弹提示/跳传输页）
  static void Function()? onIncoming;

  static void wire() {
    if (_wired) return;
    _wired = true;
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'incoming') onIncoming?.call();
      return null;
    });
  }

  /// 取走全部待收分享并入队。返回成功入队数；路径不落盘队列也没关系——
  /// 入队后由 TransferEngine 的落盘机制接管。
  static Future<int> consumePending() async {
    List<String> paths;
    try {
      final r = await _ch.invokeMethod('takePending');
      paths = (r as List?)?.whereType<String>().toList() ?? const [];
    } on PlatformException {
      return 0;
    } on MissingPluginException {
      return 0;
    }
    if (paths.isEmpty) return 0;
    final username = (await Prefs.username()) ?? '';
    if (username.isEmpty) return 0; // 未登录：文件留 Kotlin 侧待下次
    _wireCleanup();
    var n = 0;
    for (final p in paths) {
      final f = File(p);
      if (!f.existsSync()) continue;
      final name = p.split('/').last;
      // 去掉拷贝时加的时间戳前缀（<13位数字>_原名），还原分享方看到的名字
      final shown = _stripStamp(name);
      if (TransferEngine.I.enqueueUpload(
              localPath: p, destDir: 'incoming/$username/分享暂存', name: shown) !=
          null) {
        n++;
      }
    }
    return n;
  }

  /// Kotlin 侧加壳 `<13位毫秒>_原名[ (n).ext]`；这里还原展示名。
  /// 识别不出前缀（理论不可能）就原样展示。
  static String _stripStamp(String name) {
    final m = RegExp(r'^(\d{13})_(.+)$').firstMatch(name);
    return m?.group(2) ?? name;
  }

  /// 上传完成即删本地副本：监听传输引擎，done 且路径在 share_inbox 的上传任务
  static void _wireCleanup() {
    if (_cleanupWired) return;
    _cleanupWired = true;
    TransferEngine.I.addListener(() {
      for (final t in TransferEngine.I.tasks) {
        if (t.kind != TransferKind.upload ||
            t.status != TransferStatus.done ||
            _deleted.contains(t.localPath) ||
            !t.localPath.contains(inboxMarker)) {
          continue;
        }
        _deleted.add(t.localPath);
        // ignore: discarded_futures
        () async {
          try {
            await File(t.localPath).delete();
          } catch (_) {}
        }();
      }
    });
  }
}
