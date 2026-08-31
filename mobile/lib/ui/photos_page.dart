// 照片时间线：/api/photos 分页网格 + 点击全屏查看（左右滑动 + 双指缩放）。
import 'package:flutter/material.dart';

import '../data/api/api_client.dart';
import '../data/local/prefs.dart';
import 'widgets/auth_image.dart';

class PhotoItem {
  final String path;
  final String name;
  final String taken;
  final String month;
  final String uploader;
  PhotoItem({required this.path, required this.name, required this.taken,
      required this.month, required this.uploader});

  factory PhotoItem.fromJson(Map<String, dynamic> j) => PhotoItem(
      path: j['path'] as String,
      name: j['name'] as String,
      taken: j['taken'] as String? ?? '',
      month: j['month'] as String? ?? '',
      uploader: j['uploader'] as String? ?? '共享空间');
}

String _encPath(String p) => p.split('/').map(Uri.encodeComponent).join('/');

class PhotosPage extends StatefulWidget {
  final VoidCallback? onGoFiles;
  const PhotosPage({super.key, this.onGoFiles});

  @override
  State<PhotosPage> createState() => _PhotosPageState();
}

class _PhotosPageState extends State<PhotosPage> {
  final _items = <PhotoItem>[];
  final _scroll = ScrollController();
  String? _base; // 服务器地址（异步取到后再刷网格）
  bool _loading = false;
  bool _failed = false;
  bool _done = false;
  int _offset = 0;
  int _gen = 0;                 // 加载代际：刷新后丢弃过期响应，防重复/漂移
  static const _limit = 120;

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

  Future<void> _loadMore() async {
    if (_loading || _done || !mounted) return;
    final gen = _gen;
    setState(() => _loading = true);
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/photos',
          queryParameters: {'limit': _limit, 'offset': _offset});
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
      _loading = false;
    }
  }

  Future<void> _refresh() async {
    _gen++;
    setState(() {
      _items.clear();
      _offset = 0;
      _done = false;
      _failed = false;
    });
    await _loadMore();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('照片')),
      body: _base == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _refresh,
              child: _items.isEmpty && !_loading
                  ? ListView(children: [
                      const SizedBox(height: 140),
                      Center(child: Text(_failed ? '加载失败' : '相册还是空的')),
                      const SizedBox(height: 8),
                      Center(child: FilledButton.tonal(
                          onPressed: _refresh, child: const Text('重新加载'))),
                    ])
                  : GridView.builder(
                      controller: _scroll,
                      padding: const EdgeInsets.all(2),
                      gridDelegate:
                          const SliverGridDelegateWithFixedCrossAxisCount(
                              crossAxisCount: 3,
                              mainAxisSpacing: 2,
                              crossAxisSpacing: 2),
                      itemCount: _items.length + (_done ? 0 : 3),
                      itemBuilder: (context, i) {
                        if (i >= _items.length) {
                          return const ColoredBox(
                              color: Color(0x22888888),
                              child: Center(
                                  child: SizedBox(width: 16, height: 16,
                                      child: CircularProgressIndicator(
                                          strokeWidth: 2))));
                        }
                        final it = _items[i];
                        return GestureDetector(
                          onTap: _base == null ? null : () => _openViewer(i),
                          child: AuthImage(
                              url: '$_base/thumb/${_encPath(it.path)}'),
                        );
                      },
                    ),
            ),
    );
  }

  void _openViewer(int index) {
    Navigator.push(context, MaterialPageRoute(
        builder: (_) => PhotoViewerPage(base: _base!, items: _items, initial: index)));
  }
}

class PhotoViewerPage extends StatefulWidget {
  final String base;
  final List<PhotoItem> items;
  final int initial;
  const PhotoViewerPage(
      {super.key, required this.base, required this.items, required this.initial});

  @override
  State<PhotoViewerPage> createState() => _PhotoViewerPageState();
}

class _PhotoViewerPageState extends State<PhotoViewerPage> {
  late int _index = widget.initial;

  @override
  Widget build(BuildContext context) {
    final it = widget.items[_index];
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(it.name, style: const TextStyle(fontSize: 14)),
      ),
      body: Column(children: [
        Expanded(
          child: PageView.builder(
            itemCount: widget.items.length,
            controller: PageController(initialPage: _index),
            onPageChanged: (i) => setState(() => _index = i),
            itemBuilder: (context, i) {
              final p = widget.items[i];
              return InteractiveViewer(
                maxScale: 5,
                child: Center(
                  child: AuthImage(
                    url:
                        '${widget.base}/preview/${_encPath(p.path)}?w=1280',
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
              );
            },
          ),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Text('${it.taken}  ·  ${it.uploader}',
                style: const TextStyle(color: Colors.white60, fontSize: 12)),
          ),
        ),
      ]),
    );
  }
}
