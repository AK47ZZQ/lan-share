// 传输引擎：上传（init→chunk→finish 全协议）/ 下载（/preview Range 断点）统一队列。
// 设计要点（APP-DESIGN §2"传输协议 100% Dart"）：
//  · 队列 JSON 落盘（documents/transfer_queue.json）——杀进程后重启恢复；
//    断点真值在服务端：续传一律 /api/upload/state 探测，本地 received 只做并集加速
//  · 分片原子写在服务端，半截分片不会混入 received；客户端按确认块推进度
//  · 暂停=取消当前请求（服务端留 _tmp 不入账）；网络错误退避重试至多 8 次
//  · 登出全部挂起（不置 userPaused，登录后自动续传）
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/api_client.dart';
import '../data/api/upload_client.dart';
import '../data/local/notify.dart';
import '../data/local/prefs.dart';
import '../data/net.dart';
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

/// 后台 isolate 计算 sha256（compute 入口必须是顶层函数，v0.10.3）：
/// 同步打开文件逐块喂摘要，返回 hex 串；失败由调用方兜底。
String _sha256Of(String path) {
  final f = File(path);
  final sink = _DigestSink();
  final byteSink = sha256.startChunkedConversion(sink);
  final raf = f.openSync();
  try {
    final buf = Uint8List(1024 * 1024);
    while (true) {
      final n = raf.readIntoSync(buf);
      if (n <= 0) break;
      byteSink.addSlice(buf, 0, n, false);
    }
  } finally {
    raf.closeSync();
  }
  byteSink.close();
  return sink.value!.toString();
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
  // 公网弱网加固（v0.10.3）：10Mbps 下并发任务互相抢带宽，公网入口
  // 串行（1 路）更稳；局域网保持 2 路。入队/登录时刷新。
  bool _public = false;
  String _docsPath = '';

  /// 入口刷新（调度前必须完成）：切入口后并发策略即时跟上
  Future<void> _refreshPublic() async {
    _public = isPublicAddress(await Prefs.lastAddress());
  }

  /// 先刷新入口档位再调度：enqueueUpload 是同步签名（调用方靠返回值判空），
  /// 不能整体 await——公网用户冷启动首批任务若直接 _kick()，会按默认
  /// 局域网档 2 路并发跑，直到下一轮刷新才纠正（v0.10.3 审查补丁）
  Future<void> _refreshAndKick() async {
    await _refreshPublic();
    _kick();
  }

  int get activeCount => _running.length;

  // ── 登录态联动（main.dart 注入） ────────────────────────────
  Future<void> onLogin() async {
    _loggedIn = true;
    await _load();
    await _refreshPublic();
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
        // 去重命中 error 态任务时重新驱动（R1-P2-1）：此前直接返回旧任务，
        // UI 却报"已加入队列"，重试这一高频路径假成功
        if (t.status == TransferStatus.error) resumeTask(t);
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
    // ignore: discarded_futures
    _refreshAndKick();
    return task;
  }

  Future<TransferTask?> enqueueDownload({
    required String remotePath,
    required String name,
    required int size,
  }) async {
    await _ensureDocs();
    await _refreshPublic(); // 调度前先定档位（局域网 2 路 / 公网 1 路）
    for (final t in tasks) {
      if (t.kind == TransferKind.download &&
          !t.finished &&
          t.remotePath == remotePath) {
        if (t.status == TransferStatus.error) resumeTask(t);  // 同上传（R1-P2-1）
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

  /// 下载沙盒目录（documents/Downloads）：传输页"本地文件"视图、更新安装
  /// 包清理共用。不存在则建（目录缺席时 list 会抛）。
  Future<Directory> downloadsDir() async {
    await _ensureDocs();
    final d = Directory('$_docsPath/Downloads');
    if (!d.existsSync()) d.createSync(recursive: true);
    return d;
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
    final sid = t.sid;
    tasks.remove(t);
    _speed.remove(t.id);
    _schedulePersist();
    notifyListeners();
    if (running) _cancelTokens[t.id]?.cancel();
    // 永久移除 = 会话级取消（v1.1.14）：服务端同刻释放会话+分片，否则弃单
    // 会话占全家在途总量直到 3 天 TTL。尽力而为，失败由 TTL 兜底。
    if (sid != null) {
      ApiClient.I.dio()
          .then((d) => d.delete('/api/upload/sessions/$sid'))
          .ignore();   // 尽力而为：失败由服务端 3 天 TTL 兜底
    }
  }

  void clearFinished() {
    tasks.removeWhere((t) => t.status == TransferStatus.done);
    _schedulePersist();
    notifyListeners();
  }

  // ── 批量操作（App v0.6.0 传输页）──────────────────────────
  // 全部暂停：在途任务经 cancelToken 优雅停（进度留断点），未启动的直接置暂停
  void pauseAll() {
    for (final t in List.of(tasks)) {
      if (t.finished) continue;
      pauseTask(t);
    }
  }

  /// 全部继续：只唤醒"用户主动暂停"的——断网/登出造成的等待恢复
  /// 本就由 _kick 自动调度，混进来反而抢跑计费网络
  void resumeAll() {
    for (final t in List.of(tasks)) {
      if (t.status == TransferStatus.paused && t.userPaused) resumeTask(t);
    }
  }

  void retryAllFailed() {
    for (final t in List.of(tasks)) {
      if (t.status == TransferStatus.error) resumeTask(t);
    }
  }

  int get pausedCount =>
      tasks.where((t) => t.status == TransferStatus.paused && t.userPaused).length;
  int get failedCount => tasks.where((t) => t.status == TransferStatus.error).length;

  int speedOf(TransferTask t) => _speed[t.id] ?? 0;

  // ── 调度 ──────────────────────────────────────────────────
  // 并发数：局域网 2 路 / 公网 1 路（10Mbps 与蜂窝下 2 路互相抢带宽，
  // 单路能稳定吃满；切入口后 _refreshPublic 生效）
  int get _maxActive => _public ? 1 : 2;

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
      // 后台 isolate 计算：200MB 上限内 sha256 全程不啃 UI 线程
      // （v0.10.3 加固；失败只影响秒传探测，不阻塞上传）
      try {
        t.sha256 = await compute(_sha256Of, t.localPath);
      } catch (_) {
        t.sha256 = null;
      }
      _schedulePersist();
    }

    var backoff = 0;
    // 协议单实现（07 审查批次 4：init/state 校验/分片补齐/finish 不再本文件手写）
    final client = UploadClient(dio: dio, cancelToken: token)
      ..sid = t.sid
      ..received.addAll(t.received);
    while (true) {
      // 1) 会话：无 sid 则 init（含秒传探测）；有 sid 则 state 探测并集
      try {
        if (client.sid == null) {
          _setStatus(t, TransferStatus.uploading);
          final instantPath = await client.init(
              destDir: t.destDir ?? '',
              filename: t.name,
              size: t.size,
              sha256: t.sha256);
          if (instantPath != null) {
            t.remotePath = instantPath;
            t.received.clear();
            _setStatus(t, TransferStatus.done);
            _speed[t.id] = 0;
            // ignore: discarded_futures
            _notifyDone(t);
            return;
          }
          t.sid = client.sid;
          t.chunkSize = client.chunkSize;
          _schedulePersist();
        } else {
          await client.resume();
          t.chunkSize = client.chunkSize;
        }
        t.received
          ..clear()
          ..addAll(client.received);
      } on FormatException catch (e) {
        // 协议响应校验失败（bad_session/bad_chunk_size）：文案友好，
        // FormatException 原样穿到 t.error 展示给用户
        throw Exception('服务端返回异常（${e.message}），请稍后重试');
      } on DioException catch (e) {
        if (token.isCancelled) throw _Paused();
        if (e.response?.statusCode == 404 && t.sid != null) {
          // 会话过期（3 天 TTL）或被清理：作废重开，断点归零
          client.reopen();
          t.sid = null;
          t.received.clear();
          continue;
        }
        backoff = await _retryOrThrow(t, e, backoff, token: token);
        continue;
      }

      // 2) 补齐缺失分片（服务端原子写，确认过的块不重传）
      var failed = false;
      for (final i in client.missingChunks(t.size)) {
        final bytes = await UploadClient.readChunk(
            f, i * client.chunkSize, client.chunkLength(t.size, i));
        _setStatus(t, TransferStatus.uploading);
        try {
          await client.postChunk(i, bytes);
          t.received.add(i);
          t.attempts = 0;
          backoff = 0;
          _touch(t);
        } on DioException catch (e) {
          if (token.isCancelled) throw _Paused();
          if (e.response?.statusCode == 404) {
            client.reopen();
            t.sid = null; // 会话没了：重开
            failed = true;
            break;
          }
          backoff = await _retryOrThrow(t, e, backoff, token: token);
          failed = true;
          break;
        }
      }
      if (failed) continue;

      // 3) finish（服务端拼装 + sha 校验；大文件耗时长，放宽超时）
      _setStatus(t, TransferStatus.merging);
      try {
        t.remotePath = await client.finish(fallbackName: t.name);
        _setStatus(t, TransferStatus.done);
        _speed[t.id] = 0;
        // ignore: discarded_futures
        _notifyDone(t);
        return;
      } on DioException catch (e) {
        if (token.isCancelled) throw _Paused();
        backoff = await _retryOrThrow(t, e, backoff, token: token);
      }
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
          // Range 起点即文件尾（.part 已完整）：服务端按 RFC 回 416，视为完成。
          // 注意不能走 _retryOrThrow——它对 4xx 直接判死，"重试"是死代码。
          // size 未知（=0）的任务下载完整后断点续传也会 416，保留旧语义收尾；
          // size 已知但 .part 长度不符（远端被替换变小后的陈旧 .part）必须
          // 清掉从头下载，否则把坏文件改名成"已完成"（R2-P1）
          if (t.size <= 0 || part.lengthSync() == t.size) {
            _finishDownload(t, part);
            return;
          }
          part.deleteSync();
          t.receivedBytes = 0;
          t.attempts += 1;
          if (t.attempts > 3) throw Exception('远端文件大小与断点不符，已重试多次');
          continue;
        }
        backoff = await _retryOrThrow(t, e, backoff, token: token);
      }
    }
  }

  void _finishDownload(TransferTask t, File part) {
    t.receivedBytes = part.lengthSync();
    // 入口拿不到大小的任务（照片墙/查看器传 size=0）：完成时用实际
    // 字节数物化 size——否则重启恢复后总量仍是 0，界面显示 "0 B · 已下载"
    if (t.size <= 0) t.size = t.receivedBytes;
    part.renameSync(t.localPath);
    t.status = TransferStatus.done;
    _speed[t.id] = 0;
    _schedulePersist();
    notifyListeners();
    // ignore: discarded_futures
    _notifyDone(t);
  }

  // ── 公共小件 ──────────────────────────────────────────────
  void _setStatus(TransferTask t, TransferStatus s) {
    t.status = s;
    notifyListeners();
  }

  /// 单任务完成 → 系统通知（设置可关；读 sp 而非持有 provider，完成是低频事件）
  Future<void> _notifyDone(TransferTask t) async {
    try {
      final sp = await SharedPreferences.getInstance();
      if (!(sp.getBool('notify_transfer_done') ?? true)) return;
      await TransferNotifier.I.transferDone(
          upload: t.kind == TransferKind.upload, name: t.name);
    } catch (_) {}
  }

  /// 非 cancel 错误：4xx 直接判死（重试无意义）；5xx/网络错误退避重试至多 5 次。
  /// 429 例外：限速语义是"稍后再来"，走退避（与备份引擎 _backoff 同语义——
  /// 家庭共享公网出口时多端容易撞 120 req/min 的全局配额，判死会把暂时
  /// 失败变永久失败）
  Future<int> _retryOrThrow(TransferTask t, DioException e, int backoff,
      {required CancelToken token}) async {
    final status = e.response?.statusCode;
    if (status != null && status >= 400 && status < 500 && status != 429) {
      final d = e.response?.data;
      final detail = d is Map && d['detail'] != null ? d['detail'] : 'HTTP $status';
      throw Exception(detail.toString());
    }
    t.attempts += 1;
    // 公网弱网加固（v0.10.1）：蜂窝路径丢包/切换常见，退避池加长到 8 次
    // （最长 8 分钟），期间任务保持 pending 不落 error；仍失败才放弃
    if (t.attempts > 8) {
      throw Exception('连续失败多次：${_brief(e)}');
    }
    final wait = [2, 5, 10, 30, 60, 120, 240, 480][backoff.clamp(0, 7)];
    t.error = '网络波动，${wait}s 后重试（第 ${t.attempts} 次）';
    notifyListeners();
    // 长 sleep 拆 1s 分片（R1-P3-7）：暂停/取消即时生效，而非最长 8 分钟后
    // 循环下一轮才检查、任务长时间显示"传输中"却无动作
    for (var i = 0; i < wait; i++) {
      if (token.isCancelled) throw _Paused();
      await Future.delayed(const Duration(seconds: 1));
    }
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
        if (t.kind == TransferKind.download && !t.finished) {
          final p = File('${t.localPath}.part');
          t.receivedBytes = p.existsSync() ? p.lengthSync() : 0;
        } else if (t.kind == TransferKind.download && t.finished) {
          // 已完成任务：done 时 size 已物化为实际字节（即便入口传 0），
          // 不再按 .part 重算——文件改名后 .part 不存在会把进度打回 0
          if (t.size <= 0) t.size = t.receivedBytes;
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

  bool _persisting = false;
  bool _persistQueued = false;

  Future<void> _persistNow() async {
    _persistTimer?.cancel();
    if (_queueFile == null || !_loaded) return;
    // 单飞（R3-P3-8）：定时器回调与直接 await 可并发写同一个 .tmp，交错写
    // 仍能产出损坏 JSON 并被 rename 落地
    if (_persisting) {
      _persistQueued = true;
      return;
    }
    _persisting = true;
    try {
      do {
        _persistQueued = false;
        try {
          // 原子写（R2-P3-8）：写盘中途被杀会让 JSON 截断、下次整队丢弃（含全部
          // 断点任务）——先写 .tmp 再 rename
          final tmp = File('${_queueFile!.path}.tmp');
          await tmp.writeAsString(jsonEncode(tasks.map((t) => t.toJson()).toList()),
              flush: true);
          await tmp.rename(_queueFile!.path);
        } catch (_) {}
      } while (_persistQueued);
    } finally {
      _persisting = false;
    }
  }
}

final transfersProvider =
    ChangeNotifierProvider<TransferEngine>((_) => TransferEngine.I);
