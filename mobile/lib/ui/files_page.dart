// 文件浏览：/api/files 目录列表 + 面包屑导航 + 文件管理基本功（0.6.0 大批次）。
// M-A2：FAB 上传到当前目录（file_picker，分片续传走传输中心）；
// 0.6.0：长按多选（下载/删除/移动/复制/单文件分享）、新建文件夹、重命名、
//        删除（进服务端回收站）、排序切换、常用目录钉住、文件详情；
//        移动/复制走 /api/move 与 0.6.0 新增的 /api/copy（目的地选 DirPickerPage）。
// 0.12.0：Web 同款双布局（▦ 方形网格 / ☰ 横向列表，settings 持久化）、
//         图片/视频 /thumb/ 缩略图（AuthImage 鉴权+磁盘缓存，失败回退类型图标）、
//         点可预览文件直接进全屏预览器（当前目录可预览项作相邻翻页范围）。
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../data/api/auth_cache.dart';
import '../data/api/errors.dart';
import '../data/api/files_api.dart';
import '../data/api/photos_api.dart';
import '../data/local/prefs.dart';
import '../data/media_kinds.dart';
import '../data/net.dart';
import 'widgets/auth_image.dart';
import 'widgets/brand.dart';
import 'widgets/open_local.dart';
import 'widgets/prompt_dialog.dart';
import '../state/session.dart';
import '../state/settings.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'dir_picker_page.dart';
import 'notes_page.dart';
import 'photo_models.dart';
import 'audio_viewer.dart';
import 'doc_viewer.dart';
import 'photo_viewer.dart';
import 'recent_page.dart';
import 'share_create_sheet.dart';
import 'theme/design_tokens.dart';
import 'theme/responsive.dart';
import 'trash_page.dart';
import 'upload_qr_page.dart';
import 'widgets/kit.dart';

// B7 拆分（part 同 library + extension on _FilesPageState，私有互通、
// 行为零变化；build override 留在类体内——extension 不能 override）：
// - files_page_actions.dart  服务端操作与对话框簇
// - files_page_views.dart    视图层
part 'files_page_actions.dart';
part 'files_page_views.dart';

class Entry {
  final String name;
  final String path;
  final bool isDir;
  final bool isPrivateRoot;
  final int size;
  final String sizeHuman;
  final String modified;
  final String? ext;
  // 服务器真实存储路径（改进建议 9.6-P1-3），"复制路径"用；旧服务端无此字段
  final String? absPath;
  Entry({required this.name, required this.path, required this.isDir,
      required this.isPrivateRoot, required this.size,
      required this.sizeHuman, required this.modified, this.ext, this.absPath});

  factory Entry.fromJson(Map<String, dynamic> j) => Entry(
      name: j['name'] as String,
      path: j['path'] as String,
      isDir: j['is_dir'] as bool,
      isPrivateRoot: j['is_private_root'] as bool? ?? false,
      size: (j['size'] as num?)?.toInt() ?? 0,
      sizeHuman: j['size_human'] as String? ?? '',
      modified: j['modified'] as String? ?? '',
      ext: j['ext'] as String?,
      absPath: j['abs_path'] as String?);

  // 集合单一事实源在 data/media_kinds.dart（07 审查 §4.1-2：原各页手写
  // 且漂移——本页曾缺 .m4v/.3gp）；集合外服务端不出缩略图，直接走类型图标
  static const _imgExts = kImgExts;
  static const _vidExts = kVidExts;

  bool get isImage =>
      !isDir && _imgExts.contains((ext ?? '').toLowerCase());
  bool get isVideo =>
      !isDir && _vidExts.contains((ext ?? '').toLowerCase());
  // v0.17 预览大改：md/pdf/docx/txt 等文档类也可"点击即预览"
  bool get isDoc => !isDir && isDocPreviewable(name);
  // v0.18 预览扩面：mp3/m4a/flac 等音频点击直接进内建播放器
  bool get isAudio => !isDir && isAudioPreviewable(name);
  bool get previewable => isImage || isVideo || isDoc || isAudio;
}

class _Dir {
  final String path;
  final List<Entry> items;
  _Dir(this.path, this.items);
}

