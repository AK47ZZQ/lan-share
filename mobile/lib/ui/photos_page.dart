// 照片时间线：/api/photos 按拍摄日分组的 3 列网格 + 点击全屏查看。
// 0.4.4：整体改 CustomScrollView + SliverGrid 虚拟化（日期头滚动置顶）——
//       此前"ListView + shrinkWrap 网格"快滑时整组销毁重建，已加载的图划走
//       再回来全变转圈（真机复现）；Sliver 只构建视口内格子，照片量不设限。
// 0.4.3：长按进入多选（下载/删除/分享）；备份完成后 10s 内自动带 refresh=1
// 重拉（服务端放宽扫描节流），照片墙准实时。
// 0.6.0：视频纳入（角标+时长/内嵌播放）、类型筛选（全部/照片/视频）、
//        按月快跳（/api/photos/facets 月份维度，服务端 month= 过滤）。
import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../backup/backup_dest.dart';
import '../data/api/api_client.dart';
import '../data/api/errors.dart';
import '../data/local/prefs.dart';
import '../state/session.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'photo_models.dart';
import 'photo_viewer.dart';
import 'share_create_sheet.dart';
import 'widgets/auth_image.dart';

class PhotosPage extends ConsumerStatefulWidget {
  final VoidCallback? onGoFiles;
  const PhotosPage({super.key, this.onGoFiles});

  @override
  ConsumerState<PhotosPage> createState() => _PhotosPageState();
}

class _PhotosPageState extends ConsumerState<PhotosPage> {
  final _items = <PhotoItem>[];
  final _scroll = ScrollController();
  final _selected = <int>{}; // 多选模式：_items 扁平下标
  String? _base; // 服务器地址（异步取到后再刷网格）
  bool _loading = false;
  bool _failed = false;
  bool _done = false;
  bool _deleting = false;
  int _offset = 0;
  int _gen = 0;                 // 加载代际：刷新后丢弃过期响应，防重复/漂移
  int? _lastBackupDone;         // 备份完成时间戳快照：变了才触发整页刷新
  Timer? _backupWatch;
  Timer? _pulse;                // 服务端脉搏轮询（0.7.2）：任何来源的新照片自动上墙
  Map<String, Object?>? _pulseSnap; // 脉搏快照（total+首张路径），变了才整页强刷
  String _mediaFilter = 'all';  // all | photo | video
  String? _month;               // YYYY-MM 按月快跳（null=全部）
  Map<String, int>? _months;    // facets 缓存（月份选择页用）
  List<_Album>? _albums;        // 精选专辑（0.8.0，管理员维护）
  static const _limit = 120;

  @override
  void initState() {
    super.initState();
    () async {
      final base = await Prefs.lastAddress();
      if (mounted) setState(() => _base = base);
    }();
    _loadMore();
    _loadAlbums();
    _scroll.addListener(() {
      if (_scroll.position.extentAfter < 600) _loadMore();
    });
    // 照片墙准实时：备份引擎完成一轮会写 backup_last_done_at，变化即整页
    // 强刷（服务端 refresh=1 放宽扫描节流到 5s）。轮询只读本地 prefs，开销可忽略
    _backupWatch = Timer.periodic(
        const Duration(seconds: 10), (_) => _checkBackupDone());
    // 0.7.2 起触发面不只本机备份：20s 脉搏 ping 一次 limit=1&refresh=1
    // （服务端 force 独立 5s 节流，响应只有一个缩略项，开销可忽略），
    // total/首张变了即整页强刷——网页、别的手机、文件页、分享接收传的
    // 照片同样自动出现（此前只认备份引擎，家里人网页传 178 项墙面不动）
    _pulse = Timer.periodic(const Duration(seconds: 20), (_) => _pulseCheck());
  }

