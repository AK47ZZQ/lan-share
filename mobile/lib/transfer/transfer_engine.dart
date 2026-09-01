// 传输引擎：上传（init→chunk→finish 全协议）/ 下载（/preview Range 断点）统一队列。
// 设计要点（APP-DESIGN §2"传输协议 100% Dart"）：
//  · 队列 JSON 落盘（documents/transfer_queue.json）——杀进程后重启恢复；
//    断点真值在服务端：续传一律 /api/upload/state 探测，本地 received 只做并集加速
//  · 分片原子写在服务端，半截分片不会混入 received；客户端按确认块推进度
//  · 暂停=取消当前请求（服务端留 _tmp 不入账）；网络错误退避重试至多 5 次
//  · 登出全部挂起（不置 userPaused，登录后自动续传）
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';

import '../data/api/api_client.dart';
import 'transfer_task.dart';

/// 上传前预计算 sha256 的大小上限（秒传探测用；超限不预计算，
/// 服务端 finish 自算校验——与网页 ≤100MB 同思路，App 放宽到 200MB）
const _kHashLimit = 200 * 1024 * 1024;

class _Paused implements Exception {}

class _DigestSink implements Sink<Digest> {
  Digest? value;
  @override
  void add(Digest d) => value = d;
  @override
  void close() {}
}

class TransferEngine extends ChangeNotifier {
  TransferEngine._();
  static final TransferEngine I = TransferEngine._();

  final tasks = <TransferTask>[];
  final _speed = <String, int>{}; // id → 字节/秒（UI 展示，不落盘）
  final _cancelTokens = <String, CancelToken>{};
  final _running = <String>{};
  final _lastTickBytes = <String, int>{};
  final _lastTickTime = <String, int>{};
  int _lastNotifyMs = 0;

  File? _queueFile;
  Timer? _persistTimer;
  DateTime? _dirtySince;
  bool _loaded = false;
  bool _loggedIn = false;
  bool _kicking = false;
  String _docsPath = '';

  int get activeCount => _running.length;

  // ── 登录态联动（main.dart 注入） ────────────────────────────
  Future<void> onLogin() async {
    _loggedIn = true;
    await _load();
    _kick();
  }

  Future<void> onLogout() async {
    _loggedIn = false;
    for (final t in List.of(tasks)) {
      if (_running.contains(t.id)) {
        _cancelTokens[t.id]?.cancel(); // 运行循环感知 → paused（非 userPaused）
      } else if (t.isActive) {
        t.status = TransferStatus.paused;
      }
    }
    await _persistNow();
    notifyListeners();
  }

  // ── 入队 ──────────────────────────────────────────────────
  TransferTask? enqueueUpload({
    required String localPath,
    required String destDir,
    required String name,
  }) {
    final f = File(localPath);
    if (!f.existsSync()) return null;
    final size = f.lengthSync();
    // 同源同目标的在途任务去重
    for (final t in tasks) {
      if (t.kind == TransferKind.upload &&
          !t.finished &&
          t.localPath == localPath &&
          t.destDir == destDir &&
          t.name == name) {
        return t;
      }
    }
    final task = TransferTask(
      id: 'u${DateTime.now().microsecondsSinceEpoch}',
      kind: TransferKind.upload,
      name: name,
      localPath: localPath,
      destDir: destDir,
      size: size,
    );
    tasks.add(task);
    _schedulePersist();
    notifyListeners();
    _kick();
    return task;
  }

  Future<TransferTask?> enqueueDownload({
    required String remotePath,
    required String name,
    required int size,
  }) async {
    await _ensureDocs();
    for (final t in tasks) {
      if (t.kind == TransferKind.download &&
          !t.finished &&
          t.remotePath == remotePath) {
        return t;
      }
    }
    final task = TransferTask(
      id: 'd${DateTime.now().microsecondsSinceEpoch}',
      kind: TransferKind.download,
      name: name,
      localPath: _downloadTarget(name),
      remotePath: remotePath,
      size: size,
    );
    tasks.add(task);
    _schedulePersist();
    notifyListeners();
    _kick();
    return task;
  }

  /// 下载落 App 沙盒 documents/Downloads/，重名自动加序号
  String _downloadTarget(String name) {
    final dir = Directory('$_docsPath/Downloads');
    if (!dir.existsSync()) dir.createSync(recursive: true);
    var p = '${dir.path}/$name';
    var i = 1;
    final dot = name.lastIndexOf('.');
    while (File(p).existsSync()) {
      final base = dot > 0 ? name.substring(0, dot) : name;
      final ext = dot > 0 ? name.substring(dot) : '';
      p = '${dir.path}/$base(${i++})$ext';
    }
    return p;
  }

