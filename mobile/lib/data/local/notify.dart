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
  int _seq = 20260926;   // 起始值抬到 _kFocusNotifyId 之上，杜绝 id 空间撞车（审查 P2-5）

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

/// 站内通知（通知中心）的系统通知封装（轮询起步版推送，v0.11）。
/// 复用同一插件实例与图标；独立 channel 便于用户在系统设置里单独关闭。
extension InboxNotifier on TransferNotifier {
  Future<void> inbox({required String title, required String body}) async {
    try {
      await _ensure();
      await _plugin.show(
          _seq++,
          title,
          body,
          const NotificationDetails(
              android: AndroidNotificationDetails(
                  'inbox', '站内通知',
                  channelDescription: '定向分享、照片互动等站内事件提醒',
                  importance: Importance.defaultImportance,
                  priority: Priority.defaultPriority,
                  icon: 'ic_notification',
                  color: Color(0xFF5B6CFF))));
    } catch (_) {
      // 通知失败不影响业务
    }
  }
}

/// 番茄钟专注中的常驻通知（M2，App v1.1.12）：会话进行期间挂 ongoing 通知，
/// 每 30s 更新剩余时间；完成/放弃时撤下并按需补一条结果通知。
/// 约束：通知更新只在 App 存活期间发生（无前台服务）——杀 App 后由服务端
/// 惰性结算兜底，时长不丢；MIUI/Android14 真机表现仍待验证（计划 M2 硬门槛）。
const int _kFocusNotifyId = 20260925;

extension FocusNotifier on TransferNotifier {
  Future<void> focusOngoing(
      {required String title, required int remainingSec}) async {
    final m = remainingSec ~/ 60;
    final s = remainingSec % 60;
    try {
      await _ensure();
      await _plugin.show(
          _kFocusNotifyId,
          '专注中 · $title',
          '剩余 ${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}',
          const NotificationDetails(
              android: AndroidNotificationDetails(
                  'focus_running', '专注进行中',
                  channelDescription: '番茄钟专注会话的常驻计时',
                  importance: Importance.low,
                  priority: Priority.low,
                  ongoing: true,
                  onlyAlertOnce: true,
                  icon: 'ic_notification',
                  color: Color(0xFF5B6CFF))));
    } catch (_) {
      // 通知失败不影响计时本身
    }
  }

  Future<void> focusFinished({required String title}) async {
    try {
      await _ensure();
      await _plugin.cancel(_kFocusNotifyId);
      await _plugin.show(
          _kFocusNotifyId + 1,
          '专注完成 🎉',
          title,
          const NotificationDetails(
              android: AndroidNotificationDetails(
                  'focus_done', '专注结果',
                  channelDescription: '番茄会话完成提醒',
                  importance: Importance.defaultImportance,
                  priority: Priority.defaultPriority,
                  icon: 'ic_notification',
                  color: Color(0xFF5B6CFF))));
    } catch (_) {}
  }

  Future<void> focusDismissed() async {
    try {
      await _ensure();
      await _plugin.cancel(_kFocusNotifyId);
    } catch (_) {}
  }
}
