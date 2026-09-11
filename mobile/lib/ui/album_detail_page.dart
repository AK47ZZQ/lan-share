// 精选专辑详情 + 选照片（v0.14 文件夹式专辑）。
// 详情页：/api/photos?album=<id> 取条目（服务端做 visible 过滤+按拍摄日
//   排序，直接当展示数据源），长按多选移出/下载；旧月份书签型专辑
//   （有 month 无 items）自动退化为按月浏览。
// 选照片页：复用 /api/photos 分页瀑布流，点选即加（选的是 path，选中的
//   照片不必已在本页加载）；AppBar 日历按钮"按月全选"——拉 facets 月份
//   表，把该月份的全部条目一次性 PUT 进专辑。
// 权限：编辑条目/删专辑 = admin 或创建者（服务端 403 兜底）；guest 只读。
import 'package:flutter/material.dart';

import 'package:waterfall_flow/waterfall_flow.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/album_api.dart';
import '../data/api/errors.dart';
import '../data/api/photos_api.dart';
import '../data/local/prefs.dart';
import '../data/net.dart';
import '../state/session.dart';
import '../transfer/transfer_engine.dart';
import 'photo_models.dart';
import 'photo_viewer.dart';
import 'theme/design_tokens.dart';
import 'theme/responsive.dart';
import 'widgets/auth_image.dart';
import 'widgets/kit.dart';

class AlbumDetailPage extends ConsumerStatefulWidget {
  final String id;
  final String title;
  final String createdBy;
  final String month;     // 旧月份书签型专辑专用
  final bool openPicker;  // 刚创建：进入即弹选照片页
  const AlbumDetailPage(
      {super.key,
      required this.id,
      required this.title,
      this.createdBy = '',
      this.month = '',
      this.openPicker = false});

  @override
  ConsumerState<AlbumDetailPage> createState() => _AlbumDetailPageState();
}

class _AlbumDetailPageState extends ConsumerState<AlbumDetailPage> {
  final _items = <PhotoItem>[];
  final _sel = <String>{};          // 多选：照片 path
  String? _base;
  bool _loading = true;
  bool _monthMode = false;          // 旧月份书签：按 month 过滤展示
  String _title = '';
  String _createdBy = '';
  bool _deleted = false;   // 专辑被删 → pop(true)
  bool _changed = false;   // 加入/移出过条目 → pop(true) 让列表页刷新计数

  @override
  void initState() {
    super.initState();
    _title = widget.title;
    _createdBy = widget.createdBy;
    () async {
      final base = await Prefs.lastAddress();
      if (mounted) setState(() => _base = base);
    }();
    _reload();
    if (widget.openPicker) {
      // ignore: discarded_futures
      WidgetsBinding.instance.addPostFrameCallback((_) => _addPhotos());
    }
  }

  bool get _canEdit {
    final s = ref.read(sessionProvider);
    return s.role == 'admin' ||
        (_createdBy.isNotEmpty && _createdBy == (s.username ?? ''));
  }

