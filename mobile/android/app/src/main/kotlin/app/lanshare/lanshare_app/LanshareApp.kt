package app.lanshare.lanshare_app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import org.json.JSONObject

class LanshareApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Channels.ensure(this)
        MediaObserver.register(this)
    }
}

object Channels {
    const val PROGRESS = "backup_progress"
    const val RESULT = "backup_result"

    fun ensure(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(PROGRESS, "自动备份进行中", NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(
            NotificationChannel(RESULT, "自动备份结果", NotificationManager.IMPORTANCE_DEFAULT))
    }
}

/**
 * 备份状态落盘（0.7.3 可观测性）：把 Worker/队列的每个状态写进 Flutter
 * shared_preferences，供首页卡片与设置页诊断卡展示。此前任务在 WorkManager
 * 队列里等约束（充电/Wi-Fi）时一行代码都不执行，用户看到的是"点了没反应"；
 * 现在入队即落"等待充电/等待 Wi-Fi"，跑起来再覆盖为 running/done/...。
 */
object BackupState {
    fun record(ctx: Context, status: String, reason: String? = null) {
        try {
            val v = JSONObject()
                .put("status", status)
                .put("reason", reason ?: JSONObject.NULL)
                .put("ts", System.currentTimeMillis())
            ctx.getSharedPreferences("FlutterSharedPreferences", Context.MODE_PRIVATE)
                .edit()
                .putString("flutter.backup_worker_state", v.toString())
                .apply()
        } catch (_: Exception) {
        }
    }
}

/**
 * MediaStore 变更即时感知（APP-DESIGN §4）：拍照/截屏落库后 10s 防抖触发
 * 一次性备份任务。仅当 App 进程活着时能感知；进程被杀由 WorkManager 周期
 * 任务兜底（JobScheduler 跨重启持久，无需 BOOT receiver）。
 * 开关状态直接读 Flutter shared_preferences 同名文件，避免再建一套配置。
 */
object MediaObserver {
    private val handler = Handler(Looper.getMainLooper())
    private var registered = false
    private lateinit var appCtx: Context

    // 开关判定（SharedPreferences 首读有磁盘 IO）切到后台线程，主线程只做防抖
    private val debounce = Runnable {
        CoroutineScope(Dispatchers.Default).launch { enqueueOnce(appCtx) }
    }

    fun register(ctx: Context) {
        if (registered) return
        registered = true
        appCtx = ctx.applicationContext
        val observer = object : ContentObserver(handler) {
            override fun onChange(selfChange: Boolean, uri: Uri?) {
                handler.removeCallbacks(debounce)
                handler.postDelayed(debounce, 10_000)
            }
        }
        ctx.contentResolver.registerContentObserver(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true, observer)
        ctx.contentResolver.registerContentObserver(
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI, true, observer)
    }

    fun enabled(ctx: Context): Boolean =
        ctx.getSharedPreferences("FlutterSharedPreferences", Context.MODE_PRIVATE)
            .getBoolean("flutter.backup_enabled", false)

    /** 自动触发（媒体变更）入队：约束与周期任务一致（仅 Wi-Fi/仅充电）。
     *  入队即落"等待充电/Wi-Fi"状态——任务排队期间不执行任何代码，不落这个
     *  状态用户就只看到"点了没反应"（0.7.3 前的真实投诉）。 */
    fun enqueueOnce(ctx: Context) {
        if (!enabled(ctx)) return
        val p = ctx.getSharedPreferences("FlutterSharedPreferences", Context.MODE_PRIVATE)
        val wifiOnly = p.getBoolean("flutter.backup_wifi_only", true)
        val chargingOnly = p.getBoolean("flutter.backup_charging_only", true)
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(
                if (wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
            .setRequiresCharging(chargingOnly)
            .setRequiresBatteryNotLow(true)
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            "lanshare_backup_once",
            ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<BackupWorker>()
                .setConstraints(constraints)
                .setInputData(workDataOf("trigger" to "media_change"))
                .build())
        val waiting = mutableListOf<String>()
        if (chargingOnly && !BackupDartRunner.isCharging(ctx)) waiting.add("charging")
        if (wifiOnly && BackupDartRunner.netMetered(ctx)) waiting.add("wifi")
        BackupState.record(
            ctx,
            if (waiting.isEmpty()) "queued" else "queued_wait",
            if (waiting.isEmpty()) null else waiting.joinToString(","))
    }

    /** 手动"立即备份"（0.7.3）：用户当下的明确意图，**不带充电/电量约束**——
     *  此前沿用自动约束，未插电时任务永远在等充电器，"点了没反应、不弹通知"
     *  的根因（模拟器恒为充电中，E2E 从未暴露）。网络也不设约束：引擎在
     *  Dart 侧自查，仅 Wi-Fi 开着且在流量下时优雅暂停并给出可见原因。 */
    fun enqueueManual(ctx: Context) {
        if (!enabled(ctx)) {
            BackupState.record(ctx, "skip_disabled")
            return
        }
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            "lanshare_backup_manual",
            ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<BackupWorker>()
                .setInputData(workDataOf("trigger" to "manual"))
                .build())
        BackupState.record(ctx, "queued_manual")
    }
}
