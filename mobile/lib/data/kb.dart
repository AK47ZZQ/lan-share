// 知识库纯逻辑（M-K1/K2）：图谱模型与过滤、[[ wikilink 解析/补全、
// frontmatter 维护。不依赖网络与 Widget——mobile/test 纯逻辑测试直接覆盖。
import 'dart:math' as math;

/// 图谱节点（/api/kb/graph 的 nodes 项）
class KbNode {
  final String id;
  final String path; // SHARE_ROOT 相对路径；ghost 为空串；folder=分类路径
  final String kind; // ''=笔记 'folder'=分类枢纽（v0.25）
  final String name;
  final String category; // 相对图谱根的父目录，''=根
  final List<String> tags;
  final List<String> aliases;
  final int out;
  final int ind;
  final bool ghost;
  final bool external;
  final bool orphan;
  final String summary;
  final int count; // folder：后代笔记数
  double x;
  double y;
  KbNode({
    required this.id,
    this.kind = '',
    required this.path,
    required this.name,
    required this.category,
    required this.tags,
    required this.aliases,
    required this.out,
    required this.ind,
    required this.ghost,
    required this.external,
    required this.orphan,
    required this.summary,
    this.count = 0,
    required this.x,
    required this.y,
  });

  factory KbNode.fromJson(Map m) => KbNode(
        id: (m['id'] as String?) ?? '',
        path: (m['path'] as String?) ?? '',
        kind: (m['kind'] as String?) ?? '',
        count: (m['count'] as num?)?.toInt() ?? 0,
        name: (m['name'] as String?) ?? '',
        category: (m['category'] as String?) ?? '',
        tags: ((m['tags'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(),
        aliases: ((m['aliases'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(),
        out: (m['out'] as num?)?.toInt() ?? 0,
        ind: (m['ind'] as num?)?.toInt() ?? 0,
        ghost: m['ghost'] == true,
        external: m['external'] == true,
        orphan: m['orphan'] == true,
        summary: (m['summary'] as String?) ?? '',
        x: (m['x'] as num?)?.toDouble() ?? 0,
        y: (m['y'] as num?)?.toDouble() ?? 0,
      );

  bool get isFolder => kind == 'folder';

  int get degree => out + ind;
}

/// 边 [srcId, dstId, weight]
class KbEdge {
  final String src;
  final String dst;
  final int weight;
  KbEdge(this.src, this.dst, this.weight);
}

/// 分类枢纽（v0.25）：图谱响应顶层的分类清单（含空分类）。
class KbFolder {
  final String id; // folder:<分类路径>
  final String path; // 分类路径（相对图谱根）
  final String name; // 末段显示名
  final String parent; // 父分类路径，''=根
  final int count; // 后代笔记数（含子分类）
  final double x, y;
  KbFolder({
    required this.id,
    required this.path,
    required this.name,
    required this.parent,
    required this.count,
    required this.x,
    required this.y,
  });
  factory KbFolder.fromJson(Map m) => KbFolder(
        id: (m['id'] as String?) ?? '',
        path: (m['path'] as String?) ?? '',
        name: (m['name'] as String?) ?? '',
        parent: (m['parent'] as String?) ?? '',
        count: (m['count'] as num?)?.toInt() ?? 0,
        x: (m['x'] as num?)?.toDouble() ?? 0,
        y: (m['y'] as num?)?.toDouble() ?? 0,
      );
}

class KbGraph {
  final List<KbNode> nodes;
  final List<KbEdge> edges;
  final List<KbFolder> folders; // 分类枢纽（v0.25，含空分类）
  final int ghosts;
  final int orphans;
  final Map<String, KbNode> byId;
  final Map<String, List<KbEdge>> adj; // 无向邻接（高亮一跳/拖拽增量用）
  KbGraph({
    required this.nodes,
    required this.edges,
    this.folders = const [],
    required this.ghosts,
    required this.orphans,
  })  : byId = {for (final n in nodes) n.id: n},
        adj = _buildAdj(nodes, edges);

  static Map<String, List<KbEdge>> _buildAdj(
      List<KbNode> nodes, List<KbEdge> edges) {
    final ids = nodes.map((n) => n.id).toSet();
    final adj = <String, List<KbEdge>>{};
    for (final e in edges) {
      if (!ids.contains(e.src) || !ids.contains(e.dst)) continue;
      adj.putIfAbsent(e.src, () => []).add(e);
      if (e.dst != e.src) adj.putIfAbsent(e.dst, () => []).add(e);
    }
    return adj;
  }

  static KbGraph fromJson(Map data) {
    final nodes = ((data['nodes'] as List?) ?? const [])
        .whereType<Map>()
        .map(KbNode.fromJson)
        .toList();
    final edges = ((data['edges'] as List?) ?? const [])
        .whereType<List>()
        .map((e) => KbEdge(
              e[0].toString(),
              e[1].toString(),
              (e.length > 2 ? (e[2] as num?)?.toInt() : 1) ?? 1,
            ))
        .toList();
    final folders = ((data['folders'] as List?) ?? const [])
        .whereType<Map>()
        .map(KbFolder.fromJson)
        .toList();
    final stats = (data['stats'] as Map?) ?? const {};
    return KbGraph(
      nodes: nodes,
      edges: edges,
      folders: folders,
      ghosts: (stats['ghosts'] as num?)?.toInt() ?? 0,
      orphans: (stats['orphans'] as num?)?.toInt() ?? 0,
    );
  }
}

/// 图谱过滤（筛选抽屉）：分类/标签/搜索 + 孤岛/幽灵开关 + 只看幽灵
class KbFilter {
  String? category; // null=全部 ''=未分类 其他=分类路径（前缀匹配）
  String tag;
  String query;
  bool hideOrphans;
  bool hideGhosts;
  bool ghostsOnly;
  KbFilter({
    this.category,
    this.tag = '',
    this.query = '',
    this.hideOrphans = false,
    this.hideGhosts = false,
    this.ghostsOnly = false,
  });

  bool get isDefault =>
      category == null &&
      tag.isEmpty &&
      query.isEmpty &&
      !hideOrphans &&
      !hideGhosts &&
      !ghostsOnly;

  Set<String> matchIds(KbGraph g) {
    final q = query.trim().toLowerCase();
    bool base(KbNode n) {
      if (ghostsOnly && !n.ghost) return false;
      if (!ghostsOnly) {
        if (hideGhosts && n.ghost) return false;
        if (hideOrphans && n.orphan) return false;
      }
      if (category != null) {
        if (category!.isEmpty) {
          if (n.category.isNotEmpty) return false;
        } else if (n.category != category! &&
            !n.category.startsWith('$category/')) {
          return false;
        }
      }
      if (tag.isNotEmpty && !n.tags.contains(tag)) return false;
      if (q.isNotEmpty &&
          !n.name.toLowerCase().contains(q) &&
          !n.aliases.any((a) => a.toLowerCase().contains(q))) {
        return false;
      }
      return true;
    }

    return g.nodes.where(base).map((n) => n.id).toSet();
  }
}

/// `[[` 补全上下文：光标前文本里最近的未闭合 `[[partial`
class CompletionContext {
  final int start; // '[' of '[[' 在文本中的下标
  final String partial;
  CompletionContext(this.start, this.partial);
}

/// 找光标前的补全上下文；不在补全态返回 null。
CompletionContext? completionContextAt(String text, int caret) {
  if (caret < 0 || caret > text.length) return null;
  final before = text.substring(0, caret);
  // 已闭合（光标前存在 `]]` 且位于最近 `[[` 之后）→ 不在补全态
  final open = before.lastIndexOf('[[');
  if (open < 0) return null;
  final closed = before.lastIndexOf(']]');
  if (closed > open) return null;
  final partial = before.substring(open + 2);
  // [[ 里再出现 [[ → 取后者
  final inner = partial.lastIndexOf('[[');
  if (inner >= 0) {
    return CompletionContext(open + 2 + inner + 1, partial.substring(inner + 2));
  }
  if (partial.contains('\n') || partial.contains(']')) return null;
  return CompletionContext(open, partial);
}

/// 用补全项替换 `[[partial` → `[[choice]]`，返回 (新文本, 新光标位)
(String, int) applyCompletion(
    String text, CompletionContext ctx, String choice) {
  final end = ctx.start + 2 + ctx.partial.length;
  final newText =
      '${text.substring(0, ctx.start)}[[$choice]]${text.substring(end)}';
  return (newText, ctx.start + 2 + choice.length + 2);
}

// ─── wikilink 预处理（预览渲染用）─────────────────────────

final _wikilinkRe = RegExp(r'\[\[([^\[\]|#\n]+?)(?:#[^\[\]\n]*)?(?:\|([^\[\]\n]+?))?\]\]');

/// 把 `[[目标]]`/`[[目标|别名]]` 转成 `kblink:` 伪协议 md 链接，
/// 配合 flutter_markdown 的 onTapLink 实现可点双链。
String preprocessWikilinks(String md) {
  return md.replaceAllMapped(_wikilinkRe, (m) {
    final target = (m.group(1) ?? '').trim();
    var label = (m.group(2) ?? target).trim();
    if (target.isEmpty) return m.group(0) ?? '';
    label = label.replaceAll(RegExp(r'[\[\]]'), ''); // ] 会截断 md 链接文本
    return '[${label.isEmpty ? target : label}](kblink:${Uri.encodeComponent(target)})';
  });
}

/// kblink: href → 目标文本
String? kblinkTarget(String? href) {
  if (href == null || !href.startsWith('kblink:')) return null;
  return Uri.decodeComponent(href.substring(7));
}

// ─── 目标解析（客户端镜像服务端顺序，供点链跳转）─────────────

/// names = /api/kb/names 的数据。返回 SHARE_ROOT 相对路径；未命中 null。
/// 与服务端 _resolve_target 严格镜像（红队 V5）：无斜杠目标走
/// aliases → 文件名（撞名：同目录 > 最短路径 > casefold 字典序）；
/// 带路径目标只走相对路径（相对当前目录 → 相对根），不回退文件名/别名，
/// 未命中一律 null——保证"客户端跳得到 = 服务端图谱有这条边"。
String? resolveLinkTarget(
    List<Map<String, dynamic>> names, String target, String currentDir) {
  final t = target.trim().replaceAll('\\', '/');
  if (t.isEmpty) return null;
  String stemOf(String name) {
    final i = name.lastIndexOf('.');
    return i > 0 ? name.substring(0, i) : name;
  }

  if (!t.contains('/')) {
    final cf = t.toLowerCase();
    final aliasHits = <Map<String, dynamic>>[];
    for (final n in names) {
      final p = (n['path'] as String?) ?? '';
      if (p.isEmpty) continue;
      for (final al in ((n['aliases'] as List?) ?? const [])) {
        if (al.toString().trim().toLowerCase() == cf) {
          aliasHits.add(n);
          break;
        }
      }
    }
    final a = _pickCand(aliasHits, currentDir);
    if (a != null) return a['path'] as String;
    final byName = names
        .where((n) =>
            ((n['path'] as String?) ?? '').isNotEmpty &&
            stemOf((n['name'] as String?) ?? '').toLowerCase() == cf)
        .toList();
    final hit = _pickCand(byName, currentDir);
    if (hit != null) return hit['path'] as String;
    return null; // ghost：服务端同样只对无斜杠目标建幽灵
  }
  for (final cand in [
    currentDir.isEmpty ? t : '$currentDir/$t',
    t,
  ]) {
    for (final n in names) {
      if ((n['path'] as String?)?.toLowerCase() == cand.toLowerCase()) {
        return n['path'] as String;
      }
    }
  }
  return null;
}

String _dirOf(String path) {
  final i = path.lastIndexOf('/');
  return i < 0 ? '' : path.substring(0, i);
}

/// 撞名裁决（与 server._pick_entry 同式）：同目录 > 最短相对路径 > casefold 字典序
Map<String, dynamic>? _pickCand(
    List<Map<String, dynamic>> cands, String dir) {
  Map<String, dynamic>? best;
  String bp = '';
  for (final c in cands) {
    final p = (c['path'] as String?) ?? '';
    if (p.isEmpty) continue;
    if (best == null) {
      best = c;
      bp = p;
      continue;
    }
    final cSame = _dirOf(p) == dir;
    final bSame = _dirOf(bp) == dir;
    if (cSame != bSame) {
      if (cSame) {
        best = c;
        bp = p;
      }
      continue;
    }
    if (p.length != bp.length) {
      if (p.length < bp.length) {
        best = c;
        bp = p;
      }
      continue;
    }
    if (p.toLowerCase().compareTo(bp.toLowerCase()) < 0) {
      best = c;
      bp = p;
    }
  }
  return best;
}

// ─── frontmatter 自动维护（M-K2）─────────────────────────

/// 保存 md 时维护 created/updated：无 frontmatter 则补一个；created 缺失才写
/// （用户手写字段不覆盖）；updated 每次保存更新。逐行操作保留用户格式与注释。
String touchFrontmatter(String text, {DateTime? now}) {
  final t = now ?? DateTime.now();
  final mm = t.month.toString().padLeft(2, '0');
  final dd = t.day.toString().padLeft(2, '0');
  final today = '${t.year}-$mm-$dd';
  if (!text.startsWith('---')) {
    return '---\ncreated: $today\nupdated: $today\n---\n\n$text';
  }
  final lines = text.split('\n');
  var close = -1;
  for (var i = 1; i < lines.length && i < 200; i++) {
    final l = lines[i].trim();
    if (l == '---' || l == '...') {
      close = i;
      break;
    }
  }
  if (close < 1) return text; // 没有闭合线 → 不动（避免吃掉正文）
  var hasCreated = false;
  var hasUpdated = false;
  for (var i = 1; i < close; i++) {
    if (lines[i].startsWith('created:')) {
      hasCreated = true;
    } else if (lines[i].startsWith('updated:')) {
      lines[i] = 'updated: $today';
      hasUpdated = true;
    }
  }
  if (!hasCreated) lines.insert(1, 'created: $today');
  if (!hasUpdated) lines.insert(1, 'updated: $today');
  return lines.join('\n');
}

// ─── 分类色（双端同规则的确定性色相）───────────────────────

/// 与 web KbView 同式：hue = 类别字符串哈希 % 360
double categoryHue(String category) {
  var h = 0;
  for (final c in category.codeUnits) {
    h = (h * 31 + c) & 0x7fffffff;
  }
  return (h % 3600) / 10.0;
}

/// 简易弹簧-斥力松弛：拖拽松手后的缓动收敛（只动 visible 节点，O(邻居) 级）
void relaxStep(List<KbNode> nodes, Map<String, List<KbEdge>> adj,
    {double spacing = 130, double temp = 12}) {
  final idx = <String, int>{for (var i = 0; i < nodes.length; i++) nodes[i].id: i};
  final rnd = math.Random(7);
  for (final n in nodes) {
    double dx = 0, dy = 0;
    final edges = adj[n.id] ?? const <KbEdge>[];
    for (final e in edges) {
      final other = e.dst == n.id ? e.src : e.dst;
      final oi = idx[other];
      if (oi == null) continue; // 邻接可能指向被过滤掉的节点
      final o = nodes[oi];
      if (identical(o, n)) continue;
      var ddx = n.x - o.x, ddy = n.y - o.y;
      var d = math.sqrt(ddx * ddx + ddy * ddy);
      if (d == 0) {
        ddx = rnd.nextDouble() - 0.5;
        ddy = rnd.nextDouble() - 0.5;
        d = 1;
      }
      final f = (d - spacing) / d * 0.06;
      dx += ddx * f;
      dy += ddy * f;
    }
    n.x -= dx.clamp(-temp, temp);
    n.y -= dy.clamp(-temp, temp);
  }
}
