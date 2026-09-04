// 带鉴权与自签信任的图片组件：走 AuthCacheManager（Bearer 头 + SPKI 信任），
// 磁盘缓存由 flutter_cache_manager 管理——替代 cached_network_image 的最小实现。
import 'dart:io';

import 'package:flutter/material.dart';

import '../../data/api/auth_cache.dart';

class AuthImage extends StatefulWidget {
  final String url;
  final BoxFit fit;
  final Widget Function(BuildContext)? placeholder;
  final Widget Function(BuildContext, Object error)? errorBuilder;
  const AuthImage({super.key, required this.url, this.fit = BoxFit.cover,
      this.placeholder, this.errorBuilder});

  @override
  State<AuthImage> createState() => _AuthImageState();
}

class _AuthImageState extends State<AuthImage> {
  // 同步快路径：本会话已解析过的文件直接给 Image.file，重建零转圈；
  // 否则走 FutureBuilder（磁盘缓存/下载），成功后进记忆表
  File? _instant;
  late Future<File> _future;

  @override
  void initState() {
    super.initState();
    _resolve();
  }

  void _resolve() {
    // 负缓存命中（0.12.0）：本会话已稳定失败的 URL 直接走错误分支，
    // 不再重复请求打共享缩略图通道
    if (AuthCacheManager.I.isFailed(widget.url)) {
      _instant = null;
      _future = Future.error('negative-cache');
      return;
    }
    _instant = AuthCacheManager.I.peekResolved(widget.url);
    if (_instant != null) {
      _future = Future.value(_instant);
    } else {
      _future = AuthCacheManager.I
          .getSingleFile(widget.url)
          .then((f) {
            AuthCacheManager.I.rememberResolved(widget.url, f);
            return f;
          }, onError: (Object e) {
            // 只把"服务端稳定出不了图"记入负缓存：404（无缩略图/不可见）
            // 与 5xx（损坏媒体/缺解码器，内部已重试过一次）。401/429/断网/
            // 超时是瞬时故障，不记——否则 token 过期瞬间整屏缩略图会被
            // 固化成 10 分钟坏图（review 0.12.0 P1）
            final sc = e is ImageFetchException ? e.statusCode : null;
            if (sc == 404 || (sc != null && sc >= 500)) {
              AuthCacheManager.I.markFailed(widget.url);
            }
            throw e;
          });
    }
  }

  @override
  void didUpdateWidget(covariant AuthImage old) {
    super.didUpdateWidget(old);
    if (old.url != widget.url) {
      _resolve();
    }
  }

  @override
  Widget build(BuildContext context) {
    final instant = _instant;
    if (instant != null) {
      return Image.file(instant, fit: widget.fit, gaplessPlayback: true);
    }
    return FutureBuilder<File>(
      future: _future,
      builder: (context, snap) {
        if (snap.connectionState != ConnectionState.done) {
          return widget.placeholder?.call(context) ??
              const ColoredBox(
                  color: Color(0x22888888),
                  child: Center(
                      child: SizedBox(width: 16, height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2))));
        }
        if (snap.hasError || !snap.hasData) {
          return widget.errorBuilder?.call(context, snap.error ?? 'error') ??
              const ColoredBox(
                  color: Color(0x33888888),
                  child: Icon(Icons.broken_image_outlined));
        }
        return Image.file(snap.data!, fit: widget.fit, gaplessPlayback: true);
      },
    );
  }
}
