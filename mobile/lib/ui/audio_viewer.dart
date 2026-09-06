// 音频预览器（v0.18 预览扩面）：mp3/m4a/flac/wav/ogg 等直接在 App 内播放。
// 复用照片墙视频的 media_proxy 本地回环通道（ExoPlayer 经 127.0.0.1 带
// Bearer 回源 /preview/，Range 转发支持进度条拖动），播放内核仍是
// video_player（ExoPlayer 对纯音频文件天然支持，画面区用封面占位）。
import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../data/api/media_proxy.dart';

/// ExoPlayer（video_player 底座）能直接吃的音频后缀。wma 这类它不支持的
/// 格式不放入（回退操作面板，下载后系统打开）。
const kAudioExts = {
  '.mp3', '.m4a', '.aac', '.flac', '.wav', '.ogg', '.oga', '.opus',
};

/// 按文件名判断是否可进音频预览器（files_page/recent_page 共用口径）
bool isAudioPreviewable(String name) {
  final i = name.lastIndexOf('.');
  if (i < 0) return false;
  return kAudioExts.contains(name.substring(i).toLowerCase());
}

class AudioViewerPage extends StatefulWidget {
  final String path; // 远端相对路径（/ 分隔）
  final String name;
  const AudioViewerPage({super.key, required this.path, required this.name});

  @override
  State<AudioViewerPage> createState() => _AudioViewerPageState();
}

class _AudioViewerPageState extends State<AudioViewerPage> {
  VideoPlayerController? _ctrl;
  bool _failed = false;
  double? _dragValue; // 拖动进度条期间的临时值（不被位置流抢走）

  @override
  void initState() {
    super.initState();
    _start();
  }

  Future<void> _start() async {
    try {
      final url = await MediaProxy.I.urlFor(widget.path);
      final c = VideoPlayerController.networkUrl(Uri.parse(url));
      await c.initialize();
      await c.play();
      if (!mounted) {
        await c.dispose();
        return;
      }
      setState(() => _ctrl = c);
    } catch (_) {
      if (mounted) setState(() => _failed = true);
    }
  }

  @override
  void dispose() {
    _ctrl?.dispose();
    super.dispose();
  }

  String _fmt(Duration d) {
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    final h = d.inHours;
    return h > 0 ? '$h:$m:$s' : '$m:$s';
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: Text(widget.name,
          style: const TextStyle(fontSize: 14,
              overflow: TextOverflow.ellipsis))),
      body: _failed
          ? Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Icon(Icons.error_outline, size: 46, color: Colors.white38),
              const SizedBox(height: 10),
              const Text('加载失败', style: TextStyle(fontSize: 13)),
              const SizedBox(height: 8),
              TextButton.icon(
                  onPressed: () {
                    setState(() => _failed = false);
                    _start();
                  },
                  icon: const Icon(Icons.refresh, size: 18),
                  label: const Text('重试')),
            ]))
          : _ctrl == null
              ? const Center(child: CircularProgressIndicator())
              : Center(
                  child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 32),
                      child: Column(mainAxisSize: MainAxisSize.min,
                          children: [
                    // 封面占位（无专辑图场景）：主题色圆形渐变
                    Container(
                        width: 168,
                        height: 168,
                        decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            gradient: LinearGradient(
                                begin: Alignment.topLeft,
                                end: Alignment.bottomRight,
                                colors: [
                                  cs.primary.withValues(alpha: 0.35),
                                  cs.tertiary.withValues(alpha: 0.25),
                                ])),
                        child: Icon(Icons.music_note,
                            size: 64, color: cs.primary)),
                    const SizedBox(height: 20),
                    Text(widget.name,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                            fontSize: 16, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 24),
                    // 进度条 + 时间
                    ValueListenableBuilder<VideoPlayerValue>(
                        valueListenable: _ctrl!,
                        builder: (_, v, _) {
                          final dur = _ctrl!.value.duration;
                          final durMs = dur.inMilliseconds;
                          final sliderVal = _dragValue ??
                              (durMs > 0
                                  ? (v.position.inMilliseconds / durMs)
                                      .clamp(0.0, 1.0)
                                  : 0.0);
                          return Column(children: [
                            Slider(
                                value: sliderVal,
                                onChangeStart: (_) =>
                                    setState(() => _dragValue = sliderVal),
                                onChanged: (x) =>
                                    setState(() => _dragValue = x),
                                onChangeEnd: (x) {
                                  _dragValue = null;
                                  if (durMs > 0) {
                                    _ctrl!.seekTo(dur * x);
                                  }
                                  setState(() {});
                                }),
                            Row(mainAxisAlignment:
                                MainAxisAlignment.spaceBetween, children: [
                              Text(_fmt(_dragValue != null && durMs > 0
                                      ? dur * _dragValue!
                                      : v.position),
                                  style: TextStyle(
                                      fontSize: 11.5,
                                      color: cs.onSurfaceVariant)),
                              Text(_fmt(dur),
                                  style: TextStyle(
                                      fontSize: 11.5,
                                      color: cs.onSurfaceVariant)),
                            ]),
                          ]);
                        }),
                    const SizedBox(height: 8),
                    ValueListenableBuilder<VideoPlayerValue>(
                        valueListenable: _ctrl!,
                        builder: (_, v, _) => Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              IconButton(
                                  onPressed: () {
                                    final t = v.position -
                                        const Duration(seconds: 10);
                                    _ctrl!.seekTo(t < Duration.zero
                                        ? Duration.zero
                                        : t);
                                  },
                                  icon: const Icon(Icons.replay_10)),
                              const SizedBox(width: 8),
                              FilledButton(
                                  style: FilledButton.styleFrom(
                                      shape: const CircleBorder(),
                                      padding: const EdgeInsets.all(18)),
                                  onPressed: () {
                                    if (v.isPlaying) {
                                      _ctrl!.pause();
                                    } else {
                                      _ctrl!.play();
                                    }
                                  },
                                  child: Icon(v.isPlaying
                                      ? Icons.pause
                                      : Icons.play_arrow,
                                      size: 30)),
                              const SizedBox(width: 8),
                              IconButton(
                                  onPressed: () {
                                    final dur = _ctrl!.value.duration;
                                    var t = v.position +
                                        const Duration(seconds: 10);
                                    if (t > dur) t = dur;
                                    _ctrl!.seekTo(t);
                                  },
                                  icon: const Icon(Icons.forward_10)),
                            ])),
                  ]))),
    );
  }
}
