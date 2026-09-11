// 全屏查看器（0.6.0 重写并从 photos_page 抽出）：
// · 图片：PageView 左右滑 + InteractiveViewer 缩放 + 中图档预览（原有）
// · 视频：MediaProxy 本地代理流 + video_player 内嵌播放（自签 HTTPS 见
//   media_proxy.dart——ExoPlayer 不吃证书回调，经 127.0.0.1 中转）
// · 幻灯片：3 秒/张自动播放，结束停在最后一张
// · 信息/分享/删除操作内联（此前要退回列表才能操作）
import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show Clipboard, ClipboardData;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:video_player/video_player.dart';

import '../data/api/auth_cache.dart';
import '../data/api/files_api.dart';
import '../data/api/media_proxy.dart';
import '../data/api/purge.dart';
import '../data/local/gallery_saver.dart';
import '../data/net.dart';
import '../state/session.dart' show sessionProvider;
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'photo_models.dart';
import 'share_create_sheet.dart';
import 'theme/design_tokens.dart';
import 'widgets/auth_image.dart';
import 'widgets/open_local.dart';

class PhotoViewerPage extends ConsumerStatefulWidget {
  final String base;
  final List<PhotoItem> items;
  final int initial;

  /// 删除成功后经此返回 true，列表页据此刷新
  const PhotoViewerPage(
      {super.key, required this.base, required this.items, required this.initial});

  @override
  ConsumerState<PhotoViewerPage> createState() => _PhotoViewerPageState();
}

