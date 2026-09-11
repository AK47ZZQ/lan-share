// 目录选择器（App v0.6.0）：移动/复制的目标目录选取。
// 复用 /api/files 浏览协议；顶部面包屑支持回跳，"选这里"返回当前目录。
import 'package:flutter/material.dart';

import '../data/api/files_api.dart';
import 'theme/design_tokens.dart';

class DirEntry {
  final String name;
  final String path;
  final bool isPrivateRoot;
  DirEntry(this.name, this.path, this.isPrivateRoot);
}

class DirPickerPage extends StatefulWidget {
  final String title;
  const DirPickerPage({super.key, required this.title});

  @override
  State<DirPickerPage> createState() => _DirPickerPageState();
}

class _DirPickerPageState extends State<DirPickerPage> {
  final _stack = <List<DirEntry>>[const []];
  final _crumbs = <String>[''];
  bool _loading = false;
  String? _error;

  String get _path => _crumbs.last;

  @override
  void initState() {
    super.initState();
    _load('');
  }

  Future<void> _load(String path) async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await FilesApi.browse(path);
      final dirs = items
          .map((e) => DirEntry(e['name'] as String, e['path'] as String,
              e['is_private_root'] as bool? ?? false))
          .toList()
        ..sort((a, b) => a.name.toLowerCase().compareTo(b.name.toLowerCase()));
      if (!mounted) return;
      setState(() {
        if (_crumbs.last != path) {
          _crumbs.add(path);
          _stack.add(const []);
        }
        _stack[_stack.length - 1] = dirs;
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

  void _popTo(int depth) {
    if (_stack.length <= depth + 1) return;
    setState(() {
      while (_stack.length > depth + 1) {
        _stack.removeLast();
        _crumbs.removeLast();
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final current = _stack.last;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.title, style: const TextStyle(fontSize: 17)),
        actions: [
          TextButton(
              onPressed: _loading ? null : () => Navigator.pop(context, _path),
              child: Text('选这里${_path.isEmpty ? '（根目录）' : ''}')),
        ],
      ),
      body: Column(children: [
        SizedBox(
          height: 40,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: Gap.xs),
            children: [
              for (var i = 0; i < _crumbs.length; i++)
                TextButton(
                    onPressed: () => _popTo(i),
                    child: Text(i == 0 ? '根目录' : _crumbs[i].split('/').last,
                        style: TextStyle(
                            fontWeight:
                                i == _crumbs.length - 1 ? FontWeight.bold : null))),
            ],
          ),
        ),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : _error != null
                  ? ListView(children: [
                      const SizedBox(height: 100),
                      Center(child: Text(_error!)),
                      Center(
                          child: TextButton(
                              onPressed: () => _load(_path),
                              child: const Text('重试'))),
                    ])
                  : current.isEmpty
                      ? ListView(children: const [
                          SizedBox(height: 120),
                          Center(child: Text('没有子目录')),
                        ])
                      : ListView.builder(
                          itemCount: current.length,
                          itemBuilder: (context, i) {
                            final e = current[i];
                            return ListTile(
                              leading: Icon(e.isPrivateRoot
                                  ? Icons.lock
                                  : Icons.folder_outlined),
                              title: Text(e.name,
                                  maxLines: 1, overflow: TextOverflow.ellipsis),
                              trailing: const Icon(Icons.chevron_right),
                              onTap: () => _load(e.path),
                            );
                          },
                        ),
        ),
      ]),
    );
  }
}
