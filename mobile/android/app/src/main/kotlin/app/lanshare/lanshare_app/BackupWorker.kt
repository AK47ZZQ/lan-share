package app.lanshare.lanshare_app

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.CoroutineWorker
import androidx.work.ForegroundInfo
import androidx.work.WorkerParameters
import io.flutter.FlutterInjector
import io.flutter.embedding.engine.FlutterEngineGroup
import io.flutter.embedding.engine.dart.DartExecutor
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import kotlin.coroutines.resume

/**
 * 备份 Worker（自写薄桥，APP-DESIGN §4：不引第三方后台插件）：
 * 前置检查（enabled）→ 前台通知保护 → 拉起后台 isolate 跑 Dart 备份引擎 →
 * 按结果决定 success/retry → 完成通知（可关）。
 */
class BackupWorker(private val ctx: Context, params: WorkerParameters) :
    CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val prefs = ctx.getSharedPreferences("FlutterSharedPreferences", Context.MODE_PRIVATE)
        if (!prefs.getBoolean("flutter.backup_enabled", false)) return Result.success()

        // 前台保护（设计 §4"整批在通知保护下执行"）。Android 12+ 对后台启动
        // FGS 有限制：失败就静默降级——周期任务 10 分钟窗口内小批量可跑完，
        // 大批量靠账本断点分多轮跑完，不丢数据。
        try {
            setForeground(fgInfo(applicationContext, "自动备份运行中…"))
        } catch (e: Exception) {
            android.util.Log.w("BackupWorker", "setForeground failed: ${e.message}")
        }

        val json = BackupDartRunner.run(ctx, inputData.getString("trigger") ?: "periodic")
        val obj = try {
            JSONObject(json)
        } catch (e: Exception) {
            JSONObject()
        }
        return when (obj.optString("status")) {
            "done" -> {
                val uploaded = obj.optInt("uploaded", 0)
                val failed = obj.optInt("failed", 0)
                if (prefs.getBoolean("flutter.backup_notify_done", true) && uploaded > 0) {
                    notifyDone(applicationContext, uploaded, failed)
                }
                Result.success()
            }
            // Dart 崩溃/瞬时错误：WorkManager 退避重试（账本保证幂等）
            "error" -> Result.retry()
            // skip / cooldown：等下个周期，不重试
            else -> Result.success()
        }
    }

    private fun notifyDone(ctx: Context, uploaded: Int, failed: Int) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ctx.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return
        val text = if (failed > 0) "已备份 $uploaded 张，$failed 张失败" else "已备份 $uploaded 张"
        val open = PendingIntent.getActivity(
            ctx, 0, ctx.packageManager.getLaunchIntentForPackage(ctx.packageName),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(ctx, Channels.RESULT)
            .setSmallIcon(android.R.drawable.stat_sys_upload_done)
            .setContentTitle("LAN Share 自动备份")
            .setContentText(text)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(ctx).notify(RESULT_NOTIF_ID, n)
    }

    companion object {
        // 前台通知与结果通知必须分 ID：Worker 结束时系统按前台 ID 撤通知，
        // 同 ID 会把刚发的结果通知一起撤掉（E2E 实测 posted=removed=10）
        const val FG_NOTIF_ID = 1001
        const val RESULT_NOTIF_ID = 2001

        fun fgInfo(ctx: Context, text: String): ForegroundInfo {
            val n = NotificationCompat.Builder(ctx, Channels.PROGRESS)
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .setContentTitle("LAN Share 自动备份")
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .build()
            return if (Build.VERSION.SDK_INT >= 29)
                ForegroundInfo(FG_NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            else
                ForegroundInfo(FG_NOTIF_ID, n)
        }
    }
}

/**
 * 每次任务拉起独立 FlutterEngine 跑 backupMain（用完即毁）。
 * 握手：Dart 设好 handler 后回 "ready"，Kotlin 收到才下发 "run"——
 * 否则引擎启动窗口期 invoke 会落 notImplemented。
 */
object BackupDartRunner {

    suspend fun run(ctx: Context, trigger: String): String = withContext(Dispatchers.Main) {
        val entrypoint = DartExecutor.DartEntrypoint(
            FlutterInjector.instance().flutterLoader().findAppBundlePath(),
            "backupMain")
        val engine = FlutterEngineGroup(ctx).createAndRunEngine(ctx, entrypoint)
        try {
            val backupCh = MethodChannel(engine.dartExecutor.binaryMessenger, "lanshare/backup")
            val mediaCh = MethodChannel(engine.dartExecutor.binaryMessenger, "lanshare/media")
            // 媒体查询/临时文件在后台 isolate 同样可用（备份引擎要扫描媒体库）
            mediaCh.setMethodCallHandler { call, result ->
                MediaStoreBridge.handle(ctx, call, result)
            }
            val ready = CompletableDeferred<Unit>()
            backupCh.setMethodCallHandler { call, result ->
                when (call.method) {
                    "ready" -> {
                        ready.complete(Unit)
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
            withTimeoutOrNull(60_000) { ready.await() }
                ?: return@withContext "{\"status\":\"error\",\"reason\":\"dart_not_ready\"}"

            suspendCancellableCoroutine { cont ->
                backupCh.invokeMethod("run", trigger, object : MethodChannel.Result {
                    override fun success(r: Any?) {
                        if (cont.isActive) cont.resume(r as? String ?: "{}")
                    }

                    override fun error(code: String, msg: String?, details: Any?) {
                        if (cont.isActive) cont.resume(
                            JSONObject().put("status", "error")
                                .put("reason", msg ?: code).toString())
                    }

                    override fun notImplemented() {
                        if (cont.isActive) cont.resume(
                            "{\"status\":\"error\",\"reason\":\"not_implemented\"}")
                    }
                })
            }
        } finally {
            engine.destroy()
        }
    }
}