  Future<void> _reload() async {
    if (!mounted) return;   // 从选照片页返回时页面可能已退出（审查 P2）
    setState(() => _loading = true);
    try {
      final a = await AlbumApi.detail(widget.id);
      final items = (a['items'] as List? ?? const []).cast<String>();
      _monthMode = items.isEmpty && (a['month'] as String? ?? '').isNotEmpty;
      final list = await (_monthMode
          ? PhotosApi.list(month: a['month'] as String, limit: 500)
          : PhotosApi.list(album: widget.id, limit: 500));
      if (!mounted) return;
      setState(() {
        _title = a['title'] as String? ?? _title;
        _createdBy = a['created_by'] as String? ?? _createdBy;
        _items
          ..clear()
          ..addAll(list
              .map((e) => PhotoItem.fromJson(e as Map<String, dynamic>)));
        _sel.clear();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showToast('加载失败：${errDetail(e)}');
    }
  }

  Future<void> _addPhotos() async {
    final changed = await Navigator.push<bool>(context, MaterialPageRoute(
        builder: (_) => AlbumPickerPage(
            albumId: widget.id,
            albumTitle: _title,
            existing: _items.map((e) => e.path).toSet())));
    if (changed == true) {
      _changed = true;
      _reload();
    }
  }

  Future<void> _downloadSelected() async {
    final engine = TransferEngine.I;
    var n = 0;
    for (final it in _items.where((i) => _sel.contains(i.path))) {
      final t = await engine.enqueueDownload(
          remotePath: it.path, name: it.name, size: 0);
      if (t != null) n++;
    }
    setState(() => _sel.clear());
    showToast('已加入下载队列 $n 张');
  }

  Future<void> _removeSelected() async {
    final paths = _sel.toList();
    final ok = await showDialog<bool>(
        context: context,
        builder: (d) => AlertDialog(
                title: Text('从专辑移出 ${paths.length} 项？'),
                content: const Text('只移出专辑，不删除照片本身。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(d, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(d, true),
                      child: const Text('移出')),
                ]));
    if (ok != true || !mounted) return;
    try {
      await AlbumApi.removeItems(widget.id, paths);
      _changed = true;
      await _reload();
    } catch (e) {
      showToast(errDetail(e));
    }
  }

  Future<void> _deleteAlbum() async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (d) => AlertDialog(
                title: Text('删除专辑「$_title」？'),
                content: const Text('只移除专辑，不影响照片。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(d, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(d, true),
                      child: const Text('删除')),
                ]));
    if (ok != true || !mounted) return;
    try {
      await AlbumApi.delete(widget.id);
      _deleted = true;
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      showToast(errDetail(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final selecting = _sel.isNotEmpty;
    // 内容有变（删专辑/移出/加入）→ pop(true)，照片页据此刷新迷你卡
    // 计数与封面（审查 P3：此前只有删专辑才通知）
    return PopScope(
        canPop: false,
        onPopInvokedWithResult: (didPop, _) {
          if (!didPop) Navigator.pop(context, _deleted || _changed);
        },
        child: Scaffold(
      appBar: AppBar(
        leading: selecting
            ? IconButton(icon: const Icon(Icons.close),
                onPressed: () => setState(() => _sel.clear()))
            : null,
        title: Text(selecting
            ? '已选 ${_sel.length} 项'
            : _title.isEmpty
                ? '精选专辑'
                : _title),
        actions: selecting
            ? [
                TextButton(
                    onPressed: () => setState(() {
                          if (_sel.length == _items.length) {
                            _sel.clear();
                          } else {
                            _sel
                              ..clear()
                              ..addAll(_items.map((e) => e.path));
                          }
                        }),
                    child: Text(_sel.length == _items.length
                        ? '全不选'
                        : '全选')),
              ]
            : [
                if (_canEdit)
                  IconButton(
                      tooltip: '添加照片/视频',
                      icon: const Icon(Icons.add_photo_alternate_outlined),
                      onPressed: _addPhotos),
                PopupMenuButton<String>(
                    onSelected: (v) {
                      if (v == 'delete') _deleteAlbum();
                    },
                    itemBuilder: (_) => [
                          if (_canEdit)
                            const PopupMenuItem(
                                value: 'delete', child: Text('删除专辑')),
                        ]),
              ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _base == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _reload,
                  child: _items.isEmpty
                      ? ListView(children: [
                          const SizedBox(height: 120),
                          Icon(Icons.auto_awesome_mosaic_outlined,
                              size: 44,
                              color: Theme.of(context).colorScheme.outline),
                          const SizedBox(height: Gap.sm),
                          Center(
                              child: Text(_monthMode
                                  ? '这个月还没有照片'
                                  : '专辑还是空的')),
                          const SizedBox(height: Gap.xxs),
                          Center(
                              child: Text(
                                  _monthMode
                                      ? ''
                                      : (_canEdit
                                          ? '点右上角「添加」从照片墙勾选'
                                          : '创建者还未添加照片'),
                                  style: TextStyle(
                                      fontSize: 12,
                                      color: Theme.of(context)
                                          .colorScheme
                                          .outline))),
                        ])
                      : CustomScrollView(slivers: [
                          SliverWaterfallFlow(
                              gridDelegate:
                                  SliverWaterfallFlowDelegateWithFixedCrossAxisCount(
                                      // v1.0：列数随窗口档位自适应（3/4/6）
                                      crossAxisCount: context.gridColumns(),
                                      mainAxisSpacing: 2,
                                      crossAxisSpacing: 2),
                              delegate: SliverChildBuilderDelegate(
                                  addRepaintBoundaries: true, (context, i) {
                                final item = _items[i];
                                final selected = _sel.contains(item.path);
                                return AspectRatio(
                                    aspectRatio: item.aspect,
                                    child: GestureDetector(
                                        onTap: () {
                                          if (selecting) {
                                            setState(() => _sel
                                                    .contains(item.path)
                                                ? _sel.remove(item.path)
                                                : _sel.add(item.path));
                                          } else {
                                            // ignore: discarded_futures
                                            Navigator.push<bool>(context,
                                                MaterialPageRoute(
                                                    builder: (_) =>
                                                        PhotoViewerPage(
                                                            base: _base!,
                                                            items: _items,
                                                            initial: i),
                                                    fullscreenDialog: true)).then(
                                                (deleted) {
                                              // 查看器里删掉的照片回来后立即出列
                                              //（与 photos_page 同语义，R1-P3）
                                              if (deleted == true) _reload();
                                            });
                                          }
                                        },
                                        onLongPress: () => setState(() =>
                                            _sel.contains(item.path)
                                                ? _sel.remove(item.path)
                                                : _sel.add(item.path)),
                                        child: Stack(
                                            fit: StackFit.expand,
                                            children: [
                                              Hero(
                                                  tag:
                                                      'alb-${widget.id}-${item.path}',
                                                  child: AuthImage(
                                                      url:
                                                          '$_base/thumb/${encPath(item.path)}',
                                                      semanticLabel:
                                                          item.isVideo ? '视频' : '照片')),
                                              if (item.isVideo)
                                                Positioned(
                                                    left: 4,
                                                    bottom: 4,
                                                    child: Container(
                                                        padding: const EdgeInsets
                                                            .symmetric(
                                                            horizontal: Gap.xxs,
                                                            vertical: 1),
                                                        decoration: BoxDecoration(
                                                            color:
                                                                Colors.black54,
                                                            borderRadius:
                                                                BorderRadius
                                                                    .circular(
                                                                        4)),
                                                        child: Row(
                                                            mainAxisSize:
                                                                MainAxisSize
                                                                    .min,
                                                            children: [
                                                              const Icon(
                                                                  Icons
                                                                      .play_arrow,
                                                                  size: 12,
                                                                  color: Colors
                                                                      .white),
                                                              if (item
                                                                  .durationLabel
                                                                  .isNotEmpty)
                                                                Text(item
                                                                    .durationLabel,
                                                                    style: const TextStyle(
                                                                        fontSize:
                                                                            10,
                                                                        color: Colors
                                                                            .white)),
                                                            ]))),
                                              if (selecting)
                                                Positioned(
                                                    top: 6,
                                                    right: 6,
                                                    child: Container(
                                                        width: 22,
                                                        height: 22,
                                                        decoration: BoxDecoration(
                                                            shape: BoxShape
                                                                .circle,
                                                            color: selected
                                                                ? Theme.of(context)
                                                                    .colorScheme
                                                                    .primary
                                                                : Colors
                                                                    .black38,
                                                            border: Border.all(
                                                                color: Colors
                                                                    .white,
                                                                width: 1.5)),
                                                        child: selected
                                                            ? const Icon(
                                                                Icons.check,
                                                                size: 15,
                                                                color: Colors
                                                                    .white)
                                                            : null)),
                                            ])));
                              },
                              childCount: _items.length)),
                        ])),
      bottomNavigationBar: selecting
          ? SafeArea(
              child: Container(
                  decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.surface,
                      border: Border(
                          top: BorderSide(
                              color: Theme.of(context).dividerColor,
                              width: 0.5))),
                  child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceAround,
                      children: [
                        TextButton.icon(
                            onPressed: _downloadSelected,
                            icon: const Icon(Icons.download_outlined),
                            label: const Text('下载')),
                        if (_canEdit)
                          TextButton.icon(
                              onPressed: _removeSelected,
                              icon: const Icon(Icons.playlist_remove),
                              label: const Text('移出专辑')),
                      ])))
          : null,
    ));
  }
}

