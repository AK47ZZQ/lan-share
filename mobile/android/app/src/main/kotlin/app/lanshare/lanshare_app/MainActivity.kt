package app.lanshare.lanshare_app

import android.Manifest
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
}
