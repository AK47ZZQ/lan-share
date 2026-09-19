// 照片时间线：/api/photos 按拍摄日分组的 3 列网格 + 点击全屏查看。
// 0.4.4：整体改 CustomScrollView + SliverGrid 虚拟化（日期头滚动置顶）——
//       此前"ListView + shrinkWrap 网格"快滑时整组销毁重建，已加载的图划走
//       再回来全变转圈（真机复现）；Sliver 只构建视口内格子，照片量不设限。
// 0.4.3：长按进入多选（下载/删除/分享）；备份完成后 10s 内自动带 refresh=1
// 重拉（服务端放宽扫描节流），照片墙准实时。
// 0.6.0：视频纳入（角标+时长/内嵌播放）、类型筛选（全部/照片/视频）、
//        按月快跳（/api/photos/facets 月份维度，服务端 month= 过滤）。
// 1.0.2（20260914041746-6c58）：右缘月份轴从"按月过滤"改为**全时间线定位
//        跳转**——按月过滤会把时间线锁死在单月里（往上翻不了，用户反馈）。
//        跳转按筛选口径一致的 facets 计数推算偏移，并行补拉后滚动定位到
//        该月第一天日期头，跳完仍可自由上下翻；轴高亮跟随滚动实时移动；
//        日期头加大加粗（醒目）。
// 1.0.2（20260914042013-6e62）：性能——脉搏/备份轮询触发的后台刷新改为
//        **增量合并**（路径序列全等零重建，滚动/图片全不动）；批量并行拉取；
//        多选删除并发 4 路（原来逐张串行）。
// 1.0.2（20260914042829-def1）：多选**滑动连选**——长按进入多选后手指继续
//        滑动，经过的格子自动勾选/取消，接近屏幕边缘自动滚动接着选；
//        多选进行中暂缓自动刷新（长按后被顶回列表顶部即旧刷新所致），
//        退出多选后补一次。
import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

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

  /// 返回手势接管（20260914042923-b7c1）：多选状态暴露给壳层——根层返回
  /// 先退多选，再退页签（backExitSelection 由本页 initState 注入）。
  static final ValueNotifier<bool> selectionActive = ValueNotifier(false);
  static VoidCallback? backExitSelection;

  /// 跳月目标偏移：facets 月份计数（新月在顶）按序累计到目标月为止，
  /// 即目标月首项在全量时间线里的下标。返回 null = 目标月不在 facets。
  /// 纯函数（df72 批抽出可测）。
  @visibleForTesting
  static int? jumpTargetFromFacets(Map<String, int> months, String m) {
    final keys = months.keys.where((k) => k.length == 7).toList()
      ..sort((a, b) => b.compareTo(a));
    var target = 0;
    for (final k in keys) {
      if (k == m) return target;
      target += months[k] ?? 0;
    }
    return null;
  }

  @override
  ConsumerState<PhotosPage> createState() => _PhotosPageState();
}

class _PhotosPageState extends ConsumerState<PhotosPage> {
  final _items = <PhotoItem>[];
  final _scroll = ScrollController();
  final _railScroll = ScrollController(); // 月份轴自身滚动（高亮跟随居中）
  final _selected = <int>{}; // 多选模式：_items 扁平下标
  // 日期头/月份轴条目的 BuildContext 注册表（df72 批）：跳月定位需要"目标日
  // 期头是否已布局 + 其实测偏移"。不可用 GlobalKey——①GlobalObjectKey 相等
  // 判断 identical(value)，build 与查找两侧各自插值新建的同名 key 永不相等，
  // 查找恒 null（校准自 1.0.2 起从未生效，落点全靠估算误差）；②换成真
  // GlobalKey 后 sliver 子项被急切布局，渲染与滚动位置脱钩（模拟器实测
  // position=49681 而画面停在墙顶）。仿 _CellReg 用 context 注册表：
  // 只有真布局过的头才会登记。
  final _hdrCtxs = <String, BuildContext>{};
  final _railCtxs = <String, BuildContext>{};
  final _cellCtxs = <int, BuildContext>{}; // 可见格子注册表（命中测试/锚定）
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
  Map<String, int>? _months;    // facets 缓存（月份选择页/跳月轴共用）
  List<_Album>? _albums;        // 精选专辑（0.8.0，管理员维护）
  // 自定义展示范围（20260913211718-b58a）：空 = 全部文件夹；非空 = 只展示
  // 命中前缀的照片（服务端 include_dirs 下推，分页/脉搏口径都由服务端保证）
  Set<String> _includedDirs = {};
  // 每页条数：局域网 120 / 公网 60（弱网首屏少拉半屏，滚动突发更小，v0.10.3）
  int get _limit => isPublicAddress(_base ?? '') ? 60 : 120;

