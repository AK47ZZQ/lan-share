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
import 'dart:ui' show ImageFilter;

import 'package:waterfall_flow/waterfall_flow.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../backup/backup_dest.dart';
import '../data/api/album_api.dart';
import '../data/api/errors.dart';
import '../data/api/photos_api.dart';
import '../data/api/purge.dart';
import '../data/local/prefs.dart';
import '../data/media_kinds.dart';
import '../data/net.dart';
import '../state/session.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'album_detail_page.dart';
import 'photo_models.dart';
import 'photo_viewer.dart';
import 'share_create_sheet.dart';
import 'theme/design_tokens.dart';
import 'theme/responsive.dart';
import 'widgets/auth_image.dart';
import 'widgets/prompt_dialog.dart';
import 'widgets/kit.dart';

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
  // v0.20 排序维度：false=按拍摄时间（时间线默认）；true=按入库时间
  // （二维码/网页刚传的照片按 EXIF 落历史月份"看不到新图"——入库序
  // 新上传永远在最前，平板视图不分组直接平铺）
  bool _byAdded = false;
  Map<String, int>? _months;    // facets 缓存（月份选择页用）
  List<_Album>? _albums;        // 精选专辑（0.8.0，管理员维护）
  // 自定义展示范围（20260913211718-b58a）：空 = 全部文件夹；非空 = 只展示
  // 命中前缀的照片（服务端 include_dirs 下推，分页/脉搏口径都由服务端保证）
  Set<String> _includedDirs = {};
  // 每页条数：局域网 120 / 公网 60（弱网首屏少拉半屏，滚动突发更小，v0.10.3）
  int get _limit => isPublicAddress(_base ?? '') ? 60 : 120;

  @override
  void initState() {
    super.initState();
    () async {
      final base = await Prefs.lastAddress();
      if (mounted) setState(() => _base = base);
    }();
    // 会话内切换主/备隧道时同步 _base（对抗审查 P1：本页在 IndexedStack
    // 里不重建，缩略图/预览器会永久打旧地址）
    Prefs.addressNotifier.addListener(_onAddressChanged);
    _loadMore();
    _loadAlbums();
    _ensureMonths();   // 年月快滚轴数据源（v0.11），失败静默
    _restoreWallDirs();
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
    // 生命周期闸门（R1-P2-2，对齐 AutoSwitch）：退后台/锁屏后不再打网络
    if (WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) return;
    if (!mounted || _loading) return;
    try {
      final snap = await PhotosApi.probe();
      final prev = _pulseSnap;
      _pulseSnap = {'total': snap.total, 'first': snap.firstPath};
      if (mounted &&
          prev != null &&
          (prev['total'] != snap.total || prev['first'] != snap.firstPath)) {
        await _refresh();
      }
    } catch (_) {
      // 网络抖动/未登录：静默，下个 tick 再试
    }
  }

  Future<void> _checkBackupDone() async {
    // 生命周期闸门（R1-P2-2）：退后台不轮询
    if (WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) return;
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
    // force（下拉/切筛选后的整墙重拉）绕过 _loading guard：否则分页请求
    // 在途时点筛选会把照片墙清成假空态且无人再拉（R1-P1）；gen 校验
    // 已保证旧响应被丢弃，不会串页
    if ((!force && _loading) || _done || !mounted) return;
    final gen = _gen;
    // _limit 依赖异步赋值的 _base：必须在此刻物化，请求与 _done 判断
    // 用同一个值——否则首屏请求发出时 _base 还没到位被判成公网（limit=60），
    // 响应回来后 _done 又按局域网档（120）比较，60<120 恒真 → 翻页永久关闭
    final limit = _limit;
    setState(() => _loading = true);
    try {
      final list = (await PhotosApi.list(
              limit: limit,
              offset: _offset,
              refresh: force,
              mediaType: _mediaFilter == 'all' ? null : _mediaFilter,
              month: _month,
              byAdded: _byAdded,
              includeDirs:
                  _includedDirs.isEmpty ? null : _includedDirs.join(',')))
          .map((e) => PhotoItem.fromJson(e as Map<String, dynamic>))
          .toList();
      if (!mounted || gen != _gen) return;   // 已刷新过：丢弃过期响应
      setState(() {
        _items.addAll(list);
        _offset += list.length;
        _done = list.length < limit;
        _failed = false;
      });
    } catch (_) {
      if (mounted && gen == _gen) setState(() => _failed = true);
    } finally {
      // 过期请求（gen 已换代）不碰 _loading：force 请求仍在途时提前放行
      // 会让滚动监听再起一个同 gen 请求，两者都从同一 offset 追加 → 重复
      if (mounted && gen == _gen) setState(() => _loading = false);
      final sp = await SharedPreferences.getInstance();
      _lastBackupDone = sp.getInt('backup_last_done_at');
    }
  }

  Future<void> _refresh() async {
    if (!mounted) return;   // R1-P2-4：彻底删除/查看器回调路径可能在 dispose 后触发
    _gen++;
    setState(() {
      _items.clear();
      _selected.clear();
      _offset = 0;
      _done = false;
      _failed = false;
    });
    await _loadMore(force: true);
    // 月份分布随刷新重拉（v0.14）：此前 _months 首拉后永久缓存，备份/
    // 上传/删除/别的设备传新照片，时间栏数目不动、新月份也永远不进
    // 快滚轴和按月列表——用户感知"照片墙不同步"的第二根源
    _months = null;
    await _ensureMonths();
    if (mounted) setState(() {});   // 快滚轴/侧栏月份数目刷新
  }

  void _setFilter(String v) {
    if (v == _mediaFilter) return;
    setState(() => _mediaFilter = v);
    _refresh();
  }

  /// facets 月份分布拉取（快滚轴/按月快跳共用）；失败返回空表不炸 UI
  Future<Map<String, int>> _ensureMonths() async {
    var months = _months;
    if (months != null) return months;
    try {
      months = await PhotosApi.monthFacets();
      _months = months;
      return months;
    } catch (_) {
      return {};
    }
  }

  /// 自定义展示范围（20260913211718-b58a）：文件夹多选 → 服务端 include_dirs
  /// 前缀下推。选择持久化在本地（photo_wall_dirs），设备各自记忆互不影响。
  Future<void> _restoreWallDirs() async {
    final sp = await SharedPreferences.getInstance();
    final saved = sp.getStringList('photo_wall_dirs');
    if (saved != null && saved.isNotEmpty && mounted) {
      setState(() => _includedDirs = {...saved});
      await _refresh();
    }
  }

  Future<void> _persistWallDirs() async {
    final sp = await SharedPreferences.getInstance();
    await sp.setStringList('photo_wall_dirs', _includedDirs.toList());
  }

  Future<void> _pickFolders() async {
    List<({String dir, int count})> folders;
    try {
      folders = await PhotosApi.folders();
    } catch (_) {
      showToast('文件夹列表加载失败');
      return;
    }
    if (!mounted || folders.isEmpty) {
      showToast('还没有照片');
      return;
    }
    final sel = Set<String>.from(_includedDirs);
    await showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        isScrollControlled: true,
        builder: (bctx) => SafeArea(
                child: StatefulBuilder(
                    builder: (bctx, setB) => Container(
                        constraints: BoxConstraints(
                            maxHeight:
                                MediaQuery.of(bctx).size.height * 0.7),
                        padding: const EdgeInsets.fromLTRB(
                            Gap.md, Gap.xxs, Gap.md, Gap.sm),
                        child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Row(children: [
                                const Expanded(
                                    child: Text('照片墙展示范围',
                                        style: TextStyle(
                                            fontSize: 15,
                                            fontWeight: FontWeight.bold))),
                                if (sel.isNotEmpty)
                                  TextButton(
                                      onPressed: () => setB(() => sel.clear()),
                                      child: const Text('清空')),
                              ]),
                              Align(
                                  alignment: Alignment.centerLeft,
                                  child: Text('不选任何文件夹 = 展示全部',
                                      style: TextStyle(
                                          fontSize: 11.5,
                                          color: Theme.of(bctx)
                                              .colorScheme
                                              .onSurfaceVariant))),
                              const SizedBox(height: Gap.xxs),
                              Flexible(
                                  child: ListView(shrinkWrap: true, children: [
                                for (final f in folders)
                                  CheckboxListTile(
                                      dense: true,
                                      controlAffinity:
                                          ListTileControlAffinity.leading,
                                      title: Text(f.dir,
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis),
                                      subtitle: Text('${f.count} 项'),
                                      value: sel.contains(f.dir),
                                      onChanged: (v) => setB(() => v == true
                                          ? sel.add(f.dir)
                                          : sel.remove(f.dir))),
                              ])),
                              const SizedBox(height: Gap.xxs),
                              FilledButton(
                                  onPressed: () => Navigator.pop(bctx),
                                  child: const Text('完成')),
                            ])))));
    if (!mounted) return;
    final same = sel.length == _includedDirs.length &&
        sel.every(_includedDirs.contains);
    if (same) return;
    setState(() => _includedDirs = sel);
    await _persistWallDirs();
    await _refresh();
  }

  /// 按月快跳：facets 月份分布 → 选择后走服务端 month= 过滤
  Future<void> _pickMonth() async {
    final months = await _ensureMonths();
    if (!mounted) return;
    // 闭包内无法提升可空局部变量：先物化成 final 列表再用
    final monthEntries = months.entries.toList();
    final sel = await showModalBottomSheet<String>(
        context: context,
        builder: (ctx) => SafeArea(
            child: monthEntries.isEmpty
                ? ListView(shrinkWrap: true, children: const [
                    Padding(
                        padding: EdgeInsets.all(Gap.xl),
                        child: Center(child: Text('还没有照片'))),
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
                  ])));
    if (sel == null || !mounted) return;
    setState(() => _month = sel.isEmpty ? null : sel);
    _refresh();
  }

  /// 精选专辑加载（0.8.0）：失败静默（行不显示），不阻塞照片墙
  Future<void> _loadAlbums() async {
    try {
      final list = await AlbumApi.list();
      if (!mounted) return;
      setState(() => _albums = list
          .map((e) => _Album.fromJson(e as Map<String, dynamic>))
          .toList());
    } catch (_) {}
  }

  /// 新建精选专辑（v0.14 文件夹式）：起名即建空专辑 → 直接进专辑页
  /// 弹选照片（勾选或按月全选）
  Future<void> _addAlbum() async {
    if (!mounted) return;
    // controller 生命周期由 PromptDialog 自持（v0.13：旧写法在退场动画中
    // dispose 触发 framework 断言，点"下一步"必崩红屏——E2E 实测）
    final title = await showDialog<String>(
        context: context,
        builder: (d) => const PromptDialog(
              title: '新建精选专辑',
              label: '标题（如"暑假合集"）',
              hint: '暑假合集',
            ));
    if (title == null || title.trim().isEmpty || !mounted) return;
    try {
      final id = await AlbumApi.create(title.trim());
      if (!mounted) return;
      if (id == null || id.isEmpty) {
        showToast('创建失败');
        _loadAlbums();
        return;
      }
      await Navigator.push<bool>(context, MaterialPageRoute(
          builder: (_) => AlbumDetailPage(
              id: id,
              title: title.trim(),
              createdBy: ref.read(sessionProvider).username ?? '',
              openPicker: true)));
      if (mounted) _loadAlbums();   // 添加了照片/直接返回都刷新列表计数
    } catch (e) {
      showToast(errDetail(e));
    }
  }

  /// 打开专辑详情；返回 true 表示内容有变（删专辑/移出/加入），刷新列表
  Future<void> _openAlbum(_Album a) async {
    final changed = await Navigator.push<bool>(context, MaterialPageRoute(
        builder: (_) => AlbumDetailPage(
            id: a.id,
            title: a.title,
            createdBy: a.createdBy,
            month: a.month)));
    if (changed == true && mounted) _loadAlbums();
  }

  /// 专辑长按菜单：创建者信息 + 删除（admin 或创建者本人；服务端兜底 403）
  Future<void> _albumSheet(_Album a) async {
    final me = ref.read(sessionProvider).username ?? '';
    final canDelete =
        ref.read(sessionProvider).role == 'admin' || a.createdBy == me;
    await showModalBottomSheet<void>(
        context: context,
        builder: (ctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              Padding(
                  padding: const EdgeInsets.all(Gap.md),
                  child: Align(
                      alignment: Alignment.centerLeft,
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(a.title,
                                style: const TextStyle(
                                    fontWeight: FontWeight.bold)),
                            const SizedBox(height: Gap.xxs),
                            Text(
                                '${a.count > 0 ? "${a.count} 项 · " : ""}'
                                '${a.createdBy.isEmpty ? "" : "由 ${a.createdBy} 创建"}',
                                style: const TextStyle(
                                    fontSize: 12,
                                    color: Colors.grey)),
                          ]))),
              const Divider(height: 1),
              ListTile(
                  leading: const Icon(Icons.photo_library_outlined),
                  title: const Text('查看专辑'),
                  onTap: () {
                    Navigator.pop(ctx);
                    _openAlbum(a);
                  }),
              if (canDelete)
                ListTile(
                    leading: const Icon(Icons.delete_outline),
                    title: const Text('删除专辑'),
                    iconColor: Theme.of(context).colorScheme.error,
                    textColor: Theme.of(context).colorScheme.error,
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      _deleteAlbum(a);
                    }),
              const SizedBox(height: Gap.xs),
            ])));
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
      // v0.11 端点：admin 或创建者可删（403 由服务端兜底）
      await AlbumApi.delete(a.id);
      if (!mounted) return;
      setState(() =>
          _albums = _albums?.where((x) => x.id != a.id).toList());
      showToast('已删除');
    } catch (e) {
      showToast(errDetail(e));
    }
  }

  static String _monthLabel(String ym) {
    final p = ym.split('-');
    if (p.length != 2) return ym;
    return '${p[0]} 年 ${int.tryParse(p[1]) ?? p[1]} 月';
  }

  void _onAddressChanged() {
    final b = Prefs.addressNotifier.value;
    if (mounted && b.isNotEmpty && b != _base) setState(() => _base = b);
  }

  @override
  void dispose() {
    Prefs.addressNotifier.removeListener(_onAddressChanged);
    _backupWatch?.cancel();
    _pulse?.cancel();
    _scroll.dispose();
    super.dispose();
  }

  /// 手机相册多选上传（0.7.0 起落 照片|视频/<用户名>/手机上传/：选择器
  /// 不返回来源相册，给不了 <相册名> 那一层，与自动备份的相册子目录分开；
  /// 0.14 起支持视频，按扩展名分流到 视频/<用户名>/手机上传/）
  // 单一事实源 data/media_kinds.dart（07 审查 §4.1-2；FilePicker 的
  // extension 口径不带点，用无点变体）
  static final _vidExts = kVidExtsNoDot;

  Future<void> _pickAndUpload() async {
    final res = await FilePicker.platform
        .pickFiles(type: FileType.media, allowMultiple: true);
    if (res == null || res.files.isEmpty) return;
    final username = ref.read(sessionProvider).username ?? '';
    final engine = TransferEngine.I;
    var n = 0;
    final enqueued = <TransferTask>[];
    for (final f in res.files) {
      if (f.path == null) continue;
      final ext = f.extension?.toLowerCase() ?? '';
      final isVideo = _vidExts.contains(ext);
      final t = engine.enqueueUpload(
          localPath: f.path!,
          destDir:
              '${isVideo ? "视频" : "照片"}/$username/$kManualUploadAlbum',
          name: f.name);
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
      for (final it in paths) {
        try {
          if (await PhotosApi.deleteToTrash(it.path)) {
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

  /// 彻底删除（v0.16.0 用户需求：物理删除，当场释放服务端磁盘）：
  /// 两段式——DELETE /api/files 进回收站，再按 orig_path 匹配本次产生的
  /// 回收条目逐个 DELETE /api/trash/{id}。手机上的原照片不受影响。
  Future<void> _purgeSelected() async {
    final paths = _selected.map((i) => _items[i]).toList();
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text('彻底删除 ${paths.length} 张照片？'),
                content: const Text(
                    '不经过回收站，直接从服务端磁盘删除，无法恢复。\n\n'
                    '手机上已备份的原照片不受影响；删除后服务端空间立即释放。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      style: FilledButton.styleFrom(
                          backgroundColor:
                              Theme.of(dctx).colorScheme.error),
                      onPressed: () => Navigator.pop(dctx, true),
                      child: const Text('彻底删除')),
                ]));
    if (confirmed != true || !mounted) return;
    setState(() => _deleting = true);
    try {
      final res = await purgeServerFiles(paths.map((it) => it.path));
      if (!mounted) return;
      _exitSelection();
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(res.report), duration: const Duration(seconds: 3)));
    } finally {
      // 编排层任何异常都不能把底栏永久藏掉
      if (mounted) setState(() => _deleting = false);
    }
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
                    tooltip: _byAdded
                        ? '当前：按入库时间（点此切回拍摄时间）'
                        : '刚上传的照片不在最前？切换为按入库时间',
                    icon: Icon(_byAdded
                        ? Icons.history
                        : Icons.fiber_new_outlined),
                    onPressed: () {
                      setState(() => _byAdded = !_byAdded);
                      _refresh();
                    }),
                IconButton(
                    tooltip: _month == null
                        ? '按月跳转'
                        : '当前：${_monthLabel(_month!)}',
                    icon: const Icon(Icons.calendar_month_outlined),
                    onPressed: _byAdded ? null : _pickMonth),
                IconButton(
                    tooltip: '新建精选专辑',
                    icon: const Icon(Icons.auto_awesome_mosaic_outlined),
                    onPressed: ref.read(sessionProvider).role == 'guest'
                        ? null
                        : () => _addAlbum()),
                IconButton(
                    tooltip: '上传照片/视频',
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
                    // 精选专辑行（v0.14 文件夹式）：封面缩略图迷你卡，
                    // 点按进专辑详情（勾选添加/移出/下载），长按删除入口
                    if (_albums != null && _albums!.isNotEmpty)
                      SizedBox(
                          height: 92,
                          child: ListView(
                              scrollDirection: Axis.horizontal,
                              padding:
                                  const EdgeInsets.fromLTRB(10, 6, 10, 0),
                              children: [
                                for (final a in _albums!)
                                  Padding(
                                      padding:
                                          const EdgeInsets.only(right: Gap.xs),
                                      child: GestureDetector(
                                          onTap: () => _openAlbum(a),
                                          onLongPress: () =>
                                              _albumSheet(a),
                                          child: Container(
                                              width: 92,
                                              padding: const EdgeInsets
                                                  .fromLTRB(5, 5, 5, Gap.xxs),
                                              decoration: BoxDecoration(
                                                  color: Theme.of(context)
                                                      .colorScheme
                                                      .surfaceContainerHighest,
                                                  borderRadius:
                                                      Radii.bSm),
                                              child: Column(children: [
                                                Expanded(
                                                    child: ClipRRect(
                                                        borderRadius:
                                                            BorderRadius
                                                                .circular(7),
                                                        child:
                                                            SizedBox.expand(
                                                                child: (a.cover
                                                                            ?.isNotEmpty ??
                                                                        false) &&
                                                                    _base !=
                                                                        null
                                                            ? AuthImage(
                                                                url:
                                                                    '$_base/thumb/${encPath(a.cover!)}')
                                                            : Icon(
                                                                Icons
                                                                    .auto_awesome_mosaic_outlined,
                                                                color: Theme.of(
                                                                        context)
                                                                    .colorScheme
                                                                    .primary)))),
                                                const SizedBox(height: 3),
                                                Text(a.title,
                                                    maxLines: 1,
                                                    overflow: TextOverflow
                                                        .ellipsis,
                                                    style: const TextStyle(
                                                        fontSize: 11,
                                                        fontWeight:
                                                            FontWeight.w600)),
                                                Text(
                                                    a.count > 0
                                                        ? '${a.count} 项'
                                                        : '空的',
                                                    style: TextStyle(
                                                        fontSize: 9.5,
                                                        color: Theme.of(
                                                                context)
                                                            .colorScheme
                                                            .outline)),
                                              ])))),
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
                                                size: IconSizes.sm),
                                        label:
                                            Text(_monthLabel(_month!)),
                                        onDeleted: () {
                                          setState(() => _month = null);
                                          _refresh();
                                        },
                                        onPressed: _pickMonth)),
                              // 自定义展示范围入口（20260913211718-b58a）
                              Padding(
                                  padding: const EdgeInsets.only(left: 2),
                                  child: InputChip(
                                      visualDensity: VisualDensity.compact,
                                      avatar: const Icon(
                                          Icons.folder_copy_outlined,
                                          size: IconSizes.sm),
                                      label: Text(_includedDirs.isEmpty
                                          ? '文件夹'
                                          : '自定义 · ${_includedDirs.length}'),
                                      onPressed: _pickFolders,
                                      onDeleted: _includedDirs.isEmpty
                                          ? null
                                          : () {
                                              setState(
                                                  () => _includedDirs = {});
                                              // ignore: discarded_futures
                                              _persistWallDirs();
                                              _refresh();
                                            })),
                            ])),
                    Expanded(child: _gridOrEmpty(selecting)),
                  ])),
      bottomNavigationBar: selecting && !_deleting ? _selectionBar() : null,
    );
  }

  Widget _gridOrEmpty(bool selecting) {
    if (_items.isEmpty && !_loading) {
      return ListView(children: [
        const SizedBox(height: 100),
        Center(child: Text(_failed ? '加载失败' : '这里还是空的')),
        const SizedBox(height: Gap.xs),
        Center(child: FilledButton.tonal(
            onPressed: _refresh, child: const Text('重新加载'))),
      ]);
    }
    return _buildTimeline(selecting);
  }

  /// 年月快滚轴（v0.11）：右缘竖排月份，直点跳月；月份数 ≥2 且非多选时显示。
  /// 只读 _months（initState 已静默预取），不自己发请求、不 setState 别人。
  Widget? _monthRail(bool selecting) {
    if (selecting || _byAdded) return null;   // 入库序平铺视图不显示月份轴
    final months = _months;
    if (months == null || months.length < 2) return null;
    final entries = months.entries.toList()
      ..sort((a, b) => b.key.compareTo(a.key));   // 新月份在顶
    final cs = Theme.of(context).colorScheme;
    Widget entry(String label, String? month, {int? count}) {
      final on = _month == month;
      return InkWell(
          onTap: () => setState(() {
                _month = month;
                _refresh();
              }),
          child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 2),
              decoration: BoxDecoration(
                  color: on ? cs.primary.withValues(alpha: 0.18) : null,
                  borderRadius: BorderRadius.circular(6)),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Text(label,
                    style: TextStyle(
                        fontSize: 9.5,
                        height: 1.15,
                        fontWeight: on ? FontWeight.w800 : FontWeight.w500,
                        color: on ? cs.primary : cs.outline)),
                if (count != null)
                  Text('$count',
                      style: TextStyle(
                          fontSize: 8, height: 1.1, color: cs.outline)),
              ])));
    }

    // 毛玻璃底（v0.13）：此前半透明色块直接叠在第三列照片上，遮挡且发闷；
    // BackdropFilter 实时模糊透出底下内容，贴右缘窄条观感干净
    return ClipRRect(
        borderRadius: const BorderRadius.horizontal(left: Radius.circular(Radii.sm)),
        child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 8, sigmaY: 8),
            child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 2, vertical: 6),
                decoration: BoxDecoration(
                    color: cs.surface.withValues(alpha: 0.55),
                    border: Border(
                        left: BorderSide(
                            color: cs.outline.withValues(alpha: 0.25),
                            width: 0.5))),
                child: SingleChildScrollView(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                  entry('全部', null),
                  for (final e in entries)
                    entry(e.key.substring(2), e.key, count: e.value),  // 2026-08 → 26-08
                ])))));
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
                  if (canManage)
                    _op(Icons.delete_forever_outlined, '彻底删除',
                        _purgeSelected),
                ])));
  }

  Widget _op(IconData icon, String label, VoidCallback onTap) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: InkWell(
          onTap: onTap,
          borderRadius: Radii.bSm,
          child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: Gap.md, vertical: 6),
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
    // v0.20 入库序：不按日分组，整列平铺（新上传永远置顶，日期头无意义）
    final groups = <(String, List<int>)>[];
    if (_byAdded) {
      if (_items.isNotEmpty) {
        groups.add(('', [for (var i = 0; i < _items.length; i++) i]));
      }
    } else {
      for (var i = 0; i < _items.length; i++) {
        final day = _items[i].taken;
        if (groups.isNotEmpty && groups.last.$1 == day) {
          groups.last.$2.add(i);
        } else {
          groups.add((day, [i]));
        }
      }
    }
    final body = CustomScrollView(
        controller: _scroll,
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          for (final (day, flatIdx) in groups) ...[
            // 日期头随内容滚动（不用置顶 SliverAppBar——悬浮文字压在照片上
            // 很难看，真机反馈 0.4.5）；入库序无日期头
            if (day.isNotEmpty)
              SliverToBoxAdapter(
                child: Align(
                    alignment: Alignment.centerLeft,
                    child: Container(
                        margin: const EdgeInsets.fromLTRB(Gap.sm, 10, Gap.sm, 6),
                        padding:
                            const EdgeInsets.symmetric(horizontal: Gap.sm, vertical: 5),
                        decoration: BoxDecoration(
                            color: Theme.of(context)
                                .colorScheme
                                .primary
                                .withValues(alpha: 0.10),
                            borderRadius: BorderRadius.circular(Radii.pill)),
                        child: Text(_dayLabel(day),
                            style: TextStyle(
                                fontWeight: FontWeight.w700,
                                fontSize: 12.5,
                                color: Theme.of(context)
                                    .colorScheme
                                    .primary))))),
            // 瀑布流：SliverWaterfallFlow 保留 Sliver 虚拟化（0.4.4 教训不回退），
            // 格子高度来自服务端探测的真实宽高比；未知的回退方格。
            // v0.11 用 flutter_staggered_grid_view 的 SliverMasonryGrid：
            // 同一 CustomScrollView 里多个瀑布流 sliver 时滚动修正有致命 bug
            // （上游 issue #265/#286/#338），按日分组=每天一个=真机必卡死，
            // v0.13 换 waterfall_flow（该包即为此修复的社区版，API 同型）。
            SliverWaterfallFlow(
                gridDelegate:
                    SliverWaterfallFlowDelegateWithFixedCrossAxisCount(
                        // v1.0 响应式：列数随窗口档位自适应（手机 3 / 横屏 4 /
                        // 平板 6）。此前固定 3 列，平板上缩略图被撑到 300+ px，
                        // 一屏只看得到 6 张；改列数只动 delegate 参数，Sliver
                        // 结构与虚拟化路径不变（性能红线）
                        crossAxisCount: context.gridColumns(),
                        mainAxisSpacing: 2, crossAxisSpacing: 2),
                delegate: SliverChildBuilderDelegate(
                    addRepaintBoundaries: true,
                    (context, j) {
                      final flat = flatIdx[j];
                      final selected = _selected.contains(flat);
                      final item = _items[flat];
                      return AspectRatio(
                          aspectRatio: item.aspect,
                          child: GestureDetector(
                        onTap: selecting
                            ? () => _toggleSelect(flat)
                            : () => _openViewer(flat),
                        onLongPress: () => _toggleSelect(flat),
                        child: Stack(fit: StackFit.expand, children: [
                          Hero(
                              tag: 'ph-${item.path}',
                              child: AuthImage(
                                  url: '$_base/thumb/${encPath(item.path)}',
                                  // 无障碍（v1.0）：缩略图网格里没有任何文字，
                                  // 读屏至少要知道这是照片还是视频
                                  semanticLabel:
                                      item.isVideo ? '视频' : '照片')),
                          // 视频角标：▶ + 时长（0.6.0）
                          if (item.isVideo)
                            Positioned(
                                left: 4,
                                bottom: 4,
                                child: Container(
                                    padding: const EdgeInsets.symmetric(
                                        horizontal: Gap.xxs, vertical: 1),
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
                        ])),
                      );
                    },
                    childCount: flatIdx.length)),
          ],
          if (!_done)
            const SliverPadding(
                padding: EdgeInsets.all(Gap.md),
                sliver: SliverToBoxAdapter(
                    child: Center(
                        child: SizedBox(width: 18, height: 18,
                            child: CircularProgressIndicator(
                                strokeWidth: 2))))),
        ]);
    final rail = _monthRail(selecting);
    return rail == null
        ? body
        : Stack(children: [
            body,
            Positioned(right: 2, top: 60, bottom: 90, child: rail),
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

/// 精选专辑条目（v0.14 文件夹式）：/api/albums 列表只给 count/cover，
/// 全量条目在详情页按需取；month 仅旧月份书签型专辑有值
class _Album {
  final String id;
  final String title;
  final String month;
  final String createdBy;
  final int count;
  final String? cover;
  _Album.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String? ?? '',
        title = j['title'] as String? ?? '',
        month = j['month'] as String? ?? '',
        createdBy = j['created_by'] as String? ?? '',
        count = (j['count'] as num?)?.toInt() ?? 0,
        cover = j['cover'] as String?;
}