class _PhotoViewerPageState extends ConsumerState<PhotoViewerPage>
    with TickerProviderStateMixin {
  late int _index = widget.initial;
  // 图片加载失败重试：换 key 强制 AuthImage 重新解析
  int _imgRetry = 0;
  // 控制器必须持有并销毁：此前在 build 里每次 new PageController，
  // 翻页后任何重建都会把位置重置回 initial 且泄漏旧控制器
  late final PageController _pageController =
      PageController(initialPage: widget.initial);
  Timer? _slide;
  // 双击缩放（v0.17 预览大改）：受控变换 + 短动画，双击 1x↔2.5x、落点为中心
  final _transform = TransformationController();
  AnimationController? _zoomCtrl;
  // 缩放与翻页手势仲裁（R1-P3-6）：双指捏合/放大期间禁用 PageView 滑动，
  // 防止横向漂移误翻页并清零缩放；放大后也可正常横滑翻页（先收一手物理，
  // 简化实现：交互期间冻结翻页）
  bool _interacting = false;
  // 放大后的"平移模式"（改进建议 9.6-P1-8）：双击放大后单指拖动平移图片
  // 而不是翻页——跟踪当前缩放，>1x 时冻结 PageView，InteractiveViewer 接管
  // 平移；双击回 1x 自动恢复翻页
  bool _zoomed = false;

  @override
  void initState() {
    super.initState();
    _transform.addListener(_onTransformChanged);
  }

  void _onTransformChanged() {
    final zoomed = _transform.value.getMaxScaleOnAxis() > 1.005;
    if (zoomed != _zoomed && mounted) setState(() => _zoomed = zoomed);
  }

  @override
  void dispose() {
    _transform.removeListener(_onTransformChanged);
    _slide?.cancel();
    _zoomCtrl?.dispose();
    _transform.dispose();
    _pageController.dispose();
    super.dispose();
  }

  /// 双击图片：已放大 → 回 1x；1x → 以双击落点为中心放大 2.5x
  void _onDoubleTapImage(Offset localPos) {
    final cur = _transform.value.getMaxScaleOnAxis();
    _zoomCtrl?.dispose();
    final ctrl = AnimationController(
        duration: const Duration(milliseconds: 200), vsync: this);
    _zoomCtrl = ctrl;
    late final Animation<Matrix4> anim;
    if (cur > 1.05) {
      anim = Tween<Matrix4>(begin: _transform.value,
          end: Matrix4.identity()).animate(
          CurvedAnimation(parent: ctrl, curve: Curves.easeOut));
    } else {
      const target = 2.5;
      final x = -localPos.dx * (target - 1);
      final y = -localPos.dy * (target - 1);
      // Matrix4 构造是列主序：平移必须放第 4 列（此前写进矩阵主体，
      // 图片被变换飞出屏幕 = 双击黑屏根因），translate..scale 右乘语义稳妥
      anim = Tween<Matrix4>(
          begin: _transform.value,
          end: Matrix4.identity()
            ..translateByDouble(x, y, 0, 1)
            ..scaleByDouble(target, target, 1, 1)).animate(
          CurvedAnimation(parent: ctrl, curve: Curves.easeOut));
    }
    void listener() {
      _transform.value = anim.value;
      if (anim.isCompleted) {
        _transform.value = anim.value;
        ctrl.removeListener(listener);
      }
    }
    ctrl.addListener(listener);
    ctrl.forward();
  }

  void _toggleSlideshow() {
    if (_slide != null) {
      _slide?.cancel();
      _slide = null;
      setState(() {});
      return;
    }
    // 幻灯片间隔：局域网 3s / 公网 8s（蜂窝下大图加载慢，3s 会让
    // 播放永远追不上加载——v0.10.3 按入口自适应）
    final slide = Duration(seconds: isPublicAddress(widget.base) ? 8 : 3);
    _slide = Timer.periodic(slide, (_) {
      if (_index >= widget.items.length - 1) {
        _slide?.cancel();
        _slide = null;
        if (mounted) setState(() {});
        return;
      }
      _pageController.nextPage(
          duration: const Duration(milliseconds: 400), curve: Curves.easeInOut);
    });
    setState(() {});
  }

  Future<void> _shareCurrent() async {
    final it = widget.items[_index];
    // ignore: discarded_futures
    showShareCreate(context, path: it.path, name: it.name);
  }

  Future<void> _deleteCurrent() async {
    final it = widget.items[_index];
    final canManage = ref.read(sessionProvider).role != 'guest';
    if (!canManage) return;
    // 三选（v0.16.0）：删除进回收站可恢复；彻底删除两段式当场释放磁盘
    final how = await showDialog<String>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text('删除「${it.name}」？'),
                content: const Text('删除后进服务端回收站，可随时恢复；'
                    '彻底删除直接清出服务端磁盘，无法恢复。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx, 'cancel'),
                      child: const Text('取消')),
                  TextButton(
                      onPressed: () => Navigator.pop(dctx, 'purge'),
                      child: Text('彻底删除',
                          style: TextStyle(
                              color: Theme.of(dctx).colorScheme.error))),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, 'trash'),
                      child: const Text('删除')),
                ]));
    if (how == null || how == 'cancel' || !mounted) return;
    // 彻底删除不可逆：与照片墙批量路径同级别，再要一次独立确认
    if (how == 'purge') {
      final sure = await showDialog<bool>(
          context: context,
          builder: (dctx) => AlertDialog(
                  title: const Text('确认彻底删除？'),
                  content: Text('「${it.name}」将从服务端磁盘直接清除，'
                      '无法恢复、不进回收站。'),
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
      if (sure != true || !mounted) return;
    }
    try {
      final res =
          await purgeServerFiles([it.path], trashOnly: how == 'trash');
      if (!mounted) return;
      if (res.purged == 0 && res.staged == 0) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('删除失败')));
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(res.report), duration: const Duration(seconds: 2)));
      Navigator.pop(context, true);
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('删除失败')));
    }
  }

  void _showInfo() {
    final it = widget.items[_index];
    showDialog<void>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text(it.name,
                    style: const TextStyle(fontSize: 15)),
                content: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _row('类型', it.isVideo ? '视频' : '照片'),
                      // 文件页入口（uploader 为空）的 taken 实为文件修改时间，
                      // 标签随入口切换；来源空段不渲染
                      _row(it.uploader.isEmpty ? '修改时间' : '拍摄日期',
                          it.taken.isEmpty ? '-' : it.taken),
                      if (it.uploader.isNotEmpty) _row('来源', it.uploaderDisplay),
                      if (it.isVideo && it.durationLabel.isNotEmpty)
                        _row('时长', it.durationLabel),
                      const SizedBox(height: 6),
                      SelectableText(it.path,
                          style:
                              const TextStyle(fontSize: 12, color: Colors.grey)),
                    ]),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx),
                      child: const Text('关闭')),
                ]));
  }

  /// 长按操作菜单（改进建议 9.6-P1-8）：保存到相册/分享/复制路径/查看详情
  Future<void> _showActionSheet() async {
    final it = widget.items[_index];
    final canShare = ref.read(sessionProvider).role != 'guest';
    await showModalBottomSheet(
        context: context,
        showDragHandle: true,
        builder: (ctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              ListTile(
                  leading: const Icon(Icons.photo_library_outlined),
                  title: const Text('保存到相册'),
                  subtitle: const Text('下载原图存入系统相册 LAN Share 目录'),
                  onTap: () {
                    Navigator.pop(ctx);
                    _saveToGallery();
                  }),
              if (canShare)
                ListTile(
                    leading: const Icon(Icons.share_outlined),
                    title: const Text('分享'),
                    onTap: () {
                      Navigator.pop(ctx);
                      _shareCurrent();
                    }),
              ListTile(
                  leading: const Icon(Icons.copy_outlined),
                  title: const Text('复制路径'),
                  subtitle: Text(it.absPath ?? '/${it.path}',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  onTap: () async {
                    Navigator.pop(ctx);
                    await Clipboard.setData(
                        ClipboardData(text: it.absPath ?? '/${it.path}'));
                    if (!mounted) return;
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                        content: Text('已复制路径'),
                        duration: Duration(seconds: 2)));
                  }),
              ListTile(
                  leading: const Icon(Icons.info_outline),
                  title: const Text('查看详情'),
                  onTap: () {
                    Navigator.pop(ctx);
                    _showInfo();
                  }),
            ])));
  }

  /// 保存到相册：直接拉原图字节 → 临时文件 → MediaStore 通道入库
  Future<void> _saveToGallery() async {
    final it = widget.items[_index];
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('正在下载原图…'), duration: Duration(seconds: 2)));
    try {
      final bytes = await FilesApi.previewBytes(it.path);
      final tmp = File(
          '${(await getTemporaryDirectory()).path}'
          '/lanshare-viewer-${DateTime.now().millisecondsSinceEpoch}'
          '${it.name.contains('.') ? it.name.substring(it.name.lastIndexOf('.')) : ''}');
      await tmp.writeAsBytes(bytes, flush: true);
      final err = await GallerySaver.save(tmp.path, isVideo: it.isVideo);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(err ?? '已保存到系统相册'),
          duration: const Duration(seconds: 2)));
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('保存失败，请稍后重试'),
          duration: Duration(seconds: 2)));
    }
  }

  /// 预览 URL 单一事实源：显示与"重试放行负缓存"共用，改档位/路径
  /// 格式只动这里（review 0.12.0 P2——两份手写字符串迟早漂移）。
  /// 公网弱网加固：1280 原图档在蜂窝下太慢，退 960 档（手机屏仍清晰）。
  /// cacheKey（文件页入口=modified）追加 &v= 破同名覆盖后的陈旧缓存
  String _previewUrl(PhotoItem p) {
    var url = '${widget.base}/preview/${encPath(p.path)}?w='
        '${isPublicAddress(widget.base) ? 960 : 1280}';
    final v = p.cacheKey;
    if (v != null && v.isNotEmpty) {
      url += '&v=${Uri.encodeComponent(v)}';
    }
    return url;
  }

  /// 底部状态行：拍摄日期 / 上传者 任意缺省就跳过该段，两段都在才拼接
  String _statusLine(PhotoItem it) {
    final parts = [if (it.taken.isNotEmpty) it.taken, if (it.uploader.isNotEmpty) it.uploaderDisplay];
    return parts.isEmpty ? it.name : parts.join('  ·  ');
  }

  Widget _row(String k, String v) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 64,
            child: Text(k, style: const TextStyle(fontSize: 13, color: Colors.grey))),
        Expanded(child: Text(v, style: const TextStyle(fontSize: 13))),
      ]));

  @override
  Widget build(BuildContext context) {
    final it = widget.items[_index];
    final canManage = ref.watch(sessionProvider).role != 'guest';
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(it.name, style: const TextStyle(fontSize: 14)),
        actions: [
          IconButton(
              tooltip: _slide == null ? '幻灯片播放' : '停止幻灯片',
              icon: Icon(_slide == null
                  ? Icons.slideshow_outlined
                  : Icons.stop_circle_outlined),
              onPressed: _toggleSlideshow),
          if (canManage)
            IconButton(
                tooltip: '分享', icon: const Icon(Icons.share_outlined),
                onPressed: _shareCurrent),
          if (canManage)
            IconButton(
                tooltip: '删除',
                icon: const Icon(Icons.delete_outline),
                onPressed: _deleteCurrent),
          // 0.12.0：该文件已有下载完成的本地副本时，可一键交给系统应用打开
          ListenableBuilder(
              listenable: TransferEngine.I,
              builder: (context, _) {
                final done = TransferEngine.I.tasks
                    .where((t) =>
                        t.kind == TransferKind.download &&
                        t.remotePath == it.path &&
                        t.status == TransferStatus.done)
                    .toList();
                if (done.isEmpty) return const SizedBox.shrink();
                return IconButton(
                    tooltip: '用其他应用打开（已下载）',
                    icon: const Icon(Icons.open_in_new),
                    onPressed: () =>
                        openLocalFile(context, done.last.localPath));
              }),
          IconButton(
              tooltip: '下载原图',
              icon: const Icon(Icons.download_outlined),
              onPressed: () {
                TransferEngine.I.enqueueDownload(
                    remotePath: it.path, name: it.name, size: 0);
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                    content: Text('已加入下载队列'),
                    duration: Duration(seconds: 2)));
              }),
        ],
      ),
      body: Column(children: [
        Expanded(
          // 2026-09-03 美化：图片区不再黑得发闷——顶部深海军渐变过渡
          // （照片本身仍是黑底承托，观感从"空洞黑屏"变"渐变影棚"）
          child: DecoratedBox(
            decoration: const BoxDecoration(
                gradient: LinearGradient(
                    begin: Alignment.topLeft, end: Alignment.bottomRight,
                    colors: [
                  Color(0xFF0B0E18),
                  Color(0xFF101624),
                  Color(0xFF0B0E18)
                ])),
            child: PageView.builder(
              itemCount: widget.items.length,
              controller: _pageController,
              // 放大（平移模式）或捏合交互期间冻结翻页（9.6-P1-8）
              physics: (_interacting || _zoomed)
                  ? const NeverScrollableScrollPhysics()
                  : const PageScrollPhysics(),
              onPageChanged: (i) {
                _zoomCtrl?.stop(); // 跨页动画别把旧页矩阵写进新页（review P3）
                _transform.value = Matrix4.identity(); // 翻页回 1x
                setState(() => _index = i);
              },
              itemBuilder: (context, i) {
                final p = widget.items[i];
                return p.isVideo
                    ? _VideoPage(item: p)
                    : GestureDetector(
                        behavior: HitTestBehavior.opaque,
                        onDoubleTapDown: (d) => _onDoubleTapImage(
                            d.localPosition),
                        onDoubleTap: () {},
                        // 长按操作菜单（改进建议 9.6-P1-8）：保存到相册/
                        // 分享/复制路径/查看详情
                        onLongPress: _showActionSheet,
                        child: InteractiveViewer(
                        transformationController: _transform,
                        maxScale: 5,
                        onInteractionStart: (_) {
                          _zoomCtrl?.stop(); // 缩放动画与捏合抢写 transform（review P3）
                          setState(() => _interacting = true);
                        },
                        onInteractionEnd: (_) =>
                            setState(() => _interacting = false),
                        child: Center(
                          child: Hero(
                            tag: 'ph-${p.path}',
                            child: AuthImage(
                              key: ValueKey('viewer-${p.path}-$_imgRetry'),
                              url: _previewUrl(p),
                              fit: BoxFit.contain,
                              semanticLabel: p.name,
                              // 防黑屏闪烁：先用照片墙已缓存的缩略图垫底
                              // （磁盘秒出），高清在底图上渐进显现
                              placeholder: (ctx) =>
                                  _LoadingStage(base: widget.base, path: p.path),
                              errorBuilder: (ctx, _) => Column(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    const Icon(Icons.broken_image_outlined,
                                        size: 46, color: Colors.white38),
                                    const SizedBox(height: 10),
                                    const Text('加载失败，检查网络后重试',
                                        style: TextStyle(
                                            color: Colors.white54,
                                            fontSize: 12)),
                                    const SizedBox(height: Gap.xxs),
                                    TextButton.icon(
                                        onPressed: () {
                                          // 先放行负缓存再换 key 重试，否则
                                          // 命中负缓存秒回错误、重试形同虚设
                                          // （review 0.12.0 P2）
                                          AuthCacheManager.I
                                              .clearFailed(_previewUrl(p));
                                          setState(() => _imgRetry++);
                                        },
                                        icon: const Icon(Icons.refresh,
                                            size: 18),
                                        label: const Text('重试')),
                                  ]),
                            ),
                          ),
                        ),
                      ));
              },
            ),
          ),
        ),
        // 0.8.4：底部毛玻璃操作条（BackdropFilter 仅覆盖本条，不拖累图片层）
        Container(
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.35),
            border: Border(
                top: BorderSide(
                    color: Colors.white.withValues(alpha: 0.08), width: 0.5)),
          ),
          child: SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.only(left: 14, right: Gap.xxs, top: Gap.xxs, bottom: Gap.xxs),
              child: Row(children: [
                // 页码指示（v0.17 预览大改）：主流相册必备
                if (widget.items.length > 1)
                  Padding(
                    padding: const EdgeInsets.only(right: Gap.sm),
                    child: Text('${_index + 1}/${widget.items.length}',
                        style: const TextStyle(
                            color: Colors.white54, fontSize: 12)),
                  ),
                Expanded(
                  // 0.12.0：文件页入口进来 uploader 为空（/api/files 无归属
                  // 字段），空段不渲染，避免孤零零的 " · " 分隔点
                  child: Text(_statusLine(it),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white60, fontSize: 12)),
                ),
                IconButton(
                    tooltip: '信息',
                    icon: const Icon(Icons.info_outline,
                        color: Colors.white60, size: IconSizes.md),
                    onPressed: _showInfo),
              ]),
            ),
          ),
        ),
      ]),
    );
  }
}

