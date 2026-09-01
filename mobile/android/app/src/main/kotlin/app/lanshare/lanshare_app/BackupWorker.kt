package app.lanshare.lanshare_app

import android.app.Notification
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
import kotlinx.coroutines.sync.Mutex
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
        val trigger = inputData.getString("trigger") ?: "periodic"
        // 周期任务空跑跳过：账本无未完成项就不值得拉一次 FlutterEngine（数十 MB
        // 内存 + 秒级冷启动）。计数由引擎每轮结束时回写；-1 = 未知/配置刚变，
        // 强制跑。Observer 触发与手动立即备份不走此捷径（新照片靠它兜进）。
        // 注意 flutter shared_preferences 把 Dart int 存成 Long，必须 getLong。
        if (trigger == "periodic" &&
            prefs.getLong("flutter.backup_pending_count", -1L) == 0L
        ) return Result.success()

        // 前台保护（设计 §4"整批在通知保护下执行"）。Android 12+ 对后台启动
        // FGS 有限制：失败就静默降级——周期任务 10 分钟窗口内小批量可跑完，
        // 大批量靠账本断点分多轮跑完，不丢数据。
        try {
            setForeground(fgInfo(applicationContext, "自动备份运行中…"))
        } catch (e: Exception) {
            android.util.Log.w("BackupWorker", "setForeground failed: ${e.message}")
        }

        val json = BackupDartRunner.run(ctx, trigger)
        val obj = try {
            JSONObject(json)
        } catch (e: Exception) {
            // 引擎返回了非 JSON：按错误走 retry，不能空对象——空 status 会被
            // 下面 else 分支当"无事发生"吞掉
            JSONObject().put("status", "error").put("reason", "bad_json")
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
            // 服务器证书待确认（TOFU/换证书）：后台没有确认 UI，必须让用户
            // 打开 App 处理；6h 去重防周期任务反复骚扰
            "trust_required" -> {
                val last = prefs.getLong("flutter.backup_trust_notified_at", 0L)
                if (System.currentTimeMillis() - last > 6 * 3_600_000L) {
                    notifyTrust(applicationContext)
                    prefs.edit()
                        .putLong("flutter.backup_trust_notified_at", System.currentTimeMillis())
                        .apply()
                }
                Result.success()
            }
            "error" -> {
                // 引擎已进失败率冷却时不 retry：WorkManager 的退避不感知冷却，
                // 会绕过 30 分钟空窗反复拉起（读 Flutter SharedPreferences 同名键）
                val until = prefs.getLong("flutter.backup_cooldown_until", 0L)
                if (System.currentTimeMillis() < until) Result.success() else Result.retry()
            }
            // skip / cooldown：等下个周期，不重试
            else -> Result.success()
        }
    }

    private fun notifyTrust(ctx: Context) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ctx.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return
        val open = PendingIntent.getActivity(
            ctx, 1, ctx.packageManager.getLaunchIntentForPackage(ctx.packageName),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(ctx, Channels.RESULT)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle("LAN Share 自动备份已暂停")
            .setContentText("服务器证书需要确认，请打开 App 完成信任")
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(ctx).notify(TRUST_NOTIF_ID, n)
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
        const val TRUST_NOTIF_ID = 2002

        fun progressNotification(ctx: Context, text: String): Notification =
            NotificationCompat.Builder(ctx, Channels.PROGRESS)
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .setContentTitle("LAN Share 自动备份")
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .build()

        fun fgInfo(ctx: Context, text: String): ForegroundInfo =
            if (Build.VERSION.SDK_INT >= 29)
                ForegroundInfo(FG_NOTIF_ID, progressNotification(ctx, text),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            else
                ForegroundInfo(FG_NOTIF_ID, progressNotification(ctx, text))
    }
}

/**
 * 每次任务拉起独立 FlutterEngine 跑 backupMain（用完即毁）。
 * 握手：Dart 设好 handler 后回 "ready"，Kotlin 收到才下发 "run"——
 * 否则引擎启动窗口期 invoke 会落 notImplemented。
 */
object BackupDartRunner {

    // 进程级互斥：周期任务与 Observer 即时任务是两个 unique work，可同时入队，
    // 两个引擎并发跑会双份 reconcile / 双会话传同一张图。拿不到锁的这轮直接
    // skip busy，由下个周期兜底（账本保证不丢）。
    // 用协程 Mutex 而非 ReentrantLock：tryLock/unlock 分布在 withContext(Main)
    // 内外的不同恢复点，协程锁无线程亲和约束，不依赖"恢复回原线程"的隐式契约。
    private val engineLock = Mutex()

    // 单轮兜底超时：isolate 卡死不放锁会把后续备份永久挡在 busy；30 分钟足够
    // 首轮大批量（更长的靠账本断点分多轮，不丢数据）
    private const val RUN_TIMEOUT_MS = 30 * 60_000L

    suspend fun run(ctx: Context, trigger: String): String {
        if (!engineLock.tryLock()) {
            return "{\"status\":\"skip\",\"reason\":\"busy\"}"
        }
        try {
            return withContext(Dispatchers.Main) {
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
                            // 引擎逐张回报进度 → 刷新前台通知（30 分钟长任务
                            // 一直显示"运行中…"没法区分卡死还是正常）
                            "progress" -> {
                                val args = call.arguments as? Map<*, *>
                                // 数值钳制：防御异常输入刷出误导性通知文案
                                val total =
                                    ((args?.get("total") as? Number)?.toInt() ?: 0).coerceAtLeast(0)
                                val done =
                                    ((args?.get("done") as? Number)?.toInt() ?: 0)
                                        .coerceIn(0, total)
                                try {
                                    // 注意作用域：这里在 BackupDartRunner（object）里，
                                    // 没有 applicationContext——用 run(ctx) 传进来的 ctx；
                                    // 进度刷新只要 Notification 本身，不要 ForegroundInfo
                                    NotificationManagerCompat.from(ctx).notify(
                                        BackupWorker.FG_NOTIF_ID,
                                        BackupWorker.progressNotification(
                                            ctx, "已备份 $done/$total 张"))
                                } catch (_: Exception) {}
                                result.success(null)
                            }
                            else -> result.notImplemented()
                        }
                    }
                    withTimeoutOrNull(60_000) { ready.await() }
                        ?: return@withContext "{\"status\":\"error\",\"reason\":\"dart_not_ready\"}"

                    withTimeoutOrNull(RUN_TIMEOUT_MS) {
                        suspendCancellableCoroutine { cont ->
                            backupCh.invokeMethod("run", trigger, object : MethodChannel.Result {
                                override fun success(r: Any?) {
                                    if (cont.isActive) cont.resume(
                                        // null/非字符串兜底为显式错误：落到 "{}" 会被
                                        // Worker 当 status="" 吞成"无事发生"
                                        r as? String
                                            ?: "{\"status\":\"error\",\"reason\":\"null_response\"}")
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
                    } ?: "{\"status\":\"error\",\"reason\":\"run_timeout\"}"
                } finally {
                    engine.destroy()
                }
            }
        } finally {
            engineLock.unlock()
        }
    }
}
