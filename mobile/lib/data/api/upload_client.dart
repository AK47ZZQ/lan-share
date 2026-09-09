/// 分块上传协议单实现（07 审查批次 4：消灭 transfer/backup 双引擎的逐字节
/// "同坑"拷贝）。
///
/// 只承载协议本身：init（含秒传探测）/state 断点探测/分片补齐/finish 拼装，
/// 以及响应字段的全部防御校验（会话 id 白名单、分片大小协议上限——07 审查
/// P0 的教训：这类校验写两份必然漂移）。重试退避、暂停取消、账本/队列持久化
/// 仍归各引擎，通过 CancelToken 与返回值衔接。
library;

import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'protocol.dart';

/// 会话 id 白名单（服务端 token_urlsafe 字母表；红队 P3：响应字段不可信，
/// 畸形 id 进 URL 模板只会产生无意义请求/歧义路径）
final RegExp _sidRe = RegExp(r'^[A-Za-z0-9_\-]{8,64}$');

class UploadClient {
  UploadClient({required this.dio, this.cancelToken});

  final Dio dio;
  final CancelToken? cancelToken;

  String? sid;
  int chunkSize = 0;
  final Set<int> received = {};

  /// init：新会话（服务端顺带秒传探测）。返回 null = 未秒传，继续分片流程；
  /// 返回非 null = 秒传命中，值为远端 path（path 缺失兜底为 filename——别把
  /// "秒传成功"因响应字段意外误标为失败）。
  Future<String?> init({
    required String destDir,
    required String filename,
    required int size,
    String? sha256,
  }) async {
    final r = await dio.post('/api/upload/init', data: {
      'dest_dir': destDir,
      'filename': filename,
      'size': size,
      'sha256': ?sha256,
    }, cancelToken: cancelToken);
    if (r.data['instant'] == true) {
      return (r.data['path'] as String?) ?? filename;
    }
    sid = _validatedSid(r.data['session'] as String);
    chunkSize = _validatedChunkSize((r.data['chunk_size'] as num).toInt());
    received
      ..clear()
      ..addAll(_receivedOf(r.data));
    return null;
  }

  /// state：断点续传探测。
  /// [merge] 两引擎语义本就不同（07 批次4 自查 R1-A，保留各自原行为）：
  /// - true（transfer）：并集——队列 JSON 里的本地确认块是加速缓存，
  ///   且 404 重开时已 clear，会话存活期内服务端不会丢已确认块；
  /// - false（backup）：替换——账本只存 session_id，服务端 state 是
  ///   唯一真值（对账 §4），不能被本地残留索引污染。
  Future<void> resume({bool merge = true}) async {
    final r = await dio.get('/api/upload/state/$sid', cancelToken: cancelToken);
    chunkSize = _validatedChunkSize((r.data['chunk_size'] as num).toInt());
    final server = _receivedOf(r.data);
    if (merge) {
      received.addAll(server);
    } else {
      received
        ..clear()
        ..addAll(server);
    }
  }

  /// 会话 404（3 天 TTL 过期/被清理）：作废重开，断点归零。
  void reopen() {
    sid = null;
    received.clear();
  }

  /// 缺失分片序号（服务端原子写，确认过的块不重传）。
  List<int> missingChunks(int totalSize) {
    final cs = chunkSize;
    final total = (totalSize + cs - 1) ~/ cs;
    return [
      for (var i = 0; i < total; i++)
        if (!received.contains(i)) i,
    ];
  }

  /// 第 [index] 片的实际字节长（末片截断）。
  int chunkLength(int totalSize, int index) {
    final remain = totalSize - index * chunkSize;
    return remain < chunkSize ? remain : chunkSize;
  }

  Future<void> postChunk(int index, Uint8List bytes) async {
    await dio.post('/api/upload/chunk/$sid?index=$index',
        data: bytes,
        cancelToken: cancelToken,
        options: Options(
            contentType: 'application/octet-stream',
            sendTimeout: const Duration(minutes: 10)));
    received.add(index);
  }

  /// finish：服务端拼装 + sha 必校验（大文件耗时长，放宽超时）。
  /// 返回远端 path；path 字段意外缺失时兜底为 [fallbackName]——finish 200
  /// 即已拼装成功，别因响应字段把成功误标失败。
  Future<String> finish({String fallbackName = ''}) async {
    final r = await dio.post('/api/upload/finish/$sid',
        cancelToken: cancelToken,
        options: Options(receiveTimeout: const Duration(minutes: 15)));
    return (r.data['path'] as String?) ?? fallbackName;
  }

  /// 定长读片（两引擎原各有一份逐字节相同的实现）。
  static Future<Uint8List> readChunk(File f, int offset, int len) async {
    final raf = await f.open();
    try {
      await raf.setPosition(offset);
      return await raf.read(len);
    } finally {
      await raf.close();
    }
  }
}

String _validatedSid(String sid) {
  if (!_sidRe.hasMatch(sid)) {
    throw const FormatException('bad_session');
  }
  return sid;
}

int _validatedChunkSize(int chunkSize) {
  // 0/负值会让 total 计算抛 UnsupportedError；超协议上限会把整个文件读进
  // 单请求内存 OOM（红队 P3）。上限引用协议常量（07 审查 P0：曾写死 16MB，
  // 服务端 >5GB 阶梯升 32/64MB 后 App 上传必败）
  if (!isValidChunkSize(chunkSize)) {
    throw const FormatException('bad_chunk_size');
  }
  return chunkSize;
}

List<int> _receivedOf(dynamic data) =>
    ((data['received'] as List?) ?? const []).map((e) => (e as num).toInt()).toList();
