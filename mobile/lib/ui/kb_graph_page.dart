// 知识图谱页（M-K1，v0.24）：Obsidian 同款力导向星球图。
// · 数据：/api/kb/graph（服务端预布局，坐标随图下发——客户端零仿真冷启动）
// · 渲染：CustomPainter 手绘（主题零缝；LOD：>300 隐藏标签、>800 边降透明）
// · 交互：拖节点=局部增量仿真松手缓动；拖空白=平移；双指=缩放；
//   点按=底部卡片；长按=高亮一跳邻居；搜索=定位+脉冲
// · 幽灵节点点按 → 一键创建（预填文件名进新建流程）
import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../data/api/api_client.dart';
import '../data/kb.dart';
import 'doc_viewer.dart';
import 'notes_page.dart';

class KbGraphPage extends StatefulWidget {
  final String dir; // 笔记根目录（图谱根 = 它，多库天然支持）
  const KbGraphPage({super.key, required this.dir});

  @override
  State<KbGraphPage> createState() => _KbGraphPageState();
}

class _KbGraphPageState extends State<KbGraphPage>
    with SingleTickerProviderStateMixin {
  KbGraph? _graph;
  bool _loading = true;
  String? _error;
  // v0.25 点按=显示内容：选中笔记节点后拉 /api/kb/context（全文头 + abs_path）
  Map<String, dynamic>? _ctx;
  bool _ctxLoading = false;
  final _filter = KbFilter();
  String? _selectedId; // 点按选中的节点
  String? _highlightId; // 长按高亮（一跳邻居）
  String? _pulseId; // 搜索定位脉冲
  // 视口：屏幕 = 图坐标 * scale + offset + 画布中心
  double _scale = 1;
  Offset _offset = Offset.zero;
  // 拖拽/缓动
  String? _dragId;
  Timer? _settleTimer;
  late final AnimationController _pulseCtrl = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 900))
    ..addListener(() => setState(() {}));
  // 标签 TextPainter 缓存（>300 节点不画标签，缓存压力可控）
  final _labelCache = <String, TextPainter>{};

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _settleTimer?.cancel();
    _pulseCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
      _selectedId = null;
      _highlightId = null;
      _labelCache.clear();
    });
    try {
      final dio = await ApiClient.I.dio();
      final r =
          await dio.get('/api/kb/graph', queryParameters: {'path': widget.dir});
      if (!mounted) return;
      final g = KbGraph.fromJson((r.data as Map?) ?? const {});
      setState(() {
        _graph = g;
        _loading = false;
      });
      WidgetsBinding.instance.addPostFrameCallback((_) => _fitViewport());
    } catch (e) {
      if (!mounted) return;
      final s = e.toString();
      setState(() {
        _error = s.length > 140 ? s.substring(0, 140) : s;
        _loading = false;
      });
    }
  }

  /// 视口自适应：可见节点包围盒居中放大到 90% 视野
  void _fitViewport() {
    final vis = _visibleNodes();
    if (vis.isEmpty || !mounted) return;
    var minX = double.infinity, minY = double.infinity;
    var maxX = double.negativeInfinity, maxY = double.negativeInfinity;
    for (final n in vis) {
      minX = math.min(minX, n.x);
      minY = math.min(minY, n.y);
      maxX = math.max(maxX, n.x);
      maxY = math.max(maxY, n.y);
    }
    final size = MediaQuery.sizeOf(context);
    final w = math.max(1.0, maxX - minX), h = math.max(1.0, maxY - minY);
    final s = math.min(1.6, math.min(size.width * 0.9 / w, size.height * 0.85 / h));
    setState(() {
      _scale = s.clamp(0.08, 1.6);
      _offset = Offset(size.width / 2 - (minX + maxX) / 2 * _scale,
          size.height / 2 - (minY + maxY) / 2 * _scale);
    });
  }

  List<KbNode> _visibleNodes() {
    final g = _graph;
    if (g == null) return const [];
    final ids = _filter.matchIds(g);
    return g.nodes.where((n) => ids.contains(n.id)).toList();
  }

  // ── 坐标换算与命中 ─────────────────────────────────────
  Offset _toGraph(Offset screen, Size canvasSize) =>
      ((screen - _offset - Offset(canvasSize.width / 2, canvasSize.height / 2)) /
          _scale);

  KbNode? _hitNode(Offset screen, Size canvasSize) {
    final p = _toGraph(screen, canvasSize);
    KbNode? best;
    var bestD = double.infinity;
    for (final n in _visibleNodes()) {
      final d = (Offset(n.x, n.y) - p).distance;
      final r = _nodeRadius(n) + 12 / _scale; // 命中余量随缩放补偿
      if (d < r && d < bestD) {
        bestD = d;
        best = n;
      }
    }
    return best;
  }

  double _nodeRadius(KbNode n) {
    if (n.ghost) return 7;
    if (n.external) return 6;
    return 8 + math.min(8, n.degree * 1.2);
  }

  Color _nodeColor(KbNode n, ColorScheme cs, Brightness bright) {
    if (n.isFolder) return cs.primary;
    if (n.ghost) return cs.onSurface.withValues(alpha: 0.35);
    if (n.external) return cs.tertiary;
    final hue = categoryHue(n.category);
    return HSVColor.fromAHSV(
            1,
            hue / 360,
            bright == Brightness.dark ? 0.45 : 0.55,
            bright == Brightness.dark ? 0.72 : 0.58)
        .toColor();
  }

  // ── 手势（scale 手势统一仲裁：命中节点=拖节点，否则平移/缩放）──
  Offset? _panStart;
  double? _scaleStart;
  Offset? _scaleFocalStart;

  void _onScaleStart(ScaleStartDetails d) {
    _settleTimer?.cancel();
    _panStart = null;
    _dragId = null;
    if (d.pointerCount == 1) {
      final hit = _hitNode(d.localFocalPoint, MediaQuery.sizeOf(context));
      if (hit != null) {
        _dragId = hit.id;
        HapticFeedback.selectionClick();
      } else {
        _panStart = d.localFocalPoint;
      }
    } else {
      _scaleStart = _scale;
      _scaleFocalStart = d.localFocalPoint;
      _panStart = d.localFocalPoint;
    }
  }

  void _onScaleUpdate(ScaleUpdateDetails d) {
    if (_dragId != null) {
      final g = _graph;
      final n = g?.byId[_dragId];
      if (g != null && n != null) {
        final p = _toGraph(d.localFocalPoint, MediaQuery.sizeOf(context));
        setState(() {
          n.x = p.dx;
          n.y = p.dy;
          // 局部增量：把一小部分位移分摊给邻居（弹簧直觉），只动一跳
          for (final e in g.adj[n.id] ?? const <KbEdge>[]) {
            final other = g.byId[e.dst == n.id ? e.src : e.dst];
            if (other == null || identical(other, n)) continue;
            other.x += (p.dx - other.x) * 0.04;
            other.y += (p.dy - other.y) * 0.04;
          }
        });
      }
      return;
    }
    setState(() {
      if (d.pointerCount >= 2 && _scaleStart != null) {
        final ns = (_scaleStart! * d.scale).clamp(0.05, 4.0);
        // 缩放围绕手势起始焦点：保持焦点下的图坐标不动
        final graphFocal = (_scaleFocalStart! - _offset) / _scale;
        _scale = ns;
        _offset = _scaleFocalStart! - graphFocal * _scale;
      } else if (_panStart != null) {
        _offset += d.localFocalPoint - _panStart!;
        _panStart = d.localFocalPoint;
      }
    });
  }

  void _onScaleEnd(ScaleEndDetails d) {
    _panStart = null;
    _scaleStart = null;
    _scaleFocalStart = null;
    if (_dragId != null) {
      _dragId = null;
      _startSettle();
    }
  }

  /// 松手缓动：约 250ms 内跑几轮局部松弛（只动可见节点），分帧避免卡顿
  void _startSettle() {
    var tick = 0;
    _settleTimer?.cancel();
    _settleTimer = Timer.periodic(const Duration(milliseconds: 16), (t) {
      final g = _graph;
      if (g == null || tick++ > 15 || !mounted) {
        t.cancel();
        return;
      }
      setState(() => relaxStep(_visibleNodes(), g.adj));
    });
  }

  void _onTapUp(TapUpDetails d) {
    final hit = _hitNode(d.localPosition, MediaQuery.sizeOf(context));
    setState(() {
      _selectedId = hit?.id;
      _highlightId = null;
    });
    if (hit != null) _loadCtx(hit);
  }

  /// 选中笔记节点 → 拉取内容头（图谱点按即读，不再只是元数据卡片）。
  /// 幽灵/外链/分类枢纽没有正文可拉，直接清空。
  Future<void> _loadCtx(KbNode n) async {
    if (n.ghost || n.external || n.isFolder || n.path.isEmpty) {
      _ctx = null;
      return;
    }
    setState(() => _ctxLoading = true);
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/kb/context',
          queryParameters: {'path': widget.dir, 'file': n.path});
      if (!mounted) return;
      setState(() {
        _ctx = (r.data as Map).cast<String, dynamic>();
        _ctxLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() { _ctx = null; _ctxLoading = false; });
    }
  }

  void _onLongPressStart(LongPressStartDetails d) {
    final hit = _hitNode(d.localPosition, MediaQuery.sizeOf(context));
    if (hit == null) return;
    HapticFeedback.mediumImpact();
    setState(() => _highlightId = hit.id);
  }

  void _onLongPressEnd(LongPressEndDetails d) {
    setState(() => _highlightId = null);
  }

  void _locate(String id) {
    final n = _graph?.byId[id];
    if (n == null || !mounted) return;
    final size = MediaQuery.sizeOf(context);
    final targetScale = math.max(_scale, 0.8);
    setState(() {
      _pulseId = id;
      _selectedId = id;
      _scale = targetScale;
      _offset = Offset(size.width / 2 - n.x * targetScale,
          size.height / 2 - n.y * targetScale);
    });
    _pulseCtrl.forward(from: 0);
    _loadCtx(n);
  }

  Set<String> _highlightSet() {
    final g = _graph;
    if (g == null) return const {};
    if (_highlightId != null) {
      final ids = {_highlightId!};
      for (final e in g.adj[_highlightId!] ?? const <KbEdge>[]) {
        ids.add(e.src);
        ids.add(e.dst);
      }
      return ids;
    }
    return const {};
  }

  // ── 筛选抽屉 ──────────────────────────────────────────
  void _openFilterSheet() {
    final g = _graph;
    if (g == null) return;
    final cats = g.nodes
        .where((n) => !n.ghost && !n.external && n.category.isNotEmpty)
        .map((n) => n.category)
        .toSet()
        .toList()
      ..sort();
    final tags = <String>{};
    for (final n in g.nodes) {
      tags.addAll(n.tags);
    }
    showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        isScrollControlled: true,
        builder: (bctx) => StatefulBuilder(builder: (bctx, setS) {
              Widget toggle(String label, bool v, void Function() on,
                      {IconData? icon}) =>
                  ListTile(
                      dense: true,
                      leading: Icon(
                          icon ??
                              (v
                                  ? Icons.check_box
                                  : Icons.check_box_outline_blank),
                          size: 20,
                          color:
                              v ? Theme.of(bctx).colorScheme.primary : null),
                      title: Text(label, style: const TextStyle(fontSize: 13.5)),
                      onTap: () {
                        setS(on);
                        setState(() {});
                      });
              Widget chips(List<String> items, String? selected,
                      void Function(String? s) onSel) =>
                  Wrap(spacing: 6, runSpacing: 2, children: [
                    for (final c in items)
                      ChoiceChip(
                          label: Text(c, style: const TextStyle(fontSize: 12)),
                          selected: selected == c,
                          visualDensity: VisualDensity.compact,
                          onSelected: (s) {
                            setS(() => onSel(s ? c : null));
                            setState(() {});
                          }),
                  ]);
              return SafeArea(
                  child: Container(
                      padding: const EdgeInsets.only(bottom: 8),
                      constraints: BoxConstraints(
                          maxHeight:
                              MediaQuery.of(bctx).size.height * 0.7),
                      child: ListView(shrinkWrap: true, children: [
                        Padding(
                            padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
                            child: Text('筛选',
                                style: TextStyle(
                                    fontSize: 13,
                                    fontWeight: FontWeight.bold,
                                    color: Theme.of(bctx).colorScheme.primary))),
                        toggle(
                            '只看幽灵（待写清单）',
                            _filter.ghostsOnly,
                            () => _filter.ghostsOnly = !_filter.ghostsOnly,
                            icon: Icons.help_outline),
                        toggle('隐藏孤岛', _filter.hideOrphans,
                            () => _filter.hideOrphans = !_filter.hideOrphans),
                        toggle('隐藏幽灵', _filter.hideGhosts,
                            () => _filter.hideGhosts = !_filter.hideGhosts),
                        if (tags.isNotEmpty) ...[
                          const Divider(height: 12),
                          Padding(
                              padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
                              child: Text('标签',
                                  style: TextStyle(
                                      fontSize: 13,
                                      fontWeight: FontWeight.bold,
                                      color:
                                          Theme.of(bctx).colorScheme.primary))),
                          chips(tags.toList()..sort(), _filter.tag.isEmpty ? null : _filter.tag,
                              (s) => _filter.tag = s ?? ''),
                        ],
                        if (cats.isNotEmpty) ...[
                          const Divider(height: 12),
                          Padding(
                              padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
                              child: Text('分类',
                                  style: TextStyle(
                                      fontSize: 13,
                                      fontWeight: FontWeight.bold,
                                      color:
                                          Theme.of(bctx).colorScheme.primary))),
                          chips(['全部分类', ...cats],
                              _filter.category ?? '全部分类',
                              (s) => _filter.category =
                                  (s == null || s == '全部分类') ? null : s),
                        ],
                        const Divider(height: 12),
                        TextButton(
                            onPressed: () {
                              setS(() {
                                _filter
                                  ..category = null
                                  ..tag = ''
                                  ..query = ''
                                  ..hideOrphans = false
                                  ..hideGhosts = false
                                  ..ghostsOnly = false;
                              });
                              setState(() {});
                            },
                            child: const Text('重置筛选')),
                      ])));
        }));
  }

  // ── 节点卡片 / ghost 创建 ──────────────────────────────
  void _openNote(KbNode n) {
    if (n.path.isEmpty) return;
    // v0.25：预览优先（带格式渲染），编辑在预览页点编辑笔
    Navigator.push(context, MaterialPageRoute(
        builder: (_) => DocViewerPage(path: n.path, name: n.name))).then((_) {
      if (mounted) _load();
    });
  }

  /// 复制服务器真实路径（v0.25）：来自 /api/kb/context 的 abs_path，
  /// 手机端发给台式 Agent 即可定位同一文件。
  Future<void> _copyPath(KbNode n) async {
    final abs = (_ctx?['note'] as Map?)?['abs_path'] as String?;
    await Clipboard.setData(ClipboardData(
        text: abs ?? '/${n.path.isEmpty ? widget.dir : n.path}'));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已复制服务器路径')));
  }

  Future<void> _createGhost(KbNode n) async {
    final name = n.name.trim();
    if (name.isEmpty || !mounted) return;
    // 文件名合法性兜底：替换文件系统不允许的字符
    final safe = name.replaceAll(RegExp(r'[\\/:*?"<>|]'), '-');
    await openNoteEditor(context,
        dir: widget.dir, name: '$safe.md', isNew: true);
    if (mounted) _load();
  }

  Widget _nodeCard() {
    final g = _graph!;
    final n = g.byId[_selectedId];
    if (n == null) return const SizedBox.shrink();
    final cs = Theme.of(context).colorScheme;
    final isFolder = n.isFolder;

    // 分类枢纽：列出直属成员预览；笔记：正文预览（/api/kb/context）
    List<KbNode> children = const [];
    if (isFolder) {
      children = g.nodes
          .where((m) =>
              !m.isFolder &&
              !m.ghost &&
              (m.category == n.path ||
                  m.category.startsWith('${n.path}/')))
          .toList()
        ..sort((a, b) => a.name.compareTo(b.name));
    }
    final note = (_ctx?['note'] as Map?) ?? const {};
    final content = (note['content'] as String?) ?? '';
    final truncated = note['truncated'] == true;

    final meta = isFolder
        ? '分类 · ${n.count} 篇（含子分类）'
        : '度 ${n.degree}（入 ${n.ind} / 出 ${n.out}）'
            '${n.category.isEmpty ? '' : ' · ${n.category}'}'
            '${n.ghost ? ' · 幽灵（未创建）' : ''}'
            '${n.external ? ' · 库外笔记' : ''}'
            '${(n.orphan && !n.ghost) ? ' · 孤岛' : ''}';

    return Positioned(
        left: 12,
        right: 12,
        bottom: 12,
        child: Card(
            elevation: 4,
            color: cs.surface,
            child: Padding(
                padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
                child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(children: [
                        Container(
                            width: 10,
                            height: 10,
                            decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                color: _nodeColor(
                                    n, cs, Theme.of(context).brightness))),
                        const SizedBox(width: 8),
                        Expanded(
                            child: Text(n.name,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                    fontWeight: FontWeight.w600,
                                    fontSize: 14.5))),
                        IconButton(
                            visualDensity: VisualDensity.compact,
                            icon: const Icon(Icons.close, size: 18),
                            onPressed: () => setState(() => _selectedId = null)),
                      ]),
                      const SizedBox(height: 4),
                      Text(meta,
                          style: TextStyle(
                              fontSize: 11.5, color: cs.onSurfaceVariant)),
                      if (n.tags.isNotEmpty)
                        Padding(
                            padding: const EdgeInsets.only(top: 3),
                            child: Wrap(spacing: 5, children: [
                              for (final t in n.tags)
                                Text('#$t',
                                    style: TextStyle(
                                        fontSize: 11, color: cs.tertiary))
                            ])),
                      // ── 内容区（v0.25 点按即读）──────────────────
                      if (isFolder)
                        Padding(
                            padding: const EdgeInsets.only(top: 4),
                            child: Text(
                                children.isEmpty
                                    ? '空分类——还没有笔记'
                                    : children
                                        .take(4)
                                        .map((c) => '· ${c.name}')
                                        .join('\n'),
                                maxLines: 4,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                    fontSize: 12,
                                    height: 1.4,
                                    color: cs.onSurfaceVariant)))
                      else if (!n.ghost && !n.external)
                        Padding(
                            padding: const EdgeInsets.only(top: 4),
                            child: _ctxLoading
                                ? const Padding(
                                    padding: EdgeInsets.symmetric(vertical: 6),
                                    child: SizedBox(
                                        width: 14,
                                        height: 14,
                                        child: CircularProgressIndicator(
                                            strokeWidth: 2)))
                                : Text(
                                    content.isNotEmpty
                                        ? (truncated ? '$content…' : content)
                                        : (n.summary.isEmpty
                                            ? '（空笔记）'
                                            : n.summary),
                                    maxLines: 4,
                                    overflow: TextOverflow.ellipsis,
                                    style: TextStyle(
                                        fontSize: 12,
                                        height: 1.4,
                                        color: cs.onSurfaceVariant))),
                      const SizedBox(height: 8),
                      Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                        if (isFolder)
                          FilledButton.tonalIcon(
                              onPressed: () {
                                _filter.category = n.path;
                                _filter.tag = '';
                                _filter.query = '';
                                setState(() => _selectedId = null);
                              },
                              icon: const Icon(Icons.filter_alt_outlined,
                                  size: 16),
                              label: const Text('只看该分类'))
                        else if (n.ghost)
                          FilledButton.tonalIcon(
                              onPressed: () => _createGhost(n),
                              icon: const Icon(Icons.add_circle_outline,
                                  size: 18),
                              label: const Text('创建这篇笔记'))
                        else if (n.path.isNotEmpty) ...[
                          FilledButton.tonalIcon(
                              onPressed: () => _copyPath(n),
                              icon: const Icon(Icons.copy_outlined, size: 16),
                              label: const Text('复制路径')),
                          const SizedBox(width: 6),
                          FilledButton.icon(
                              onPressed: () => _openNote(n),
                              icon: const Icon(Icons.open_in_new, size: 16),
                              label: const Text('打开')),
                        ]
                      ]),
                    ]))));
  }

  // ── 搜索 / 重布局 ─────────────────────────────────────
  Future<void> _openSearch() async {
    final g = _graph;
    if (g == null) return;
    final ctrl = TextEditingController(text: _filter.query);
    final q = await showDialog<String>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: const Text('定位节点'),
                content: TextField(
                    controller: ctrl,
                    autofocus: true,
                    decoration: const InputDecoration(
                        hintText: '文件名或别名…', prefixIcon: Icon(Icons.search))),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, ctrl.text.trim()),
                      child: const Text('定位')),
                ]));
    if (q == null || !mounted) return;
    setState(() => _filter.query = q);
    final vis = _visibleNodes();
    if (vis.isNotEmpty) _locate(vis.first.id);
  }

  /// 本地全量重布局（≤500）：黄金角重播 + 客户端松弛收敛
  void _relayout() {
    final g = _graph;
    if (g == null) return;
    final vis = _visibleNodes();
    if (vis.isEmpty || vis.length > 500) return;
    final golden = math.pi * (3.0 - math.sqrt(5.0));
    final sorted = vis.toList()..sort((a, b) => a.id.compareTo(b.id));
    for (var i = 0; i < sorted.length; i++) {
      final rad = 117.0 * 0.9 * math.sqrt(i + 1.0);
      final ang = i * golden;
      sorted[i].x = math.cos(ang) * rad;
      sorted[i].y = math.sin(ang) * rad;
    }
    _fitViewport();
    var tick = 0;
    _settleTimer?.cancel();
    _settleTimer = Timer.periodic(const Duration(milliseconds: 16), (t) {
      if (tick++ > 90 || !mounted) {
        t.cancel();
        return;
      }
      setState(() => relaxStep(vis, g.adj, temp: 20));
    });
  }

  // ── 构建 ──────────────────────────────────────────────
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final g = _graph;
    final vis = _visibleNodes();
    final showLabels = vis.length <= 300;
    final dimEdges = vis.length > 800;
    final hl = _highlightSet();
    return Scaffold(
      appBar: AppBar(
        title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('知识图谱', style: TextStyle(fontSize: 16)),
              if (g != null)
                Text(
                    '${vis.length}/${g.nodes.length} 节点 · ${g.edges.length} 边'
                    '${g.ghosts > 0 ? ' · 幽灵 ${g.ghosts}' : ''}',
                    style:
                        TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
            ]),
        actions: [
          IconButton(
              tooltip: '筛选',
              icon: Badge(
                  isLabelVisible: !_filter.isDefault,
                  smallSize: 7,
                  child: const Icon(Icons.filter_alt_outlined)),
              onPressed: _openFilterSheet),
          IconButton(
              tooltip: '搜索定位',
              icon: const Icon(Icons.search),
              onPressed: _openSearch),
          IconButton(
              tooltip: vis.length > 500 ? '重布局仅支持 ≤500 节点' : '重新布局',
              icon: const Icon(Icons.auto_awesome_motion_outlined),
              onPressed: vis.isEmpty || vis.length > 500 ? null : _relayout),
          IconButton(
              tooltip: '适配视图',
              icon: const Icon(Icons.center_focus_weak),
              onPressed: _fitViewport),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? ListView(children: [
                  const SizedBox(height: 100),
                  Center(
                      child: Text(_error!,
                          textAlign: TextAlign.center,
                          style:
                              const TextStyle(fontSize: 12, color: Colors.grey))),
                  const SizedBox(height: 8),
                  Center(
                      child:
                          TextButton(onPressed: _load, child: const Text('重试'))),
                ])
              : g == null || g.nodes.isEmpty
                  ? _emptyState()
                  : Stack(children: [
                      GestureDetector(
                          onTapUp: _onTapUp,
                          onLongPressStart: _onLongPressStart,
                          onLongPressEnd: _onLongPressEnd,
                          onScaleStart: _onScaleStart,
                          onScaleUpdate: _onScaleUpdate,
                          onScaleEnd: _onScaleEnd,
                          child: Container(
                              color: cs.surface,
                              width: double.infinity,
                              height: double.infinity,
                              child: CustomPaint(
                                  size: MediaQuery.sizeOf(context),
                                  painter: _GraphPainter(
                                    graph: g,
                                    visible: vis,
                                    scale: _scale,
                                    offset: _offset,
                                    canvasSize: MediaQuery.sizeOf(context),
                                    showLabels: showLabels,
                                    dimEdges: dimEdges,
                                    highlight: hl,
                                    selectedId: _selectedId,
                                    pulseId: _pulseId,
                                    pulse: _pulseCtrl.value,
                                    cs: cs,
                                    brightness: Theme.of(context).brightness,
                                    nodeColor: _nodeColor,
                                    nodeRadius: _nodeRadius,
                                    labelCache: _labelCache,
                                  )))),
                      if (_selectedId != null) _nodeCard(),
                    ]),
    );
  }

  Widget _emptyState() {
    return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
      const Icon(Icons.hub_outlined, size: 56, color: Colors.grey),
      const SizedBox(height: 12),
      const Text('这个目录还没有链接', style: TextStyle(fontSize: 15)),
      const SizedBox(height: 6),
      const Padding(
          padding: EdgeInsets.symmetric(horizontal: 36),
          child: Text(
              '在笔记里写 [[另一篇笔记的名字]] 建立第一条连接，图谱就会长出第一个节点关系。',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 12.5, color: Colors.grey))),
      const SizedBox(height: 14),
      FilledButton.tonalIcon(
          onPressed: _load,
          icon: const Icon(Icons.refresh),
          label: const Text('刷新')),
    ]));
  }
}

