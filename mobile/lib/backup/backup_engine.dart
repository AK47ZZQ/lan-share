// 自动备份引擎（M-A3 / FR-2 / APP-DESIGN §4）：WorkManager 唤醒 → 账本 diff →
// 逐张 sha256 流式 → init（秒传）→ 分片（服务端原子写）→ finish（sha 必校验）。
// 断点真值在服务端：账本只存 session_id，恢复一律 /api/upload/state 探测；
// 单张失败 attempts>5 置 error；单轮失败率 >30%（≥10 张）冷却 30 分钟防风暴雨。
// 并发 1（逐张顺序），家庭场景比并发更稳（≤3 块的礼仪约束自然满足）。
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/api_client.dart';
import '../data/local/prefs.dart';
import '../data/local/secure_store.dart';
import 'backup_config.dart';
import 'backup_ledger.dart';
import 'media_source.dart';

const _kHashLimit = 200 * 1024 * 1024;

class _DigestSink implements Sink<Digest> {
  Digest? value;
  @override
  void add(Digest d) => value = d;
  @override
  void close() {}
}

class BackupEngine {
  static const _destAlbum = '手机相册';

  /// WorkManager Worker 的入口：返回结果 JSON（原生侧解析并决定 retry/通知）
  static Future<String> run() async {
    final res = <String, dynamic>{'status': 'done', 'uploaded': 0, 'failed': 0};
    try {
      final cfg = await BackupConfig.load();
      if (!cfg.enabled) {
        return jsonEncode({'status': 'skip', 'reason': 'disabled'});
      }
      final token = await SecureStore.token();
      if (token == null || token.isEmpty) {
        return jsonEncode({'status': 'skip', 'reason': 'not_logged_in'});
      }
      final addr = await Prefs.lastAddress();
      final host = Uri.tryParse(addr)?.host ?? '';
      if (_isPublicHost(host) && !cfg.allowPublicServer) {
        return jsonEncode({'status': 'skip', 'reason': 'public_server'});
      }
      final sp = await SharedPreferences.getInstance();
      final cooldownUntil = sp.getInt('backup_cooldown_until') ?? 0;
      if (DateTime.now().millisecondsSinceEpoch < cooldownUntil) {
        return jsonEncode({'status': 'cooldown', 'reason': 'failure_rate'});
      }
      final username = sp.getString('username') ?? '';
      if (username.isEmpty) {
        return jsonEncode({'status': 'skip', 'reason': 'no_username'});
      }
      final destDir = 'incoming/$username/$_destAlbum';

      final scanned = (await MediaSource.query(includeVideos: cfg.includeVideos))
          .map((m) => LedgerRow(
              mediaId: m.mediaId,
              kind: m.kind,
              name: m.name,
              size: m.size,
              mtime: m.mtime))
          .toList();
      final ledger = BackupLedger.I;
      final pending = await ledger.reconcile(scanned);
      res['pending'] = pending.length;

      final dio = await ApiClient.I.dio();
      var attempted = 0, failed = 0;
      for (final row in pending) {
        attempted++;
        final ok = await _uploadOne(dio, ledger, row, destDir);
        if (!ok) failed++;
        // 防风暴雨：批量失败超阈值 → 冷却 30 分钟（设计 §4 限流）
        if (attempted >= 10 && failed / attempted > 0.3) {
          await sp.setInt(
              'backup_cooldown_until',
              DateTime.now()
                  .add(const Duration(minutes: 30))
                  .millisecondsSinceEpoch);
          res['status'] = 'cooldown';
          res['reason'] = 'failure_rate';
          break;
        }
      }
      res['uploaded'] = attempted - failed;
      res['failed'] = failed;
    } catch (e) {
      final s = e.toString();
      return jsonEncode({
        'status': 'error',
        'reason': s.length > 200 ? s.substring(0, 200) : s,
      });
    }
    return jsonEncode(res);
  }

