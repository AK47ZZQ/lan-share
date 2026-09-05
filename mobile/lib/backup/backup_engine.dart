// 自动备份引擎（M-A3 / FR-2 / APP-DESIGN §4）：WorkManager 唤醒 → 账本 diff →
// 逐张 sha256 流式 → init（秒传）→ 分片（服务端原子写）→ finish（sha 必校验）。
// 断点真值在服务端：账本只存 session_id，恢复一律 /api/upload/state 探测；
// 单张失败累加 attempts，>5 暂停自动重试（账本页可手动重试）；单轮失败率
// >30%（≥10 张）冷却 30 分钟防风暴雨。并发 1（逐张顺序），家庭场景比并发更稳
// （≤3 块的礼仪约束自然满足）。
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:dio/dio.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/api_client.dart';
import '../data/local/prefs.dart';
import '../data/net.dart';
import '../data/local/secure_store.dart';
import 'backup_config.dart';
import 'backup_dest.dart';
import 'backup_ledger.dart';
import 'media_source.dart';

const _kHashLimit = 200 * 1024 * 1024;

/// 服务器证书未被信任（TOFU 待确认/已更换）：后台 isolate 没有 UI 确认路径，
/// 继续退避只会把 MITM/换证书伪装成普通失败。抛出此哨兵让引擎立即终止本轮，
/// 由 Worker 发系统通知提示用户打开 App 确认（对抗审查 CRIT-2）。
class _TrustAbort implements Exception {
  const _TrustAbort();
}

bool _isTrustError(DioException e) =>
    e.error is TrustRequiredError || e.error is HandshakeException;

class _DigestSink implements Sink<Digest> {
  Digest? value;
  @override
  void add(Digest d) => value = d;
  @override
  void close() {}
}

class BackupEngine {
  /// 逐张上传前的实时暂停检查：返回 null=继续，否则为暂停原因。
  /// WorkManager 约束只在任务启动时把关——跑起来之后系统叫停（isStopped）、
  /// 离开 Wi-Fi（转计费网络）、拔充电器、App 里关开关都管不住，真机上
  /// 表现为"备份无法自动暂停"。这里逐张复查，违反即优雅停：账本留断点，
  /// 约束恢复后的下个周期自动续。
  /// manual 触发（用户点"立即备份"）只尊重系统叫停与开关——0.7.3 前手动
  /// 轮被"仅充电"约束静默吞掉是头号投诉；流量保护保留（wifiLost 仍暂停）。
  static Future<String?> _pauseReason(String trigger) async {
    final sp = await SharedPreferences.getInstance();
    if (!(sp.getBool('backup_enabled') ?? false)) return 'disabled';
    try {
      final r = await const MethodChannel('lanshare/backup')
          .invokeMethod<Map<Object?, Object?>>('checkStop');
      if (r != null) {
        if (r['stopped'] == true) return 'system_stop';
        if (trigger != 'manual') {
          final cfg = await BackupConfig.load();
          if (cfg.wifiOnly && r['metered'] == true) return 'wifi_lost';
          if (cfg.chargingOnly && r['charging'] == false) return 'unplugged';
        }
      }
    } catch (_) {
      // 原生桥不可用（理论不发生）：宁缺毋滥地继续，别让检查本身卡死备份
    }
    return null;
  }

