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
  late Future<File> _future;

  @override
  void initState() {
    super.initState();
    _future = AuthCacheManager.I.getSingleFile(widget.url);
  }

  @override
  void didUpdateWidget(covariant AuthImage old) {
    super.didUpdateWidget(old);
    if (old.url != widget.url) {
      _future = AuthCacheManager.I.getSingleFile(widget.url);
    }
  }

  @override
  Widget build(BuildContext context) {
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
