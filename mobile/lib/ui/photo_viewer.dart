// 全屏查看器（0.6.0 重写并从 photos_page 抽出）：
// · 图片：PageView 左右滑 + InteractiveViewer 缩放 + 中图档预览（原有）
// · 视频：MediaProxy 本地代理流 + video_player 内嵌播放（自签 HTTPS 见
//   media_proxy.dart——ExoPlayer 不吃证书回调，经 127.0.0.1 中转）
// · 幻灯片：3 秒/张自动播放，结束停在最后一张
// · 信息/分享/删除操作内联（此前要退回列表才能操作）
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';

import '../data/api/api_client.dart';
import '../data/api/media_proxy.dart';
import '../state/session.dart' show sessionProvider;
import '../transfer/transfer_engine.dart';
import 'photo_models.dart';
import 'share_create_sheet.dart';
import 'widgets/auth_image.dart';

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

class _PhotoViewerPageState extends ConsumerState<PhotoViewerPage> {
  late int _index = widget.initial;
  // 控制器必须持有并销毁：此前在 build 里每次 new PageController，
  // 翻页后任何重建都会把位置重置回 initial 且泄漏旧控制器
  late final PageController _pageController =
      PageController(initialPage: widget.initial);
  Timer? _slide;

  @override
  void dispose() {
    _slide?.cancel();
    _pageController.dispose();
    super.dispose();
  }

  void _toggleSlideshow() {
    if (_slide != null) {
      _slide?.cancel();
      _slide = null;
      setState(() {});
      return;
    }
    _slide = Timer.periodic(const Duration(seconds: 3), (_) {
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
    final ok = await showDialog<bool>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text('删除「${it.name}」？'),
                content: const Text('将进入服务端回收站，可随时恢复。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, true),
                      child: const Text('删除')),
                ]));
    if (ok != true) return;
    try {
      final dio = await ApiClient.I.dio();
      await dio.delete('/api/files/${encPath(it.path)}');
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('删除失败')));
      return;
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(const SnackBar(content: Text('已删除'), duration: Duration(seconds: 2)));
    Navigator.pop(context, true);
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
                      _row('拍摄日期', it.taken.isEmpty ? '-' : it.taken),
                      _row('来源', it.uploader),
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
          child: PageView.builder(
            itemCount: widget.items.length,
            controller: _pageController,
            onPageChanged: (i) => setState(() => _index = i),
            itemBuilder: (context, i) {
              final p = widget.items[i];
              return p.isVideo
                  ? _VideoPage(item: p)
                  : InteractiveViewer(
                      maxScale: 5,
                      child: Center(
                        child: Hero(
                          tag: 'ph-${p.path}',
                          child: AuthImage(
                          url:
                              '${widget.base}/preview/${encPath(p.path)}?w=1280',
                          fit: BoxFit.contain,
                          placeholder: (ctx) => const Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                CircularProgressIndicator(),
                                SizedBox(height: 12),
                                Text('加载中…', style: TextStyle(color: Colors.white70)),
                              ]),
                          errorBuilder: (ctx, _) => const Icon(
                              Icons.broken_image_outlined,
                              size: 48, color: Colors.white38),
                          ),
                        ),
                      ),
                    );
            },
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
              padding: const EdgeInsets.only(left: 14, right: 4, top: 4, bottom: 4),
              child: Row(children: [
                Expanded(
                  child: Text('${it.taken}  ·  ${it.uploader}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white60, fontSize: 12)),
                ),
                IconButton(
                    tooltip: '信息',
                    icon: const Icon(Icons.info_outline,
                        color: Colors.white60, size: 20),
                    onPressed: _showInfo),
              ]),
            ),
          ),
        ),
      ]),
    );
  }
}

// ── 视频页 ─────────────────────────────────────────────────

class _VideoPage extends StatefulWidget {
  final PhotoItem item;
  const _VideoPage({required this.item});

  @override
  State<_VideoPage> createState() => _VideoPageState();
}

class _VideoPageState extends State<_VideoPage> {
  VideoPlayerController? _ctrl;
  Future<void>? _initing;
  String? _error;

  @override
  void initState() {
    super.initState();
    _initing = _start();
  }

  Future<void> _start() async {
    try {
      final url = await MediaProxy.I.urlFor(widget.item.path);
      final c = VideoPlayerController.networkUrl(Uri.parse(url));
      _ctrl = c;
      await c.initialize();
      await c.play();
      if (mounted) setState(() {});
    } catch (_) {
      if (mounted) {
        setState(() => _error = '视频无法播放');
      }
    }
  }

  @override
  void dispose() {
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
        const SizedBox(height: 8),
        Text(_error ?? '加载中…', style: const TextStyle(color: Colors.white60)),
        const SizedBox(height: 8),
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
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
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
