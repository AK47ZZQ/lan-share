// 传输页"本地文件"视图的纯逻辑（v0.16.0）：Downloads 目录的可见文件过滤、
// 孤儿分片判定、文件类型分档。纯函数便于单测；目录遍历留在 UI 层。
import '../data/media_kinds.dart';
import 'transfer_task.dart';

/// 分片下载器的临时文件命名：`<path>.part0` … `<path>.partN`，合并中间态 `<path>.tmp`。
///
/// 必须用**后缀正则**而不是 contains —— `movie.part.mp4` 是用户的正常文件，
/// batch075_test 已钉死它必须可见。
final _partNRe = RegExp(r'\.part\d+$');

/// 是否应从"本地文件"列表隐藏：未完成下载的临时产物。
///
/// 2026-11-18：分片下载上线后这里必须同时认 `.part0..N` 和 `.tmp`。
/// 否则 ≥10MB 的下载一开始，「本地文件」里就冒出 9 个垃圾条目，
/// 用户能点开（内容是坏文件）、能删除（删掉正在写的分片 = 下载失败重来）、
/// 还能被「清空」一键扫掉。
bool isHiddenLocalEntry(String name) =>
    name.endsWith('.part') || name.endsWith('.tmp') || _partNRe.hasMatch(name);

/// 把临时文件名还原成它所属的最终文件路径。
String _baseOfTempFile(String p) {
  var s = p;
  if (s.endsWith('.tmp')) s = s.substring(0, s.length - 4);
  s = s.replaceFirst(RegExp(r'\.part\d*$'), '');
  return s;
}

/// 孤儿分片判定（纯函数，单测锚点）：临时文件只属于下载任务；done 任务的
/// 临时文件已改名落位，残留必是任务被移除后的垃圾，可清理。**error 也受保
/// 护**——失败任务可"重试全部失败"续传，续传起点就是这些文件，清掉等于把
/// 半程下载打回零（二轮审查 P1）。
///
/// 2026-11-18：同时覆盖 `.part` / `.partN` / `.tmp` 三种形态 —— 按「最终
/// 路径」比对，而不是拼字符串精确匹配，否则分片下载的产物永远判不成孤儿，
/// 进程被杀留下的残片（可能等于一整个文件大小）无人回收。
bool isOrphanPart(String partPath, Iterable<TransferTask> tasks) {
  final base = _baseOfTempFile(partPath);
  return !tasks.any((t) =>
      t.kind == TransferKind.download &&
      t.status != TransferStatus.done &&
      t.localPath == base);
}

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
