package app.lanshare.lanshare_app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.ActivityCompat
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * 主引擎通道（M-A3）：备份调度/立即备份/权限申请。
 * 备份执行本身不在主引擎——Worker 每次拉起独立后台 isolate（BackupDartRunner）。
 */
class MainActivity : io.flutter.embedding.android.FlutterFragmentActivity() {

    private var pendingPermission: MethodChannel.Result? = null

    /**
     * 启动窗口深浅色跟随 App 内"深色模式"设置（v0.16.1）。
     * 系统级开屏（Android 12+ windowSplashScreenBackground/AnimatedIcon）由
     * system 在进程启动前按系统深浅色画好，App 代码无法介入——那一段只能
     * 跟随系统；进程一起来（Activity 资源解析起），若用户在设置里固定了
     * 浅色/深色，这里覆写 uiMode 让 values-night 资源（NormalTheme 窗口底色
     * launch_bg、API<31 的 launch_background）按用户选择解析，消除
     * "系统浅色 + App 固定深色"时启动窗口闪白。
     * shared_preferences 的 String 值落盘在 FlutterSharedPreferences.xml，
     * 键名带 flutter. 前缀；读不到/异常一律回退跟随系统。
     */
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(applyLaunchNightMode(newBase))
    }

    private fun applyLaunchNightMode(base: android.content.Context): android.content.Context {
        return try {
            val sp = base.getSharedPreferences(
                "FlutterSharedPreferences", android.content.Context.MODE_PRIVATE)
            val mode = sp.getString("flutter.ui_theme_mode", "system") ?: "system"
            if (mode == "light" || mode == "dark") {
                val cfg = android.content.res.Configuration(base.resources.configuration)
                val want = if (mode == "dark") android.content.res.Configuration.UI_MODE_NIGHT_YES
                           else android.content.res.Configuration.UI_MODE_NIGHT_NO
                if (cfg.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK != want) {
                    cfg.uiMode = (cfg.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK.inv()) or want
                    base.createConfigurationContext(cfg)
                } else base
            } else base
        } catch (_: Exception) {
            base // prefs 读不到（首次启动/损坏）：跟随系统，不阻断启动
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // 系统分享接收（M-A4）：Dart 主动 takePending；引擎活着时 Kotlin 推 incoming
        val shareChannel = MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger, "lanshare/share")
        shareChannel.setMethodCallHandler { call, result ->
            when (call.method) {
                "takePending" -> result.success(ShareInbox.takeAll())
                else -> result.notImplemented()
            }
        }
        ShareInbox.channel = shareChannel
        // 安装包身份校验（2026-11-17 review）：装之前必须确认这个 APK 的包名
        // 与签名都和本 App 一致。否则任何能写更新源的人都能让用户「更新」成
        // 一个包名不同的假 App —— 系统安装器只在**同包名不同签名**时才拒绝，
        // 不同包名等于新装一个应用，图标和名字都可以照抄。
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "lanshare/apk")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "verify" -> result.success(
                        call.argument<String>("path")?.let { verifyApkIdentity(it) })
                    "documentsDir" -> result.success(
                        try { documentsDir() } catch (_: Exception) { null })
                    else -> result.notImplemented()
                }
            }
        // 媒体库通道（0.6.2 修复）：排除相册页在主 isolate 调 buckets()/query()，
        // 此前只有 Worker 后台 isolate 注册过该通道——主引擎侧永远
        // MissingPluginException，页面一直显示"无法读取相册列表"
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "lanshare/media")
            .setMethodCallHandler { call, result ->
                MediaStoreBridge.handle(this, call, result)
            }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "lanshare/backup_host")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "schedulePeriodic" -> {
                        BackupScheduler.schedulePeriodic(this, call.arguments as? Map<*, *>)
                        result.success(null)
                    }
                    "cancelPeriodic" -> {
                        BackupScheduler.cancelPeriodic(this)
                        result.success(null)
                    }
                    "runOnce" -> {
                        // 手动"立即备份"走专用入队（0.7.3）：不带充电/电量约束。
                        // 此前沿用自动约束，未插电时任务永远在等充电器——
                        // "点了没反应、不弹通知"的根因
                        MediaObserver.enqueueManual(this)
                        result.success(null)
                    }
                    // 备份诊断（0.7.3）：设置页诊断卡的实时环境快照
                    "diag" -> {
                        val pm = getSystemService(POWER_SERVICE) as android.os.PowerManager
                        val nm = getSystemService(android.app.NotificationManager::class.java)
                        result.success(mapOf(
                            "charging" to BackupDartRunner.isCharging(this),
                            "metered" to BackupDartRunner.netMetered(this),
                            "battery" to pm.isIgnoringBatteryOptimizations(packageName),
                            "notifications" to
                                (Build.VERSION.SDK_INT < 33 || nm.areNotificationsEnabled())))
                    }
                    // 电池优化豁免：小米/华为等激进省电会把 WorkManager 周期任务
                    // 压到几小时一次甚至不跑，设置页引导用户一键申请白名单
                    "battery" -> {
                        val pm = getSystemService(POWER_SERVICE) as android.os.PowerManager
                        result.success(mapOf("ignoring" to
                            pm.isIgnoringBatteryOptimizations(packageName)))
                    }
                    "batteryRequest" -> {
                        try {
                            val i = Intent(
                                android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                Uri.parse("package:$packageName"))
                            startActivity(i)
                        } catch (_: Exception) {}
                        result.success(null)
                    }
                    "openNotifSettings" -> {
                        try {
                            val i = Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                                .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, packageName)
                            startActivity(i)
                        } catch (_: Exception) {}
                        result.success(null)
                    }
                    "requestPermissions" -> {
                        val wantVideos =
                            (call.arguments as? Map<*, *>)?.get("videos") == true
                        val perms = ArrayList<String>()
                        if (Build.VERSION.SDK_INT >= 33) {
                            perms.add(Manifest.permission.READ_MEDIA_IMAGES)
                            if (wantVideos) perms.add(Manifest.permission.READ_MEDIA_VIDEO)
                            perms.add(Manifest.permission.POST_NOTIFICATIONS)
                        } else {
                            perms.add(Manifest.permission.READ_EXTERNAL_STORAGE)
                        }
                        pendingPermission = result
                        ActivityCompat.requestPermissions(this, perms.toTypedArray(), 4201)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        super.onCreate(savedInstanceState)
        // 冷启动带分享 intent：此时 Dart handler 大概率未就绪，先落盘排队，
        // configureFlutterEngine 末尾会推一次，登录后 takePending 再兜底
        ShareReceiver.handleIntent(this, intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // 热接收（singleTop）：进程活着，Dart handler 通常已就绪
        ShareReceiver.handleIntent(this, intent)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != 4201) return
        val r = pendingPermission
        pendingPermission = null
        if (r == null) return
        // 首项 = 图片权限（主开关）；未授权则备份无法开启
        val granted = grantResults.isNotEmpty() &&
            grantResults[0] == PackageManager.PERMISSION_GRANTED
        r.success(mapOf("granted" to granted))
    }

    /**
     * App 私有数据目录（更新包落盘根）。
     *
     * 与 path_provider 的 `getApplicationDocumentsDirectory()` **落在同一个
     * 目录**：引擎 `io.flutter.util.PathUtils.getDataDirectory` 取
     * `getFilesDir() + "/app_flutter"`，path_provider 走
     * `getDir("flutter", MODE_PRIVATE)`，而 `ContextImpl.getDir` 对以
     * `app_` 开头的名字就是拼到 filesDir 下 —— 两边都是
     * `filesDir/app_flutter`，所以老版本下载过的安装包不会因为这次改动
     * 而"消失"。
     *
     * 为什么要多这一条通道（2026-11-20）：path_provider 的 Android 实现自
     * 2.3 起改走 `package:jni` 去拿 `android.content.Context`，个别机型/分身
     * 环境下 JNI 查系统类会抛 `ClassNotFoundException: Landroid/content/Context;`
     * —— 用户实测"点更新立刻报错、进度条一点没动"。更新包放哪这件事没有
     * 任何理由绕 JNI：embedding 自带 PathUtils 就够，而且同步返回、无异步。
     */
    private fun documentsDir(): String =
        io.flutter.util.PathUtils.getDataDirectory(this)

    /**
     * 读目标 APK 的包名与签名，与自身比对。
     *
     * 读不到一律返回 null —— fail-closed：校验不了就不该让它装上去。
     */
    private fun verifyApkIdentity(path: String): Map<String, Any>? = try {
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P)
            PackageManager.GET_SIGNING_CERTIFICATES
        else @Suppress("DEPRECATION") PackageManager.GET_SIGNATURES
        val target = packageManager.getPackageArchiveInfo(path, flags)
        val self = packageManager.getPackageInfo(packageName, flags)
        if (target == null) {
            null
        } else {
            val a = signaturesOf(target).toSet()
            val b = signaturesOf(self).toSet()
            mapOf(
                "packageName" to (target.packageName ?: ""),
                "selfPackage" to packageName,
                "samePackage" to (target.packageName == packageName),
                "signatureMatch" to
                    (a.isNotEmpty() && b.isNotEmpty() && a.intersect(b).isNotEmpty()),
            )
        }
    } catch (e: Exception) {
        null
    }

    private fun signaturesOf(info: android.content.pm.PackageInfo): List<String> {
        val raw: List<android.content.pm.Signature> =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                info.signingInfo?.apkContentsSigners?.toList() ?: emptyList()
            } else {
                @Suppress("DEPRECATION") (info.signatures?.toList() ?: emptyList())
            }
        val md = java.security.MessageDigest.getInstance("SHA-256")
        return raw.map { s ->
            md.digest(s.toByteArray()).joinToString("") { "%02x".format(it) }
        }
    }

    override fun onDestroy() {
        // 权限弹窗期间 Activity 被销毁：补一个结果，别让 Dart 侧 await 永久挂起
        pendingPermission?.success(mapOf("granted" to false))
        pendingPermission = null
        super.onDestroy()
    }
}
