// 传输任务模型：队列 JSON 落盘的载体（杀进程恢复的最小持久集）。
// 协议对齐网页 v3.6：init→chunk→finish + /api/upload/state 断点探测。
import 'dart:io';

enum TransferKind { upload, download }

enum TransferStatus {
  pending,      // 排队
  hashing,      // 上传：本地 sha256 预计算
  uploading,    // 上传：分片传输中
  merging,      // 上传：finish 合并中（服务端拼装大文件耗时）
  downloading,  // 下载：流式落盘
  paused,       // 暂停（用户暂停或进程被杀/登出，见 userPaused）
  done,
  error,
}

class TransferTask {
  final String id;
  final TransferKind kind;
  final String name;        // 展示名（上传=本地文件名；下载=远端文件名）
  final String localPath;   // 上传=源文件；下载=落盘目标绝对路径
  final String? destDir;    // 上传：网盘目标目录（相对 SHARE_ROOT）
  String? remotePath;       // 下载：网盘源路径 / 上传完成后回填
  int size;                 // 上传运行时若发现文件变化会校正
  String? sha256;           // 预计算成功才有（≤200MB）；finish 服务端必校验/自算
  String? sid;              // 上传会话（断点续传的锚）
  int chunkSize;
  final Set<int> received;  // 服务端已确认的块号（分片原子写，可放心信任）
  int receivedBytes;        // 下载：.part 已写字节（加载时以 .part 实长为准）
  TransferStatus status;
  bool userPaused;          // true=用户主动暂停（登录后不自动恢复）；false=中断待恢复
  String? error;
  int attempts;
  final int createdAt;

  TransferTask({
    required this.id,
    required this.kind,
    required this.name,
    required this.localPath,
    this.destDir,
    this.remotePath,
    required this.size,
    this.sha256,
    this.sid,
    this.chunkSize = 5 * 1024 * 1024,
    Set<int>? received,
    this.receivedBytes = 0,
    this.status = TransferStatus.pending,
    this.userPaused = false,
    this.error,
    this.attempts = 0,
    int? createdAt,
  })  : received = received ?? {},
        createdAt = createdAt ?? DateTime.now().millisecondsSinceEpoch;

  /// 已确认字节数（上传=已收分片精确和；下载=.part 实长）
  int get uploadedBytes {
    if (kind == TransferKind.download) return receivedBytes;
    var sum = 0;
    for (final i in received) {
      sum += i * chunkSize < size
          ? (size - i * chunkSize < chunkSize ? size - i * chunkSize : chunkSize)
          : 0;
    }
    return sum.clamp(0, size);
  }

  bool get isActive =>
      status == TransferStatus.pending || // pending 视为可运行；由引擎调度
      status == TransferStatus.hashing ||
      status == TransferStatus.uploading ||
      status == TransferStatus.merging ||
      status == TransferStatus.downloading;

  bool get finished => status == TransferStatus.done || status == TransferStatus.error;

  Map<String, dynamic> toJson() => {
        'id': id,
        'kind': kind == TransferKind.upload ? 'upload' : 'download',
        'name': name,
        'localPath': localPath,
        'destDir': destDir,
        'remotePath': remotePath,
        'size': size,
        'sha256': sha256,
        'sid': sid,
        'chunkSize': chunkSize,
        'received': received.toList(),
        'receivedBytes': receivedBytes,
        'status': status.name,
        'userPaused': userPaused,
        'error': error,
        'attempts': attempts,
        'createdAt': createdAt,
      };

  static TransferTask fromJson(Map<String, dynamic> j) {
    return TransferTask(
      id: j['id'] as String,
      kind: j['kind'] == 'upload' ? TransferKind.upload : TransferKind.download,
      name: j['name'] as String,
      localPath: j['localPath'] as String,
      destDir: j['destDir'] as String?,
      remotePath: j['remotePath'] as String?,
      size: (j['size'] as num).toInt(),
      sha256: j['sha256'] as String?,
      sid: j['sid'] as String?,
      chunkSize: (j['chunkSize'] as num?)?.toInt() ?? 5 * 1024 * 1024,
      received: ((j['received'] as List?) ?? const [])
          .map((e) => (e as num).toInt())
          .toSet(),
      receivedBytes: (j['receivedBytes'] as num?)?.toInt() ?? 0,
      status: TransferStatus.values.firstWhere((s) => s.name == j['status'],
          orElse: () => TransferStatus.paused),
      userPaused: j['userPaused'] as bool? ?? false,
      error: j['error'] as String?,
      attempts: (j['attempts'] as num?)?.toInt() ?? 0,
      createdAt: (j['createdAt'] as num?)?.toInt(),
    );
  }
}

String humanSize(int b) {
  if (b < 1024) return '$b B';
  if (b < 1024 * 1024) return '${(b / 1024).toStringAsFixed(1)} KB';
  if (b < 1024 * 1024 * 1024) return '${(b / 1024 / 1024).toStringAsFixed(1)} MB';
  return '${(b / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
}

String? localFileSize(String path) {
  try {
    final f = File(path);
    return f.existsSync() ? humanSize(f.lengthSync()) : null;
  } catch (_) {
    return null;
  }
}
