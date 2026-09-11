// 笔记域模型（自 notes_page.dart 拆出，「一文两页面 + State 手写字段映射」整改）：
// Note.fromJson 把原先 _load 里 10 行手写 as-cast 收进模型——/api/notes/list
// 契约只解析这一处，与 data 层模型同规。CatNode 仅树构建期使用，与 Note
// 同文件便于一起理解。两个类原为库内私有，拆库后公共化（本仓先例：
// backup_settings 拆分批）。

class Note {
  final String name;
  final String path;
  final String sizeHuman;
  final String modified;
  // v0.21 分类体系字段（/api/notes/list 下发）
  final String category; // 相对笔记目录的子路径，''=未分类
  final String summary;  // 前 200 字纯文本摘要
  final String ext;      // 含点的扩展名（.md/.txt/.html）
  final double mtime;
  final double ctime;
  // v0.25：服务器真实存储路径（/api/notes/list 下发，与 files 列表同语义）
  // ——"复制路径"用：手机端直接把绝对路径发给台式上的 Agent
  final String? absPath;
  const Note(
      {required this.name, required this.path,
      required this.sizeHuman, required this.modified,
      this.category = '', this.summary = '', this.ext = '',
      this.mtime = 0, this.ctime = 0, this.absPath});

  /// /api/notes/list 行 → Note（v3.50 自 _load 挪入：契约只解析一处）
  factory Note.fromJson(Map m) => Note(
        name: (m['name'] as String?) ?? '',
        path: (m['path'] as String?) ?? '',
        sizeHuman: (m['size_human'] as String?) ?? '',
        modified: (m['modified'] as String?) ?? '',
        category: (m['category'] as String?) ?? '',
        summary: (m['summary'] as String?) ?? '',
        ext: (m['ext'] as String?) ?? '',
        mtime: (m['mtime'] as num?)?.toDouble() ?? 0,
        ctime: (m['ctime'] as num?)?.toDouble() ?? 0,
        absPath: m['abs_path'] as String?);
}

/// 分类树节点（从笔记 category 聚合而来；可变，树构建期使用）
class CatNode {
  final String name;   // 最后一段（显示名）
  final String path;   // 完整分类路径
  final Set<CatNode> children = {};
  List<CatNode> childList = []; // 排序后的子节点（_catTree 里填充）
  CatNode(this.name, this.path);
}
