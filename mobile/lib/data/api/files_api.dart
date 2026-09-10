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

  static Future<void> rename(String path, String newName) async {
    final dio = await _dio();
    await dio.post('/api/rename', data: {'path': path, 'new_name': newName});
  }

  /// /api/move 一次搬运多路径（9.6-P1-7 方案 B）
  static Future<void> move(List<String> sources, String destDir) async {
    final dio = await _dio();
    await dio.post('/api/move', data: {'sources': sources, 'dest_dir': destDir});
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
