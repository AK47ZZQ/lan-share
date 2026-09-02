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
  final List<String> excludedBuckets; // 排除的相册（0.6.0：截图/聊天图等）

  const BackupConfig({
    this.enabled = false,
    this.wifiOnly = true,
    this.chargingOnly = true,
    this.includeVideos = false,
    this.notifyDone = true,
    this.allowPublicServer = false,
    this.excludedBuckets = const [],
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
      excludedBuckets: sp.getStringList(_kExcluded) ?? const [],
    );
  }

  BackupConfig copyWith({
    bool? enabled,
    bool? wifiOnly,
    bool? chargingOnly,
    bool? includeVideos,
    bool? notifyDone,
    bool? allowPublicServer,
    List<String>? excludedBuckets,
  }) =>
      BackupConfig(
        enabled: enabled ?? this.enabled,
        wifiOnly: wifiOnly ?? this.wifiOnly,
        chargingOnly: chargingOnly ?? this.chargingOnly,
        includeVideos: includeVideos ?? this.includeVideos,
        notifyDone: notifyDone ?? this.notifyDone,
        allowPublicServer: allowPublicServer ?? this.allowPublicServer,
        excludedBuckets: excludedBuckets ?? this.excludedBuckets,
      );

  Future<void> save() async {
    final sp = await SharedPreferences.getInstance();
    await sp.setBool(_kEnabled, enabled);
    await sp.setBool(_kWifi, wifiOnly);
    await sp.setBool(_kCharging, chargingOnly);
    await sp.setBool(_kVideos, includeVideos);
    await sp.setBool(_kNotify, notifyDone);
    await sp.setBool(_kPublic, allowPublicServer);
    await sp.setStringList(_kExcluded, excludedBuckets);
    // 约束/范围变化会改变待备集合（如开启视频），pending 计数归 -1 让下个
    // 周期强制跑一轮，别被 Worker 的"空跑跳过"捷径挡住
    await sp.setInt('backup_pending_count', -1);
  }

  static const _kEnabled = 'backup_enabled';
  static const _kWifi = 'backup_wifi_only';
  static const _kCharging = 'backup_charging_only';
  static const _kVideos = 'backup_include_videos';
  static const _kNotify = 'backup_notify_done';
  static const _kPublic = 'backup_allow_public';
  static const _kExcluded = 'backup_excluded_buckets';
}
