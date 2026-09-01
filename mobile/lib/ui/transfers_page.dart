// 传输中心：上传/下载队列统一展示（进度/速度/剩余时间/暂停/续传/取消/重试）。
// FR-5：应用被杀后重启，未完成队列自动恢复续传（恢复动作在引擎 onLogin）。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';

class TransfersPage extends ConsumerWidget {
  const TransfersPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final engine = ref.watch(transfersProvider);
    final tasks = engine.tasks;
    final doneCount = tasks
        .where((t) => t.status == TransferStatus.done)
        .length;

    return Scaffold(
      appBar: AppBar(
        title: const Text('传输'),
        actions: [
          if (doneCount > 0)
            TextButton(
                onPressed: engine.clearFinished,
                child: Text('清空已完成($doneCount)')),
        ],
      ),
      body: tasks.isEmpty
          ? Center(
              child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                Icon(Icons.swap_vert_circle_outlined,
                    size: 56, color: Theme.of(context).colorScheme.outline),
                const SizedBox(height: 12),
                const Text('暂无传输任务'),
                const SizedBox(height: 4),
                Text('在"文件"页可上传，点按文件可下载',
                    style: TextStyle(
                        fontSize: 12,
                        color: Theme.of(context).colorScheme.outline)),
              ]))
          : ListView.builder(
              itemCount: tasks.length,
              itemBuilder: (context, i) =>
                  _Tile(task: tasks[i], engine: engine),
            ),
    );
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
      TransferStatus.done => task.kind == TransferKind.upload
          ? '已上传${task.remotePath != null ? '' : ''}'
          : '已下载',
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
        trailing: _actions(context),
      ),
    );
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
              tooltip: '移除',
              icon: const Icon(Icons.close),
              onPressed: () => engine.removeTask(task)),
        ]);
      case TransferStatus.done:
        return IconButton(
            tooltip: '移除',
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
