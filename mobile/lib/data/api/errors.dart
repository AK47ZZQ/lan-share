// 统一的 API 错误 → 用户可读文案（0.8.1 审查批次抽出，
// 与 session.dart _errMsg 同语义：优先服务端中文 detail）
import 'package:dio/dio.dart';

import 'api_client.dart';

String errDetail(Object e, {int max = 120}) {
  String s;
  if (e is DioException) {
    final d = e.response?.data;
    if (d is Map && d['detail'] != null) {
      s = d['detail'].toString();
    } else if (e.error is TrustRequiredError) {
      s = '服务器证书需要确认';
    } else {
      final status = e.response?.statusCode;
      s = status != null ? '服务端返回 HTTP $status' : '连接失败，请检查网络';
    }
  } else {
    s = e.toString();
  }
  if (s.length <= max) return s;
  return '${s.substring(0, max)}…';
}
