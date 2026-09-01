package app.lanshare.lanshare_app

import android.content.ContentResolver
import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * MediaStore 扫描薄通道（APP-DESIGN §4"媒体扫描器（平台通道）"）：
 * query → 全量 [id,kind,name,size,mtime] 紧凑数组（账本 diff 用）；
 * open  → 单张媒体拷到缓存 backup_tmp/（引擎哈希/分片走真实文件路径）。
 * 主引擎（权限申请用不到 query，但保留对称）与 Worker 引擎都注册。
 */
object MediaStoreBridge {

    fun handle(ctx: Context, call: MethodCall, result: MethodChannel.Result) {
        CoroutineScope(Dispatchers.IO).launch {
            val reply: String = try {
                when (call.method) {
                    "query" -> query(ctx, call.arguments as? Map<*, *>)
                    "open" -> open(ctx, call.arguments as? Map<*, *>)
                    else -> err("unknown_method")
                }
            } catch (e: Exception) {
                err(e.message ?: e.toString())
            }
            withContext(Dispatchers.Main) { result.success(reply) }
        }
    }

    private fun err(msg: String) = JSONObject().put("error", msg).toString()

    private fun query(ctx: Context, args: Map<*, *>?): String {
        val includeVideos = args?.get("videos") as? Boolean ?: false
        val out = JSONArray()
        collect(ctx, MediaStore.Images.Media.EXTERNAL_CONTENT_URI, "i", out)
        if (includeVideos) collect(ctx, MediaStore.Video.Media.EXTERNAL_CONTENT_URI, "v", out)
        return out.toString()
    }

    private fun collect(ctx: Context, uri: Uri, kind: String, out: JSONArray) {
        val proj = arrayOf(
            MediaStore.MediaColumns._ID,
            MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.SIZE,
            MediaStore.MediaColumns.DATE_MODIFIED)
        ctx.contentResolver.query(uri, proj, null, null,
            "${MediaStore.MediaColumns.DATE_MODIFIED} ASC")?.use { c ->
            val iId = c.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
            val iName = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
            val iSize = c.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE)
            val iMtime = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_MODIFIED)
            while (c.moveToNext()) {
                val size = c.getLong(iSize)
                if (size <= 0) continue // 0 字节脏记录（下载中/损坏项）跳过
                val mtime = c.getString(iMtime)?.toLongOrNull() ?: 0L
                // DATE_MODIFIED 官方为 TEXT 秒，个别机型为 INTEGER，getString 兼容两种
                val arr = JSONArray()
                arr.put("${kind}_${c.getLong(iId)}")
                arr.put(if (kind == "i") "image" else "video")
                arr.put(c.getString(iName) ?: "unnamed")
                arr.put(size)
                arr.put(mtime)
                out.put(arr)
            }
        }
    }

    private fun open(ctx: Context, args: Map<*, *>?): String {
        val id = args?.get("id") as? String ?: throw IllegalArgumentException("id required")
        val kind = args["kind"] as? String ?: "image"
        val prefix = if (kind == "image") "i_" else "v_"
        require(id.startsWith(prefix)) { "bad id" }
        val numeric = id.removePrefix(prefix).toLong()
        val uri = if (kind == "image")
            ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, numeric)
        else
            ContentUris.withAppendedId(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, numeric)
        val name = queryName(ctx.contentResolver, uri) ?: "photo.jpg"
        val dir = File(ctx.cacheDir, "backup_tmp")
        if (!dir.exists()) dir.mkdirs()
        sweepStale(dir)
        // DISPLAY_NAME 个别机型含路径分隔符，会落到子目录导致兜底清理失效
        val safeName = name.replace('/', '_').replace('\\', '_')
        val dst = File(dir, "${id}_$safeName") // 带原名：扩展名/中文名服务端按 name 落盘
        ctx.contentResolver.openInputStream(uri)?.use { input ->
            dst.outputStream().use { output -> input.copyTo(output, 1 shl 16) }
        } ?: throw IllegalStateException("openInputStream null: $id")
        // 标记"刚由本引擎写过"，与 24h 残留清理的判定对齐
        dst.setLastModified(System.currentTimeMillis())
        return dst.absolutePath
    }

    /// 24h 前的残留临时文件兜底清理：进程被杀/引擎硬销毁时 Dart 侧 finally
    /// 跑不到，靠这里回收（正好在 cacheDir，被系统清理也无害）
    private fun sweepStale(dir: File) {
        val cutoff = System.currentTimeMillis() - 24 * 3_600_000L
        dir.listFiles()?.forEach { if (it.lastModified() < cutoff) it.delete() }
    }

    private fun queryName(resolver: ContentResolver, uri: Uri): String? {
        resolver.query(uri, arrayOf(MediaStore.MediaColumns.DISPLAY_NAME), null, null, null)
            ?.use { c -> if (c.moveToFirst()) return c.getString(0) }
        return null
    }
}