  /// 服务端脉搏：对时间线做一次"有没有变"的轻探测。快照与上次不同
  /// （新增/删除/最新一张换人）→ 整页强刷。首次只记录不触发。
  Future<void> _pulseCheck() async {
    if (!mounted || _loading) return;
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/photos',
          queryParameters: {'limit': 1, 'refresh': true});
      final total = (r.data['total'] as num?)?.toInt() ?? 0;
      final items = (r.data['items'] as List? ?? const []);
      final first =
          items.isEmpty ? '' : ((items.first as Map)['path'] as String? ?? '');
      final prev = _pulseSnap;
      _pulseSnap = {'total': total, 'first': first};
      if (mounted &&
          prev != null &&
          (prev['total'] != total || prev['first'] != first)) {
        await _refresh();
      }
    } catch (_) {
      // 网络抖动/未登录：静默，下个 tick 再试
    }
  }

  Future<void> _checkBackupDone() async {
    if (!mounted || _loading) return;
    final sp = await SharedPreferences.getInstance();
    // 引擎在独立 headless isolate 写 prefs，UI isolate 的插件缓存不 reload
    // 永远读到旧值——0.4.3 的准实时因此从未生效过
    await sp.reload();
    final v = sp.getInt('backup_last_done_at');
    if (v != _lastBackupDone) {
      await _refresh();
    }
  }

  Future<void> _loadMore({bool force = false}) async {
    if (_loading || _done || !mounted) return;
    final gen = _gen;
    setState(() => _loading = true);
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/photos', queryParameters: {
        'limit': _limit,
        'offset': _offset,
        if (force) 'refresh': true,
        if (_mediaFilter != 'all') 'media_type': _mediaFilter,
        if (_month != null) 'month': _month,
      });
      final list = (r.data['items'] as List)
          .map((e) => PhotoItem.fromJson(e as Map<String, dynamic>))
          .toList();
      if (!mounted || gen != _gen) return;   // 已刷新过：丢弃过期响应
      setState(() {
        _items.addAll(list);
        _offset += list.length;
        _done = list.length < _limit;
        _failed = false;
      });
    } catch (_) {
      if (mounted && gen == _gen) setState(() => _failed = true);
    } finally {
      if (mounted) setState(() => _loading = false);
      final sp = await SharedPreferences.getInstance();
      _lastBackupDone = sp.getInt('backup_last_done_at');
    }
  }

  Future<void> _refresh() async {
    _gen++;
    setState(() {
      _items.clear();
      _selected.clear();
      _offset = 0;
      _done = false;
      _failed = false;
    });
    await _loadMore(force: true);
  }

  void _setFilter(String v) {
    if (v == _mediaFilter) return;
    setState(() => _mediaFilter = v);
    _refresh();
  }

  /// 按月快跳：facets 月份分布 → 选择后走服务端 month= 过滤
  Future<void> _pickMonth() async {
    var months = _months;
    if (months == null) {
      try {
        final dio = await ApiClient.I.dio();
        final r = await dio.get('/api/photos/facets');
        months = {
          for (final m in ((r.data['months'] as List?) ?? const []))
            (m as Map)['month'] as String? ?? '':
                (m['count'] as num?)?.toInt() ?? 0,
        }..removeWhere((k, _) => k.isEmpty);
        _months = months;
      } catch (_) {
        months = {};
      }
    }
    if (!mounted) return;
    final isAdmin = ref.read(sessionProvider).role == 'admin';
    // 闭包内无法提升可空局部变量：先物化成 final 列表再用
    final monthEntries = months.entries.toList();
    final sel = await showModalBottomSheet<String>(
        context: context,
        builder: (ctx) => SafeArea(
            child: monthEntries.isEmpty
                ? ListView(shrinkWrap: true, children: [
                    const Padding(
                        padding: EdgeInsets.all(24),
                        child: Center(child: Text('还没有照片'))),
                    if (isAdmin)
                      ListTile(
                          leading: const Icon(Icons.add),
                          title: const Text('新建精选专辑'),
                          onTap: () => Navigator.pop(ctx, '__add__')),
                  ])
                : ListView(shrinkWrap: true, children: [
                    ListTile(
                        leading: const Icon(Icons.calendar_today_outlined),
                        title: const Text('全部月份'),
                        selected: _month == null,
                        onTap: () => Navigator.pop(ctx, '')),
                    for (final e in monthEntries)
                      ListTile(
                          leading: const Icon(Icons.calendar_view_month),
                          title: Text(_monthLabel(e.key)),
                          trailing: Text('${e.value} 张',
                              style: TextStyle(
                                  fontSize: 12,
                                  color: Theme.of(ctx).colorScheme.outline)),
                          selected: _month == e.key,
                          onTap: () => Navigator.pop(ctx, e.key)),
                    if (isAdmin)
                      ListTile(
                          leading: const Icon(Icons.add),
                          title: const Text('新建精选专辑'),
                          onTap: () => Navigator.pop(ctx, '__add__')),
                  ])));
    if (sel == '__add__') {
      await _addAlbum();
      return;
    }
    if (sel == null || !mounted) return;
    setState(() => _month = sel.isEmpty ? null : sel);
    _refresh();
  }

  List<String> _sortedMonths() {
    final keys = _months?.keys.toList() ?? <String>[];
    keys.sort();
    return keys;
  }

  /// 精选专辑加载（0.8.0）：失败静默（行不显示），不阻塞照片墙
  Future<void> _loadAlbums() async {
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/albums');
      if (!mounted) return;
      setState(() => _albums = (r.data as List)
          .map((e) => _Album.fromJson(e as Map<String, dynamic>))
          .toList());
    } catch (_) {}
  }

  /// 新建精选专辑（admin）：标题 + 从现有月份里选
  Future<void> _addAlbum() async {
    if (!mounted) return;
    final c = TextEditingController();
    final title = await showDialog<String>(
        context: context,
        builder: (d) => AlertDialog(
              title: const Text('新建精选专辑'),
              content: TextField(controller: c,
                  autofocus: true,
                  decoration: const InputDecoration(
                      labelText: '标题（如"暑假合集"）')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(d),
                    child: const Text('取消')),
                FilledButton(
                    onPressed: () => Navigator.pop(d, c.text.trim()),
                    child: const Text('下一步')),
              ]));
    c.dispose();
    if (title == null || title.isEmpty || !mounted) return;
    final month = await showModalBottomSheet<String>(
        context: context,
        builder: (ctx) => SafeArea(
            child: ListView(shrinkWrap: true, children: [
              const ListTile(title: Text('选择专辑月份')),
              for (final m in _sortedMonths())
                ListTile(title: Text(_monthLabel(m)),
                    onTap: () => Navigator.pop(ctx, m)),
            ])));
    if (month == null || !mounted) return;
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/admin/albums',
          data: {'title': title, 'month': month});
      _toast('专辑已创建');
      _loadAlbums();
      setState(() => _months = null); // 破缓存让下次进来重新拉
    } catch (e) {
      _toast(errDetail(e));
    }
  }

  Future<void> _deleteAlbum(_Album a) async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (d) => AlertDialog(
                title: Text('删除专辑「${a.title}」？'),
                content: const Text('只移除专辑条目，不影响照片。'),
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
      final dio = await ApiClient.I.dio();
      await dio.delete('/api/admin/albums/${Uri.encodeComponent(a.id)}');
      if (!mounted) return;
      setState(() =>
          _albums = _albums?.where((x) => x.id != a.id).toList());
      _toast('已删除');
    } catch (e) {
      _toast(errDetail(e));
    }
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(msg), duration: const Duration(seconds: 2)));
  }

  static String _monthLabel(String ym) {
    final p = ym.split('-');
    if (p.length != 2) return ym;
    return '${p[0]} 年 ${int.parse(p[1])} 月';
  }

  @override
  void dispose() {
    _backupWatch?.cancel();
    _pulse?.cancel();
    _scroll.dispose();
    super.dispose();
  }

  /// 手机相册多选上传（0.7.0 起落 照片/<用户名>/手机上传/：选择器不返回
  /// 来源相册，给不了 <相册名> 那一层，与自动备份的相册子目录分开；
  /// 0.4.2~0.6.x 曾落 照片/手机相册/<用户名>/ 与自动备份同夹）
  Future<void> _pickAndUpload() async {
    final res = await FilePicker.platform
        .pickFiles(type: FileType.image, allowMultiple: true);
    if (res == null || res.files.isEmpty) return;
    final username = ref.read(sessionProvider).username ?? '';
    final dest = '照片/$username/$kManualUploadAlbum';
    final engine = TransferEngine.I;
    var n = 0;
    final enqueued = <TransferTask>[];
    for (final f in res.files) {
      if (f.path == null) continue;
      final t = engine.enqueueUpload(
          localPath: f.path!, destDir: dest, name: f.name);
      if (t != null) {
        enqueued.add(t);
        n++;
      }
    }
    // 手动上传也要准实时：任务全部完成即整页强刷，不等 10s 备份轮询
    if (enqueued.isNotEmpty) {
      void watch() {
        if (!enqueued.any((t) => !t.finished)) {
          engine.removeListener(watch);
          if (mounted) _refresh();
        }
      }

      engine.addListener(watch);
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(n > 0 ? '已加入上传队列 $n 张' : '照片已在队列中'),
        duration: const Duration(seconds: 2)));
  }

  // ── 多选模式 ──────────────────────────────────────────────

  void _toggleSelect(int flat) {
    setState(() => _selected.contains(flat)
        ? _selected.remove(flat)
        : _selected.add(flat));
    if (_selected.isEmpty) setState(() {}); // 空选集退出选择模式（AppBar 还原）
  }

  void _exitSelection() => setState(() => _selected.clear());

  Future<void> _downloadSelected() async {
    final engine = TransferEngine.I;
    var n = 0;
    for (final i in _selected) {
      final it = _items[i];
      final t = await engine.enqueueDownload(
          remotePath: it.path, name: it.name, size: 0);
      if (t != null) n++;
    }
    _exitSelection();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(n > 0 ? '已加入下载队列 $n 张' : '任务已在队列中'),
        duration: const Duration(seconds: 2)));
  }

  Future<void> _deleteSelected() async {
    final paths = _selected.map((i) => _items[i]).toList();
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text('删除 ${paths.length} 张照片？'),
                content: const Text('照片将进入服务端回收站，可随时恢复。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, true),
                      child: const Text('删除')),
                ]));
    if (confirmed != true || !mounted) return;
    setState(() => _deleting = true);
    var ok = 0, failed = 0;
    try {
      final dio = await ApiClient.I.dio();
      for (final it in paths) {
        try {
          final r = await dio.delete('/api/files/${encPath(it.path)}');
          if (r.statusCode == 200) {
            ok++;
          } else {
            failed++;
          }
        } catch (_) {
          failed++;
        }
      }
    } catch (_) {
      failed += paths.length - ok;
    }
    if (!mounted) return;
    setState(() => _deleting = false);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(failed > 0 ? '已删 $ok 张，$failed 张失败' : '已删除 $ok 张'),
        duration: const Duration(seconds: 2)));
    await _refresh();
  }

  void _shareSingle() {
    final it = _items[_selected.first];
    _exitSelection();
    // ignore: discarded_futures
    showShareCreate(context, path: it.path, name: it.name);
  }

  // ── 构建 ─────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final selecting = _selected.isNotEmpty;
    return Scaffold(
      appBar: AppBar(
        leading: selecting
            ? IconButton(icon: const Icon(Icons.close), onPressed: _exitSelection)
            : null,
        title: Text(selecting ? '已选 ${_selected.length} 张' : '照片'),
        actions: selecting
            ? [
                TextButton(
                    onPressed: () => setState(() {
                          if (_selected.length == _items.length) {
                            _selected.clear();
                          } else {
                            _selected
                              ..clear()
                              ..addAll(List.generate(_items.length, (i) => i));
                          }
                        }),
                    child: Text(_selected.length == _items.length ? '全不选' : '全选')),
              ]
            : [
                IconButton(
                    tooltip: _month == null
                        ? '按月跳转'
                        : '当前：${_monthLabel(_month!)}',
                    icon: const Icon(Icons.calendar_month_outlined),
                    onPressed: _pickMonth),
                IconButton(
                    tooltip: '上传手机照片',
                    icon: const Icon(Icons.add_photo_alternate_outlined),
                    onPressed: _pickAndUpload),
              ],
      ),
      body: _deleting
          ? const Center(child: CircularProgressIndicator())
          : _base == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _refresh,
                  child: Column(children: [
                    // 精选专辑行（0.8.0 对齐 Web PhotosView）：管理员维护的
                    // 月份合集，点击即按月过滤；长按删除（仅管理员）
                    if (_albums != null && _albums!.isNotEmpty)
                      SizedBox(
                          height: 76,
                          child: ListView(
                              scrollDirection: Axis.horizontal,
                              padding:
                                  const EdgeInsets.fromLTRB(10, 6, 10, 0),
                              children: [
                                for (final a in _albums!)
                                  Padding(
                                      padding:
                                          const EdgeInsets.only(right: 8),
                                      child: GestureDetector(
                                          onLongPress: ref
                                                      .read(sessionProvider)
                                                      .role ==
                                                  'admin'
                                              ? () => _deleteAlbum(a)
                                              : null,
                                          child: InputChip(
                                              visualDensity:
                                                  VisualDensity.compact,
                                              avatar: Icon(
                                                  Icons.auto_awesome,
                                                  size: 16,
                                                  color: Theme.of(context)
                                                      .colorScheme
                                                      .primary),
                                              label: Text(a.title,
                                                  style: TextStyle(
                                                      fontSize: 12,
                                                      fontWeight:
                                                          FontWeight.w500,
                                                      color: Theme.of(context)
                                                          .colorScheme
                                                          .primary)),
                                              selected:
                                                  _month == a.month,
                                              onPressed: () {
                                                setState(() => _month =
                                                    _month == a.month
                                                        ? null
                                                        : a.month);
                                                _refresh();
                                              }))),
                              ])),
                    // 类型筛选 + 月份 chip 行
                    SizedBox(
                        height: 44,
                        child: ListView(scrollDirection: Axis.horizontal,
                            padding: const EdgeInsets.symmetric(horizontal: 10),
                            children: [
                              for (final (v, label) in const [
                                ('all', '全部'),
                                ('photo', '照片'),
                                ('video', '视频'),
                              ])
                                Padding(
                                    padding: const EdgeInsets.only(right: 6),
                                    child: ChoiceChip(
                                        label: Text(label),
                                        selected: _mediaFilter == v,
                                        visualDensity: VisualDensity.compact,
                                        onSelected: (_) => _setFilter(v))),
                              if (_month != null)
                                Padding(
                                    padding: const EdgeInsets.only(left: 2),
                                    child: InputChip(
                                        visualDensity: VisualDensity.compact,
                                        avatar:
                                            const Icon(Icons.calendar_month,
                                                size: 16),
                                        label:
                                            Text(_monthLabel(_month!)),
                                        onDeleted: () {
                                          setState(() => _month = null);
                                          _refresh();
                                        },
                                        onPressed: _pickMonth)),
                            ])),
                    Expanded(child: _gridOrEmpty(selecting)),
                  ])),
      bottomNavigationBar: selecting ? _selectionBar() : null,
    );
  }

  Widget _gridOrEmpty(bool selecting) {
    if (_items.isEmpty && !_loading) {
      return ListView(children: [
        const SizedBox(height: 100),
        Center(child: Text(_failed ? '加载失败' : '这里还是空的')),
        const SizedBox(height: 8),
        Center(child: FilledButton.tonal(
            onPressed: _refresh, child: const Text('重新加载'))),
      ]);
    }
    return _buildTimeline(selecting);
  }

  Widget _selectionBar() {
    final multi = _selected.length > 1;
    // guest 只读：删除/分享必然失败（服务端会拒），不给出入口
    final canManage = ref.read(sessionProvider).role != 'guest';
    return SafeArea(
        child: Container(
            decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surface,
                border: Border(
                    top: BorderSide(
                        color: Theme.of(context).dividerColor, width: 0.5))),
            child: Row(mainAxisAlignment: MainAxisAlignment.spaceAround,
                children: [
                  _op(Icons.download_outlined, '下载', _downloadSelected),
                  if (!multi && canManage)
                    _op(Icons.link_outlined, '分享', _shareSingle),
                  if (canManage)
                    _op(Icons.delete_outline, '删除', _deleteSelected),
                ])));
  }

  Widget _op(IconData icon, String label, VoidCallback onTap) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(10),
          child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Icon(icon, size: 22),
                const SizedBox(height: 2),
                Text(label, style: const TextStyle(fontSize: 12)),
              ]))));

  /// 按拍摄日分组（与网页时间线一致）：**Sliver 虚拟化**时间线——每组一个
  /// SliverMainAxisGroup（置顶日期头 + SliverGrid）。
  /// 0.4.2 曾用"外层 ListView + 内层 shrinkWrap 网格"：shrinkWrap 会一次性
  /// 布局整组所有照片，快滑时整组销毁、回来整组并发重建重拉——已加载的图
  /// 划走再回来全变转圈（真机实测复现）。Sliver 版只构建视口内格子，滚出
  /// 即廉价回收，回来只重建可见的十几个格且直接命中磁盘缓存；照片量不设限。
  Widget _buildTimeline(bool selecting) {
    final groups = <(String, List<int>)>[];
    for (var i = 0; i < _items.length; i++) {
      final day = _items[i].taken;
      if (groups.isNotEmpty && groups.last.$1 == day) {
        groups.last.$2.add(i);
      } else {
        groups.add((day, [i]));
      }
    }
    return CustomScrollView(
        controller: _scroll,
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          for (final (day, flatIdx) in groups) ...[
            // 日期头随内容滚动（不用置顶 SliverAppBar——悬浮文字压在照片上
            // 很难看，真机反馈 0.4.5）
            SliverToBoxAdapter(
                child: Padding(
                    padding: const EdgeInsets.fromLTRB(14, 14, 14, 4),
                    child: Text(_dayLabel(day),
                        style: TextStyle(
                            fontWeight: FontWeight.w600,
                            fontSize: 13,
                            color: Theme.of(context)
                                .colorScheme
                                .onSurfaceVariant)))),
            SliverGrid(
                gridDelegate:
                    const SliverGridDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 3,
                        mainAxisSpacing: 2,
                        crossAxisSpacing: 2),
                delegate: SliverChildBuilderDelegate(
                    addRepaintBoundaries: true,
                    (context, j) {
                      final flat = flatIdx[j];
                      final selected = _selected.contains(flat);
                      final item = _items[flat];
                      return GestureDetector(
                        onTap: selecting
                            ? () => _toggleSelect(flat)
                            : () => _openViewer(flat),
                        onLongPress: () => _toggleSelect(flat),
                        child: Stack(fit: StackFit.expand, children: [
                          AuthImage(
                              url: '$_base/thumb/${encPath(item.path)}'),
                          // 视频角标：▶ + 时长（0.6.0）
                          if (item.isVideo)
                            Positioned(
                                left: 4,
                                bottom: 4,
                                child: Container(
                                    padding: const EdgeInsets.symmetric(
                                        horizontal: 4, vertical: 1),
                                    decoration: BoxDecoration(
                                        color: Colors.black54,
                                        borderRadius:
                                            BorderRadius.circular(4)),
                                    child: Row(mainAxisSize: MainAxisSize.min,
                                        children: [
                                          const Icon(Icons.play_arrow,
                                              size: 12, color: Colors.white),
                                          if (item.durationLabel.isNotEmpty)
                                            Text(item.durationLabel,
                                                style: const TextStyle(
                                                    fontSize: 10,
                                                    color: Colors.white)),
                                        ]))),
                          if (selecting)
                            Positioned(
                                top: 6,
                                right: 6,
                                child: Container(
                                    width: 22,
                                    height: 22,
                                    decoration: BoxDecoration(
                                        shape: BoxShape.circle,
                                        color: selected
                                            ? Theme.of(context).colorScheme.primary
                                            : Colors.black38,
                                        border: Border.all(
                                            color: Colors.white, width: 1.5)),
                                    child: selected
                                        ? const Icon(Icons.check,
                                            size: 15, color: Colors.white)
                                        : null)),
                        ]),
                      );
                    },
                    childCount: flatIdx.length)),
          ],
          if (!_done)
            const SliverPadding(
                padding: EdgeInsets.all(16),
                sliver: SliverToBoxAdapter(
                    child: Center(
                        child: SizedBox(width: 18, height: 18,
                            child: CircularProgressIndicator(
                                strokeWidth: 2))))),
        ]);
  }

  static String _dayLabel(String iso) {
    final d = DateTime.tryParse(iso);
    if (d == null) return iso.isEmpty ? '未知日期' : iso;
    const wk = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
    return '${d.year}年${d.month}月${d.day}日 ${wk[d.weekday - 1]}';
  }

  void _openViewer(int index) {
    // ignore: discarded_futures
    Navigator.push<bool>(context, MaterialPageRoute(
        builder: (_) => PhotoViewerPage(base: _base!, items: _items, initial: index),
        fullscreenDialog: true)).then((deleted) {
      if (deleted == true) _refresh();
    });
  }
}

/// 精选专辑条目（0.8.0）：/api/albums，管理员维护的月份合集
class _Album {
  final String id;
  final String title;
  final String month;
  _Album.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String? ?? '',
        title = j['title'] as String? ?? '',
        month = j['month'] as String? ?? '';
}
