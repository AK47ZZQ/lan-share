package app.lanshare.lanshare_app

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import java.util.concurrent.TimeUnit

/**
 * 周期备份调度（APP-DESIGN §4）：WorkManager PeriodicWorkRequest，15 分钟起步，
 * 系统约束 = NetworkType（仅 Wi-Fi→UNMETERED）+ 可选充电；跨重启由 JobScheduler
 * 自动恢复。约束变化用 UPDATE 策略原地换（不打断周期节奏）。
 */
object BackupScheduler {
    private const val PERIODIC = "lanshare_backup_periodic"

    fun schedulePeriodic(ctx: Context, args: Map<*, *>?) {
        val wifiOnly = args?.get("wifiOnly") as? Boolean ?: true
        val chargingOnly = args?.get("chargingOnly") as? Boolean ?: false
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(if (wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
            .setRequiresCharging(chargingOnly)
            // 电量低不启动（约束只管启动；跑起来之后由引擎逐张 checkStop 复查）
            .setRequiresBatteryNotLow(true)
            .build()
        val req = PeriodicWorkRequestBuilder<BackupWorker>(15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .setInputData(workDataOf("trigger" to "periodic"))
            .build()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
            PERIODIC, ExistingPeriodicWorkPolicy.UPDATE, req)
    }

    fun cancelPeriodic(ctx: Context) {
        WorkManager.getInstance(ctx).cancelUniqueWork(PERIODIC)
    }
}
