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
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf

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

    private val debounce = Runnable { enqueueOnce(appCtx) }

    fun register(ctx: Context) {
        if (registered) return
        registered = true
        appCtx = ctx.applicationContext
        val observer = object : ContentObserver(handler) {
            override fun onChange(selfChange: Boolean, uri: Uri?) {
                if (!enabled(appCtx)) return
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
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            "lanshare_backup_once",
            ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<BackupWorker>()
                .setInputData(workDataOf("trigger" to "media_change"))
                .build())
    }
}