  /// WorkManager Worker 的入口：返回结果 JSON（原生侧解析并决定 retry/通知）
  static Future<String> run([String trigger = 'periodic']) async {
    final res = <String, dynamic>{'status': 'done', 'uploaded': 0, 'failed': 0};
    final sp = await SharedPreferences.getInstance();
    // 每个出口都把本轮结果落盘（0.7.3 可观测性）：此前十来个静默 return
    // 让"点了没反应"完全无法排障
    Future<String> finish([Map<String, dynamic>? override]) async {
      final m = override ?? res;
      try {
        await sp.setString('backup_last_round', jsonEncode({
          'status': m['status'],
          'reason': m['reason'],
          'uploaded': m['uploaded'] ?? 0,
          'failed': m['failed'] ?? 0,
          'pending': m['pending'] ?? 0,
          'trigger': trigger,
          'ts': DateTime.now().millisecondsSinceEpoch,
        }));
      } catch (_) {}
      return jsonEncode(m);
    }

    try {
      final cfg = await BackupConfig.load();
      if (!cfg.enabled) {
        return await finish({'status': 'skip', 'reason': 'disabled'});
      }
      final token = await SecureStore.token();
      if (token == null || token.isEmpty) {
        return await finish({'status': 'skip', 'reason': 'not_logged_in'});
      }
      final addr = await Prefs.lastAddress();
      final host = Uri.tryParse(addr)?.host ?? '';
      if (_isPublicHost(host) && !cfg.allowPublicServer) {
        return await finish({'status': 'skip', 'reason': 'public_server'});
      }
      final cooldownUntil = sp.getInt('backup_cooldown_until') ?? 0;
      // 手动触发豁免失败冷却：用户看到失败点了"立即备份"，应该立刻再试
      // 而不是被上一轮的 30 分钟冷却挡住（冷却本身照常刷新）
      if (trigger != 'manual' &&
          DateTime.now().millisecondsSinceEpoch < cooldownUntil) {
        return await finish({'status': 'skip', 'reason': 'cooldown'});
      }
      final username = sp.getString('username') ?? '';
      if (username.isEmpty) {
        return await finish({'status': 'skip', 'reason': 'no_username'});
      }
      // prefs 属明文本地存储（root/adb 可改）：路径相关字符一律拒跑，
      // 防被改成穿越形态后去清账本/拼 destDir
      if (username.contains('/') ||
          username.contains('\\') ||
          username.contains('..')) {
        return jsonEncode({'status': 'skip', 'reason': 'bad_username'});
      }
      // 账本按账号隔离：同机换账号（或服务端改名）时旧账号的 done 记录
      // 会把新账号的照片永久跳过——检测到账号变化就清账本重对账
      if ((sp.getString('backup_ledger_user') ?? '') != username) {
        await BackupLedger.I.clearAll();
        await sp.setString('backup_ledger_user', username);
        // 换账号（含服务端改名）：账本清空 + 新落位 <类型>/<新用户名>/…，
        // 重对账全量重备；sha 秒传零流量，旧用户名目录管理员手动清
      }
      List<LedgerRow> scanned;
      List<LedgerRow> pending;
      try {
        var media = await MediaSource.query(includeVideos: cfg.includeVideos);
        // 相册白名单（0.14，同日改为"一个不勾 = 全部不备份"）：只备勾选的
        // 相册，空白名单 = 不备份任何相册；旧排除表（0.6.0）继续生效直至
        // 用户在新 UI 保存一次即清空。都只影响扫描阶段，账本不动——之后
        // 勾选/取消勾选，同 id 的照片自动进出待备集合
        if (cfg.selectedBuckets.isEmpty) {
          media = const [];
        } else {
          final sel = cfg.selectedBuckets.toSet();
          media = media.where((m) => sel.contains(m.bucket)).toList();
        }
        if (cfg.excludedBuckets.isNotEmpty) {
          final excl = cfg.excludedBuckets.toSet();
          media = media.where((m) => !excl.contains(m.bucket)).toList();
        }
        scanned = media
            .map((m) => LedgerRow(
                mediaId: m.mediaId,
                kind: m.kind,
                name: m.name,
                size: m.size,
                mtime: m.mtime,
                bucket: m.bucket))
            .toList();
        pending = await BackupLedger.I.reconcile(scanned);
      } catch (_) {
        // 媒体库/账本暂不可用（DB 锁、磁盘满、权限被回收）：本轮跳过，
        // 等下个周期再试——不算引擎错误，避免 Worker 无限 retry
        return await finish({'status': 'skip', 'reason': 'scan_or_ledger_unavailable'});
      }
      res['pending'] = pending.length;
      final pendingBytes = pending.fold<int>(0, (s, r) => s + r.size);

      final dio = await ApiClient.I.dio();
      final ledger = BackupLedger.I;
      var attempted = 0, failed = 0, doneBytes = 0;
      for (final row in pending) {
        final pause = await _pauseReason(trigger);
        if (pause != null) {
          res['status'] = 'paused';
          res['reason'] = pause;
          break;
        }
        attempted++;
        try {
          final ok = await _uploadOne(dio, ledger, row, username);
          if (!ok) {
            failed++;
          } else {
            doneBytes += row.size;
          }
        } on _TrustAbort {
          res['status'] = 'trust_required';
          res['reason'] = 'certificate';
          break;
        }
        _reportProgress(attempted - failed, pending.length);
        // 进度落盘：首页备份卡 2s 轮询直接显示"备份中 X/Y"（通知栏之外的第二只眼）
        await sp.setInt('backup_progress_done', attempted - failed);
        await sp.setInt('backup_progress_total', pending.length);
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
      // 首页备份卡的"上次完成"；有实际成果才刷新
      if ((attempted - failed) > 0) {
        await sp.setInt('backup_last_done_at',
            DateTime.now().millisecondsSinceEpoch);
      }
      // 本轮结束清进度；失败数/待备数按账本现状回写，且感知排除相册：
      // 被排除项永远不会再传，留在"失败/待备"里红字永远消不掉（0.7.1）。
      // 分照片/视频回写供首页分类显示。
      await sp.setInt('backup_progress_done', 0);
      await sp.setInt('backup_progress_total', 0);
      try {
        final st = await ledger.stats(excluded: cfg.excludedBuckets,
            included: cfg.selectedBuckets);
        await sp.setInt('backup_failed_count', st.error);
        await sp.setInt('backup_pending_photos', st.pendingPhotos);
        await sp.setInt('backup_pending_videos', st.pendingVideos);
      } catch (_) {}
      // 回写剩余待备数：Worker 周期触发时为 0 就不拉引擎（省电省内存）；
      // -1 表示未知/配置刚变强制跑。Observer 触发不走此捷径。
      await sp.setInt(
          'backup_pending_count', (pending.length - (attempted - failed))
              .clamp(0, 1 << 31));
      // 待备字节（首页空间预警用）：本轮成功部分实时扣减
      await sp.setInt('backup_pending_bytes',
          (pendingBytes - doneBytes).clamp(0, 1 << 62));
    } catch (e) {
      final s = e.toString();
      return finish({
        'status': 'error',
        'reason': s.length > 200 ? s.substring(0, 200) : s,
      });
    } finally {
      // 兜底清进度：循环中途抛异常走不到正常收尾时，别让"备份中 X/Y"卡死在首页。
      // 注意这里绝不 close() 账本——sqflite 连接进程级共享，关掉会把主 isolate
      // 的统计卡/账本页一起打死（database_closed，0.7.1 实测复现后移除）。
      try {
        await sp.setInt('backup_progress_done', 0);
        await sp.setInt('backup_progress_total', 0);
      } catch (_) {}
    }
    return finish();
  }

  /// 逐张进度 → Kotlin 刷新前台通知（发完即忘，失败不影响备份）
  static void _reportProgress(int done, int total) {
    // ignore: discarded_futures
    const MethodChannel('lanshare/backup')
        .invokeMethod('progress', {'done': done, 'total': total})
        .catchError((_) {});
  }

  static Future<bool> _uploadOne(
      Dio dio, BackupLedger ledger, LedgerRow row, String username) async {
    String? tmp;
    try {
      // 0.7.0 落位：照片/<u>/<相册>/、视频/<u>/<相册>/，逐行按类型+相册算
      final destDir = backupDestDir(
          username: username, kind: row.kind, bucket: row.bucket);
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
      var reopens = 0; // 会话重开计数：防 init 正常但 chunk/finish 恒 404 的死循环
      final received = <int>{};
      var backoff = 0;
      while (true) {
        if (reopens > 5) {
          await ledger.fail(row.mediaId, '会话反复失效，已暂停');
          return false;
        }
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
            // 会话 id 白名单（服务端 token_urlsafe 字母表）：响应字段不可信，
            // 防御性校验避免畸形 id 进 URL 模板造成无意义请求/歧义路径
            if (!RegExp(r'^[A-Za-z0-9_\-]{8,64}$').hasMatch(sid)) {
              throw const FormatException('bad_session');
            }
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
          // 服务端异常响应可能给 0/负值，后面除法与分片总数会全崩
          if (chunkSize <= 0) throw const FormatException('bad_chunk_size');
        } on DioException catch (e) {
          if (e.response?.statusCode == 404 && sid != null) {
            sid = null; // 会话过期（3 天 TTL）→ 重开，断点归零
            received.clear();
            reopens++;
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
            // 账本 received 仅 UI 展示（断点真值在服务端 state），按 1/10 节流
            // 落盘，避免长视频几百片 × 逐片 UPDATE 的写放大
            if (received.length % 10 == 0) {
              await ledger.setReceived(row.mediaId, received.length);
            }
          } on DioException catch (e) {
            if (e.response?.statusCode == 404) {
              sid = null;
              received.clear();
              reopens++;
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
          // path 异常兜底：finish 200 即已拼装成功，别因响应字段意外
          // 抛 TypeError 把"服务端已有文件"误标失败（sha 在账本，下轮秒传可纠）
          await ledger.markDone(
              row.mediaId, (r.data['path'] as String?) ?? row.name);
          return true;
        } on DioException catch (e) {
          if (e.response?.statusCode == 404) {
            sid = null;
            received.clear();
            reopens++;
            continue;
          }
          if (!await _backoff(ledger, row, e, backoff)) return false;
          backoff++;
        }
      }
    } on DioException catch (e) {
      if (_isTrustError(e)) {
        await ledger.fail(row.mediaId, '服务器证书未信任，请打开 App 确认');
        throw const _TrustAbort();
      }
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

  /// 4xx 判死（重试无意义）置 error 返回 false；5xx/网络错误退避重试。
  /// 429 例外：限速语义是"稍后再来"，走退避（家庭共享公网出口可能多端
  /// 共享 120 req/min 配额，判死会把暂时失败变永久暂停）
  static Future<bool> _backoff(
      BackupLedger ledger, LedgerRow row, DioException e, int backoff) async {
    // 证书问题不是"网络抖动"：退避 5 次只会让用户误以为网盘坏了
    if (_isTrustError(e)) {
      await ledger.fail(row.mediaId, '服务器证书未信任，请打开 App 确认');
      throw const _TrustAbort();
    }
    final status = e.response?.statusCode;
    if (status != null && status >= 400 && status < 500 && status != 429) {
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

  /// §6：公网地址默认不自动备份，省流量。判定逻辑收口到 data/net.dart
  ///（2026-09-03：公网徽标/上传提醒共用同一事实源）
  static bool _isPublicHost(String host) => isPublicHost(host);
}
