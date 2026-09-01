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
}
