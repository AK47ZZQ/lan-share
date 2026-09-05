// 传输中心：上传/下载队列统一展示（进度/速度/剩余时间/暂停/续传/取消/重试）。
// FR-5：应用被杀后重启，未完成队列自动恢复续传（恢复动作在引擎 onLogin）。
// 0.6.0：状态筛选（全部/进行中/已完成/失败）、失败原因点按看全量、
//        全部暂停/全部继续/重试全部失败（AppBar 菜单）。
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:share_plus/share_plus.dart';

import '../data/local/gallery_saver.dart';
import 'widgets/brand.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'widgets/open_local.dart';

enum _Filter { all, active, done, failed }

class TransfersPage extends ConsumerStatefulWidget {
  const TransfersPage({super.key});

  @override
  ConsumerState<TransfersPage> createState() => _TransfersPageState();
}

class _TransfersPageState extends ConsumerState<TransfersPage> {
  _Filter _filter = _Filter.all;

  @override
  Widget build(BuildContext context) {
    final engine = ref.watch(transfersProvider);
    final tasks = engine.tasks;
    final doneCount =
        tasks.where((t) => t.status == TransferStatus.done).length;
    final visible = tasks.where((t) {
      switch (_filter) {
        case _Filter.all:
          return true;
        case _Filter.active:
          return t.isActive;
        case _Filter.done:
          return t.status == TransferStatus.done;
        case _Filter.failed:
          return t.status == TransferStatus.error;
      }
    }).toList();

    return Scaffold(
      appBar: AppBar(
        title: const Text('传输'),
        actions: [
          if (engine.pausedCount > 0)
            TextButton(
                onPressed: engine.resumeAll, child: Text('继续(${engine.pausedCount})')),
          PopupMenuButton<String>(
              onSelected: (v) {
                if (v == 'pauseAll') engine.pauseAll();
                if (v == 'resumeAll') engine.resumeAll();
                if (v == 'retryFailed') engine.retryAllFailed();
              },
              itemBuilder: (_) => [
                    const PopupMenuItem(value: 'pauseAll', child: Text('全部暂停')),
                    const PopupMenuItem(value: 'resumeAll', child: Text('全部继续')),
                    PopupMenuItem(
                        value: 'retryFailed',
                        enabled: engine.failedCount > 0,
                        child: Text('重试全部失败(${engine.failedCount})')),
                  ]),
          if (doneCount > 0)
            TextButton(
                onPressed: engine.clearFinished,
                child: Text('清空已完成($doneCount)')),
        ],
      ),
      body: Column(children: [
        // 状态筛选：一行 chips，紧凑不挤列表
        SizedBox(
          height: 44,
          child: ListView(scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 10),
              children: [
                for (final f in _Filter.values)
                  Padding(
                      padding: const EdgeInsets.only(right: 6),
                      child: ChoiceChip(
                          label: Text(_filterLabel(f, engine)),
                          selected: _filter == f,
                          visualDensity: VisualDensity.compact,
                          onSelected: (_) => setState(() => _filter = f))),
              ]),
        ),
        Expanded(
          child: tasks.isEmpty
              ? const EmptyState(
                  title: '暂无传输任务',
                  hint: '在「文件」页可上传，点按文件可下载；下载保存在 App 专属目录，完成任务点按可打开或存入手机相册')
              : visible.isEmpty
                  ? ListView(children: [
                      const SizedBox(height: 80),
                      Center(
                          child: Text('该状态下没有任务',
                              style: TextStyle(
                                  color: Theme.of(context).colorScheme.outline))),
                    ])
                  : ListView.builder(
                      itemCount: visible.length,
                      itemBuilder: (context, i) =>
                          _Tile(task: visible[i], engine: engine),
                    ),
        ),
      ]),
    );
  }

  String _filterLabel(_Filter f, TransferEngine e) {
    switch (f) {
      case _Filter.all:
        return '全部';
      case _Filter.active:
        return '进行中';
      case _Filter.done:
        return '已完成';
      case _Filter.failed:
        return '失败(${e.failedCount})';
    }
  }
}

class _Tile extends ConsumerWidget {
  final TransferTask task;
  final TransferEngine engine;
  const _Tile({required this.task, required this.engine});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final unknown = task.size <= 0; // 照片原图下载等未知总量的任务：进度转圈
    final total = unknown ? 1 : task.size;
    final done = task.kind == TransferKind.upload
        ? task.uploadedBytes
        : task.receivedBytes;
    final progress = unknown ? null : (done / total).clamp(0.0, 1.0);
    final speed = engine.speedOf(task);
    final remain = total - done;
    final eta = (!unknown && speed > 0)
        ? Duration(seconds: (remain / speed).round())
        : null;