  static Future<bool> _uploadOne(
      Dio dio, BackupLedger ledger, LedgerRow row, String destDir) async {
    String? tmp;
    try {
      await ledger.setStatus(row.mediaId, 'uploading');
      tmp = await MediaSource.openTemp(row.mediaId, row.kind);
      final f = File(tmp);
      final size = f.lengthSync();

      String? sha = row.sha256;
      if (sha == null && size <= _kHashLimit) {
        final sink = _DigestSink();
        final byteSink = sha256.startChunkedConversion(sink);
        final raf = await f.open();
        try {
          final buf = List<int>.filled(1024 * 1024, 0);
          while (true) {
            final n = await raf.readInto(buf);
            if (n <= 0) break;
            byteSink.addSlice(buf, 0, n, false);
          }
        } finally {
          await raf.close();
        }
        byteSink.close();
        sha = sink.value!.toString();
        await ledger.setSha(row.mediaId, sha);
      }

      var sid = row.sessionId;
      var chunkSize = 0;
      final received = <int>{};
      var backoff = 0;
      while (true) {
        // 1) 会话：新开 init（含秒传）；有 sid 则 state 探测续传（对账 §4）
        try {
          if (sid == null) {
            final r = await dio.post('/api/upload/init', data: {
              'dest_dir': destDir,
              'filename': row.name,
              'size': size,
              'sha256': ?sha,
            });
            if (r.data['instant'] == true) {
              await ledger.markDone(row.mediaId, r.data['path'] as String);
              return true;
            }
            sid = r.data['session'] as String;
            chunkSize = (r.data['chunk_size'] as num).toInt();
            received
              ..clear()
              ..addAll(((r.data['received'] as List?) ?? const [])
                  .map((e) => (e as num).toInt()));
            await ledger.setSession(row.mediaId, sid, chunkSize, received.toList());
          } else {
            final r = await dio.get('/api/upload/state/$sid');
            chunkSize = (r.data['chunk_size'] as num).toInt();
            received
              ..clear()
              ..addAll(((r.data['received'] as List?) ?? const [])
                  .map((e) => (e as num).toInt()));
            await ledger.setReceived(row.mediaId, received.length);
          }
        } on DioException catch (e) {
          if (e.response?.statusCode == 404 && sid != null) {
            sid = null; // 会话过期（3 天 TTL）→ 重开，断点归零
            received.clear();
            continue;
          }
          if (!await _backoff(ledger, row, e, backoff)) return false;
          backoff++;
          continue;
        }

        // 2) 补缺失分片
        final total = (size + chunkSize - 1) ~/ chunkSize;
        final missing = [
          for (var i = 0; i < total; i++)
            if (!received.contains(i)) i,
        ];
        for (final i in missing) {
          final len =
              (size - i * chunkSize) < chunkSize ? (size - i * chunkSize) : chunkSize;
          final bytes = await _readChunk(f, i * chunkSize, len);
          try {
            await dio.post('/api/upload/chunk/$sid?index=$i',
                data: bytes,
                options: Options(
                    contentType: 'application/octet-stream',
                    sendTimeout: const Duration(minutes: 10)));
            received.add(i);
            backoff = 0;
            await ledger.setReceived(row.mediaId, received.length);
          } on DioException catch (e) {
            if (e.response?.statusCode == 404) {
              sid = null;
              received.clear();
              break;
            }
            if (!await _backoff(ledger, row, e, backoff)) return false;
            backoff++;
            break;
          }
        }
        if (sid == null) continue;
        // 上面分片循环 break 表示有失败要重走会话/分片补齐
        if (received.length < total) continue;

        // 3) finish（服务端拼装 + sha 必校验）
        try {
          final r = await dio.post('/api/upload/finish/$sid',
              options: Options(receiveTimeout: const Duration(minutes: 15)));
          await ledger.markDone(row.mediaId, r.data['path'] as String);
          return true;
        } on DioException catch (e) {
          if (e.response?.statusCode == 404) {
            sid = null;
            received.clear();
            continue;
          }
          if (!await _backoff(ledger, row, e, backoff)) return false;
          backoff++;
        }
      }
    } on DioException catch (e) {
      final st = e.response?.statusCode;
      final d = e.response?.data;
      final detail =
          d is Map && d['detail'] != null ? d['detail'].toString() : 'HTTP $st';
      await ledger.fail(row.mediaId, detail);
      return false;
    } catch (e) {
      await ledger.fail(
          row.mediaId, e.toString().substring(0, e.toString().length.clamp(0, 160)));
      return false;
    } finally {
      if (tmp != null) MediaSource.cleanup(tmp);
    }
  }

  /// 4xx 判死（重试无意义）置 error 返回 false；5xx/网络错误退避重试
  static Future<bool> _backoff(
      BackupLedger ledger, LedgerRow row, DioException e, int backoff) async {
    final status = e.response?.statusCode;
    if (status != null && status >= 400 && status < 500) {
      final d = e.response?.data;
      final detail =
          d is Map && d['detail'] != null ? d['detail'].toString() : 'HTTP $status';
      await ledger.fail(row.mediaId, detail);
      return false;
    }
    final dead = await ledger.bumpAttempt(row.mediaId);
    if (dead) return false;
    final wait = [2, 5, 10, 30, 60][backoff.clamp(0, 4)];
    await Future.delayed(Duration(seconds: wait));
    return true;
  }

  static Future<List<int>> _readChunk(File f, int offset, int len) async {
    final raf = await f.open();
    try {
      await raf.setPosition(offset);
      return await raf.read(len);
    } finally {
      await raf.close();
    }
  }

  /// §6：公网地址（非 RFC1918/localhost 域名或 IP）默认不自动备份，省流量
  static bool _isPublicHost(String host) {
    if (host.isEmpty) return true;
    if (RegExp(r'^\d{1,3}(\.\d{1,3}){3}$').hasMatch(host)) {
      final p = host.split('.').map(int.parse).toList();
      if (p[0] == 10 || p[0] == 127 || (p[0] == 192 && p[1] == 168)) return false;
      if (p[0] == 172 && p[1] >= 16 && p[1] <= 31) return false;
      return true;
    }
    return !(host.endsWith('.local') || host.endsWith('.lan'));
  }
}
