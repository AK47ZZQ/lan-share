/// 备份状态镜像的 SharedPreferences key 单一事实源（07 审查 §3.4/§5.1-4）。
///
/// 备份引擎在后台 isolate 写这些键，首页/备份设置页 2s 轮询读——键名字符串
/// 此前散落 engine/config/settings/home/main 五个文件，靠人肉对齐（隐性
/// 跨 isolate 契约）。读写两端一律引用本类常量。
/// `workerState` 由 Kotlin 侧 WorkManager 回调写入（backup_round.dart 注释），
/// 属 Dart↔原生契约，改名必须两端同步。
class BackupKeys {
  BackupKeys._();

  /// 开关：Kotlin 侧也读它（WorkManager Worker 前置检查 + ContentObserver
  /// 即时触发判定），键名不可改
  static const enabled = 'backup_enabled';
  static const workerState = 'backup_worker_state';
  static const lastRound = 'backup_last_round';
  static const progressDone = 'backup_progress_done';
  static const progressTotal = 'backup_progress_total';
  static const progressCurrent = 'backup_progress_current';
  static const pendingCount = 'backup_pending_count';
  static const pendingPhotos = 'backup_pending_photos';
  static const pendingVideos = 'backup_pending_videos';
  static const pendingBytes = 'backup_pending_bytes';
  static const failedCount = 'backup_failed_count';
  static const lastDoneAt = 'backup_last_done_at';
  static const cooldownUntil = 'backup_cooldown_until';
  static const missingBuckets = 'backup_missing_buckets';
  static const ledgerUser = 'backup_ledger_user';


  /// 清空服务器备份的临时关停标记：wipe 流程写 '1'，App 启动时读到后
  /// 暂停自动备份并清除（main.dart 启动自检）
  static const wipePaused = 'wipe_backup_paused';
}