IconData _iconOf(Entry e) {
  if (e.isDir) return e.isPrivateRoot ? Icons.lock : Icons.folder;
  final ext = (e.ext ?? '').toLowerCase();
  // 扩展名集合与 Entry._imgExts/_vidExts 同源（review 0.12.0：单一事实源）
  if (Entry._imgExts.contains(ext)) return Icons.image_outlined;
  if (Entry._vidExts.contains(ext)) return Icons.movie_outlined;
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
  bool _deleting = false;
  // 以下两个互斥位原在 actions 拆分件内（B7）——extension 不能声明实例字段，
  // 回收进 State 本体；语义与拆分前逐字一致
  bool _opening = false; // 防重入：快速双击会 push 两个 viewer
  bool _zipping = false;
  final _selected = <String>{}; // 多选模式：当前目录内条目 path
  // 服务器地址（缩略图 URL 与预览器 base）。initState 异步取首值，此后
  // 监听 Prefs.addressNotifier——会话内切换主/备隧道时同步。本页挂在
  // IndexedStack 里不重建，不监听就会永久打旧地址（对抗审查 P1）
  String? _base;

  @override
  void initState() {
    super.initState();
    () async {
      final b = await Prefs.lastAddress();
      if (mounted) setState(() => _base = b);
    }();
    Prefs.addressNotifier.addListener(_onAddressChanged);
    _push('');
  }

  void _onAddressChanged() {
    final b = Prefs.addressNotifier.value;
    if (mounted && b.isNotEmpty && b != _base) setState(() => _base = b);
  }

  @override
  void dispose() {
    Prefs.addressNotifier.removeListener(_onAddressChanged);
    super.dispose();
  }

  String get _path => _stack.isEmpty ? '' : _stack.last.path;
  List<Entry> get _current => _stack.isEmpty ? const [] : _stack.last.items;
  bool get _selecting => _selected.isNotEmpty;

  /// 排序（设置页持久化）：目录恒在前；modified 为 ISO 字符串可直接字典序比较
  List<Entry> get _sorted {
    final settings = ref.read(settingsProvider);
    final s = settings.filesSort;
    final asc = settings.filesAsc;
    final list = List<Entry>.of(_current)..sort((a, b) {
      if (a.isDir != b.isDir) return a.isDir ? -1 : 1;
      var r = switch (s) {
        'modified' => a.modified.compareTo(b.modified),
        'size' => a.size.compareTo(b.size),
        _ => a.name.toLowerCase().compareTo(b.name.toLowerCase()),
      };
      return asc ? r : -r;
    });
    return list;
  }

  // ── 导航 ──────────────────────────────────────────────────

  String? _failedPath; // 最近一次加载失败的目标路径（错误视图"重试"用）

  Future<void> _push(String path) async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
      _selected.clear();
    });
    try {
      final items = await _fetchDir(path);
      if (!mounted) return;
      setState(() {
        _stack.add(_Dir(path, items));
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      // 记下失败目标：错误视图"重试"必须重试它而不是栈顶（对抗审查 P2：
      // 旧实现重试 _path 会把"当前目录"重复压栈，面包屑出现重复项且
      // 用户真正想进的目录被丢失）
      setState(() {
        _loading = false;
        _error = '目录加载失败';
        _failedPath = path;
      });
    }
  }

  Future<List<Entry>> _fetchDir(String path) async {
    final items = await FilesApi.browse(path);
    return items
        .map((e) => Entry.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 刷新当前层（替换栈顶）
  Future<void> _refresh() async {
    if (_loading) return;
    final target = _path;
    try {
      final items = await _fetchDir(target);
      if (!mounted) return;
      setState(() {
        if (_stack.isNotEmpty) _stack.last = _Dir(target, items);
        _selected.clear();
        // AppBar"刷新"也复位错误态（对抗审查 P2：此前成功也不清，
        // 错误视图永驻；失败则静默）
        _error = null;
      });
      // 刷新成功顺手放行本目录缩略图负缓存：服务端/文件修复后下拉
      // 即自愈，不必干等 10 分钟 TTL（对抗审查 P3）
      if (_base != null) {
        for (final e in items) {
          // 与 _thumbUrl 同口径（图/视频才请求缩略图），否则 null 断言崩
          if ((e.isImage || e.isVideo) && _thumbUrl(e) != null) {
            AuthCacheManager.I.clearFailed(_thumbUrl(e)!);
          }
        }
      }
    } catch (_) {
      // 空吞会静默残留旧列表（R1-P3）：至少给个反馈
      showToast('刷新失败');
    }
  }

  /// 面包屑回退：直接出栈，不重新请求
  void _popTo(int depth) {
    if (_stack.length <= depth + 1) return;
    setState(() {
      while (_stack.length > depth + 1) {
        _stack.removeLast();
      }
      _selected.clear();
      _error = null; // 回退到已知有效的栈内目录，退出错误视图
    });
  }

  // ── 构建 ─────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(settingsProvider);
    final canManage = ref.read(sessionProvider).role != 'guest';
    // 照片墙隐藏是全局 admin 能力（20260913211805-b4e0）
    final isAdmin = ref.read(sessionProvider).role == 'admin';
    // 系统返回键先回上级目录（R1-P3-8）：深层目录按返回直接退出整个页面
    return PopScope(
      canPop: _stack.length <= 1,
      onPopInvokedWithResult: (didPop, _) {
        // 在途 _push 期间返回会与完成回写出栈竞态，被重新压回目标目录（R3-P3-3）
        if (!didPop && !_loading && _stack.length > 1) {
          setState(() => _popTo(_stack.length - 2));
        }
      },
      child: Scaffold(
      appBar: AppBar(
        leading: _selecting
            ? IconButton(
                icon: const Icon(Icons.close),
                onPressed: () => setState(() => _selected.clear()))
            : null,
        title: Text(_selecting
            ? '已选 ${_selected.length} 项'
            : _path.isEmpty
                ? '文件'
                : _path.split('/').last),
        actions: _selecting
            ? [
                TextButton(
                    onPressed: () => setState(() {
                          if (_selected.length == _current.length) {
                            _selected.clear();
                          } else {
                            _selected
                              ..clear()
                              ..addAll(_current.map((e) => e.path));
                          }
                        }),
                    child: Text(_selected.length == _current.length
                        ? '全不选'
                        : '全选')),
                // 恰好选了一项时提供操作菜单——目录项原在长按菜单里
                //（重命名/钉住/详情），目录长按改为进多选
                //（20260913211605-91fc）后收编到这里；c220 扩展：勾选单个
                // **文件**同样出菜单并可重命名（此前文件在多选态无重命名入口）。
                if (_selectedEntries.length == 1)
                  PopupMenuButton<String>(
                      tooltip: '条目操作',
                      icon: const Icon(Icons.more_vert),
                      onSelected: (v) {
                        // 菜单展开期间选中集可能被异步刷新清空
                        // （R2 审查：_selectedEntries.first 会抛 StateError）
                        if (_selectedEntries.isEmpty) return;
                        final e = _selectedEntries.first;
                        if (v == 'pin') {
                          // ignore: discarded_futures
                          ref
                              .read(settingsProvider.notifier)
                              .togglePinnedDir(e.path);
                        }
                        if (v == 'rename') {
                          setState(() => _selected.clear());
                          // ignore: discarded_futures
                          _rename(e);
                        }
                        if (v == 'details') {
                          // ignore: discarded_futures
                          _showDetails(e);
                        }
                        if (v == 'hide') {
                          _toggleWallHidden(e);
                        }
                      },
                      itemBuilder: (_) {
                        final e = _selectedEntries.first;
                        return [
                          if (e.isDir)
                            const PopupMenuItem(
                                value: 'pin',
                                child: ListTile(
                                    dense: true,
                                    contentPadding: EdgeInsets.zero,
                                    leading: Icon(Icons.star_border),
                                    title: Text('钉为常用目录'))),
                          if (canManage)
                            const PopupMenuItem(
                                value: 'rename',
                                child: ListTile(
                                    dense: true,
                                    contentPadding: EdgeInsets.zero,
                                    leading: Icon(
                                        Icons.drive_file_rename_outline),
                                    title: Text('重命名'))),
                          const PopupMenuItem(
                              value: 'details',
                              child: ListTile(
                                  dense: true,
                                  contentPadding: EdgeInsets.zero,
                                  leading: Icon(Icons.info_outline),
                                  title: Text('详情'))),
                          if (isAdmin && e.isDir)
                            const PopupMenuItem(
                                value: 'hide',
                                child: ListTile(
                                    dense: true,
                                    contentPadding: EdgeInsets.zero,
                                    leading: Icon(
                                        Icons.visibility_off_outlined),
                                    title: Text('照片墙隐藏/恢复'))),
                        ];
                      }),
              ]
            : [
                // v0.20：新建收进菜单——文件夹 / Markdown / 文本
                PopupMenuButton<String>(
                    tooltip: '新建',
                    enabled: canManage,
                    icon: const Icon(Icons.add),
                    onSelected: (v) {
                      if (v == 'folder') _mkdir();
                      if (v == 'md') _newDoc(true);
                      if (v == 'txt') _newDoc(false);
                    },
                    itemBuilder: (_) => const [
                          PopupMenuItem(
                              value: 'folder',
                              child: ListTile(
                                  dense: true,
                                  contentPadding: EdgeInsets.zero,
                                  leading:
                                      Icon(Icons.create_new_folder_outlined),
                                  title: Text('新建文件夹'))),
                          PopupMenuItem(
                              value: 'md',
                              child: ListTile(
                                  dense: true,
                                  contentPadding: EdgeInsets.zero,
                                  leading: Icon(Icons.edit_note),
                                  title: Text('新建 Markdown 笔记'))),
                          PopupMenuItem(
                              value: 'txt',
                              child: ListTile(
                                  dense: true,
                                  contentPadding: EdgeInsets.zero,
                                  leading: Icon(Icons.text_snippet_outlined),
                                  title: Text('新建文本文件'))),
                        ]),
                IconButton(
                    tooltip: settings.filesGrid ? '切换为列表视图' : '切换为网格视图',
                    icon: Icon(settings.filesGrid
                        ? Icons.view_list_outlined
                        : Icons.grid_view_outlined),
                    onPressed: () => ref
                        .read(settingsProvider.notifier)
                        .setFilesGrid(!settings.filesGrid)),
                IconButton(
                    tooltip: '排序', icon: const Icon(Icons.sort), onPressed: _sortMenu),
                PopupMenuButton<String>(
                    onSelected: (v) {
                      if (v == 'recent') {
                        Navigator.push(context,
                            MaterialPageRoute(builder: (_) => const RecentPage()));
                      }
                      if (v == 'trash') {
                        Navigator.push(context,
                            MaterialPageRoute(builder: (_) => const TrashPage()));
                      }
                      if (v == 'qr') {
                        // v0.20：手机上传二维码（对齐 Web"📲 手机上传"），
                        // 含二维码图保存到相册
                        // ignore: discarded_futures
                        showUploadQrSheet(context);
                      }
                      if (v == 'refresh') _refresh();
                    },
                    itemBuilder: (_) => const [
                          PopupMenuItem(value: 'recent', child: Text('最近文件')),
                          PopupMenuItem(value: 'qr', child: Text('📲 手机上传二维码')),
                          PopupMenuItem(value: 'trash', child: Text('回收站')),
                          PopupMenuItem(value: 'refresh', child: Text('刷新')),
                        ]),
              ],
      ),
      floatingActionButton: _selecting || _deleting
          ? null
          : FloatingActionButton.extended(
              onPressed: _pickAndUpload,
              icon: const Icon(Icons.upload_file),
              label: const Text('上传到此处')),
      body: _deleting
          ? const Center(child: CircularProgressIndicator())
          : Column(children: [
              if (_stack.length > 1 && !_selecting)
                SizedBox(
                  height: 40,
                  child: ListView(
                    scrollDirection: Axis.horizontal,
                    padding: const EdgeInsets.symmetric(horizontal: Gap.xs),
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
                                    onPressed: () =>
                                        _push(_failedPath ?? _path),
                                    child: const Text('重试'))),
                              ])
                        : RefreshIndicator(
                            onRefresh: _refresh,
                            child: _current.isEmpty
                                ? ListView(children: const [
                                    SizedBox(height: 140),
                                    Center(child: Text('此目录为空')),
                                  ])
                                : _buildBody(settings),
                          ),
              ),
            ]),
      bottomNavigationBar: _selecting ? _selectionBar(canManage) : null,
    ));
  }

}
