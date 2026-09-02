// 主 isolate ↔ 原生调度桥（APP-DESIGN §4）：设置页开关/立即备份经此
// 驱动 WorkManager。平台未实现（iOS 等）一律静默降级。
import 'package:flutter/services.dart';

import 'backup_config.dart';

class BackupBridge {
  static const _ch = MethodChannel('lanshare/backup_host');

  /// 按配置挂/撤周期任务（15min 起步，系统约束：网络类型 + 充电）
  static Future<void> applySchedule(BackupConfig c) async {
    try {
      if (!c.enabled) {
        await _ch.invokeMethod('cancelPeriodic');
      } else {
        await _ch.invokeMethod('schedulePeriodic', {
          'wifiOnly': c.wifiOnly,
          'chargingOnly': c.chargingOnly,
        });
      }
    } catch (_) {}
  }

  /// 媒体读权限（+通知，完成提醒用）。granted=false 时备份无法开启。
  /// 兜底超时：权限弹窗期间 Activity 被系统回收时原生结果可能丢失，
  /// 不能让设置页的开关永久停在等待态。
  static Future<bool> requestPermissions({required bool includeVideos}) async {
    try {
      final r = await _ch
          .invokeMethod<Map<Object?, Object?>>(
              'requestPermissions', {'videos': includeVideos})
          .timeout(const Duration(minutes: 2),
              onTimeout: () => {'granted': false});
      return r?['granted'] == true;
    } catch (_) {
      return false;
    }
  }

  /// 立即跑一轮（走同一 Worker 路径，与周期任务行为完全一致）
  static Future<void> runOnce() async {
    try {
      await _ch.invokeMethod('runOnce');
    } catch (_) {}
  }

  /// 是否已在电池优化白名单（0.6.2）。取不到按"已豁免"处理——
  /// 引导提示宁缺毋滥，别在通道异常的机型上空报警。
  static Future<bool> batteryIgnoring() async {
    try {
      final r = await _ch.invokeMethod<Map<Object?, Object?>>('battery');
      return r?['ignoring'] == true;
    } catch (_) {
      return true;
    }
  }

  /// 拉起系统"忽略电池优化"确认弹窗（小米/华为等靠它保住周期备份）
  static Future<void> batteryRequest() async {
    try {
      await _ch.invokeMethod('batteryRequest');
    } catch (_) {}
  }

  /// 备份诊断快照（0.7.3）：充电中/是否计费网络/电池白名单/通知权限。
  /// 取不到返回 null，诊断卡对应行显示"未知"而不是猜。
  static Future<Map<String, Object?>?> diag() async {
    try {
      final r = await _ch.invokeMethod<Map<Object?, Object?>>('diag');
      return r?.map((k, v) => MapEntry(k.toString(), v));
    } catch (_) {
      return null;
    }
  }

  /// 打开系统 App 通知设置页（诊断卡发现通知被关时一键前往）
  static Future<void> openNotificationSettings() async {
    try {
      await _ch.invokeMethod('openNotifSettings');
    } catch (_) {}
  }
}