  // ── 1.0.2 批次新增状态 ──────────────────────────────────
  bool _jumping = false;        // 时间轴跳月中（拉取+定位），期间挡新跳转
  // 月份轴高亮（跟随滚动）。用 ValueNotifier 而非 setState：滚动中每到月份
  // 边界就会变——setState 会重建整页（全部 sliver 树 + O(n) 分组重算），
  // 快滑跨月时就是一串重建风暴（31cd「滑动特别卡」主因之一）；收敛到轴自身重建
  final ValueNotifier<String?> _railMonthN = ValueNotifier<String?>(null);
  Timer? _railTick;             // 滚动高亮节流
  bool _dragSelecting = false;  // 拖拽连选进行中
  bool _dragAdd = true;         // 拖拽方向：true=勾选 false=取消
  final _dragTouched = <int>{}; // 本次拖拽已处理过的格子（防来回抖动）
  Offset? _lastDragPos;         // 自动滚动 tick 的命中测试位置
  Timer? _dragScroll;           // 拖拽边缘自动滚动
  bool _pendingRefresh = false; // 多选中暂缓的自动刷新（退出后补一次）

  @override
  void initState() {
    super.initState();
    PhotosPage.backExitSelection = _exitSelection;
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
    _scroll.addListener(_onScroll);
    // 照片墙准实时：备份引擎完成一轮会写 backup_last_done_at，变化即
    // 增量合并刷新（服务端 refresh=1 放宽扫描节流到 5s）。轮询只读本地
    // prefs，开销可忽略
    _backupWatch = Timer.periodic(
        const Duration(seconds: 10), (_) => _checkBackupDone());
    // 0.7.2 起触发面不只本机备份：20s 脉搏 ping 一次 limit=1&refresh=1
    // （服务端 force 独立 5s 节流，响应只有一个缩略项，开销可忽略），
    // total/首张变了即增量合并刷新——网页、别的手机、文件页、分享接收
    // 传的照片同样自动出现（此前只认备份引擎，家里人网页传 178 项墙面不动）
    _pulse = Timer.periodic(const Duration(seconds: 20), (_) => _pulseCheck());
  }

  void _onScroll() {
    if (_scroll.position.extentAfter < 600) _loadMore();
    // 月份轴高亮跟随（1.0.2）：节流 180ms，避免每个滚动帧都遍历格子注册表
    _railTick ??= Timer(const Duration(milliseconds: 180), () {
      _railTick = null;
      if (mounted) _updateRailMonth();
    });
  }

