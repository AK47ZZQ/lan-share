// 服务端相册管理页（9.6-P1-10 场景2）：列出 照片|视频/<用户名>/ 下的相册目录，
// 逐个"删除服务端副本"（DELETE /api/files → 服务端回收站，30 天可恢复）；
// 删除成功后同步清理备份账本中该相册的全部记录。
// v1.0（2026-09-11）从 backup_settings_page.dart 拆出——此前单文件 1523 行
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../backup/backup_ledger.dart';
import '../data/api/files_api.dart';
import '../state/session.dart';

/// 服务端相册管理页（9.6-P1-10 场景2）：列出 照片|视频/<我的用户名>/ 下的
/// 相册目录，逐个"删除服务端副本"（DELETE /api/files → 服务端回收站，
/// 30 天可恢复）；删除成功后同步清理备份账本中该相册的全部记录。
class ServerAlbumsPage extends ConsumerStatefulWidget {
  const ServerAlbumsPage({super.key});

  @override
  ConsumerState<ServerAlbumsPage> createState() => _ServerAlbumsPageState();
}

class _ServerAlbumsPageState extends ConsumerState<ServerAlbumsPage> {
  List<_ServerAlbum> _albums = const [];
  bool _loading = true;
  String? _error;
  String _deleting = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final username =
          ref.read(sessionProvider).username ?? '';
      if (username.isEmpty || username.contains('/') ||
          username.contains(r'\') || username.contains('..')) {
        throw Exception('用户名异常');
      }
      final out = <_ServerAlbum>[];
      for (final e in const [('image', '照片'), ('video', '视频')]) {
        try {
          final seg = '${e.$2}/${Uri.encodeComponent(username)}';
          final items = await FilesApi.listDir(seg);
          for (final it in items) {
            final m = it as Map;
            if (m['is_dir'] != true) continue;
            out.add(_ServerAlbum(
                kind: e.$1,
                bucket: m['name'] as String? ?? '',
                path: m['path'] as String? ?? '',
                count: 0));
          }
        } catch (_) {/* 该类型目录不存在（还没备过视频等）→ 跳过 */}
      }
      if (!mounted) return;
      setState(() {
        _albums = out;
        _loading = false;
        _error = null;
      });
    } catch (err) {
      if (!mounted) return;
      setState(() {
        _error = err.toString();
        _loading = false;
      });
    }
  }

  Future<void> _delete(_ServerAlbum a) async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text('删除服务端副本「${a.bucket}」？'),
                content: const Text('该相册在网盘里的全部内容将进服务端回收站'
                    '（30 天内可恢复）；本机照片不受影响，备份记录同步清理。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      style: FilledButton.styleFrom(
                          backgroundColor:
                              Theme.of(dctx).colorScheme.error),
                      onPressed: () => Navigator.pop(dctx, true),
                      child: const Text('删除')),
                ]));
    if (ok != true || !mounted) return;
    setState(() => _deleting = a.path);
    try {
      await FilesApi.delete(a.path);
      // 账本同步清理：该相册的全部备份记录（done/missing 等）一并移除，
      // 否则"已备份 N 项"与实际不符、重开备份又会秒传重建目录
      try {
        await BackupLedger.I.dropBucketAll(a.kind, a.bucket);
      } catch (_) {}
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('已删除「${a.bucket}」（可在回收站恢复）'),
          duration: const Duration(seconds: 2)));
      _load();
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('删除失败，请稍后重试')));
    } finally {
      if (mounted) setState(() => _deleting = '');
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
        appBar: AppBar(title: const Text('管理服务端相册')),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ListView(children: [
                    const SizedBox(height: 100),
                    Center(
                        child: Text(_error!,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                                fontSize: 12, color: Colors.grey))),
                    Center(
                        child: TextButton(
                            onPressed: _load, child: const Text('重试'))),
                  ])
                : _albums.isEmpty
                    ? ListView(children: const [
                        SizedBox(height: 160),
                        Center(
                            child: Text('服务端还没有备份的相册',
                                style: TextStyle(color: Colors.grey))),
                      ])
                    : RefreshIndicator(
                        onRefresh: _load,
                        child: ListView(padding: const EdgeInsets.all(8), children: [
                          Padding(
                              padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                              child: Text('删除进服务端回收站，30 天内可恢复；本机照片不受影响。',
                                  style: TextStyle(
                                      fontSize: 11.5,
                                      color: cs.onSurfaceVariant))),
                          for (final a in _albums)
                            ListTile(
                                leading: Icon(a.kind == 'video'
                                    ? Icons.videocam_outlined
                                    : Icons.photo_outlined),
                                title: Text(a.bucket),
                                subtitle: Text(a.kind == 'video' ? '视频相册' : '照片相册',
                                    style: const TextStyle(fontSize: 11.5)),
                                trailing: _deleting == a.path
                                    ? const SizedBox(
                                        width: 18,
                                        height: 18,
                                        child: CircularProgressIndicator(
                                            strokeWidth: 2))
                                    : TextButton(
                                        onPressed: () => _delete(a),
                                        child: Text('删除',
                                            style: TextStyle(
                                                color: cs.error))),
                              ),
                        ])));
  }
}

class _ServerAlbum {
  final String kind;
  final String bucket;
  final String path;
  final int count;
  const _ServerAlbum(
      {required this.kind, required this.bucket, required this.path, required this.count});
}
