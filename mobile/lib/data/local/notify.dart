// 传输完成系统通知（App v0.6.0）：备份通知走 Kotlin 自有通道；App 内
// 传输队列（上传/下载）完成时经本封装发系统通知，可在设置里关。
// 权限在初始化时顺带请求（Android 13+ POST_NOTIFICATIONS 运行时授权）。
import 'dart:ui' show Color;

import 'package:flutter_local_notifications/flutter_local_notifications.dart';

class TransferNotifier {
  TransferNotifier._();
  static final TransferNotifier I = TransferNotifier._();

  final FlutterLocalNotificationsPlugin _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;
  int _seq = 1;

  Future<void> _ensure() async {
    if (_ready) return;
    await _plugin.initialize(const InitializationSettings(
        // 状态栏小图标必须用白色剪影（ic_notification），启动器图标会糊成一坨
        android: AndroidInitializationSettings('ic_notification')));
    await _plugin
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
    _ready = true;
  }

  Future<void> transferDone({required bool upload, required String name}) async {
    try {
      await _ensure();
      await _plugin.show(
          _seq++,
          upload ? '上传完成' : '下载完成',
          name,
          const NotificationDetails(
              android: AndroidNotificationDetails(
                  'transfer_done', '传输完成',
                  channelDescription: '单个文件上传/下载完成提醒',
                  importance: Importance.defaultImportance,
                  priority: Priority.defaultPriority,
                  // 状态栏小图标 + 品牌色（下拉展开时 app 名着色）
                  icon: 'ic_notification',
                  color: Color(0xFF5B6CFF))));
    } catch (_) {
      // 通知失败不影响传输本身
    }
  }
}