// ── 图片加载过渡（2026-09-03 美化）────────────────────────
// 以照片墙已缓存的缩略图垫底（磁盘秒出，Hero 飞入不黑屏），
// 高清图在其上渐进显现；中央一枚柔光圆盘 + 小号 spinner，
// 替代此前的纯黑底 + 大号"加载中…"。
class _LoadingStage extends StatelessWidget {
  final String base;
  final String path;
  const _LoadingStage({required this.base, required this.path});

  @override
  Widget build(BuildContext context) {
    final enc = path.split('/').map(Uri.encodeComponent).join('/');
    return Stack(alignment: Alignment.center, children: [
      Positioned.fill(
          child: AuthImage(url: '$base/thumb/$enc', fit: BoxFit.contain)),
      Container(
          width: 72,
          height: 72,
          decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.35),
              shape: BoxShape.circle,
              border:
                  Border.all(color: Colors.white.withValues(alpha: 0.15))),
          child: const Center(
              child: SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(strokeWidth: 2.5)))),
    ]);
  }
}

// ── 视频页 ─────────────────────────────────────────────────

class _VideoPage extends StatefulWidget {
  final PhotoItem item;
  const _VideoPage({required this.item});

  @override
  State<_VideoPage> createState() => _VideoPageState();
}

class _VideoPageState extends State<_VideoPage> with WidgetsBindingObserver {
  VideoPlayerController? _ctrl;
  Future<void>? _initing;
  String? _error;

