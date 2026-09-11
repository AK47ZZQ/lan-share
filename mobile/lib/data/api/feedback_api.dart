// 反馈域 API（T7 裸 dio 收编第二批：从 feedback_page 抽出 UI 层手拼请求）。
import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'api_client.dart';

/// 待上传截图（字节与文件名由 UI 层的文件选择器提供）
class FeedbackImage {
  final Uint8List bytes;
  final String name;
  FeedbackImage({required this.bytes, required this.name});
}

class FeedbackApi {
  FeedbackApi._();
  static Future<Dio> _dio() => ApiClient.I.dio();

  /// 历史列表（own-only；服务端对 Map/裸数组两形态兼容，本层归一为
  /// Map 列表——v0.28.1 修的 347f 历史报错在此兜底）
  static Future<List<Map<String, dynamic>>> list() async {
    final dio = await _dio();
    final r = await dio.get('/api/feedback');
    final d = r.data;
    final rows = (d is Map ? d['items'] : d) as List? ?? const [];
    return rows
        .whereType<Map>()
        .map((m) => Map<String, dynamic>.from(m))
        .toList();
  }

  /// 提交反馈（类型 idea|bug|other；附件 ≤3 个，服务端单件 ≤25MB）
  static Future<void> submit({
    required String title,
    required String type,
    required String body,
    required String appVer,
    required String device,
    List<FeedbackImage> images = const [],
  }) async {
    final dio = await _dio();
    final fd = FormData.fromMap({
      'title': title,
      'type': type,
      'body': body,
      'app_ver': appVer,
      'device': device,
      'files': [
        for (final i in images)
          MultipartFile.fromBytes(i.bytes, filename: i.name),
      ],
    });
    await dio.post('/api/feedback', data: fd);
  }

  /// 截图鉴权读取（详情页缩略/大图）
  static Future<Uint8List?> fetchBytes(String url) async {
    final dio = await _dio();
    final r = await dio.get<List<int>>(url,
        options: Options(responseType: ResponseType.bytes));
    return Uint8List.fromList(r.data ?? const []);
  }
}
