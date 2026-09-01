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

    fun enqueueOnce(ctx: Context) {
        if (!enabled(ctx)) return
        // 即时任务与周期任务同约束（读同一份 Flutter prefs）：否则拍照会绕过
        // 仅 Wi-Fi/仅充电，在蜂窝/未充电时也开跑——约束收口（0.4.2）
        val p = ctx.getSharedPreferences("FlutterSharedPreferences", Context.MODE_PRIVATE)
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(
                if (p.getBoolean("flutter.backup_wifi_only", true)) NetworkType.UNMETERED
                else NetworkType.CONNECTED)
            .setRequiresCharging(p.getBoolean("flutter.backup_charging_only", true))
            .setRequiresBatteryNotLow(true)
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            "lanshare_backup_once",
            ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<BackupWorker>()
                .setConstraints(constraints)
                .setInputData(workDataOf("trigger" to "media_change"))
                .build())
    }
}
