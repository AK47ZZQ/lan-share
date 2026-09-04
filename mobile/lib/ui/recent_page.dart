// 最近文件 + 搜索（App v0.6.0）：/api/recent 全库按修改时间倒排；
// 搜索框走 /api/search（服务端 rglob 文件名子串匹配）。点按进文件操作面板。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/api_client.dart';
import '../state/session.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'share_create_sheet.dart';
import 'widgets/open_local.dart';

class RecentPage extends ConsumerStatefulWidget {
  const RecentPage({super.key});

  @override
  ConsumerState<RecentPage> createState() => _RecentPageState();
}

class _RecentItem {
  final String name;
  final String path;
  final String sizeHuman;
  final String modified;
  final String? ext;
  _RecentItem({required this.name, required this.path,
      required this.sizeHuman, required this.modified, required this.ext});

  factory _RecentItem.fromJson(Map<String, dynamic> j) => _RecentItem(
      name: j['name'] as String? ?? '',
      path: j['path'] as String? ?? '',
      sizeHuman: j['size_human'] as String? ?? '-',
      modified: j['modified'] as String? ?? '',
      ext: j['ext'] as String?);
}

class _RecentPageState extends ConsumerState<RecentPage> {
  final _search = TextEditingController();
  List<_RecentItem>? _items;
  bool _searching = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/recent', queryParameters: {'limit': 100});
      final items = (r.data as List)
          .map((e) => _RecentItem.fromJson(Map<String, dynamic>.from(e as Map)))
          .toList();
      if (!mounted) return;
      setState(() => _items = items);
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _items = [];
        _error = '加载失败';
      });
    }
  }

  Future<void> _doSearch() async {
    final q = _search.text.trim();
    if (q.isEmpty) return _load();
    setState(() => _searching = true);
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get('/api/search', queryParameters: {'q': q});
      final items = (r.data as List)
          .map((e) => _RecentItem.fromJson(Map<String, dynamic>.from(e as Map)))
          .toList();
      if (!mounted) return;
      setState(() {
        _items = items;
        _searching = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _items = [];
        _error = '搜索失败';
        _searching = false;
      });
    }
  }

  Future<void> _itemSheet(_RecentItem it) async {
    final canShare = ref.read(sessionProvider).role != 'guest';
    final downloaded = TransferEngine.I.tasks
        .where((t) =>
            t.kind == TransferKind.download &&
            t.remotePath == it.path &&
            t.status == TransferStatus.done)
        .toList();
    await showModalBottomSheet(
        context: context,
        builder: (ctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              Padding(
                  padding: const EdgeInsets.all(16),
                  child: Align(
                      alignment: Alignment.centerLeft,
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                        Text(it.name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.bold)),
                        const SizedBox(height: 4),
                        Text('${it.sizeHuman} · ${it.modified.split('T').first}',
                            style: const TextStyle(
                                fontSize: 12, color: Colors.grey)),
                      ]))),
              const Divider(height: 1),
              ListTile(
                  leading: const Icon(Icons.download),
                  title: const Text('下载到手机'),
                  onTap: () {
                    Navigator.pop(ctx);
                    // ignore: discarded_futures
                    TransferEngine.I.enqueueDownload(
                        remotePath: it.path, name: it.name, size: 0);
                  }),
              if (downloaded.isNotEmpty)
                ListTile(
                    leading: const Icon(Icons.open_in_new),
                    title: const Text('用其他应用打开'),
                    onTap: () {
                      Navigator.pop(ctx);
                      openLocalFile(context, downloaded.last.localPath);
                    }),
              if (canShare)
                ListTile(
                    leading: const Icon(Icons.share_outlined),
                    title: const Text('创建分享链接'),
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      showShareCreate(context, path: it.path, name: it.name);
                    }),
              ListTile(
                  leading: const Icon(Icons.info_outline),
                  title: const Text('所在位置'),
                  subtitle: Text(it.path,
                      style: const TextStyle(fontSize: 11)),
                  onTap: () {
                    Navigator.pop(ctx);
                  }),
              const SizedBox(height: 8),
            ])));
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    return Scaffold(
        appBar: AppBar(title: const Text('最近文件')),
        body: Column(children: [
          Padding(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
              child: TextField(
                  controller: _search,
                  textInputAction: TextInputAction.search,
                  onSubmitted: (_) => _doSearch(),
                  decoration: InputDecoration(
                      isDense: true,
                      prefixIcon: const Icon(Icons.search),
                      hintText: '搜索文件名',
                      border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(10)),
                      suffixIcon: IconButton(
                          icon: const Icon(Icons.arrow_forward),
                          onPressed: _doSearch)))),
          Expanded(
            child: _searching
                ? const Center(child: CircularProgressIndicator())
                : items == null
                    ? const Center(child: CircularProgressIndicator())
                    : items.isEmpty
                        ? ListView(children: [
                            const SizedBox(height: 120),
                            Center(
                                child: Text(_error ?? '暂无文件',
                                    style:
                                        const TextStyle(color: Colors.grey))),
                            const SizedBox(height: 8),
                            Center(child: TextButton(
                                onPressed: _load, child: const Text('刷新'))),
                          ])
                        : RefreshIndicator(
                            onRefresh: _load,
                            child: ListView.builder(
                                itemCount: items.length,
                                itemBuilder: (context, i) {
                                  final it = items[i];
                                  return ListTile(
                                      leading: const Icon(
                                          Icons.insert_drive_file_outlined),
                                      title: Text(it.name,
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis),
                                      subtitle: Text(
                                          '${it.sizeHuman} · ${it.modified.split('T').first} · ${it.path.split('/').length > 2 ? it.path.split('/').sublist(0, it.path.split('/').length - 1).join('/') : '根目录'}',
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          style:
                                              const TextStyle(fontSize: 12)),
                                      onTap: () => _itemSheet(it));
                                })),
          ),
        ]));
  }
}
