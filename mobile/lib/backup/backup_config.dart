// 自动备份配置（FR-2 / APP-DESIGN §4）：非敏感开关走 SharedPreferences。
// 约束开关默认值与需求一致：仅 Wi-Fi、充电时均默认开；视频默认关（已与用户确认）。
// enabled 同时被 Kotlin 侧读取（WorkManager Worker 前置检查 +
// ContentObserver 即时触发判定），键名不可改：flutter.backup_enabled。
import 'package:shared_preferences/shared_preferences.dart';

class BackupConfig {
  final bool enabled;
  final bool wifiOnly;
  final bool chargingOnly;
  final bool includeVideos;
  final bool notifyDone;
  final bool allowPublicServer; // §6：公网地址默认不备份（流量保护），可开

  const BackupConfig({
    this.enabled = false,
    this.wifiOnly = true,
    this.chargingOnly = true,
    this.includeVideos = false,
    this.notifyDone = true,
    this.allowPublicServer = false,
  });

  static Future<BackupConfig> load() async {
    final sp = await SharedPreferences.getInstance();
    return BackupConfig(
      enabled: sp.getBool(_kEnabled) ?? false,
      wifiOnly: sp.getBool(_kWifi) ?? true,
      chargingOnly: sp.getBool(_kCharging) ?? true,
      includeVideos: sp.getBool(_kVideos) ?? false,
      notifyDone: sp.getBool(_kNotify) ?? true,
      allowPublicServer: sp.getBool(_kPublic) ?? false,
    );
  }

  Future<void> save() async {
    final sp = await SharedPreferences.getInstance();
    await sp.setBool(_kEnabled, enabled);
    await sp.setBool(_kWifi, wifiOnly);
    await sp.setBool(_kCharging, chargingOnly);
    await sp.setBool(_kVideos, includeVideos);
    await sp.setBool(_kNotify, notifyDone);
    await sp.setBool(_kPublic, allowPublicServer);
  }

  static const _kEnabled = 'backup_enabled';
  static const _kWifi = 'backup_wifi_only';
  static const _kCharging = 'backup_charging_only';
  static const _kVideos = 'backup_include_videos';
  static const _kNotify = 'backup_notify_done';
  static const _kPublic = 'backup_allow_public';
}
