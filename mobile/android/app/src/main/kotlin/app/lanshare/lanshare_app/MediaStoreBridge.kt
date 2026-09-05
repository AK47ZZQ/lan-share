package app.lanshare.lanshare_app

import android.content.ContentResolver
import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.os.Build
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
 * query → 全量 [id,kind,name,size,mtime,bucket] 紧凑数组（账本 diff 用；
 *   0.6.0 追加 bucket=相册目录名，App 侧按相册做备份排除）；
 * buckets → {相册名: 数量} 统计（排除设置页用，免拉全量清单）；
 * open → 单张媒体拷到缓存 backup_tmp/（引擎哈希/分片走真实文件路径）。
 * 主引擎（权限申请用不到 query，但保留对称）与 Worker 引擎都注册。
 */
object MediaStoreBridge {

    fun handle(ctx: Context, call: MethodCall, result: MethodChannel.Result) {
        CoroutineScope(Dispatchers.IO).launch {
            val reply: String = try {
                when (call.method) {
                    "query" -> query(ctx, call.arguments as? Map<*, *>)
                    "buckets" -> buckets(ctx)
                    "open" -> open(ctx, call.arguments as? Map<*, *>)
                    "saveToGallery" -> saveToGallery(ctx, call.arguments as? Map<*, *>)
                    else -> err("unknown_method")
                }
            } catch (e: Exception) {
                err(e.message ?: e.toString())
            }
            withContext(Dispatchers.Main) { result.success(reply) }
        }
    }

    private fun err(msg: String) = JSONObject().put("error", msg).toString()

    private val videoExts = setOf("mp4", "mov", "webm", "avi", "mkv", "m4v")

    private fun mimeFor(ext: String, video: Boolean): String {
        val e = ext.lowercase()
        return if (video) when (e) {
            "mov" -> "video/quicktime"
            "webm" -> "video/webm"
            "avi" -> "video/x-msvideo"
            "mkv", "m4v" -> "video/x-matroska"
            else -> "video/mp4"
        } else when (e) {
            "png" -> "image/png"
            "webp" -> "image/webp"
            "gif" -> "image/gif"
            "heic", "heif" -> "image/heic"
            "bmp" -> "image/bmp"
            else -> "image/jpeg"
        }
    }

    /// 已下载文件保存进系统相册（v0.13 传输页"保存到手机相册"）。
    /// API 29+ 走 IS_PENDING 无需存储权限；26-28 需运行时 WRITE 权限，
    /// App 未申请——直接报错引导（目标机型基本 29+）。
    private fun saveToGallery(ctx: Context, args: Map<*, *>?): String {
        val path = args?.get("path") as? String ?: return err("missing_path")
        val video = args["isVideo"] as? Boolean ?: false
        val f = File(path)
        if (!f.exists()) return err("file_missing")
        if (Build.VERSION.SDK_INT < 29) return err("requires_android_10")
        val collection =
            if (video) MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
            else MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val values = android.content.ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, f.name)
            put(MediaStore.MediaColumns.MIME_TYPE, mimeFor(f.extension, video))
            put(MediaStore.MediaColumns.RELATIVE_PATH,
                if (video) "Movies/LAN Share" else "Pictures/LAN Share")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val resolver = ctx.contentResolver
        val uri = resolver.insert(collection, values) ?: return err("insert_failed")
        try {
            resolver.openOutputStream(uri)?.use { out ->
                f.inputStream().use { it.copyTo(out) }
            } ?: return err("open_output_failed")
        } catch (e: Exception) {
            resolver.delete(uri, null, null)   // 残留 pending 记录清掉
            return err(e.message ?: "copy_failed")
        }
        val done = android.content.ContentValues().apply {
            put(MediaStore.MediaColumns.IS_PENDING, 0)
        }
        // 发布失败（罕见）：记录会滞留 pending 相册不可见，必须如实报错
        if (resolver.update(uri, done, null, null) == 0) return err("publish_failed")
        return JSONObject().put("ok", true).toString()
    }

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
            MediaStore.MediaColumns.DATE_MODIFIED,
            MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
        ctx.contentResolver.query(uri, proj, null, null,
            "${MediaStore.MediaColumns.DATE_MODIFIED} ASC")?.use { c ->
            val iId = c.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
            val iName = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
            val iSize = c.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE)
            val iMtime = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_MODIFIED)
            // BUCKET_DISPLAY_NAME 个别厂商/旧库可能缺列：按名找不到就退 -1，置空串
            val iBucket = c.getColumnIndex(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
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
                arr.put(if (iBucket >= 0) c.getString(iBucket) ?: "" else "")
                out.put(arr)
            }
        }
    }

    /** 相册名 → 媒体数（图片+视频合并，0 字节脏记录不计）。排除设置页候选用。 */
    private fun buckets(ctx: Context): String {
        val counts = LinkedHashMap<String, Int>()
        for (uri in listOf(MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                MediaStore.Video.Media.EXTERNAL_CONTENT_URI)) {
            val proj = arrayOf(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
            ctx.contentResolver.query(uri, proj, null, null, null)?.use { c ->
                val iBucket = c.getColumnIndex(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
                if (iBucket < 0) return@use
                while (c.moveToNext()) {
                    val b = c.getString(iBucket) ?: ""
                    counts[b] = (counts[b] ?: 0) + 1
                }
            }
        }
        val obj = JSONObject()
        for ((k, v) in counts) obj.put(k, v)
        return obj.toString()
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