  @override
  void initState() {
    super.initState();
    // 生命周期观察者（R1-P2-3）：退后台只有声音没有画面的问题
    WidgetsBinding.instance.addObserver(this);
    _initing = _start();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final c = _ctrl;
    if (c == null || !c.value.isInitialized) return;
    if (state == AppLifecycleState.paused && c.value.isPlaying) {
      c.pause();
      if (mounted) setState(() {});
    }
  }

  Future<void> _start() async {
    try {
      final url = await MediaProxy.I.urlFor(widget.item.path);
      // 翻页动画期间页面可能已销毁：此刻再建控制器会泄漏、play 会
      // 打在已 dispose 的控制器上，catch 又会误杀共享代理（对抗审查 P2）
      if (!mounted) return;
      final c = VideoPlayerController.networkUrl(Uri.parse(url));
      _ctrl = c;
      await c.initialize();
      if (!mounted) return;
      await c.play();
      if (mounted) setState(() {});
    } catch (_) {
      // 页面已销毁：不动共享代理（可能正在给下一页供流），
      // 也不再碰已随 State 释放的控制器
      if (!mounted) return;
      // 公网弱网加固：回源 socket 可能已僵死——重试前重置代理并释放
      // 旧控制器，否则"重试"永远复用同一个坏连接
      // ignore: discarded_futures
      MediaProxy.I.reset();
      _ctrl?.dispose();
      _ctrl = null;
      if (mounted) {
        setState(() => _error = '视频无法播放');
      }
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _ctrl?.dispose();
    super.dispose();
  }

  String _fmt(Duration d) {
    final s = d.inSeconds;
    final m = s ~/ 60;
    final sec = s % 60;
    final h = m ~/ 60;
    return h > 0
        ? '$h:${(m % 60).toString().padLeft(2, '0')}:${sec.toString().padLeft(2, '0')}'
        : '$m:${sec.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final init = _initing;
    if (init != null) {
      return FutureBuilder<void>(
          future: init,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return const Center(child: CircularProgressIndicator());
            }
            return _body();
          });
    }
    return _body();
  }

  Widget _body() {
    final c = _ctrl;
    if (_error != null || c == null || !c.value.isInitialized) {
      return Center(
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
        const Icon(Icons.videocam_off, size: 48, color: Colors.white38),
        const SizedBox(height: Gap.xs),
        Text(_error ?? '加载中…', style: const TextStyle(color: Colors.white60)),
        const SizedBox(height: Gap.xs),
        TextButton(
            onPressed: () {
              setState(() {
                _error = null;
                _initing = _start();
              });
            },
            child: const Text('重试')),
      ]));
    }
    return GestureDetector(
        onTap: () {
          c.value.isPlaying ? c.pause() : c.play();
          setState(() {});
        },
        onDoubleTapDown: (d) {
          // 主流播放器手势：左 1/3 快退 10s、右 1/3 快进 10s、中间播放/暂停
          final w = MediaQuery.of(context).size.width;
          final pos = c.value.position;
          if (d.localPosition.dx < w / 3) {
            c.seekTo(pos - const Duration(seconds: 10) < Duration.zero
                ? Duration.zero
                : pos - const Duration(seconds: 10));
          } else if (d.localPosition.dx > w * 2 / 3) {
            final target = pos + const Duration(seconds: 10);
            c.seekTo(target > c.value.duration ? c.value.duration : target);
          } else {
            c.value.isPlaying ? c.pause() : c.play();
          }
          if (mounted) setState(() {});
        },
        onDoubleTap: () {},
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          Expanded(
              child: Center(
                  child: AspectRatio(
                      aspectRatio: c.value.aspectRatio,
                      child: VideoPlayer(c)))),
          // 迷你控制条：进度 + 拖动 seek（tap 正文即暂停/播放）
          ListenableBuilder(
              listenable: c,
              builder: (context, _) => Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: Gap.sm, vertical: 6),
                  child: Row(children: [
                    IconButton(
                        icon: Icon(
                            c.value.isPlaying
                                ? Icons.pause
                                : Icons.play_arrow,
                            color: Colors.white),
                        onPressed: () {
                          c.value.isPlaying ? c.pause() : c.play();
                          setState(() {});
                        }),
                    // 倍速（v0.17 预览大改）：1.0/1.25/1.5/2.0 轮换
                    GestureDetector(
                        onTap: () {
                          const speeds = [1.0, 1.25, 1.5, 2.0];
                          final i = speeds.indexWhere(
                              (e) => e == c.value.playbackSpeed);
                          c.setPlaybackSpeed(
                              speeds[(i + 1) % speeds.length]);
                          if (mounted) setState(() {});
                        },
                        child: Text(
                            '×${c.value.playbackSpeed == 1.0 ? '1' : c.value.playbackSpeed.toString()}',
                            style: const TextStyle(
                                color: Colors.white70, fontSize: 12))),
                    const SizedBox(width: Gap.xxs),
                    // 静音开关：看视频不想外放声的主流需求
                    IconButton(
                        icon: Icon(
                            c.value.volume == 0
                                ? Icons.volume_off
                                : Icons.volume_up,
                            color: Colors.white70, size: IconSizes.md),
                        onPressed: () {
                          c.setVolume(c.value.volume == 0 ? 1.0 : 0.0);
                          setState(() {});
                        }),
                    Text(
                        '${_fmt(c.value.position)} / ${_fmt(c.value.duration)}',
                        style:
                            const TextStyle(color: Colors.white70, fontSize: 12)),
                    Expanded(
                        child: Slider(
                            min: 0,
                            max: c.value.duration.inMilliseconds
                                .toDouble()
                                .clamp(1, double.infinity),
                            value: c.value.position.inMilliseconds
                                .toDouble()
                                .clamp(
                                    0,
                                    c.value.duration.inMilliseconds
                                        .toDouble()),
                            onChanged: (v) {
                              c.seekTo(Duration(milliseconds: v.round()));
                            })),
                  ]))),
        ]));
  }
}