  /// 服务端脉搏：对时间线做一次"有没有变"的轻探测。快照与上次不同
  /// （新增/删除/最新一张换人）→ 增量合并刷新。
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
        await _refreshSoft();
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
      await _refreshSoft();
    }
  }

  /// 批量并行拉取 [from, from+want) 区间（每页 300、最多 4 页并行一轮；
  /// 末页按 want 截断请求长度——此前固定要 300 条但只留 want 条，白烧
  /// 服务端扫描与流量）。供翻页(_loadMore)、增量合并(_refreshSoft)、
  /// 时间轴跳月(_jumpToMonth)共用；过期响应由调用方的 _gen 机制丢弃。
  Future<(List<PhotoItem>, bool)> _fetchBatch(int from, int want) async {
    const page = 300;
    final out = <PhotoItem>[];
    final seen = <String>{};   // 并行分页与远端数据漂移的去重保险
    var offset = from;
    var endReached = false;
    while (!endReached && out.length < want && mounted) {
      final offs = <(int, int)>[];   // (offset, limit)
      for (var o = offset;
          o < offset + page * 4 && o < from + want;
          o += page) {
        offs.add((o, (from + want - o).clamp(1, page)));
      }
      if (offs.isEmpty) break;
      final lists = await Future.wait([
        for (final (o, lim) in offs)
          PhotosApi.list(
              limit: lim,
              offset: o,
              mediaType: _mediaFilter == 'all' ? null : _mediaFilter,
              month: _month,
              byAdded: _byAdded,
              includeDirs:
                  _includedDirs.isEmpty ? null : _includedDirs.join(','))
      ]);
      var got = 0;
      for (var i = 0; i < lists.length; i++) {
        final lim = offs[i].$2;
        final l = lists[i];
        for (final e in l) {
          final it = PhotoItem.fromJson(e as Map<String, dynamic>);
          if (seen.add(it.path)) out.add(it);
        }
        got += l.length;
        if (l.length < lim) endReached = true;
      }
      if (got == 0) break;
      offset += got;
    }
    if (out.length > want) out.removeRange(want, out.length);
    return (out, endReached);
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
      final (list, endReached) = await _fetchBatch(_offset, limit);
      if (!mounted || gen != _gen) return;   // 已刷新过：丢弃过期响应
      setState(() {
        _items.addAll(list);
        _offset = _items.length;
        _done = endReached;
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

  /// 硬刷新（下拉/切筛选/看图删除后）：清空重拉，回到墙顶。
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

  /// 增量合并刷新（20260914042013-6e62 / 20260914042829-def1）：脉搏/备份
  /// 轮询触发的后台刷新不再"清空重拉"——那会整墙转圈、滚动位置归顶
  /// （多选中长按被顶回列表顶部即旧刷新所致）。改为并行拉到覆盖原数量
  /// +1 页，路径序列全等则零重建（滚动/图片/动画全不动）；有变化才整体
  /// 替换并把视口锚回原日期组。多选/跳月中暂缓，退出后补一次。
  Future<void> _refreshSoft() async {
    if (!mounted) return;
    if (_selected.isNotEmpty || _jumping || _dragSelecting) {
      // _dragSelecting：拖拽连选中合并会把 _items 整体替换，_dragTouched
      // 的下标瞬间指向别的照片——继续拖会勾错格子（对抗审查 R31）
      _pendingRefresh = true;
      return;
    }
    final gen = ++_gen;          // 换代：作废在途翻页响应，防重放追加
    final anchorDay = _dayOfFlat(_topVisibleFlat());
    setState(() => _loading = true);
    try {
      final (list, endReached) =
          await _fetchBatch(0, _items.length + _limit);
      if (!mounted || gen != _gen) return;
      var same = list.length == _items.length;
      if (same) {
        for (var i = 0; i < list.length; i++) {
          if (list[i].path != _items[i].path) {
            same = false;
            break;
          }
        }
      }
      if (same) return;    // 零变化：不 setState，滚动与已加载图片全保持
      setState(() {
        _items..clear()..addAll(list);
        _offset = _items.length;
        _done = endReached;
        _failed = false;
      });
      // 月份分布同步重拉（有实际变化才花这次请求）
      _months = null;
      await _ensureMonths();
      if (mounted) setState(() {});
      if (anchorDay != null) _scrollToDay(anchorDay);
    } catch (_) {
      // 合并失败静默：旧内容仍可用，下个脉搏周期再试
    } finally {
      if (mounted && gen == _gen) setState(() => _loading = false);
    }
  }

  void _setFilter(String v) {
    if (v == _mediaFilter) return;
    setState(() => _mediaFilter = v);
    _refresh();
  }

  /// facets 月份分布拉取（跳月轴/按月快跳共用）；带当前筛选口径
  /// （media_type/include_dirs）保证跳月偏移推算与时间线完全一致；
  /// 失败返回空表不炸 UI
  Future<Map<String, int>> _ensureMonths() async {
    var months = _months;
    if (months != null) return months;
    try {
      months = await PhotosApi.monthFacets(
          mediaType: _mediaFilter == 'all' ? null : _mediaFilter,
          includeDirs:
              _includedDirs.isEmpty ? null : _includedDirs.join(','));
      _months = months;
      return months;
    } catch (_) {
      return {};
    }
  }

  // ── 时间轴跳月（20260914041746-6c58）────────────────────
  /// 右缘月份轴点按：在**全量时间线**内定位到目标月（不再按月过滤）。
  /// 偏移按 facets 月份计数累计推算（新月份在顶），补拉到目标月后滚动
  /// 定位该月第一天的日期头；跳完仍可自由上下翻。
  Future<void> _jumpToMonth(String m) async {
    if (_jumping || _loading || _selected.isNotEmpty || !mounted) return;
    final months = await _ensureMonths();
    if (!mounted || months.isEmpty) return;
    final target = PhotosPage.jumpTargetFromFacets(months, m);
    if (target == null) return;
    final gen = ++_gen;
    setState(() {
      _jumping = true;
      _loading = true;
    });
    try {
      final need = target + 30;   // 目标月起点再预载半屏，落点即有内容
      if (_items.length < need && !_done) {
        final (list, endReached) =
            await _fetchBatch(_items.length, need - _items.length);
        if (!mounted || gen != _gen) return;
        setState(() {
          _items.addAll(list);
          _offset = _items.length;
          if (endReached) _done = true;
        });
      }
      String? day;
      for (final it in _items) {
        if (it.taken.length >= 7 && it.taken.substring(0, 7) == m) {
          day = it.taken;
          break;
        }
      }
    if (!mounted || gen != _gen) return;
    _railMonthN.value = m;
    if (day != null) {
      await _scrollToDay(day);
    } else if (_scroll.hasClients) {
      // facets 有计数但墙上找不到（计数为 0 的空月，防御）：回墙顶
      await _scroll.animateTo(0,
            duration: const Duration(milliseconds: 300),
            curve: Curves.easeOutCubic);
      }
    } catch (_) {
      // 网络失败：停在原地（不粗暴回顶），落点收尾统一走 finally
    } finally {
      // _jumping 必须盖住整个落点校准（_scrollToDay 多轮估算+jumpTo）——
      // 此前在**校准前**就清了标志：主人正在上传时，20s 脉搏检测到变化
      // 触发 _refreshSoft，趁隙整表重拉+全墙重建，与校准滚动相撞——落点
      // 被甩回顶部、缩略图在反复重建中永远追不上（反馈
      // 20260919004417-df72「定位后照片不加载/自己划回顶部」的根因）。
      // 收尾统一在此；跳转期间暂缓的刷新补跑一次，锚点=落点日期。
      if (mounted) {
        setState(() { _jumping = false; _loading = false; });
        if (gen == _gen && _pendingRefresh) {
          _pendingRefresh = false;
          // ignore: discarded_futures
          _refreshSoft();
        }
      }
    }
  }

  /// 滚动定位到某天的日期头。瀑布流高度不定、远处日期头未构建拿不到
  /// context——用"格子宽高比估算偏移 → 跳过去 → 已构建日期头实测校准"
  /// 迭代收敛（一般 2~3 轮）。
  Future<void> _scrollToDay(String day) async {
    for (var i = 0; i < 5; i++) {
      await WidgetsBinding.instance.endOfFrame;
      if (!mounted || !_scroll.hasClients) return;
      final ctx = _hdrCtxs[day];
      if (ctx != null && ctx.mounted) {
        // 命中已布局日期头：按其实测绝对偏移直接落点（ensureVisible 对
        // 深处 sliver 子元素可能不产生滚动，df72 批实测），落定后再用
        // ensureVisible 对齐到头行。
        final ro = ctx.findRenderObject();
        if (ro is RenderBox && ro.attached && ro.hasSize) {
          final abs = _scroll.offset + ro.localToGlobal(Offset.zero).dy;
          _scroll.jumpTo(abs < 0 ? 0 : abs);
          await Future.delayed(const Duration(milliseconds: 300));
          if (!ctx.mounted) return;
          await Scrollable.ensureVisible(ctx,
              duration: const Duration(milliseconds: 250), alignment: 0);
          if (mounted) _updateRailMonth();
          return;
        }
      }
      final est = _estimateDayOffset(day);
      if (est == null) return;   // 目标日不在已加载数据里
      final jump = _calibrate(day, est) ?? est;
      _scroll.jumpTo(jump < 0 ? 0 : jump);
    }
  }

  /// 分组计算（build 与跳转估算共用同一口径）。
  List<(String, List<int>)> _computeGroups() {
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
    return groups;
  }

  /// 目标日期头的估算绝对偏移（瀑布流总高 ≈ 总格高/列数 + 单格余量）。
  /// 返回 null = 目标日不在已加载数据里。
  double? _estimateDayOffset(String targetDay) {
    final groups = _computeGroups();
    final cols = context.gridColumns();
    final cw = MediaQuery.sizeOf(context).width / cols;
    const headerH = 54.0;   // 日期头：上下边距 12+8 + 内边距 7*2 + 文本行
    const spacing = 2.0;
    var acc = 0.0;
    for (final (day, flatIdx) in groups) {
      if (day == targetDay) return acc;
      var sum = 0.0;
      for (final f in flatIdx) {
        final a = _items[f].aspect;
        sum += cw / (a <= 0 ? 1.0 : a);
      }
      acc += headerH + sum / cols + spacing * (flatIdx.length / cols);
    }
    return null;
  }

  /// 用已构建日期头实测绝对偏移校准估算值：返回建议 jumpTo 的位置。
  double? _calibrate(String targetDay, double estTarget) {
    final groups = _computeGroups();
    final tIdx = groups.indexWhere((g) => g.$1 == targetDay);
    if (tIdx < 0) return null;
    String? nearDay;
    var nearDist = 1 << 30;
    double? nearAbs;
    for (var i = 0; i < groups.length; i++) {
      final ctx = _hdrCtxs[groups[i].$1];
      if (ctx == null) continue;
      final ro = ctx.findRenderObject();
      if (ro is! RenderBox || !ro.attached || !ro.hasSize) continue;
      final abs = _scroll.offset + ro.localToGlobal(Offset.zero).dy;
      final dist = (i - tIdx).abs();
      if (dist < nearDist) {
        nearDist = dist;
        nearDay = groups[i].$1;
        nearAbs = abs;
      }
    }
    if (nearDay == null || nearAbs == null) return null;
    final estNear = _estimateDayOffset(nearDay);
    if (estNear == null) return null;
    return nearAbs + (estTarget - estNear);
  }

  /// 视口顶部所属格子下标：可见格子注册表里全局 y ≥ -60 中最小者。
  /// 供合并刷新锚定与月份轴高亮跟随共用。
  int? _topVisibleFlat() {
    if (_cellCtxs.isEmpty) return null;
    var bestY = double.infinity;
    int? best;
    _cellCtxs.forEach((flat, ctx) {
      final ro = ctx.findRenderObject();
      if (ro is RenderBox && ro.attached && ro.hasSize) {
        final y = ro.localToGlobal(Offset.zero).dy;
        if (y >= -60 && y < bestY) {
          bestY = y;
          best = flat;
        }
      }
    });
    return best;
  }

  String? _dayOfFlat(int? flat) {
    if (flat == null || flat >= _items.length) return null;
    final t = _items[flat].taken;
    return t.isEmpty ? null : t;
  }

  /// 月份轴高亮跟随（1.0.2）：滚动时把轴上高亮移到当前视口顶部所属月份，
  /// 并让轴自身滚动把该月份条目滚进可视区。只碰 ValueNotifier，不 setState。
  void _updateRailMonth() {
    if (_selected.isNotEmpty || _dragSelecting || _byAdded || _month != null) {
      return;
    }
    final months = _months;
    if (months == null || months.length < 2) return;
    final top = _topVisibleFlat();
    if (top == null || top >= _items.length) return;
    final t = _items[top].taken;
    final m = t.length >= 7 ? t.substring(0, 7) : '';
    if (m.isEmpty || m == _railMonthN.value) return;
    _railMonthN.value = m;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final ctx = _railCtxs[m];
      if (ctx != null) {
        Scrollable.ensureVisible(ctx,
            alignment: 0.5,
            duration: const Duration(milliseconds: 150));
      }
    });
  }

  // ── 拖拽连选（20260914042829-def1）──────────────────────

  /// 长按起始格：进入多选（或以"取消"方向开始），并开启拖拽连选与
  /// 边缘自动滚动。
  void _dragBegin(int flat, Offset pos) {
    if (_deleting || _jumping) return;
    _dragTouched.clear();
    _dragAdd = !_selected.contains(flat);   // 起始格已选中 → 拖动为取消
    _toggleSelect(flat);
    _dragTouched.add(flat);
    _dragSelecting = true;
    _lastDragPos = pos;
    _dragScroll ??= Timer.periodic(
        const Duration(milliseconds: 32), (_) => _dragTick());
  }

  void _dragMove(Offset pos) {
    if (!_dragSelecting) return;
    _lastDragPos = pos;
    _dragHitTest(pos);
  }

  void _dragEnd() {
    _dragSelecting = false;
    _lastDragPos = null;
    _dragTouched.clear();
    _dragScroll?.cancel();
    _dragScroll = null;
  }

  /// 命中测试：手指经过的未处理格子按拖拽方向切换勾选态。
  void _dragHitTest(Offset pos) {
    _cellCtxs.forEach((flat, ctx) {
      if (_dragTouched.contains(flat)) return;
      final ro = ctx.findRenderObject();
      if (ro is RenderBox && ro.attached && ro.hasSize) {
        final tl = ro.localToGlobal(Offset.zero);
        if (Rect.fromLTWH(tl.dx, tl.dy, ro.size.width, ro.size.height)
            .inflate(2)
            .contains(pos)) {
          _dragTouched.add(flat);
          if (_dragAdd && !_selected.contains(flat)) {
            _toggleSelect(flat);
          } else if (!_dragAdd && _selected.contains(flat)) {
            _toggleSelect(flat);
          }
        }
      }
    });
  }

  /// 拖拽边缘自动滚动：接近屏幕上/下缘按接近程度加速滚动，滚动后补一次
  /// 命中测试（手指不动、内容在动也要连选）。
  void _dragTick() {
    if (!_dragSelecting || !mounted || !_scroll.hasClients) return;
    final y = _lastDragPos?.dy;
    if (y == null) return;
    const edge = 90.0, maxV = 24.0;
    var v = 0.0;
    if (y < edge) {
      v = -(1 - y / edge) * maxV - 2;
    } else {
      final bottomDist = MediaQuery.sizeOf(context).height - y;
      if (bottomDist < edge) v = (1 - bottomDist / edge) * maxV + 2;
    }
    if (v == 0) return;
    _scroll.jumpTo(
        (_scroll.offset + v).clamp(0.0, _scroll.position.maxScrollExtent));
    final pos = _lastDragPos;
    if (pos != null) _dragHitTest(pos);
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

  /// 按月快跳：facets 月份分布 → 选择后走服务端 month= 过滤（显式筛选，
  /// 与右缘跳月轴的"全时间线定位"是两个入口）
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
    PhotosPage.backExitSelection = null;
    PhotosPage.selectionActive.value = false;
    Prefs.addressNotifier.removeListener(_onAddressChanged);
    _backupWatch?.cancel();
    _pulse?.cancel();
    _railTick?.cancel();
    _dragScroll?.cancel();
    _railMonthN.dispose();
    _scroll.dispose();
    _railScroll.dispose();
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
    // 手动上传也要准实时：任务全部完成即增量合并刷新，不等 10s 备份轮询
    if (enqueued.isNotEmpty) {
      void watch() {
        if (!enqueued.any((t) => !t.finished)) {
          engine.removeListener(watch);
          if (mounted) _refreshSoft();
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
    if (_selected.isEmpty) _afterSelectionEnd();
  }

  /// 多选结束（清空选集）公共出口：补跑多选期间暂缓的自动刷新
  void _afterSelectionEnd() {
    setState(() {});
    if (_pendingRefresh) {
      _pendingRefresh = false;
      // ignore: discarded_futures
      _refreshSoft();
    }
  }

  void _exitSelection() {
    setState(() => _selected.clear());
    _afterSelectionEnd();
  }

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
      // 并发 4 路删除（20260914042013-6e62）：大批量逐张串行太慢——
      // 几百张要等几十秒；4 路并行在局域网约 4 倍速收敛
      final queue = List<PhotoItem>.from(paths);
      Future<void> worker() async {
        while (queue.isNotEmpty && mounted) {
          final it = queue.removeLast();
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
      }

      await Future.wait(
          [for (var i = 0; i < 4; i++) worker()]);
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
    // 返回手势接管（20260914042923-b7c1）：壳层读这个通知器决定先退多选
    PhotosPage.selectionActive.value = selecting;
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

  /// 年月快滚轴（v0.11 引入，1.0.2 改造为**跳转轴**）：右缘竖排月份，
  /// 点按在全量时间线内定位到该月（不再按月过滤——那会锁死单月翻不了）；
  /// 高亮跟随滚动实时移动。月份数 ≥2、非多选、非入库序、未按月过滤时显示。
  /// 只读 _months（已带筛选口径），不自己发请求。
  /// 31cd 性能改造：①高亮用 ValueListenableBuilder 局部重建（此前随页面
  /// setState 整页重建）；②BackdropFilter 实时模糊撤掉——滑动中每帧对轴
  /// 区域 saveLayer+blur，低端机显著掉帧，半透明实色观感相近、成本近零。
  Widget? _monthRail(bool selecting) {
    if (selecting || _byAdded || _month != null) return null;
    final months = _months;
    if (months == null || months.length < 2) return null;
    final entries = months.entries.toList()
      ..sort((a, b) => b.key.compareTo(a.key));   // 新月份在顶
    final cs = Theme.of(context).colorScheme;
    Widget entry(String label, String month, String? monthKey,
        {int? count, required bool on, required VoidCallback onTap}) {
      return InkWell(
          key: monthKey == null ? null : ValueKey<String>('rail-$monthKey'),
          onTap: onTap,
          child: _CtxReg(
              registry: _railCtxs,
              tag: monthKey ?? '',
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
                  ]))));
    }

    return ClipRRect(
        borderRadius: const BorderRadius.horizontal(left: Radius.circular(Radii.sm)),
        child: Container(
            padding:
                const EdgeInsets.symmetric(horizontal: 2, vertical: 6),
            decoration: BoxDecoration(
                color: cs.surface.withValues(alpha: 0.92),
                border: Border(
                    left: BorderSide(
                        color: cs.outline.withValues(alpha: 0.25),
                        width: 0.5))),
            child: ValueListenableBuilder<String?>(
                valueListenable: _railMonthN,
                builder: (_, railMonth, _) => SingleChildScrollView(
                    controller: _railScroll,
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      entry('全部', '', null,
                          on: railMonth == null || railMonth.isEmpty,
                          onTap: () {
                        if (_scroll.hasClients) {
                          _scroll.animateTo(0,
                              duration: const Duration(milliseconds: 300),
                              curve: Curves.easeOutCubic);
                        }
                      }),
                      for (final e in entries)
                        entry(e.key.substring(2), e.key, e.key,
                            count: e.value,
                            on: railMonth == e.key,
                            onTap: () => _jumpToMonth(e.key)),
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
    final groups = _computeGroups();
    final body = CustomScrollView(
        controller: _scroll,
        // 提前构建缓存区（默认 250px）：缩略图请求提前起步，快滑到时已在
        // 下载/解码路上，减少落地后的转圈（31cd「加载慢」感知优化）。
        // 新 API ScrollCacheExtent 未从 material 导出，暂用旧参数（已实测可用）
        // ignore: deprecated_member_use
        cacheExtent: 600,
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          for (final (day, flatIdx) in groups) ...[
            // 日期头随内容滚动（不用置顶 SliverAppBar——悬浮文字压在照片上
            // 很难看，真机反馈 0.4.5）；入库序无日期头。
            // 1.0.2 醒目化：全宽色条 + 15px 粗体（此前 12.5 小药丸不显眼）；
            // GlobalObjectKey 供跳月定位/校准与滚动锚定使用。
            // sliver 级 ValueKey（31cd）：增量合并插入新组后，框架按 key
            // 而非位置匹配 sliver——否则插入点之后的组全部错位复用，
            // 视口内格子 URL 集体变化 → 已加载图全变转圈重拉
            if (day.isNotEmpty)
              SliverToBoxAdapter(
                key: ValueKey<String>('grp-h-$day'),
                child: _CtxReg(
                    registry: _hdrCtxs,
                    tag: day,
                    child: Container(
                        width: double.infinity,
                        margin:
                            const EdgeInsets.fromLTRB(Gap.sm, 12, Gap.sm, 8),
                        padding: const EdgeInsets.symmetric(
                            horizontal: Gap.md, vertical: 7),
                        decoration: BoxDecoration(
                            color: Theme.of(context)
                                .colorScheme
                                .primary
                                .withValues(alpha: 0.12),
                            borderRadius: BorderRadius.circular(Radii.sm)),
                        child: Text(_dayLabel(day),
                            style: TextStyle(
                                fontWeight: FontWeight.w800,
                                fontSize: 15,
                                letterSpacing: 0.2,
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
                key: ValueKey<String>('grp-${day.isEmpty ? "~" : day}'),
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
                    // 格子按路径找新下标（31cd）：合并刷新后元素随路径复用，
                    // AuthImage 状态原封不动——插入新照片不再把视口内
                    // 未变的格子打回转圈重拉。key 形如 cell-<path>，截前缀还路径
                    findChildIndexCallback: (key) {
                      final p = (key as ValueKey<String>).value.substring(5);
                      for (var j = 0; j < flatIdx.length; j++) {
                        if (_items[flatIdx[j]].path == p) return j;
                      }
                      return null;
                    },
                    (context, j) {
                      final flat = flatIdx[j];
                      final selected = _selected.contains(flat);
                      final item = _items[flat];
                      return _CellReg(
                          key: ValueKey<String>('cell-${item.path}'),
                          flat: flat,
                          registry: _cellCtxs,
                          child: AspectRatio(
                          aspectRatio: item.aspect,
                          child: GestureDetector(
                        onTap: selecting
                            ? () => _toggleSelect(flat)
                            : () => _openViewer(flat),
                        // 拖拽连选（20260914042829-def1）：长按起始即开始，
                        // 手指继续滑动经过的格子自动勾选/取消
                        onLongPressStart: (d) =>
                            _dragBegin(flat, d.globalPosition),
                        onLongPressMoveUpdate: (d) =>
                            _dragMove(d.globalPosition),
                        onLongPressEnd: (_) => _dragEnd(),
                        onLongPressCancel: _dragEnd,
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
                      ));
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
    // 6b09 根修：CustomScrollView 必须永远占据本方法返回树的**同一槽位、
    // 同一层级**。此前包裹结构随状态变形——月份轴出现/消失（_months 加载
    // 完成、进多选、按月过滤、入库序）时 body ↔ Stack([body, rail]) 互切，
    // _jumping 时再套一层 Stack([Stack([body, rail]), toast])：父槽位
    // runtimeType 变化（CustomScrollView ↔ Stack）会令 CustomScrollView
    // **整树重建、ScrollPosition 新建（从 0 起）**——跳月落点在 finally 清
    // _jumping 的那一帧瞬间作废弹回墙顶（真机录屏：落点 2025-03 后 1 秒
    // 回顶；模拟器插桩：finally 时 position=129168 而画面/轴高亮在墙顶）。
    // 改为固定 Stack：child 0 恒为 body，rail/toast 只作兄弟节点增删。
    final rail = _monthRail(selecting);
    final children = <Widget>[body];
    if (rail != null) {
      children.add(Positioned(right: 2, top: 60, bottom: 90, child: rail));
    }
    if (_jumping) {
      // 跳月定位中：底部悬浮进度提示（拉取+校准滚动一般 1~3 秒）
      children.add(Positioned(
          left: 0,
          right: 0,
          bottom: 28,
          child: Center(
              child: Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: Gap.md, vertical: Gap.xs),
                  decoration: BoxDecoration(
                      color: Colors.black54,
                      borderRadius: BorderRadius.circular(20)),
                  child: const Row(mainAxisSize: MainAxisSize.min, children: [
                    SizedBox(
                        width: 12,
                        height: 12,
                        child: CircularProgressIndicator(
                            strokeWidth: 2, color: Colors.white)),
                    SizedBox(width: 8),
                    Text('正在定位时间线…',
                        style: TextStyle(fontSize: 12, color: Colors.white)),
                  ])))));
    }
    return Stack(children: children);
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

/// 可见格子注册表（1.0.2）：多选拖拽命中测试与滚动锚定需要"flat → 坐标"，
/// Sliver 懒构建下只有可见格子有 RenderObject——挂载/更新/卸载时维护映射，
/// 可见约 30 格，遍历零成本。
class _CellReg extends StatefulWidget {
  final int flat;
  final Map<int, BuildContext> registry;
  final Widget child;
  const _CellReg(
      {super.key, required this.flat, required this.registry, required this.child});

  @override
  State<_CellReg> createState() => _CellRegState();
}

/// 按字符串登记 BuildContext 的轻量包装（df72 批）：日期头/月份轴条目用。
/// 不用 GlobalObjectKey——其相等判断 identical(value)，两侧各自插值新建的
/// 同名 key 永不相等；真 GlobalKey 又会让 sliver 子项急切布局、渲染与滚动
/// 位置脱钩。注册表 + dispose 清理，语义与 _CellReg 相同。
class _CtxReg extends StatefulWidget {
  final Map<String, BuildContext> registry;
  final String tag;
  final Widget child;
  const _CtxReg(
      {required this.registry, required this.tag, required this.child});
  @override
  State<_CtxReg> createState() => _CtxRegState();
}

class _CtxRegState extends State<_CtxReg> {
  @override
  void initState() {
    widget.registry[widget.tag] = context;
    super.initState();
  }

  @override
  void didUpdateWidget(covariant _CtxReg old) {
    if (old.tag != widget.tag && widget.registry[old.tag] == context) {
      widget.registry.remove(old.tag);
    }
    widget.registry[widget.tag] = context;
    super.didUpdateWidget(old);
  }

  @override
  void dispose() {
    if (widget.registry[widget.tag] == context) {
      widget.registry.remove(widget.tag);
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}

class _CellRegState extends State<_CellReg> {
  @override
  void initState() {
    super.initState();
    widget.registry[widget.flat] = context;
  }

  @override
  void didUpdateWidget(covariant _CellReg old) {
    super.didUpdateWidget(old);
    if (old.flat != widget.flat) {
      // Sliver 复用 element：换号时先注销旧下标再登记新下标
      if (widget.registry[old.flat] == context) {
        widget.registry.remove(old.flat);
      }
      widget.registry[widget.flat] = context;
    }
  }

  @override
  void dispose() {
    if (widget.registry[widget.flat] == context) {
      widget.registry.remove(widget.flat);
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
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
