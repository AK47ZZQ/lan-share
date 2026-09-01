package app.lanshare.lanshare_app

import android.content.ContentResolver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * 系统分享接收（M-A4 / APP-REQUIREMENTS FR-6）：
 * Activity 收到 ACTION_SEND/SEND_MULTIPLE 后立刻把 content:// 流拷进
 * cacheDir/share_inbox/（授权方 App 可随时回收 URI，先落地再慢慢传），
 * 路径排队等待 Dart 侧取走入上传队列（incoming/<用户>/分享暂存/）。
 *
 * 时序与 BackupDartRunner 的 ready 握手同理反向：冷启动时 Dart handler
 * 可能尚未注册，invokeMethod 失败不打紧——路径留在 pending，Dart 登录后
 * 主动 takePending 兜底。
 */
object ShareInbox {
    val pending = java.util.concurrent.CopyOnWriteArrayList<String>()

    @Volatile
    var channel: MethodChannel? = null

    /** 通知 Dart 有新分享；handler 未就绪则静默（等 takePending 兜底） */
    fun notifyDart() {
        val ch = channel ?: return
        ch.invokeMethod("incoming", null, object : MethodChannel.Result {
            override fun success(result: Any?) {}
            override fun error(code: String, message: String?, details: Any?) {}
            override fun notImplemented() {}
        })
    }

    fun takeAll(): List<String> {
        val out = pending.toList()
        pending.clear()
        return out
    }
}

object ShareReceiver {

    fun handleIntent(ctx: Context, intent: Intent?) {
        if (intent == null) return
        val action = intent.action ?: return
        val uris = ArrayList<Uri>()
        var text: String? = null
        when (action) {
            Intent.ACTION_SEND -> {
                val u = getStreamExtra(intent)
                if (u != null) {
                    uris.add(u)
                } else {
                    text = intent.getStringExtra(Intent.EXTRA_TEXT)
                }
            }
            Intent.ACTION_SEND_MULTIPLE -> {
                uris.addAll(getStreamListExtra(intent))
                if (uris.isEmpty()) {
                    // 兜底：个别分享方（以及 am 调试指令）对 SEND_MULTIPLE 只塞
                    // 单个 Uri extra 而非 ArrayList——别把这样的分享静默丢掉
                    getStreamExtra(intent)?.let { uris.add(it) }
                }
                if (uris.isEmpty()) text = intent.getStringExtra(Intent.EXTRA_TEXT)
            }
            else -> return
        }
        if (uris.isEmpty() && text.isNullOrBlank()) return
        CoroutineScope(Dispatchers.IO).launch {
            // 顺带清扫超过 24h 的残留（分享件是副本，上传完成后 Dart 侧也会即时删）
            sweepStale(inboxDir(ctx))
            val saved = ArrayList<String>()
            for (u in uris) {
                val p = copyToInbox(ctx.contentResolver, ctx, u)
                if (p != null) saved.add(p)
            }
            if (!text.isNullOrBlank()) {
                val p = saveText(ctx, text)
                if (p != null) saved.add(p)
            }
            if (saved.isNotEmpty()) {
                ShareInbox.pending.addAll(saved)
                withContext(Dispatchers.Main) { ShareInbox.notifyDart() }
            }
        }
    }

    private fun inboxDir(ctx: Context): File =
        File(ctx.cacheDir, "share_inbox").apply { mkdirs() }

    /** cache/share_inbox 里超过 24h 的副本（正常已被 Dart 侧删除，兜底） */
    private fun sweepStale(dir: File) {
        val cutoff = System.currentTimeMillis() - 24 * 3_600_000L
        dir.listFiles()?.forEach { if (it.isFile && it.lastModified() < cutoff) it.delete() }
    }

    private fun safeName(name: String): String =
        name.replace('/', '_').replace('\\', '_').ifBlank { "分享文件" }

    private fun queryDisplayName(resolver: ContentResolver, uri: Uri): Pair<String, Long>? {
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
            null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                val name = if (idx >= 0 && !c.isNull(idx)) c.getString(idx) else null
                val sidx = c.getColumnIndex(OpenableColumns.SIZE)
                val size = if (sidx >= 0 && !c.isNull(sidx)) c.getLong(sidx) else -1L
                return (name ?: uri.lastPathSegment ?: "分享文件") to size
            }
        }
        return null
    }

    private fun copyToInbox(resolver: ContentResolver, ctx: Context, uri: Uri): String? {
        return try {
            val named = queryDisplayName(resolver, uri)
            val base = safeName(named?.first ?: "分享文件")
            val stamp = System.currentTimeMillis()
            var dst = File(inboxDir(ctx), "${stamp}_$base")
            var i = 1
            while (dst.exists()) {
                val dot = base.lastIndexOf('.')
                dst = File(inboxDir(ctx),
                    if (dot > 0) "${stamp}_${base.substring(0, dot)}(${i++})${base.substring(dot)}"
                    else "${stamp}_$base(${i++})")
            }
            resolver.openInputStream(uri)?.use { input ->
                dst.outputStream().use { output -> input.copyTo(output) }
            } ?: return null
            dst.lastModified() // 置为当前时刻，sweepStale 以它计时
            dst.absolutePath
        } catch (_: Exception) {
            null
        }
    }

    private fun saveText(ctx: Context, text: String): String? {
        return try {
            val fmt = java.text.SimpleDateFormat("yyyyMMdd_HHmmss", java.util.Locale.US)
            val f = File(inboxDir(ctx), "分享文本_${fmt.format(java.util.Date())}.txt")
            f.writeText(text)
            f.absolutePath
        } catch (_: Exception) {
            null
        }
    }

    @Suppress("DEPRECATION")
    private fun getStreamExtra(intent: Intent): Uri? =
        if (android.os.Build.VERSION.SDK_INT >= 33)
            intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        else intent.getParcelableExtra(Intent.EXTRA_STREAM) as? Uri

    @Suppress("DEPRECATION")
    private fun getStreamListExtra(intent: Intent): List<Uri> =
        if (android.os.Build.VERSION.SDK_INT >= 33)
            intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
                ?: emptyList()
        else (intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM) ?: ArrayList())
            .filterNotNull()
}
