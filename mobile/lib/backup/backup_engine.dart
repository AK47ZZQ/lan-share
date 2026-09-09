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
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/api/api_client.dart';
import '../data/api/upload_client.dart';
import '../data/local/prefs.dart';
import '../data/net.dart';
import '../data/local/secure_store.dart';
import 'backup_keys.dart';
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

/// 路径逐段 URL 编码（/api/files/{path} 的中文/空格段）
String _encSegs(String p) =>
    p.split('/').map(Uri.encodeComponent).join('/');

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
    if (!(sp.getBool(BackupKeys.enabled) ?? false)) return 'disabled';
    // 用户手动暂停（v0.17）：手动轮同样尊重——"暂停备份"是明确叫停，
    // 点了"立即备份"想跑，先回设置页解除暂停
    final cfg = await BackupConfig.load();
    if (cfg.paused) return 'user_paused';
    try {
      final r = await const MethodChannel('lanshare/backup')
          .invokeMethod<Map<Object?, Object?>>('checkStop');
      if (r != null) {
        if (r['stopped'] == true) return 'system_stop';
        if (trigger != 'manual') {
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
        await sp.setString(BackupKeys.lastRound, jsonEncode({
          'status': m['status'],
          'reason': m['reason'],
          'uploaded': m['uploaded'] ?? 0,
          'failed': m['failed'] ?? 0,
          'pending': m['pending'] ?? 0,
          'trigger': trigger,
          'ts': DateTime.now().millisecondsSinceEpoch,
        }));
      } catch (e) {
        // prefs 写失败（磁盘满/权限）会让首页备份卡永久停在旧数据，得有痕
        debugPrint('[BackupEngine] last_round write failed: $e');
      }
      return jsonEncode(m);
    }

    try {
      final cfg = await BackupConfig.load();
      if (!cfg.enabled) {
        return await finish({'status': 'skip', 'reason': 'disabled'});
      }
      // 用户暂停：调度照在（开关没关），引擎直接早退——省一轮媒体库扫描
      if (cfg.paused) {
        return await finish({'status': 'skip', 'reason': 'user_paused'});
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
      final cooldownUntil = sp.getInt(BackupKeys.cooldownUntil) ?? 0;
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
        return await finish({'status': 'skip', 'reason': 'bad_username'});
      }
      // 账本按账号隔离：同机换账号（或服务端改名）时旧账号的 done 记录
      // 会把新账号的照片永久跳过——检测到账号变化就清账本重对账
      if ((sp.getString(BackupKeys.ledgerUser) ?? '') != username) {
        await BackupLedger.I.clearAll();
        await sp.setString(BackupKeys.ledgerUser, username);
        // 换账号（含服务端改名）：账本清空 + 新落位 <类型>/<新用户名>/…，
        // 重对账全量重备；sha 秒传零流量，旧用户名目录管理员手动清
      }
      List<LedgerRow> scanned;
      List<LedgerRow> pending;
      List<BucketMove> bucketMoves = const [];
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
        final rec = await BackupLedger.I.reconcile(scanned);
        pending = rec.pending;
        bucketMoves = rec.bucketMoves;
      } catch (_) {
        // 媒体库/账本暂不可用（DB 锁、磁盘满、权限被回收）：本轮跳过，
        // 等下个周期再试——不算引擎错误，避免 Worker 无限 retry
        return await finish({'status': 'skip', 'reason': 'scan_or_ledger_unavailable'});
      }
      res['pending'] = pending.length;
      final pendingBytes = pending.fold<int>(0, (s, r) => s + r.size);

      final dio = await ApiClient.I.dio();
      final ledger = BackupLedger.I;

      // ── 轮前对账（9.6-P1-10 智能识别，全部采纳）──────────────
      // 场景5：空间预检——待备总量超过服务端剩余空间时整轮跳过（比传到
      // 一半失败友好），round JSON reason=insufficient_space，设置页可见
      try {
        final disk = await dio.get('/api/disk');
        final free = (disk.data['free_bytes'] as num?)?.toInt() ?? 0;
        if (free > 0 && pendingBytes > free) {
          return await finish({
            'status': 'skip',
            'reason': 'insufficient_space',
            'pending': pending.length,
          });
        }
      } catch (_) {/* 空间接口不可用不阻塞备份，上传失败仍有逐张处理 */}

      // 场景3：相册改名识别——同 (类型, 旧→新) 方向迁移 ≥5 张，且本地扫描
      // 中旧相册已整体消失（不是拆分/挪几张），判定为改名：服务端 rename
      // 目录 + 账本改指向恢复 done，不重传；rename 失败照旧重传兜底
      if (bucketMoves.isNotEmpty) {
        final scannedBuckets = scanned.map((s) => s.bucket).toSet();
        final byDir = <String, int>{}; // 'kind|from|to' -> 张数
        for (final m in bucketMoves) {
          byDir['${m.kind}|${m.from}|${m.to}'] =
              (byDir['${m.kind}|${m.from}|${m.to}'] ?? 0) + 1;
        }
        for (final e in byDir.entries) {
          if (e.value < 5) continue;
          final parts = e.key.split('|');
          final kind = parts[0], from = parts[1], to = parts[2];
          if (scannedBuckets.contains(from) || from.isEmpty) continue;
          try {
            final fromDir = backupDestDir(
                username: username, kind: kind, bucket: from);
            final newName = safeBucketName(to);
            await dio.post('/api/rename',
                data: {'path': fromDir, 'new_name': newName});
            final newDir = backupDestDir(username: username, kind: kind, bucket: to);
            await ledger.applyBucketRename(kind, from, to, '$fromDir/', '$newDir/');
            pending = pending
                .where((r) => !(r.kind == kind && r.bucket == to))
                .toList();
            res['pending'] = pending.length;
          } catch (_) {/* rename 失败：行保持 pending 走正常重传 */}
        }
      }

      // 场景1：服务端相册被删对账——账本 done 相册（safe 名）与远端目录集
      // 对比；缺失的 done 行转入 server_missing（不再自动重传），详情页
      // 提示用户"重新备份/从备份移除"
      try {
        final missing = <Map<String, String>>[];
        final doneBuckets = await ledger.doneBucketsByKind();
        for (final kindEntry in doneBuckets.entries) {
          final top = kindEntry.key == 'video' ? '视频' : '照片';
          List items = const [];
          try {
            final r = await dio.get(
                '/api/files/${_encSegs('$top/$username')}');
            items = (r.data['items'] as List? ?? const [])
                .where((e) => (e as Map)['is_dir'] == true)
                .toList();
          } catch (_) {
            items = const []; // 目录拉不到（无权限/暂未创建）→ 本轮不判定
          }
          final serverDirs = items
              .map((e) => (e as Map)['name'] as String? ?? '')
              .toSet();
          for (final b in kindEntry.value) {
            if (serverDirs.contains(safeBucketName(b))) continue;
            await ledger.markBucketServerMissing(kindEntry.key, b);
            missing.add({'kind': kindEntry.key, 'bucket': b});
          }
        }
        if (missing.isNotEmpty) {
          await sp.setString(BackupKeys.missingBuckets, jsonEncode(missing));
        } else {
          await sp.remove(BackupKeys.missingBuckets);
        }
      } catch (_) {/* 对账失败不阻塞本轮备份 */}

      var attempted = 0, failed = 0, doneBytes = 0;
      for (final row in pending) {
        final pause = await _pauseReason(trigger);
        if (pause != null) {
          res['status'] = 'paused';
          res['reason'] = pause;
          break;
        }
        attempted++;
        await sp.setString(BackupKeys.progressCurrent, row.name);
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
        await sp.setInt(BackupKeys.progressDone, attempted - failed);
        await sp.setInt(BackupKeys.progressTotal, pending.length);
        // 防风暴雨：批量失败超阈值 → 冷却 30 分钟（设计 §4 限流）
        if (attempted >= 10 && failed / attempted > 0.3) {
          await sp.setInt(
              BackupKeys.cooldownUntil,
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
        await sp.setInt(BackupKeys.lastDoneAt,
            DateTime.now().millisecondsSinceEpoch);
      }
      // 本轮结束清进度；失败数/待备数按账本现状回写，且感知排除相册：
      // 被排除项永远不会再传，留在"失败/待备"里红字永远消不掉（0.7.1）。
      // 分照片/视频回写供首页分类显示。
      await sp.setInt(BackupKeys.progressDone, 0);
      await sp.setInt(BackupKeys.progressTotal, 0);
      await sp.setString(BackupKeys.progressCurrent, '');
      try {
        final st = await ledger.stats(excluded: cfg.excludedBuckets,
            included: cfg.selectedBuckets);
        await sp.setInt(BackupKeys.failedCount, st.error);
        await sp.setInt(BackupKeys.pendingPhotos, st.pendingPhotos);
        await sp.setInt(BackupKeys.pendingVideos, st.pendingVideos);
      } catch (e) {
        debugPrint('[BackupEngine] pending stats write failed: $e');
      }
      // 回写剩余待备数：Worker 周期触发时为 0 就不拉引擎（省电省内存）；
      // -1 表示未知/配置刚变强制跑。Observer 触发不走此捷径。
      await sp.setInt(
          BackupKeys.pendingCount, (pending.length - (attempted - failed))
              .clamp(0, 1 << 31));
      // 待备字节（首页空间预警用）：本轮成功部分实时扣减
      await sp.setInt(BackupKeys.pendingBytes,
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
        await sp.setInt(BackupKeys.progressDone, 0);
        await sp.setInt(BackupKeys.progressTotal, 0);
        await sp.setString(BackupKeys.progressCurrent, '');
      } catch (e) {
        // 兜底清进度失败会让"备份中 X/Y"卡死在首页，至少留个日志
        debugPrint('[BackupEngine] progress reset failed: $e');
      }
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

      // 协议单实现（07 审查批次 4：init/state 校验/分片补齐/finish 不再本文件手写）
      final client = UploadClient(dio: dio)..sid = row.sessionId;
      var reopens = 0; // 会话重开计数：防 init 正常但 chunk/finish 恒 404 的死循环
      var backoff = 0;
      while (true) {
        if (reopens > 5) {
          await ledger.fail(row.mediaId, '会话反复失效，已暂停');
          return false;
        }
        // 1) 会话：新开 init（含秒传）；有 sid 则 state 探测续传（对账 §4）
        try {
          if (client.sid == null) {
            final instantPath = await client.init(
                destDir: destDir,
                filename: row.name,
                size: size,
                sha256: sha);
            if (instantPath != null) {
              // path 缺失兜底已在 client 内处理（R2-P3）：别把秒传成功误标失败
              await ledger.markDone(row.mediaId, instantPath);
              return true;
            }
            await ledger.setSession(
                row.mediaId, client.sid!, client.chunkSize, client.received.toList());
          } else {
            await client.resume(merge: false); // 服务端 state 是唯一真值（对账 §4）
            await ledger.setReceived(row.mediaId, client.received.length);
          }
        } on FormatException catch (e) {
          // 协议响应校验失败（bad_session/bad_chunk_size）
          await ledger.fail(row.mediaId, '服务端返回异常（${e.message}）');
          return false;
        } on DioException catch (e) {
          if (e.response?.statusCode == 404 && client.sid != null) {
            client.reopen(); // 会话过期（3 天 TTL）→ 重开，断点归零
            reopens++;
            continue;
          }
          if (!await _backoff(ledger, row, e, backoff)) return false;
          backoff++;
          continue;
        }

        // 2) 补缺失分片
        for (final i in client.missingChunks(size)) {
          final bytes = await UploadClient.readChunk(
              f, i * client.chunkSize, client.chunkLength(size, i));
          try {
            await client.postChunk(i, bytes);
            backoff = 0;
            // 账本 received 仅 UI 展示（断点真值在服务端 state），按 1/10 节流
            // 落盘，避免长视频几百片 × 逐片 UPDATE 的写放大
            if (client.received.length % 10 == 0) {
              await ledger.setReceived(row.mediaId, client.received.length);
            }
          } on DioException catch (e) {
            if (e.response?.statusCode == 404) {
              client.reopen();
              reopens++;
              break;
            }
            if (!await _backoff(ledger, row, e, backoff)) return false;
            backoff++;
            break;
          }
        }
        if (client.sid == null) continue;
        // 上面分片循环 break 表示有失败要重走会话/分片补齐
        if (client.missingChunks(size).isNotEmpty) continue;

        // 3) finish（服务端拼装 + sha 必校验）
        try {
          // path 异常兜底：finish 200 即已拼装成功，别因响应字段意外
          // 抛 TypeError 把"服务端已有文件"误标失败（sha 在账本，下轮秒传可纠）
          final remotePath = await client.finish(fallbackName: row.name);
          await ledger.markDone(row.mediaId, remotePath);
          return true;
        } on DioException catch (e) {
          if (e.response?.statusCode == 404) {
            client.reopen();
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


  /// §6：公网地址默认不自动备份，省流量。判定逻辑收口到 data/net.dart
  ///（2026-09-03：公网徽标/上传提醒共用同一事实源）
  static bool _isPublicHost(String host) => isPublicHost(host);
}
