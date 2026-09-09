// UploadClient 纯逻辑单测（三轮审查 R3 补充：协议数学边界 + resume 语义钉）。
// 网络层用 dio FakeAdapter 假装服务端；引擎集成由真机 E2E 与服务端
// test_largefile 对账覆盖。
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/api/upload_client.dart';

/// 假服务端：按 (method, path) 返回预设 JSON
class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.routes);

  final Map<String, Map<String, dynamic>> routes; // "GET /api/upload/state/s" -> body

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream,
      Future<void>? cancelFuture) async {
    final key = '${options.method} ${options.uri.path}';
    final body = routes[key] ?? routes[key.split('/').last] ?? {};
    return ResponseBody.fromString(
        json.encode(body), 200,
        headers: {Headers.contentTypeHeader: [Headers.jsonContentType]});
  }

  @override
  void close({bool force = false}) {}
}

UploadClient _client(Map<String, Map<String, dynamic>> routes) =>
    UploadClient(
        dio: Dio(BaseOptions(baseUrl: 'https://test'))
          ..httpClientAdapter = _FakeAdapter(routes));

void main() {
  group('missingChunks/chunkLength 数学边界', () {
    test('size=0 无分片', () {
      final c = _client({})..chunkSize = 5;
      expect(c.missingChunks(0), isEmpty);
    });

    test('整除/非整除/单片文件', () {
      final c = _client({})..chunkSize = 5;
      expect(c.missingChunks(10), [0, 1]); // 恰好整除
      expect(c.missingChunks(11), [0, 1, 2]); // 末片截断
      expect(c.chunkLength(11, 2), 1); // 末片 1 字节
      expect(c.chunkLength(11, 0), 5);
      expect(c.chunkLength(5, 0), 5); // 单片文件
      expect(c.chunkLength(4, 0), 4); // 不足一片
    });

    test('已确认块不再进缺失清单', () {
      final c = _client({})..chunkSize = 5;
      c.received.addAll({0, 2});
      expect(c.missingChunks(15), [1]);
    });
  });

  group('init 防御校验', () {
    test('畸形会话 id 拒绝', () async {
      final c = _client({
        'POST /api/upload/init': {
          'session': '../evil',
          'chunk_size': 5,
          'received': [],
        }
      });
      await expectLater(c.init(destDir: '', filename: 'a', size: 1),
          throwsFormatException);
    });

    test('非法分片大小拒绝（0/负/超协议上限）', () async {
      for (final bad in [0, -5, 65 * 1024 * 1024]) {
        final c = _client({
          'POST /api/upload/init': {
            'session': 'abcdefgh',
            'chunk_size': bad,
            'received': [],
          }
        });
        await expectLater(c.init(destDir: '', filename: 'a', size: 1),
            throwsFormatException);
      }
    });

    test('合法 64MB 分片（协议上限内）接受', () async {
      final c = _client({
        'POST /api/upload/init': {
          'session': 'abcdefgh',
          'chunk_size': 64 * 1024 * 1024,
          'received': [0],
        }
      });
      expect(await c.init(destDir: '', filename: 'a', size: 1), isNull);
      expect(c.received, {0});
    });
  });

  group('resume(merge) 语义（R1-A 回归钉：两引擎本就不同）', () {
    test('merge=false 替换：本地幻影块被服务端真值清除（backup 语义）', () async {
      final c = _client({
        'GET /api/upload/state/s': {'chunk_size': 5, 'received': [0, 1]}
      })
        ..sid = 's'
        ..received.addAll({0, 1, 99});
      await c.resume(merge: false);
      expect(c.received, {0, 1});
    });

    test('merge=true 并集：本地加速缓存保留（transfer 语义）', () async {
      final c = _client({
        'GET /api/upload/state/s': {'chunk_size': 5, 'received': [0, 1]}
      })
        ..sid = 's'
        ..received.addAll({0, 1, 99});
      await c.resume(merge: true);
      expect(c.received, {0, 1, 99});
    });
  });
}
