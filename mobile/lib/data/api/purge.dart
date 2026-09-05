// 服务端"物理删除"两段式封装（v0.16.0）：DELETE /api/files 只是把文件移进
// 服务端回收站（30 天后定时清理），要当场释放磁盘必须再到回收站里把本次
// 产生的条目彻底删除（DELETE /api/trash/{id}）。服务端没有"一步永久删"
// 端点，且彻底删除单条是 admin 之外用户唯一能触达的物理删除。
// 条目匹配用 orig_path + deleted_at 最新：同路径历史上残留的旧回收条目
// 不能误伤（用户可能还指着恢复）。
import 'package:dio/dio.dart' show DioException;

import 'api_client.dart';
import '../local/prefs.dart';

/// 纯函数（单测锚点）：为每个已移入回收站的路径挑出"本次删除产生的"条目
/// id（orig_path 精确匹配、deleted_at 最新）。deleted_at 是服务端
/// isoformat 时间串（含微秒），前缀字典序=时间序；同值并列取 id 更大者。
Map<String, int> matchNewestTrashIds(
    Iterable<String> movedPaths, List<dynamic> trashEntries) {
  final moved = movedPaths.toSet();
  final best = <String, (int, String)>{}; // orig_path → (id, deleted_at)
  for (final raw in trashEntries) {
    if (raw is! Map) continue;
    final e = Map<String, dynamic>.from(raw);
    final orig = (e['orig_path'] ?? '').toString();
    if (!moved.contains(orig)) continue;
    final id = (e['id'] as num?)?.toInt() ?? 0;
    if (id <= 0) continue;
    final at = (e['deleted_at'] ?? '').toString();
    final cur = best[orig];
    // deleted_at 同秒并列（服务端秒级格式化）取 id 更大者：回收站 id 自增，
    // id 大者必是本次新产生的条目——取错会把用户想留的历史旧条目物理删除
    if (cur == null ||
        at.compareTo(cur.$2) > 0 ||
        (at == cur.$2 && id > cur.$1)) {
      best[orig] = (id, at);
    }
  }
  return best.map((k, v) => MapEntry(k, v.$1));
}

class PurgeResult {
  final int total; // 发起的删除数
  final int purged; // 已删除（trashOnly=进回收站即算；否则=已彻底删出磁盘）
  final int staged; // 已进回收站但自动清理失败（可去回收站手动删，不丢数据）
  final int failed; // 连回收站都没进成（权限/网络）
  final int absent; // 目标本就不存在（absentOk 时 404 计入，不算失败）
  final String verb; // '删除' / '彻底删除'
  PurgeResult(
      {required this.total,
      required this.purged,
      required this.staged,
      required this.failed,
      this.absent = 0,
      this.verb = '彻底删除'});

  /// 纯函数（单测锚点）：给用户的结果一句话。
  String get report {
    if (failed == 0 && staged == 0) {
      final extra = absent > 0 ? '（$absent 项目标本就不存在）' : '';
      return '已$verb $purged 项$extra';
    }
    final parts = <String>[];
    if (purged > 0) parts.add('$verb $purged 项');
    if (staged > 0) parts.add('$staged 项滞留回收站（可去回收站手动删除）');
    if (failed > 0) parts.add('$failed 项删除失败');
    if (absent > 0) parts.add('$absent 项目标本就不存在');
    return parts.join('，');
  }
}

String _enc(String p) => p.split('/').map(Uri.encodeComponent).join('/');

/// 删除一组服务端路径：逐个 DELETE /api/files 进回收站；permanent 模式再
/// 按回收站快照差集匹配本次条目、逐个 DELETE /api/trash/{id} 当场释放磁盘。
/// trashOnly=true 是老语义（只进回收站），用于查看器"删除"档。
/// absentOk=true 时 404 计入 absent（"目标本就不存在"），不算失败也不进
/// 回收站匹配——清空备份树等"视频目录可能不存在"的场景用。
/// 防御性设计（对抗审查）：
///  · 进回收站**前**先取回收站快照 id 集，purge 只删快照之外的新条目——
///    另一会话（另一台设备/另一成员）在窗口内删了同路径文件时，不至把
///    对方那条物理销毁；快照取不到则保守放弃彻底删除（文件仍可恢复）。
///  · 换入口/换账号即中止（Bearer 按请求实时取，中途漂移会把删除打到
///    别的会话上；服务端鉴权兜底越权，但 401 会踢无辜的新会话）。
///  · 连续 3 次失败熔断：断网时不吃满 N×超时，文件仍在回收站不丢。
Future<PurgeResult> purgeServerFiles(Iterable<String> paths,
    {bool trashOnly = false, bool absentOk = false}) async {
  final all = paths.toList();
  final dio = await ApiClient.I.dio();
  final addr0 = Prefs.addressNotifier.value;
  final moved = <String>[];
  var failed = 0;
  var absent = 0;
  try {
    Set<int> snapshotIds = {};
    var snapshotOk = false;
    if (!trashOnly) {
      try {
        final s = await dio.get('/api/trash');
        if (s.data is List) {
          for (final raw in s.data as List) {
            if (raw is Map) {
              final id = (raw['id'] as num?)?.toInt() ?? 0;
              if (id > 0) snapshotIds.add(id);
            }
          }
          snapshotOk = true;
        }
      } catch (_) {}
    }
    var consecFails = 0;
    for (final p in all) {
      if (Prefs.addressNotifier.value != addr0) break; // 入口/会话漂移
      try {
        await dio.delete('/api/files/${_enc(p)}');
        moved.add(p);
        consecFails = 0;
      } catch (e) {
        if (absentOk && e is DioException && e.response?.statusCode == 404) {
          absent++; // 目标本就不存在：不进回收站、不算失败
          consecFails = 0;
          continue;
        }
        failed++;
        if (++consecFails >= 3) break; // 熔断：大概率断网/失权，别磨完超时
      }
    }
    if (trashOnly) {
      return PurgeResult(
          total: all.length,
          purged: moved.length,
          staged: 0,
          failed: failed,
          absent: absent,
          verb: '删除');
    }
    var purged = 0, staged = 0;
    if (moved.isNotEmpty && snapshotOk) {
      List<dynamic> entries = const [];
      try {
        final r = await dio.get('/api/trash');
        if (r.data is List) entries = r.data as List;
      } catch (_) {}
      // 只删快照之外的新条目（同路径旧条目=历史遗留或他人并发产生，不碰）
      final ids = matchNewestTrashIds(moved, entries)
          .values
          .where((id) => !snapshotIds.contains(id))
          .toList();
      consecFails = 0;
      for (final id in ids) {
        if (Prefs.addressNotifier.value != addr0) break;
        try {
          await dio.delete('/api/trash/$id');
          purged++;
          consecFails = 0;
        } catch (_) {
          staged++; // 彻底删除失败：文件还在回收站，不丢
          if (++consecFails >= 3) break;
        }
      }
      // 快照之外没匹配到条目（服务端旧版/列表异常）：按滞留算，不谎报
      staged += moved.length - ids.length;
    } else if (moved.isNotEmpty) {
      // 快照没取到（服务端异常）：保守起见不猜条目，全部按滞留报
      staged += moved.length;
    }
    return PurgeResult(
        total: all.length,
        purged: purged,
        staged: staged,
        failed: failed,
        absent: absent);
  } catch (_) {
    // 编排层异常（dio 构造等）：已进回收站的不丢，如实报滞留
    return PurgeResult(
        total: all.length,
        purged: 0,
        staged: moved.length,
        failed: failed,
        absent: absent);
  }
}