    final (icon, color) = switch (task.status) {
      TransferStatus.done => (Icons.check_circle, Colors.green.shade600),
      TransferStatus.error => (Icons.error_outline,
          Theme.of(context).colorScheme.error),
      TransferStatus.paused => (Icons.pause_circle_outline,
          Theme.of(context).colorScheme.outline),
      _ => (
          task.kind == TransferKind.upload ? Icons.upload : Icons.download,
          Theme.of(context).colorScheme.primary,
        ),
    };
    final statusText = switch (task.status) {
      TransferStatus.pending => '排队中',
      TransferStatus.hashing => '校验计算中…',
      TransferStatus.uploading ||
      TransferStatus.downloading =>
        speed > 0
            ? '${humanSize(speed)}/s · 剩余 ${_etaText(eta)}'
            : '传输中…',
      TransferStatus.merging => '服务端合并中…',
      TransferStatus.paused =>
        task.userPaused ? '已暂停' : '等待恢复…',
      TransferStatus.done => task.kind == TransferKind.upload ? '已上传' : '已下载',
      TransferStatus.error => task.error ?? '失败',
    };

    return Dismissible(
      key: ValueKey(task.id),
      direction: task.status == TransferStatus.done ||
              task.status == TransferStatus.error
          ? DismissDirection.horizontal
          : DismissDirection.none,
      onDismissed: (_) => engine.removeTask(task),
      child: ListTile(
        leading: Icon(icon, color: color),
        title: Text(task.name, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SizedBox(height: 4),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                    value: task.status == TransferStatus.done
                        ? 1
                        : (unknown ? null : progress),
                    minHeight: 4),
              ),
              const SizedBox(height: 4),
              Text(
                unknown
                    ? '${humanSize(done)} · $statusText'
                    : '${humanSize(done)} / ${humanSize(task.size)} · $statusText',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12),
              ),
            ]),
        // 失败任务点按看全量错误（单行截断排障不够用）；
        // 已下载任务点按弹操作单（打开/保存到相册/分享/位置，v0.13）
        onTap: task.status == TransferStatus.error
            ? () => _showError(context)
            : (task.status == TransferStatus.done &&
                    task.kind == TransferKind.download)
                ? () => _doneSheet(context)
                : null,
        trailing: _actions(context),
      ),
    );
  }

  static const _imgExts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp',
      '.heic', '.heif'};
  static const _vidExts = {'.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v'};

  bool get _isMedia {
    final ext = task.name.contains('.')
        ? task.name.substring(task.name.lastIndexOf('.')).toLowerCase()
        : '';
    return _imgExts.contains(ext) || _vidExts.contains(ext);
  }

  bool get _isVideo {
    final ext = task.name.contains('.')
        ? task.name.substring(task.name.lastIndexOf('.')).toLowerCase()
        : '';
    return _vidExts.contains(ext);
  }

  /// 已下载任务操作单（v0.13 用户痛点：下载完点不动、不知道存哪了）。
  /// 打开走 openLocalFile（存在性校验+失败 toast）；媒体可存入系统相册
  /// （落 Pictures|Movies/LAN Share/）；位置明示 App 专属目录。
  void _doneSheet(BuildContext context) {
    showModalBottomSheet<void>(
        context: context,
        builder: (ctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              Padding(
                  padding: const EdgeInsets.all(16),
                  child: Align(
                      alignment: Alignment.centerLeft,
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(task.name,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                    fontWeight: FontWeight.bold)),
                            const SizedBox(height: 4),
                            Text(
                                '${humanSize(task.size)} · 已下载至 App 下载目录',
                                style: const TextStyle(
                                    fontSize: 12, color: Colors.grey)),
                          ]))),
              const Divider(height: 1),
              ListTile(
                  leading: const Icon(Icons.open_in_new),
                  title: const Text('打开'),
                  subtitle: const Text('用手机上能处理此文件的应用打开'),
                  onTap: () {
                    Navigator.pop(ctx);
                    // ignore: discarded_futures
                    openLocalFile(context, task.localPath);
                  }),
              if (_isMedia)
                ListTile(
                    leading: const Icon(Icons.photo_library_outlined),
                    title: const Text('保存到手机相册'),
                    subtitle: const Text('在系统相册的 LAN Share 目录可见'),
                    onTap: () async {
                      Navigator.pop(ctx);
                      final err = await GallerySaver.save(task.localPath,
                          isVideo: _isVideo);
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                            content: Text(err ?? '已保存到相册'),
                            duration: const Duration(seconds: 2)));
                      }
                    }),
              ListTile(
                  leading: const Icon(Icons.share_outlined),
                  title: const Text('分享 / 发送到其他应用'),
                  onTap: () {
                    Navigator.pop(ctx);
                    if (!File(task.localPath).existsSync()) {
                      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                          content: Text('本地文件已被清理，请重新下载'),
                          duration: Duration(seconds: 2)));
                      return;
                    }
                    // ignore: discarded_futures
                    Share.shareXFiles([XFile(task.localPath)],
                        subject: task.name);
                  }),
              ListTile(
                  leading: const Icon(Icons.folder_outlined),
                  title: const Text('查看保存位置'),
                  subtitle: Text(task.localPath,
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  onTap: () async {
                    Navigator.pop(ctx);
                    await showDialog<void>(
                        context: context,
                        builder: (dctx) => AlertDialog(
                                title: const Text('保存位置',
                                    style: TextStyle(fontSize: 16)),
                                content: SelectableText(
                                    'App 专属目录（其他应用不可见）：\n'
                                    '${task.localPath}\n\n'
                                    '另存到手机其他位置请用「保存到手机相册」或「分享」。',
                                    style: const TextStyle(fontSize: 13)),
                                actions: [
                                  TextButton(
                                      onPressed: () => Navigator.pop(dctx),
                                      child: const Text('关闭')),
                                ]));
                  }),
              ListTile(
                  leading: const Icon(Icons.delete_outline),
                  title: const Text('移除记录（不删除文件）',
                      style: TextStyle(color: Colors.red)),
                  subtitle: const Text('仅从列表清掉这条记录，手机里的文件保留',
                      style: TextStyle(fontSize: 11)),
                  onTap: () {
                    Navigator.pop(ctx);
                    engine.removeTask(task);
                  }),
              ListTile(
                  leading: const Icon(Icons.delete_forever_outlined),
                  title: const Text('删除本地文件并移除记录',
                      style: TextStyle(color: Colors.red)),
                  subtitle: const Text('把下载到 App 目录的这份文件从手机删掉',
                      style: TextStyle(fontSize: 11)),
                  onTap: () async {
                    Navigator.pop(ctx);
                    await _deleteLocalFile(context);
                  }),
              const SizedBox(height: 8),
            ])));
  }

  /// 删除本地副本并移除任务记录（v0.14：用户问"删除到底删没删文件"——
  /// 此前移除只清记录不碰文件，语义不明；这里给出明确的文件删除入口，
  /// 服务端原件不受影响）
  Future<void> _deleteLocalFile(BuildContext context) async {
    final exists = File(task.localPath).existsSync();
    final ok = await showDialog<bool>(
        context: context,
        builder: (d) => AlertDialog(
                title: Text('删除「${task.name}」的本地文件？'),
                content: Text(exists
                    ? '只删除下载到手机 App 目录里的这份副本，服务端原件不受影响。'
                    : '本地文件已不存在（可能被系统清理），本次只移除任务记录。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(d, false),
                      child: const Text('取消')),
                  FilledButton(
                      style: FilledButton.styleFrom(
                          backgroundColor:
                              Theme.of(d).colorScheme.error),
                      onPressed: () => Navigator.pop(d, true),
                      child: const Text('删除')),
                ]));
    if (ok != true) return;
    if (exists) {
      try {
        await File(task.localPath).delete();
      } catch (_) {
        // 删不掉（权限/被占用）：仍移除记录，提示用户
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
              content: Text('文件删除失败，已仅移除记录'),
              duration: Duration(seconds: 2)));
        }
      }
    }
    engine.removeTask(task);
  }

  void _showError(BuildContext context) {
    showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
            title: Text('「${task.name}」失败', style: const TextStyle(fontSize: 16)),
            content: SingleChildScrollView(
                child: Text(task.error ?? '未知错误',
                    style: const TextStyle(fontSize: 13))),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(ctx), child: const Text('关闭')),
              FilledButton(
                  onPressed: () {
                    Navigator.pop(ctx);
                    engine.retryAllFailed();
                  },
                  child: const Text('重试全部失败')),
            ]));
  }

  Widget? _actions(BuildContext context) {
    switch (task.status) {
      case TransferStatus.paused:
      case TransferStatus.error:
        return Row(mainAxisSize: MainAxisSize.min, children: [
          IconButton(
              tooltip: '续传',
              icon: const Icon(Icons.play_arrow),
              onPressed: () => engine.resumeTask(task)),
          IconButton(
              tooltip: '移除记录（不删文件）',
              icon: const Icon(Icons.close),
              onPressed: () => engine.removeTask(task)),
        ]);
      case TransferStatus.done:
        return IconButton(
            tooltip: '移除记录（不删文件）',
            icon: const Icon(Icons.close),
            onPressed: () => engine.removeTask(task));
      default:
        return IconButton(
            tooltip: '暂停',
            icon: const Icon(Icons.pause),
            onPressed: () => engine.pauseTask(task));
    }
  }
}

String _etaText(Duration? d) {
  if (d == null) return '--';
  if (d.inHours > 0) return '${d.inHours}时${d.inMinutes % 60}分';
  if (d.inMinutes > 0) return '${d.inMinutes}分${d.inSeconds % 60}秒';
  return '${d.inSeconds}秒';
}