  // ── 队列操作 ──────────────────────────────────────────────
  void pauseTask(TransferTask t) {
    if (t.finished) return;
    t.userPaused = true;
    if (_running.contains(t.id)) {
      _cancelTokens[t.id]?.cancel();
    } else {
      t.status = TransferStatus.paused;
      _speed[t.id] = 0;
    }
    _schedulePersist();
    notifyListeners();
  }

  void resumeTask(TransferTask t) {
    if (t.status != TransferStatus.paused && t.status != TransferStatus.error) {
      return;
    }
    t.userPaused = false;
    t.error = null;
    t.attempts = 0;
    t.status = TransferStatus.paused; // 交给 _kick 调度
    notifyListeners();
    _kick();
  }

  void removeTask(TransferTask t) {
    final running = _running.contains(t.id);
    tasks.remove(t);
    _speed.remove(t.id);
    _schedulePersist();
    notifyListeners();
    if (running) _cancelTokens[t.id]?.cancel();
  }

  void clearFinished() {
    tasks.removeWhere((t) => t.status == TransferStatus.done);
    _schedulePersist();
    notifyListeners();
  }

  int speedOf(TransferTask t) => _speed[t.id] ?? 0;

  // ── 调度 ──────────────────────────────────────────────────
  static const _maxActive = 2;

  void _kick() {
    if (_kicking || !_loggedIn || !_loaded) return;
    _kicking = true;
    try {
      while (_running.length < _maxActive) {
        TransferTask? next;
        for (final t in tasks) {
          if (_running.contains(t.id) || t.userPaused || t.finished) continue;
          if (t.status == TransferStatus.pending ||
              t.status == TransferStatus.paused) {
            next = t;
            break;
          }
        }
        if (next == null) break;
        final task = next;
        _running.add(task.id);
        // ignore: unawaited_futures
        _run(task).whenComplete(() {
          _running.remove(task.id);
          _schedulePersist();
          notifyListeners();
          _kick();
        });
      }
    } finally {
      _kicking = false;
    }
  }

  Future<void> _run(TransferTask t) async {
    final token = CancelToken();
    _cancelTokens[t.id] = token;
    try {
      if (t.kind == TransferKind.upload) {
        await _runUpload(t, token);
      } else {
        await _runDownload(t, token);
      }
    } on _Paused {
      t.status = TransferStatus.paused;
      _speed[t.id] = 0;
      await _persistNow();
      notifyListeners();
    } catch (e) {
      t.status = TransferStatus.error;
      t.error = _friendly(e);
      _speed[t.id] = 0;
      await _persistNow();
      notifyListeners();
    } finally {
      _cancelTokens.remove(t.id);
    }
  }

  static String _friendly(Object e) {
    final s = e.toString();
    return s.length > 160 ? s.substring(0, 160) : s;
  }

