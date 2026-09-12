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

/// 无响应的网络故障（连接失败/超时，非服务端拒绝）——首页"网络断开"
/// 横幅判定（T7 裸 dio 收编：UI 层不再 import dio，经此判定）
bool isNetworkError(Object e) => e is DioException && e.response == null;

/// 404 判定（UI 层不 import dio 也能分型：如笔记页"记忆目录已被删除"的
/// 回退场景，bug·0663）。
bool isNotFound(Object e) => e is DioException && e.response?.statusCode == 404;
