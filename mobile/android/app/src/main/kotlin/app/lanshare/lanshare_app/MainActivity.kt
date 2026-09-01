package app.lanshare.lanshare_app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.ActivityCompat
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * 主引擎通道（M-A3）：备份调度/立即备份/权限申请。
 * 备份执行本身不在主引擎——Worker 每次拉起独立后台 isolate（BackupDartRunner）。
 */
class MainActivity : io.flutter.embedding.android.FlutterActivity() {

    private var pendingPermission: MethodChannel.Result? = null

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
                        MediaObserver.enqueueOnce(this)
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

    override fun onDestroy() {
        // 权限弹窗期间 Activity 被销毁：补一个结果，别让 Dart 侧 await 永久挂起
        pendingPermission?.success(mapOf("granted" to false))
        pendingPermission = null
        super.onDestroy()
    }
}
