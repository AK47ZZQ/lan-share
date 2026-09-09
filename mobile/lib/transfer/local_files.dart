// 传输页"本地文件"视图的纯逻辑（v0.16.0）：Downloads 目录的可见文件过滤、
// 孤儿分片判定、文件类型分档。纯函数便于单测；目录遍历留在 UI 层。
import '../data/media_kinds.dart';
import 'transfer_task.dart';

/// 是否应从"本地文件"列表隐藏：.part 是未完成下载的分片锚（续传依赖），
/// 本地文件视图只展示完成态文件。
bool isHiddenLocalEntry(String name) => name.endsWith('.part');

/// 孤儿分片判定（纯函数，单测锚点）：.part 只属于下载任务；done 任务的
/// .part 已改名落位，残留必是任务被移除后的垃圾，可清理。**error 也受保
/// 护**——失败任务可"重试全部失败"续传，续传起点就是 .part 文件尾，清掉
/// 等于把半程下载打回零（二轮审查 P1）。
bool isOrphanPart(String partPath, Iterable<TransferTask> tasks) =>
    !tasks.any((t) =>
        t.kind == TransferKind.download &&
        t.status != TransferStatus.done &&
        '${t.localPath}.part' == partPath);

/// 文件类型分档（图标/存相册判断共用）：image/video/audio/archive/doc/other
String localFileKind(String name) {
  final dot = name.lastIndexOf('.');
  final ext = dot >= 0 ? name.substring(dot + 1).toLowerCase() : '';
  // img/vid/aud 单一事实源在 data/media_kinds.dart；archive/doc 是
  // "本地文件分档"专属分类，留在本地
  const img = kImgExts;
  const vid = kVidExts;
  const aud = kAudExts;
  const arc = {'.zip', '.7z', '.rar', '.tar', '.gz', '.apk'};
  const doc = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
      '.txt', '.md'};
  if (img.contains('.$ext')) return 'image';
  if (vid.contains('.$ext')) return 'video';
  if (aud.contains('.$ext')) return 'audio';
  if (arc.contains('.$ext')) return 'archive';
  if (doc.contains('.$ext')) return 'doc';
  return 'other';
}
