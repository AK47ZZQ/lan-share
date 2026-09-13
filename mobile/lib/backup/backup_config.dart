// 自动备份配置（FR-2 / APP-DESIGN §4）：非敏感开关走 SharedPreferences。
// 约束开关默认值与需求一致：仅 Wi-Fi、充电时均默认开；视频默认关（已与用户确认）。
// enabled 同时被 Kotlin 侧读取（WorkManager Worker 前置检查 +
// ContentObserver 即时触发判定），键名不可改：flutter.backup_enabled。
import 'backup_keys.dart';
import 'package:shared_preferences/shared_preferences.dart';

class BackupConfig {
  final bool enabled;
  final bool paused; // 用户手动暂停（v0.17）：开关仍开、调度仍在，引擎逐张前检查。
  final bool wifiOnly;
  final bool chargingOnly;
  final bool includeVideos;
  final bool notifyDone;
  final bool allowPublicServer; // §6：公网地址默认不备份（流量保护），可开
  final List<String> selectedBuckets; // 白名单：要备份的相册（0.14；空=一个都不备份，
                                      // 见 backup_engine 白名单语义）
  final List<String> excludedBuckets; // 旧排除表（0.6.0）：仅兼容存量配置，
                                      // 新 UI 不再编辑，保存白名单时清空

  const BackupConfig({
    this.enabled = false,
    this.paused = false,
    this.wifiOnly = true,
    this.chargingOnly = true,
    this.includeVideos = false,
    this.notifyDone = true,
    this.allowPublicServer = false,
    this.selectedBuckets = const [],
    this.excludedBuckets = const [],
  });

  /// 读取配置。
  ///
  /// ⚠️ `fresh: true` 必须用于**后台引擎**路径（bug·933e「暂停备份按钮失效」根修）：
  /// 备份引擎跑在独立 isolate（FlutterEngineGroup 每任务拉起），而
  /// `SharedPreferences.getInstance()` 的 Dart 侧缓存**是每 isolate 一份**——
  /// 前台 UI 写入 `paused=true` 后，后台引擎的缓存实例仍看到旧值 `false`，
  /// 于是"暂停"点了没反应、照片照跑。这与 0.4.6 批记录的"跨 isolate prefs
  /// 缓存坑"同源（当时是后台写、UI 读不到，这次方向相反）。
  /// `reload()` 强制从平台侧重读，保证引擎看到前台刚写的开关。
  static Future<BackupConfig> load({bool fresh = false}) async {
    final sp = await SharedPreferences.getInstance();
    if (fresh) {
      try {
        await sp.reload();
      } catch (_) {
        // reload 失败（理论不发生）就按当前缓存继续：宁可沿用旧值，也不要
        // 让读配置本身抛异常把整轮备份打断。
      }
    }
    return BackupConfig(
      enabled: sp.getBool(_kEnabled) ?? false,
      paused: sp.getBool(_kPaused) ?? false,
      wifiOnly: sp.getBool(_kWifi) ?? true,
      chargingOnly: sp.getBool(_kCharging) ?? true,
      includeVideos: sp.getBool(_kVideos) ?? false,
      notifyDone: sp.getBool(_kNotify) ?? true,
      allowPublicServer: sp.getBool(_kPublic) ?? false,
      selectedBuckets: sp.getStringList(_kSelected) ?? const [],
      excludedBuckets: sp.getStringList(_kExcluded) ?? const [],
    );
  }

  BackupConfig copyWith({
    bool? enabled,
    bool? paused,
    bool? wifiOnly,
    bool? chargingOnly,
    bool? includeVideos,
    bool? notifyDone,
    bool? allowPublicServer,
    List<String>? selectedBuckets,
    List<String>? excludedBuckets,
  }) =>
      BackupConfig(
        enabled: enabled ?? this.enabled,
        paused: paused ?? this.paused,
        wifiOnly: wifiOnly ?? this.wifiOnly,
        chargingOnly: chargingOnly ?? this.chargingOnly,
        includeVideos: includeVideos ?? this.includeVideos,
        notifyDone: notifyDone ?? this.notifyDone,
        allowPublicServer: allowPublicServer ?? this.allowPublicServer,
        selectedBuckets: selectedBuckets ?? this.selectedBuckets,
        excludedBuckets: excludedBuckets ?? this.excludedBuckets,
      );

  Future<void> save() async {
    final sp = await SharedPreferences.getInstance();
    await sp.setBool(_kEnabled, enabled);
    await sp.setBool(_kPaused, paused);
    await sp.setBool(_kWifi, wifiOnly);
    await sp.setBool(_kCharging, chargingOnly);
    await sp.setBool(_kVideos, includeVideos);
    await sp.setBool(_kNotify, notifyDone);
    await sp.setBool(_kPublic, allowPublicServer);
    await sp.setStringList(_kSelected, selectedBuckets);
    await sp.setStringList(_kExcluded, excludedBuckets);
    // 约束/范围变化会改变待备集合（如开启视频），pending 计数归 -1 让下个
    // 周期强制跑一轮，别被 Worker 的"空跑跳过"捷径挡住
    await sp.setInt(BackupKeys.pendingCount, -1);
  }

  static const _kEnabled = BackupKeys.enabled;
  static const _kPaused = 'backup_user_paused';
  static const _kWifi = 'backup_wifi_only';
  static const _kCharging = 'backup_charging_only';
  static const _kVideos = 'backup_include_videos';
  static const _kNotify = 'backup_notify_done';
  static const _kPublic = 'backup_allow_public';
  static const _kSelected = 'backup_selected_buckets';
  static const _kExcluded = 'backup_excluded_buckets';
}