  // ── 上传 ──────────────────────────────────────────────────
  Future<void> _runUpload(TransferTask t, CancelToken token) async {
    final f = File(t.localPath);
    if (!f.existsSync()) throw Exception('本地文件不存在');
    if (f.lengthSync() != t.size) {
      t.size = f.lengthSync();
      t.sha256 = null; // 文件变了，指纹作废重算
    }
    final dio = await ApiClient.I.dio();

    if (t.sha256 == null && t.size <= _kHashLimit) {
      _setStatus(t, TransferStatus.hashing);
      final sink = _DigestSink();
      final byteSink = sha256.startChunkedConversion(sink);
      final raf = await f.open();
      try {
        final buf = Uint8List(1024 * 1024);
        while (true) {
          final n = await raf.readInto(buf);
          if (n <= 0) break;
          byteSink.addSlice(buf, 0, n, false);
        }
      } finally {
        await raf.close();
      }
      byteSink.close();
      t.sha256 = sink.value!.toString();
      _schedulePersist();
    }

    var backoff = 0;
    while (true) {
      // 1) 会话：无 sid 则 init（含秒传探测）；有 sid 则 state 探测并集
      try {
        if (t.sid == null) {
          _setStatus(t, TransferStatus.uploading);
          final r = await dio.post('/api/upload/init',
              data: {
                'dest_dir': t.destDir ?? '',
                'filename': t.name,
                'size': t.size,
                if (t.sha256 != null) 'sha256': t.sha256,
              },
              cancelToken: token);
          if (r.data['instant'] == true) {
            t.remotePath = r.data['path'] as String;
            t.received.clear();
            _setStatus(t, TransferStatus.done);
            return;
          }
          t.sid = r.data['session'] as String;
          t.chunkSize = (r.data['chunk_size'] as num).toInt();
          t.received
            ..clear()
            ..addAll(((r.data['received'] as List?) ?? const [])
                .map((e) => (e as num).toInt()));
          _schedulePersist();
        } else {
          final r =
              await dio.get('/api/upload/state/${t.sid}', cancelToken: token);
          t.chunkSize = (r.data['chunk_size'] as num).toInt();
          t.received.addAll(((r.data['received'] as List?) ?? const [])
              .map((e) => (e as num).toInt()));
        }
      } on DioException catch (e) {
        if (token.isCancelled) throw _Paused();
        if (e.response?.statusCode == 404 && t.sid != null) {
          // 会话过期（3 天 TTL）或被清理：作废重开，断点归零
          t.sid = null;
          t.received.clear();
          continue;
        }
        backoff = await _retryOrThrow(t, e, backoff);
        continue;
      }

      // 2) 补齐缺失分片（服务端原子写，确认过的块不重传）
      final cs = t.chunkSize;
      final total = (t.size + cs - 1) ~/ cs;
      final missing = [
        for (var i = 0; i < total; i++)
          if (!t.received.contains(i)) i,
      ];
      var failed = false;
      for (final i in missing) {
        final expected =
            (t.size - i * cs) < cs ? (t.size - i * cs) : cs;
        final bytes = await _readChunk(f, i * cs, expected);
        _setStatus(t, TransferStatus.uploading);
        try {
          await dio.post('/api/upload/chunk/${t.sid}?index=$i',
              data: bytes,
              cancelToken: token,
              options: Options(
                  contentType: 'application/octet-stream',
                  sendTimeout: const Duration(minutes: 10)));
          t.received.add(i);
          t.attempts = 0;
          backoff = 0;
          _touch(t);
        } on DioException catch (e) {
          if (token.isCancelled) throw _Paused();
          if (e.response?.statusCode == 404) {
            t.sid = null; // 会话没了：重开
            failed = true;
            break;
          }
          backoff = await _retryOrThrow(t, e, backoff);
          failed = true;
          break;
        }
      }
      if (failed) continue;

      // 3) finish（服务端拼装 + sha 校验；大文件耗时长，放宽超时）
      _setStatus(t, TransferStatus.merging);
      try {
        final r = await dio.post('/api/upload/finish/${t.sid}',
            cancelToken: token,
            options: Options(receiveTimeout: const Duration(minutes: 15)));
        t.remotePath = r.data['path'] as String;
        _setStatus(t, TransferStatus.done);
        _speed[t.id] = 0;
        return;
      } on DioException catch (e) {
        if (token.isCancelled) throw _Paused();
        backoff = await _retryOrThrow(t, e, backoff);
      }
    }
  }

  Future<Uint8List> _readChunk(File f, int offset, int len) async {
    final raf = await f.open();
    try {
      await raf.setPosition(offset);
      return await raf.read(len);
    } finally {
      await raf.close();
    }
  }

  // ── 下载 ──────────────────────────────────────────────────
  Future<void> _runDownload(TransferTask t, CancelToken token) async {
    final dio = await ApiClient.I.dio();
    final part = File('${t.localPath}.part');
    final url =
        '/preview/${t.remotePath!.split('/').map(Uri.encodeComponent).join('/')}';
    var backoff = 0;
    while (true) {
      var start = part.existsSync() ? part.lengthSync() : 0;
      t.receivedBytes = start;
      if (t.size > 0 && start >= t.size) {
        _finishDownload(t, part);
        return;
      }
      _setStatus(t, TransferStatus.downloading);
      try {
        final rsp = await dio.get<ResponseBody>(url,
            options: Options(
                responseType: ResponseType.stream,
                receiveTimeout: const Duration(minutes: 30),
                headers: {
                  if (start > 0) 'Range': 'bytes=$start-',
                }),
            cancelToken: token);
        var append = false;
        if (rsp.statusCode == 206) {
          append = true;
        } else if (rsp.statusCode == 200) {
          start = 0; // 服务端不支持 Range：从头来
          t.receivedBytes = 0;
        }
        final raf =
            await part.open(mode: append ? FileMode.append : FileMode.write);
        try {
          await for (final chunk in rsp.data!.stream) {
            await raf.writeFrom(chunk);
            t.receivedBytes += chunk.length;
            _touch(t);
          }
        } finally {
          await raf.close();
        }
        _finishDownload(t, part);
        return;
      } on DioException catch (e) {
        if (token.isCancelled) throw _Paused();
        final status = e.response?.statusCode;
        if (status == 404) {
          throw Exception('远端文件不存在');
        }
        if (status == 416) {
          // Range 起点即文件尾（.part 已完整）：服务端按 RFC 回 416，视为完成
          _finishDownload(t, part);
          return;
        }
        backoff = await _retryOrThrow(t, e, backoff);
      }
    }
  }

  void _finishDownload(TransferTask t, File part) {
    t.receivedBytes = part.lengthSync();
    part.renameSync(t.localPath);
    t.status = TransferStatus.done;
    _speed[t.id] = 0;
    _schedulePersist();
    notifyListeners();
  }

