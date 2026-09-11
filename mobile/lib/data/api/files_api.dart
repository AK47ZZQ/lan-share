// 通用文件通道 API（T7 裸 dio 收编第一批：从 notes_page/rich_note_editor
// 抽出 UI 层手拼请求；文件页/笔记页等域共用）。
// UI 层从此不再 import dio：错误文案统一走 errors.dart 的 errDetail()。
import 'package:dio/dio.dart';

import 'api_client.dart';

/// 文件超过大小上限（doc_viewer 预检用；UI 据此提示"先下载后再打开"）
class FileTooLarge implements Exception {
  final int limit;
  FileTooLarge(this.limit);
}

class FilesApi {
  FilesApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();
  static String _enc(String p) => p.split('/').map(Uri.encodeComponent).join('/');

  /// 目录清单（items 原始 Map 列表，字段由调用方按需取）
  static Future<List<dynamic>> listDir(String dir) async {
    final dio = await _dio();
    final r = await dio.get('/api/files/${_enc(dir)}');
    return (r.data['items'] as List? ?? const []);
  }

  /// 目录浏览（files_page / dir_picker 同款拼法）：**空路径请求 /api/files
  /// 不带尾斜杠**（与 listDir 只差这一点，勿混用——服务端对两种形态都可能
  /// 有不同路由行为，收编时必须逐字保持原 UI 的 URL）。items 原样透出，
  /// 缺字段照旧抛（页面显示"目录加载失败"而不是假空目录）。
  static Future<List<dynamic>> browse(String dir) async {
    final dio = await _dio();
    final enc = dir.split('/').map(Uri.encodeComponent).join('/');
    final r = await dio.get(dir.isEmpty ? '/api/files' : '/api/files/$enc');
    return r.data['items'] as List;
  }

  static Future<void> rename(String path, String newName) async {
    final dio = await _dio();
    await dio.post('/api/rename', data: {'path': path, 'new_name': newName});
  }

  /// 新建文件夹（files_page 同款拼法）：父路径不转码、仅名字
  /// encodeComponent——与原 UI 逐字一致，避免 Uri 归一化行为差异。
  static Future<void> mkdirIn(String parentDir, String name) async {
    final dio = await _dio();
    final base = parentDir.isEmpty ? '' : '$parentDir/';
    await dio.post('/api/mkdir/$base${Uri.encodeComponent(name.trim())}');
  }

  /// results[].ok 提取（服务端逐项结果；缺字段回空列表=整体成败不明）
  static List<bool> _perItemResults(Response r) =>
      (r.data['results'] as List? ?? const [])
          .whereType<Map>()
          .map((m) => m['ok'] == true)
          .toList();

  /// /api/move 一次搬运多路径（9.6-P1-7 方案 B）。返回逐项成败，
  /// 列表页用它拼「X 项成功，Y 项失败」（旧调用方忽略返回值不受影响）。
  static Future<List<bool>> move(List<String> sources, String destDir) async {
    final dio = await _dio();
    final r =
        await dio.post('/api/move', data: {'sources': sources, 'dest_dir': destDir});
    return _perItemResults(r);
  }

  /// /api/copy 同型复制（0.6.0；与 move 同契约）
  static Future<List<bool>> copy(List<String> sources, String destDir) async {
    final dio = await _dio();
    final r =
        await dio.post('/api/copy', data: {'sources': sources, 'dest_dir': destDir});
    return _perItemResults(r);
  }

  static Future<void> mkdir(String path) async {
    final dio = await _dio();
    await dio.post('/api/mkdir/${_enc(path)}');
  }

  /// 删除（进服务端回收站，与界面语义一致）
  static Future<void> delete(String path) async {
    final dio = await _dio();
    await dio.delete('/api/files/${_enc(path)}');
  }

  /// /preview/ 通道读字节：/files/ 被 Caddy forward_auth 拦（authcheck 只认
  /// Cookie），App Bearer 必 401；/preview 对非图片回 FileResponse（v0.17.2）
  static Future<List<int>> previewBytes(String path) async {
    final dio = await _dio();
    final r = await dio.get<List<int>>('/preview/${_enc(path)}',
        options: Options(responseType: ResponseType.bytes));
    return r.data ?? const [];
  }

  /// 带大小预检的预览下载（doc_viewer）：HEAD content-length 超过 [maxBytes]
  /// 抛 FileTooLarge；HEAD 不被支持（405）或非 4xx 视为预检通过直接下载。
  /// [onProgress] 透传下载进度（15 分钟 receiveTimeout，大文档渲染用）。
  static Future<List<int>> previewChecked(String path,
      {required int maxBytes, void Function(int, int)? onProgress}) async {
    final dio = await _dio();
    final enc = _enc(path);
    try {
      final head = await dio.head('/preview/$enc');
      final cl = head.headers.value('content-length');
      if (cl != null && int.tryParse(cl) != null && int.parse(cl) > maxBytes) {
        throw FileTooLarge(maxBytes);
      }
    } on DioException catch (e) {
      if (e.error is FileTooLarge) rethrow;
      final st = e.response?.statusCode;
      if (st != null && st >= 400 && st != 405) rethrow;
    }
    final r = await dio.get<List<int>>('/preview/$enc',
        options: Options(responseType: ResponseType.bytes,
            receiveTimeout: const Duration(minutes: 15)),
        onReceiveProgress: onProgress);
    return r.data ?? const [];
  }

  /// 打包下载第一步（0.8.0 对齐 Web doZip）：服务端打 zip，返回
  /// (url, name)；url 为 null 时页面提示「服务端未返回下载地址」。
  static Future<({String? url, String name})> zip(List<String> paths) async {
    final dio = await _dio();
    final r = await dio.post('/api/zip', data: {'paths': paths});
    return (
      url: r.data['url'] as String?,
      name: r.data['name'] as String? ?? 'share.zip',
    );
  }

  /// 经同一底座下载文件到本地（zip 产物用；带 Bearer 走 baseUrl，
  /// 相对地址如 /preview/... 直接可下）
  static Future<void> downloadTo(String url, String savePath) async {
    final dio = await _dio();
    await dio.download(url, savePath);
  }

  /// 单请求文本上传（笔记编辑器用；大文件走 upload_client 分片通道）。
  /// [overwrite]=true 时带 on_dup=overwrite：就地覆盖源文件，不走"旧文件进
  /// 回收站"（改进建议 9.6-P0-12，杜绝回收站堆积同一笔记的多个副本）。
  static Future<void> uploadText(String dir, String filename, String content,
      {String mimeType = 'text/plain', bool overwrite = false}) async {
    final dio = await _dio();
    final slash = mimeType.indexOf('/');
    await dio.post(
        '/api/upload?path=${Uri.encodeComponent(dir)}'
        '${overwrite ? '&on_dup=overwrite' : ''}',
        data: FormData.fromMap({
          'file': MultipartFile.fromString(content,
              filename: filename,
              contentType: DioMediaType(
                  slash > 0 ? mimeType.substring(0, slash) : 'application',
                  slash > 0 ? mimeType.substring(slash + 1) : 'octet-stream')),
        }));
  }
}
