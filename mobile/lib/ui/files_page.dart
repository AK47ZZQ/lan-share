// 文件浏览：/api/files 目录列表 + 面包屑导航。
// M-A2：FAB 上传到当前目录（file_picker，分片续传走传输中心）；
//       点按文件 → 底部操作（下载到手机 / 分享 / 用其他应用打开已下载的）。
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:open_filex/open_filex.dart';

import '../data/api/api_client.dart';
import '../state/session.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'share_create_sheet.dart';

class Entry {
  final String name;
  final String path;
  final bool isDir;
  final bool isPrivateRoot;
  final int size;
  final String sizeHuman;
  final String modified;
  final String? ext;
  Entry({required this.name, required this.path, required this.isDir,
      required this.isPrivateRoot, required this.size,
      required this.sizeHuman, required this.modified, this.ext});

  factory Entry.fromJson(Map<String, dynamic> j) => Entry(
      name: j['name'] as String,
      path: j['path'] as String,
      isDir: j['is_dir'] as bool,
      isPrivateRoot: j['is_private_root'] as bool? ?? false,
      size: (j['size'] as num?)?.toInt() ?? 0,
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

class FilesPage extends ConsumerStatefulWidget {
  final VoidCallback? onGoTransfers;
  const FilesPage({super.key, this.onGoTransfers});

  @override
  ConsumerState<FilesPage> createState() => _FilesPageState();
}

class _FilesPageState extends ConsumerState<FilesPage> {
  final _stack = <_Dir>[];
  // 初始必须为 false：initState 的首次 _push('') 受 _loading guard 保护，
  // 初始 true 会把它拦死（M-A1 遗留：文件页永远转圈从未加载过）
  bool _loading = false;
  String? _error;
  bool _picking = false; // 选文件互斥（SAF 面板可能很慢）

  @override
  void initState() {
    super.initState();
    _push('');
  }

  String get _path => _stack.isEmpty ? '' : _stack.last.path;
  List<Entry> get _current => _stack.isEmpty ? const [] : _stack.last.items;

  /// 进入一层目录（push）。加载中忽略新请求（防连点导致栈错乱）
  Future<void> _push(String path) async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final dio = await ApiClient.I.dio();
      final enc = path.split('/').map(Uri.encodeComponent).join('/');
      final r = await dio.get(path.isEmpty ? '/api/files' : '/api/files/$enc');
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
    if (_loading) return;
    final target = _path;
    try {
      final dio = await ApiClient.I.dio();
      final enc = target.split('/').map(Uri.encodeComponent).join('/');
      final r = await dio.get(target.isEmpty ? '/api/files' : '/api/files/$enc');
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

  /// 上传到当前目录（M-A2 手动传输主入口）
  Future<void> _pickAndUpload() async {
    if (_picking) return;
    _picking = true;
    try {
      final res = await FilePicker.platform
          .pickFiles(type: FileType.any, allowMultiple: true);
      if (res == null || res.files.isEmpty) return;
      var n = 0;
      for (final f in res.files) {
        if (f.path == null) continue;
        if (TransferEngine.I.enqueueUpload(
            localPath: f.path!, destDir: _path, name: f.name) !=
            null) {
          n++;
        }
      }
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(n > 0 ? '已加入传输队列 $n 个文件' : '文件已在队列中'),
          duration: const Duration(seconds: 2)));
      widget.onGoTransfers?.call();
    } finally {
      _picking = false;
    }
  }

  /// 文件点按：下载到手机 / 分享 / 打开已下载的
  Future<void> _fileSheet(Entry e) async {
    final engine = TransferEngine.I;
    final canShare = ref.read(sessionProvider).role != 'guest'; // 访客只读
    final downloaded = engine.tasks
        .where((t) =>
            t.kind == TransferKind.download &&
            t.remotePath == e.path &&
            t.status == TransferStatus.done)
        .toList();
    await showModalBottomSheet(
        context: context,
        builder: (ctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(e.name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.bold)),
                        const SizedBox(height: 4),
                        Text('${e.sizeHuman} · ${e.modified.split('T').first}',
                            style:
                                const TextStyle(fontSize: 12, color: Colors.grey)),
                      ])),
              const Divider(height: 1),
              ListTile(
                  leading: const Icon(Icons.download),
                  title: const Text('下载到手机'),
                  subtitle: const Text('保存到 App 下载目录，支持断点续传'),
                  onTap: () {
                    Navigator.pop(ctx);
                    engine.enqueueDownload(
                        remotePath: e.path, name: e.name, size: e.size);
                    widget.onGoTransfers?.call();
                  }),
              if (canShare)
                ListTile(
                    leading: const Icon(Icons.share_outlined),
                    title: const Text('创建分享链接'),
                    subtitle: const Text('带提取码/有效期/定向成员的取件链接'),
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      showShareCreate(context, path: e.path, name: e.name);
                    }),
              if (downloaded.isNotEmpty)
                ListTile(
                    leading: const Icon(Icons.open_in_new),
                    title: const Text('用其他应用打开'),
                    subtitle: Text(
                        downloaded.map((t) => t.localPath.split('/').last).join('、'),
                        style: const TextStyle(fontSize: 11)),
                    onTap: () {
                      Navigator.pop(ctx);
                      OpenFilex.open(downloaded.first.localPath);
                    }),
              const SizedBox(height: 8),
            ])));
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
      floatingActionButton: FloatingActionButton.extended(
          onPressed: _pickAndUpload,
          icon: const Icon(Icons.upload_file),
          label: const Text('上传到此处')),
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
                                  if (e.isDir) {
                                    _push(e.path);
                                  } else {
                                    _fileSheet(e);
                                  }
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