// ─── 画笔（纯函数式：主题色经构造参数传入，painter 不持 context）───

class _GraphPainter extends CustomPainter {
  final KbGraph graph;
  final List<KbNode> visible;
  final double scale;
  final Offset offset;
  final Size canvasSize;
  final bool showLabels;
  final bool dimEdges;
  final Set<String> highlight;
  final String? selectedId;
  final String? pulseId;
  final double pulse;
  final ColorScheme cs;
  final Brightness brightness;
  final Color Function(KbNode, ColorScheme, Brightness) nodeColor;
  final double Function(KbNode) nodeRadius;
  final Map<String, TextPainter> labelCache;

  _GraphPainter({
    required this.graph,
    required this.visible,
    required this.scale,
    required this.offset,
    required this.canvasSize,
    required this.showLabels,
    required this.dimEdges,
    required this.highlight,
    required this.selectedId,
    required this.pulseId,
    required this.pulse,
    required this.cs,
    required this.brightness,
    required this.nodeColor,
    required this.nodeRadius,
    required this.labelCache,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final visibleIds = visible.map((n) => n.id).toSet();
    final base = cs.onSurface;
    final center = Offset(size.width / 2, size.height / 2);

    canvas.save();
    canvas.translate(center.dx + offset.dx, center.dy + offset.dy);
    canvas.scale(scale);

    // ── 边 ──
    for (final e in graph.edges) {
      final s = graph.byId[e.src], t = graph.byId[e.dst];
      if (s == null || t == null) continue;
      if (!visibleIds.contains(e.src) || !visibleIds.contains(e.dst)) continue;
      final isHl = highlight.contains(e.src) && highlight.contains(e.dst);
      final sel =
          selectedId != null && (e.src == selectedId || e.dst == selectedId);
      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = math.max(0.6, 1.1 / scale)
        ..color = (isHl || sel)
            ? cs.primary.withValues(alpha: isHl ? 0.85 : 0.6)
            : base.withValues(alpha: dimEdges ? 0.08 : 0.12);
      if (isHl || sel) paint.strokeWidth = math.max(1.0, 1.8 / scale);
      canvas.drawLine(Offset(s.x, s.y), Offset(t.x, t.y), paint);
    }

    // ── 节点 ──
    for (final n in visible) {
      final isHl = highlight.isEmpty || highlight.contains(n.id);
      var color = nodeColor(n, cs, brightness);
      if (!isHl) color = color.withValues(alpha: 0.15);
      final r = nodeRadius(n);
      final c = Offset(n.x, n.y);
      if (n.ghost) {
        // 幽灵：虚线空心（待写提示）
        final ring = Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.4 / scale
          ..color = color;
        _dashedCircle(canvas, c, r, ring);
      } else {
        canvas.drawCircle(c, r, Paint()..color = color);
        if (n.external) {
          // 库外笔记：外圈细环区分
          canvas.drawCircle(
              c,
              r + 2.5 / scale,
              Paint()
                ..style = PaintingStyle.stroke
                ..strokeWidth = 1.2 / scale
                ..color = cs.tertiary.withValues(alpha: isHl ? 0.9 : 0.2));
        }
      }
      if (n.id == selectedId) {
        canvas.drawCircle(
            c,
            r + 3.5 / scale,
            Paint()
              ..style = PaintingStyle.stroke
              ..strokeWidth = 2.2 / scale
              ..color = cs.primary);
      }
      if (n.id == pulseId) {
        final t = pulse;
        canvas.drawCircle(
            c,
            r + 4 + t * 26 / scale,
            Paint()
              ..style = PaintingStyle.stroke
              ..strokeWidth = math.max(0.4, 2.5 * (1 - t)) / scale
              ..color = cs.primary.withValues(alpha: (1 - t) * 0.8));
      }
    }

    // ── 标签（LOD：≤300 才画；TextPainter 有缓存）──
    if (showLabels) {
      for (final n in visible) {
        final isHl = highlight.isEmpty || highlight.contains(n.id);
        canvas.save();
        canvas.translate(n.x + nodeRadius(n) + 3, n.y - 6);
        canvas.scale(1 / scale);
        _labelFor(n, isHl).paint(canvas, Offset.zero);
        canvas.restore();
      }
    }
    canvas.restore();
  }

  TextPainter _labelFor(KbNode n, bool isHl) {
    final key = n.id;
    final cached = labelCache[key];
    if (cached != null) {
      return cached;
    }
    final tp = TextPainter(
        text: TextSpan(
            text: n.name,
            style: TextStyle(
                fontSize: 10.5,
                color: cs.onSurface.withValues(
                    alpha: (n.ghost ? 0.5 : 0.75) * (isHl ? 1 : 0.25)))),
        textDirection: TextDirection.ltr,
        maxLines: 1)
      ..layout();
    if (labelCache.length > 600) labelCache.clear();
    labelCache[key] = tp;
    return tp;
  }

  void _dashedCircle(Canvas canvas, Offset c, double r, Paint paint) {
    const segments = 12;
    final rect = Rect.fromCircle(center: c, radius: r);
    for (var i = 0; i < segments; i++) {
      if (i.isEven) {
        final a0 = i * 2 * math.pi / segments;
        final a1 = (i + 1) * 2 * math.pi / segments;
        canvas.drawArc(rect, a0, a1 - a0, false, paint);
      }
    }
  }

  @override
  bool shouldRepaint(_GraphPainter old) =>
      old.scale != scale ||
      old.offset != offset ||
      old.selectedId != selectedId ||
      old.pulseId != pulseId ||
      old.pulse != pulse ||
      old.showLabels != showLabels ||
      old.dimEdges != dimEdges ||
      old.highlight != highlight ||
      old.visible != visible ||
      old.brightness != brightness;
}
