// 文件浏览：/api/files 目录列表 + 面包屑导航（M-A1 只读；上传/操作 M-A2 接入）。
// 导航模型：栈里存每层 (path, items)——前进 fetch 入栈、面包屑回退直接出栈不重新请求。
import 'package:flutter/material.dart';

import '../data/api/api_client.dart';

class Entry {
  final String name;
  final String path;
  final bool isDir;
  final bool isPrivateRoot;
  final String sizeHuman;
  final String modified;
  final String? ext;
  Entry({required this.name, required this.path, required this.isDir,
      required this.isPrivateRoot, required this.sizeHuman,
      required this.modified, this.ext});

  factory Entry.fromJson(Map<String, dynamic> j) => Entry(
      name: j['name'] as String,
      path: j['path'] as String,
      isDir: j['is_dir'] as bool,
      isPrivateRoot: j['is_private_root'] as bool? ?? false,
      sizeHuman: j['size_human'] as String? ?? '',
      modified: j['modified'] as String? ?? '',
      ext: j['ext'] as String?);
}

class _Dir {
  final String path;
  final List<Entry> items;
  _Dir(this.path, this.items);
}

IconData _iconOf(Entry e) {
  if (e.isDir) return e.isPrivateRoot ? Icons.lock : Icons.folder;
  final ext = (e.ext ?? '').toLowerCase();
  if (['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.heif'].contains(ext)) {
    return Icons.image_outlined;
  }
  if (['.mp4', '.mkv', '.mov', '.webm', '.avi'].contains(ext)) {
    return Icons.movie_outlined;
  }
  if (['.mp3', '.flac', '.wav', '.m4a'].contains(ext)) return Icons.music_note;
  if (['.zip', '.7z', '.rar'].contains(ext)) return Icons.archive_outlined;
  if (ext == '.pdf') return Icons.picture_as_pdf_outlined;
  if (['.doc', '.docx'].contains(ext)) return Icons.description_outlined;
  if (['.xls', '.xlsx', '.csv'].contains(ext)) return Icons.table_chart_outlined;
  return Icons.insert_drive_file_outlined;
}

class FilesPage extends StatefulWidget {
  const FilesPage({super.key});

  @override
  State<FilesPage> createState() => _FilesPageState();
}

class _FilesPageState extends State<FilesPage> {
  final _stack = <_Dir>[];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _push('');
  }

  String get _path => _stack.isEmpty ? '' : _stack.last.path;
  List<Entry> get _current => _stack.isEmpty ? const [] : _stack.last.items;

  /// 进入一层目录（push）
  Future<void> _push(String path) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get(path.isEmpty ? '/api/files' : '/api/files/$path');
      final items = (r.data['items'] as List)
          .map((e) => Entry.fromJson(e as Map<String, dynamic>))
          .toList()
        ..sort((a, b) {
          if (a.isDir != b.isDir) return a.isDir ? -1 : 1;
          return a.name.toLowerCase().compareTo(b.name.toLowerCase());
        });
      if (!mounted) return;
      setState(() {
        _stack.add(_Dir(path, items));
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = '目录加载失败';
      });
    }
  }

  /// 刷新当前层（替换栈顶）
  Future<void> _refresh() async {
    final target = _path;
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.get(target.isEmpty ? '/api/files' : '/api/files/$target');
      final items = (r.data['items'] as List)
          .map((e) => Entry.fromJson(e as Map<String, dynamic>))
          .toList()
        ..sort((a, b) {
          if (a.isDir != b.isDir) return a.isDir ? -1 : 1;
          return a.name.toLowerCase().compareTo(b.name.toLowerCase());
        });
      if (!mounted) return;
      setState(() {
        if (_stack.isNotEmpty) _stack.last = _Dir(target, items);
      });
    } catch (_) {}
  }

  /// 面包屑回退：直接出栈，不重新请求
  void _popTo(int depth) {
    if (_stack.length <= depth + 1) return;
    setState(() {
      while (_stack.length > depth + 1) {
        _stack.removeLast();
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_path.isEmpty ? '文件' : _path.split('/').last),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _refresh),
        ],
      ),
      body: Column(children: [
        if (_stack.length > 1)
          SizedBox(
            height: 40,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 8),
              children: [
                for (var i = 0; i < _stack.length; i++)
                  TextButton(
                      onPressed: () => _popTo(i),
                      child: Text(
                          i == 0 ? '根目录' : _stack[i].path.split('/').last,
                          style: TextStyle(
                              fontWeight: i == _stack.length - 1
                                  ? FontWeight.bold
                                  : null))),
              ],
            ),
          ),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : _error != null
                  ? ListView(children: [
                      const SizedBox(height: 120),
                      Center(child: Text(_error!)),
                      Center(child: TextButton(
                          onPressed: () => _push(_path),
                          child: const Text('重试'))),
                    ])
                  : _current.isEmpty
                      ? ListView(children: const [
                          SizedBox(height: 140),
                          Center(child: Text('此目录为空')),
                        ])
                      : RefreshIndicator(
                          onRefresh: _refresh,
                          child: ListView.builder(
                            itemCount: _current.length,
                            itemBuilder: (context, i) {
                              final e = _current[i];
                              return ListTile(
                                leading: Icon(_iconOf(e),
                                    color: e.isPrivateRoot
                                        ? Theme.of(context).colorScheme.primary
                                        : null),
                                title: Text(e.name,
                                    maxLines: 1, overflow: TextOverflow.ellipsis),
                                subtitle: e.isDir
                                    ? null
                                    : Text(
                                        '${e.sizeHuman} · ${e.modified.split('T').first}',
                                        style: const TextStyle(fontSize: 12)),
                                trailing:
                                    e.isDir ? const Icon(Icons.chevron_right) : null,
                                onTap: () {
                                  if (e.isDir) _push(e.path);
                                },
                              );
                            },
                          ),
                        ),
        ),
      ]),
    );
  }
}