  // ── 公共小件 ──────────────────────────────────────────────
  void _setStatus(TransferTask t, TransferStatus s) {
    t.status = s;
    notifyListeners();
  }

  /// 非 cancel 错误：4xx 直接判死（重试无意义）；5xx/网络错误退避重试至多 5 次
  Future<int> _retryOrThrow(TransferTask t, DioException e, int backoff) async {
    final status = e.response?.statusCode;
    if (status != null && status >= 400 && status < 500) {
      final d = e.response?.data;
      final detail = d is Map && d['detail'] != null ? d['detail'] : 'HTTP $status';
      throw Exception(detail.toString());
    }
    t.attempts += 1;
    if (t.attempts > 5) {
      throw Exception('连续失败多次：${_brief(e)}');
    }
    final wait = [2, 5, 10, 30, 60][backoff.clamp(0, 4)];
    t.error = '网络波动，${wait}s 后重试（第 ${t.attempts} 次）';
    notifyListeners();
    await Future.delayed(Duration(seconds: wait));
    return backoff + 1;
  }

  static String _brief(DioException e) {
    final d = e.response?.data;
    if (d is Map && d['detail'] != null) return d['detail'].toString();
    final s = e.message ?? e.toString();
    return s.length > 80 ? s.substring(0, 80) : s;
  }

  /// 进度心跳：速度窗口 500ms，UI 刷新节流 200ms
  void _touch(TransferTask t) {
    final now = DateTime.now().millisecondsSinceEpoch;
    final last = _lastTickTime[t.id];
    final bytes =
        t.kind == TransferKind.upload ? t.uploadedBytes : t.receivedBytes;
    if (last != null) {
      final dt = now - last;
      if (dt >= 500) {
        final db = bytes - (_lastTickBytes[t.id] ?? bytes);
        _speed[t.id] = (db * 1000 / dt).round().clamp(0, 1 << 40);
        _lastTickTime[t.id] = now;
        _lastTickBytes[t.id] = bytes;
      }
    } else {
      _lastTickTime[t.id] = now;
      _lastTickBytes[t.id] = bytes;
    }
    _schedulePersist();
    if (now - _lastNotifyMs > 200) {
      _lastNotifyMs = now;
      notifyListeners();
    }
  }

  // ── 落盘 ──────────────────────────────────────────────────
  Future<void> _ensureDocs() async {
    if (_docsPath.isEmpty) {
      final docs = await getApplicationDocumentsDirectory();
      _docsPath = docs.path;
    }
  }

  Future<void> _load() async {
    if (_loaded) {
      await _ensureDocs();
      return;
    }
    _loaded = true;
    await _ensureDocs();
    final f = File('$_docsPath/transfer_queue.json');
    _queueFile = f;
    if (!f.existsSync()) return;
    try {
      final arr = jsonDecode(await f.readAsString()) as List;
      for (final j in arr.whereType<Map>()) {
        final t = TransferTask.fromJson(Map<String, dynamic>.from(j));
        // 进程被杀遗留的"在途"状态统一转 paused（登录后自动续传）
        if (t.isActive) t.status = TransferStatus.paused;
        if (t.kind == TransferKind.download) {
          final p = File('${t.localPath}.part');
          t.receivedBytes = p.existsSync() ? p.lengthSync() : 0;
        } else if (!t.finished && !File(t.localPath).existsSync()) {
          // 只纠"未完成"的：已完成任务本地副本被清（如分享接收的 cache 拷贝）
          // 不改判 error，免得重启后满屏假错误
          t.status = TransferStatus.error;
          t.error = '本地文件不存在';
        }
        tasks.add(t);
      }
      notifyListeners();
    } catch (_) {
      // 队列文件损坏：丢弃（任务重新发起即可）
    }
  }

  void _schedulePersist() {
    final now = DateTime.now();
    _dirtySince ??= now;
    _persistTimer?.cancel();
    // 最长挂起 5s 强制刷盘：分片间隔小于 debounce 时，纯 debounce 会一直
    // 被推迟，杀进程只留下很旧的快照（真值虽在服务端 state，进度条会回退）
    if (now.difference(_dirtySince!) >= const Duration(seconds: 5)) {
      _dirtySince = null;
      _persistNow();
      return;
    }
    _persistTimer = Timer(const Duration(milliseconds: 800), () {
      _dirtySince = null;
      _persistNow();
    });
  }

  Future<void> _persistNow() async {
    _persistTimer?.cancel();
    if (_queueFile == null || !_loaded) return;
    try {
      await _queueFile!
          .writeAsString(jsonEncode(tasks.map((t) => t.toJson()).toList()),
              flush: true);
    } catch (_) {}
  }
}

final transfersProvider =
    ChangeNotifierProvider<TransferEngine>((_) => TransferEngine.I);