class AlbumPickerPage extends ConsumerStatefulWidget {
  final String albumId;
  final String albumTitle;
  final Set<String> existing;
  const AlbumPickerPage(
      {super.key, required this.albumId, required this.albumTitle,
      required this.existing});

  @override
  ConsumerState<AlbumPickerPage> createState() => _AlbumPickerPageState();
}

class _AlbumPickerPageState extends ConsumerState<AlbumPickerPage> {
  final _items = <PhotoItem>[];
  final _sel = <String>{};      // 选中的 path（不要求已加载进本页）
  final _scroll = ScrollController();
  String? _base;
  bool _loading = false;
  bool _done = false;
  bool _failed = false;   // 网络抖动：置失败态给重试入口，别静默终止分页
  int _offset = 0;
  int _saving = 0;
  int _gen = 0;   // 刷新代际：force 绕过 _loading 后，在途的旧分页响应必须被丢弃，
                  // 否则与刷新请求重复入列、_offset 虚增（R2-P1，照搬 photos_page）
  int get _limit => isPublicAddress(_base ?? '') ? 60 : 120;

  @override
  void initState() {
    super.initState();
    () async {
      final base = await Prefs.lastAddress();
      if (mounted) setState(() => _base = base);
    }();
    _loadMore();
    _scroll.addListener(() {
      if (_scroll.position.extentAfter < 600) _loadMore();
    });
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _loadMore({bool force = false}) async {
    // force（下拉刷新）绕过 _loading guard：分页在途时刷新会把列表清成
    // 假空态且无人再拉（R1-P1，与 photos_page 同修）
    if ((!force && _loading) || _done || !mounted) return;
    final gen = ++_gen;
    setState(() => _loading = true);
    try {
      final list = (await PhotosApi.list(limit: _limit, offset: _offset))
          .map((e) => PhotoItem.fromJson(e as Map<String, dynamic>))
          .toList();
      if (!mounted || gen != _gen) return;   // 已刷新过：丢弃过期响应
      setState(() {
        _items.addAll(list);
        _offset += list.length;
        _done = list.length < _limit;
      });
    } catch (_) {
      if (mounted && gen == _gen) setState(() => _failed = true);
    } finally {
      if (mounted && gen == _gen) setState(() => _loading = false);
    }
  }

  /// 按月全选：facets 月份表 → 选月 → 分页拉全该月条目 → 路径并入选集
  Future<void> _pickMonthAll() async {
    Map<String, int> months;
    try {
      months = await PhotosApi.monthFacets();
    } catch (_) {
      showToast('月份列表加载失败');
      return;
    }
    if (!mounted) return;
    final entries = months.entries.toList()
      ..sort((a, b) => b.key.compareTo(a.key));
    final sel = await showModalBottomSheet<String>(
        context: context,
        builder: (ctx) => SafeArea(
                child: ListView(shrinkWrap: true, children: [
              const ListTile(title: Text('把哪个月的照片全部加入？')),
              for (final e in entries)
                ListTile(
                    leading: const Icon(Icons.calendar_view_month),
                    title: Text(_monthLabel(e.key)),
                    trailing: Text('${e.value} 张',
                        style: TextStyle(
                            fontSize: 12,
                            color: Theme.of(ctx).colorScheme.outline)),
                    onTap: () => Navigator.pop(ctx, e.key)),
            ])));
    if (sel == null || !mounted) return;
    // 分页拉全该月（limit 上限 500，一般一页够；防极端月份死循环封顶 5000）
    var added = 0;
    try {
      for (var off = 0; off < 5000; off += 500) {
        final list =
            await PhotosApi.list(month: sel, limit: 500, offset: off);
        if (!mounted) return;
        setState(() {
          for (final e in list) {
            final p = (e as Map)['path'] as String;
            if (widget.existing.contains(p) || _sel.contains(p)) continue;
            _sel.add(p);
            added++;
          }
        });
        if (list.length < 500) break;
      }
    } catch (e) {
      showToast(errDetail(e));
    }
    showToast(added > 0 ? '已选入 $added 张' : '该月照片都已加入或已在专辑里');
  }

  /// 分批提交（审查 P2：服务端单次上限 500，按月全选大月份会整体 400；
  /// 每批 200 走多次 PUT，逐批幂等去重，中途失败已提交的批次仍生效）
  static const _saveBatch = 200;

  Future<void> _save() async {
    if (_sel.isEmpty || _saving > 0) return;
    if (widget.existing.length + _sel.length > 500) {
      showToast('专辑最多 500 项，当前已有 ${widget.existing.length} 项');
      return;
    }
    final paths = _sel.toList();
    setState(() => _saving = paths.length);
    try {
      for (var i = 0; i < paths.length; i += _saveBatch) {
        await AlbumApi.addItems(widget.albumId,
            paths.sublist(i, (i + _saveBatch).clamp(0, paths.length)));
      }
      if (!mounted) return;
      Navigator.pop(context, true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = 0);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(errDetail(e)), duration: const Duration(seconds: 3)));
    }
  }

  void showToast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(msg), duration: const Duration(seconds: 2)));
  }

  static String _monthLabel(String ym) {
    final p = ym.split('-');
    if (p.length != 2) return ym;
    // tryParse：服务端 facets 的坏月份串在 build 里求值，parse 抛错=红屏
    return '${p[0]} 年 ${int.tryParse(p[1]) ?? p[1]} 月';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('添加到「${widget.albumTitle}」',
            style: const TextStyle(fontSize: 17)),
        actions: [
          IconButton(
              tooltip: '按月全选',
              icon: const Icon(Icons.calendar_month_outlined),
              onPressed: _pickMonthAll),
        ],
      ),
      body: _base == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: () async {
                setState(() {
                  _items.clear();
                  _offset = 0;
                  _done = false;
                });
                await _loadMore(force: true);
              },
              child: _items.isEmpty && !_loading
                  ? ListView(children: const [
                      SizedBox(height: 120),
                      Center(child: Text('没有可添加的照片')),
                    ])
                  : CustomScrollView(controller: _scroll, slivers: [
                      SliverWaterfallFlow(
                          gridDelegate:
                              SliverWaterfallFlowDelegateWithFixedCrossAxisCount(
                                  // v1.0：列数随窗口档位自适应（3/4/6）
                                  crossAxisCount: context.gridColumns(),
                                  mainAxisSpacing: 2,
                                  crossAxisSpacing: 2),
                          delegate: SliverChildBuilderDelegate(
                              addRepaintBoundaries: true, (context, i) {
                            final item = _items[i];
                            final already = widget.existing.contains(item.path);
                            final selected = _sel.contains(item.path);
                            return AspectRatio(
                                aspectRatio: item.aspect,
                                child: GestureDetector(
                                    onTap: already
                                        ? null
                                        : () => setState(() =>
                                                _sel.contains(item.path)
                                                    ? _sel.remove(item.path)
                                                    : _sel.add(item.path)),
                                    child: Stack(fit: StackFit.expand, children: [
                                      Opacity(
                                          opacity: already ? 0.4 : 1,
                                          child: AuthImage(
                                              url:
                                                  '$_base/thumb/${encPath(item.path)}',
                                              semanticLabel:
                                                  item.isVideo ? '视频' : '照片')),
                                      if (already)
                                        const Positioned(
                                            left: 4,
                                            bottom: 4,
                                            child: Chip(
                                                visualDensity:
                                                    VisualDensity.compact,
                                                padding: EdgeInsets.zero,
                                                labelPadding: EdgeInsets
                                                    .symmetric(horizontal: Gap.xxs),
                                                label: Text('已在专辑',
                                                    style: TextStyle(
                                                        fontSize: 9)))),
                                      Positioned(
                                          top: 6,
                                          right: 6,
                                          child: Container(
                                              width: 22,
                                              height: 22,
                                              decoration: BoxDecoration(
                                                  shape: BoxShape.circle,
                                                  color: selected
                                                      ? Theme.of(context)
                                                          .colorScheme
                                                          .primary
                                                      : Colors.black38,
                                                  border: Border.all(
                                                      color: Colors.white,
                                                      width: 1.5)),
                                              child: selected
                                                  ? const Icon(Icons.check,
                                                      size: 15,
                                                      color: Colors.white)
                                                  : null)),
                                    ])));
                          },
                              childCount: _items.length)),
                      if (_failed)
                        SliverToBoxAdapter(
                            child: Padding(
                                padding: const EdgeInsets.all(Gap.sm),
                                child: Center(
                                    child: TextButton(
                                        onPressed: () {
                                          setState(() => _failed = false);
                                          _loadMore();
                                        },
                                        child: const Text('加载失败，点此重试'))))),
                      if (!_done && !_failed)
                        const SliverPadding(
                            padding: EdgeInsets.all(Gap.md),
                            sliver: SliverToBoxAdapter(
                                child: Center(
                                    child: SizedBox(
                                        width: 18,
                                        height: 18,
                                        child: CircularProgressIndicator(
                                            strokeWidth: 2))))),
                    ])),
      bottomNavigationBar: SafeArea(
          child: Container(
              padding: const EdgeInsets.fromLTRB(Gap.md, Gap.xs, Gap.md, Gap.xs),
              decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.surface,
                  border: Border(
                      top: BorderSide(
                          color: Theme.of(context).dividerColor, width: 0.5))),
              child: Row(children: [
                Expanded(
                    child: Text(
                        _saving > 0
                            ? '正在加入 $_saving 项…'
                            : '已选 ${_sel.length} 项',
                        style: const TextStyle(fontSize: 13))),
                FilledButton.icon(
                    onPressed: _sel.isEmpty || _saving > 0 ? null : _save,
                    icon: const Icon(Icons.check, size: 18),
                    label: const Text('加入专辑')),
              ]))),
    );
  }
}
