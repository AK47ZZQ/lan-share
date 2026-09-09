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

import '../data/api/api_client.dart';
import '../data/api/auth_cache.dart';
import '../data/api/errors.dart';
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
import 'trash_page.dart';
import 'upload_qr_page.dart';

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
    final dio = await ApiClient.I.dio();
    final enc = path.split('/').map(Uri.encodeComponent).join('/');
    final r = await dio.get(path.isEmpty ? '/api/files' : '/api/files/$enc');
    return (r.data['items'] as List)
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
          if (e.previewable) AuthCacheManager.I.clearFailed(_thumbUrl(e)!);
        }
      }
    } catch (_) {
      // 空吞会静默残留旧列表（R1-P3）：至少给个反馈
      _toast('刷新失败');
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

  // ── 服务端操作（0.6.0 文件管理）───────────────────────────

  Future<void> _mkdir() async {
    final name = await _promptText('新建文件夹', '文件夹名称', '新建文件夹');
    if (name == null || name.trim().isEmpty || !mounted) return;
    try {
      final dio = await ApiClient.I.dio();
      final base = _path.isEmpty ? '' : '$_path/';
      await dio.post('/api/mkdir/$base${Uri.encodeComponent(name.trim())}');
      await _refresh();
    } catch (e) {
      _toast('创建失败：${_detail(e)}');
    }
  }

  /// 新建文档（v0.20）：md/txt 不预建空文件——直接进编辑器（isNew），
  /// 保存时 multipart 直传落盘，取消则不产生任何垃圾文件
  Future<void> _newDoc(bool markdown) async {
    final ext = markdown ? '.md' : '.txt';
    final name = await _promptText(
        markdown ? '新建 Markdown 笔记' : '新建文本文件',
        '标题（自动加 $ext）',
        markdown ? '会议记录' : '备忘');
    if (name == null || name.trim().isEmpty || !mounted) return;
    // ignore: use_build_context_synchronously
    await openNoteEditor(context,
        dir: _path, name: '${name.trim()}$ext', isNew: true);
    if (mounted) await _refresh();
  }

  Future<void> _rename(Entry e) async {
    final name = await _promptText('重命名', '新名称', e.name, initial: e.name);
    if (name == null || name.trim().isEmpty || name.trim() == e.name || !mounted) {
      return;
    }
    try {
      final dio = await ApiClient.I.dio();
      await dio.post('/api/rename',
          data: {'path': e.path, 'new_name': name.trim()});
      await _refresh();
    } catch (e2) {
      _toast('重命名失败：${_detail(e2)}');
    }
  }

  Future<void> _delete(List<Entry> items) async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text('删除 ${items.length} 项？'),
                content: const Text('将进入服务端回收站，30 天内可随时恢复。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(dctx, true),
                      child: const Text('删除')),
                ]));
    if (ok != true || !mounted) return;
    setState(() => _deleting = true);
    var done = 0, failed = 0;
    final dio = await ApiClient.I.dio();
    for (final it in items) {
      try {
        final enc = it.path.split('/').map(Uri.encodeComponent).join('/');
        await dio.delete('/api/files/$enc');
        done++;
      } catch (_) {
        failed++;
      }
    }
    if (!mounted) return;
    setState(() => _deleting = false);
    _toast(failed > 0 ? '已删 $done 项，$failed 项失败' : '已删除 $done 项');
    await _refresh();
  }

  /// 移动/复制到所选目录
  Future<void> _transferTo(List<Entry> items, {required bool copy}) async {
    final dest = await Navigator.push<String>(context, MaterialPageRoute(
        builder: (_) => DirPickerPage(title: copy ? '复制到…' : '移动到…')));
    if (dest == null || !mounted) return;
    final dio = await ApiClient.I.dio();
    dynamic r;
    try {
      r = await (copy
          ? dio.post('/api/copy', data: {
              'sources': items.map((e) => e.path).toList(),
              'dest_dir': dest,
            })
          : dio.post('/api/move', data: {
              'sources': items.map((e) => e.path).toList(),
              'dest_dir': dest,
            }));
    } catch (e) {
      // 断网/服务端 5xx 时静默失败连刷新都不做，用户会以为成功（review 0.12.0 P2）
      _toast('${copy ? "复制" : "移动"}失败：${_detail(e)}');
      return;
    }
    if (!mounted) return;
    final results = (r.data['results'] as List? ?? const [])
        .whereType<Map>()
        .map((m) => m['ok'] == true)
        .toList();
    final okCount = results.where((v) => v).length;
    _toast(results.isEmpty
        ? (copy ? '复制完成' : '移动完成')
        : okCount == results.length
            ? '已${copy ? "复制" : "移动"} $okCount 项到「${dest.split('/').last}」'
            : '$okCount 项成功，${results.length - okCount} 项失败');
    await _refresh();
  }

  // ── 菜单 / 弹窗 ───────────────────────────────────────────

  Future<String?> _promptText(String title, String label, String hint,
      {String? initial}) async {
    // controller 生命周期由 PromptDialog 自持（同 photos_page 新建专辑的
    // 退场动画 dispose 崩溃雷，v0.13 统一收口）
    return showDialog<String>(
        context: context,
        builder: (dctx) => PromptDialog(
            title: title, label: label, hint: hint, initial: initial));
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(msg), duration: const Duration(seconds: 2)));
  }

  static String _detail(Object e) {
    final s = e.toString();
    return s.length > 80 ? s.substring(0, 80) : s;
  }

  void _showDetails(Entry e) {
    showDialog<void>(
        context: context,
        builder: (dctx) => AlertDialog(
                title: Text(e.name, style: const TextStyle(fontSize: 16)),
                content: Column(mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                  _detailRow('类型', e.isDir ? '文件夹' : '文件${e.ext ?? ''}'),
                  _detailRow('大小', e.isDir ? '-' : e.sizeHuman),
                  _detailRow('修改时间', e.modified.split('T').first),
                  const SizedBox(height: 6),
                  SelectableText(e.path,
                      style: const TextStyle(fontSize: 12, color: Colors.grey)),
                ]),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dctx),
                      child: const Text('关闭')),
                ]));
  }

  Widget _detailRow(String k, String v) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 64, child: Text(k,
            style: const TextStyle(fontSize: 13, color: Colors.grey))),
        Expanded(child: Text(v, style: const TextStyle(fontSize: 13))),
      ]));

  void _sortMenu() {
    final settings = ref.read(settingsProvider);
    showModalBottomSheet<void>(
        context: context,
        builder: (ctx) => SafeArea(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Padding(
                  padding: EdgeInsets.all(12),
                  child: Text('排序方式', style: TextStyle(fontWeight: FontWeight.bold))),
              for (final (key, label) in [
                ('name', '名称'),
                ('modified', '修改时间'),
                ('size', '大小'),
              ])
                ListTile(
                    leading: Icon(settings.filesSort == key
                        ? Icons.radio_button_checked
                        : Icons.radio_button_off),
                    title: Text(label),
                    trailing: settings.filesSort == key
                        ? IconButton(
                            tooltip: settings.filesAsc ? '当前升序，点改降序' : '当前降序，点改升序',
                            icon: Icon(settings.filesAsc
                                ? Icons.arrow_upward
                                : Icons.arrow_downward),
                            onPressed: () {
                              ref
                                  .read(settingsProvider.notifier)
                                  .setFilesSort(key, asc: !settings.filesAsc);
                              Navigator.pop(ctx);
                            })
                        : null,
                    onTap: () {
                      ref.read(settingsProvider.notifier).setFilesSort(key);
                      Navigator.pop(ctx);
                    }),
              const SizedBox(height: 8),
            ])));
  }

  // ── 上传（M-A2 手动传输主入口）───────────────────────────

  Future<void> _pickAndUpload() async {
    if (_picking) return;
    _picking = true;
    try {
      final res = await FilePicker.platform
          .pickFiles(type: FileType.any, allowMultiple: true);
      if (res == null || res.files.isEmpty) return;
      // 公网入口大文件提醒（2026-09-03）：>100MB 走隧道慢且耗月流量，先确认
      final bigs = res.files.where((f) => f.size > 100 * 1024 * 1024).toList();
      if (bigs.isNotEmpty && isPublicAddress(await Prefs.lastAddress())) {
        final total = bigs.fold<int>(0, (a, f) => a + f.size);
        if (!mounted) return;
        final ok = await showDialog<bool>(
            context: context,
            builder: (ctx) => AlertDialog(
                title: const Text('当前为公网入口'),
                content: Text(
                    '选中的文件里有 ${bigs.length} 个大文件，共约 ${humanSize(total)}。'
                    '公网隧道限速且消耗月流量，大文件建议连家里 Wi-Fi 再传。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('仍要上传')),
                ]));
        if (ok != true) return;
      }
      var n = 0;
      final enqueued = <TransferTask>[];
      for (final f in res.files) {
        if (f.path == null) continue;
        final t = TransferEngine.I.enqueueUpload(
            localPath: f.path!, destDir: _path, name: f.name);
        if (t != null) {
          enqueued.add(t);
          n++;
        }
      }
      // 上传完成后刷新当前目录列表，否则从传输页回来仍是旧列表
      final dirAtEnqueue = _path;
      if (enqueued.isNotEmpty) {
        void watch() {
          if (!enqueued.any((t) => !t.finished)) {
            TransferEngine.I.removeListener(watch);
            if (mounted && _path == dirAtEnqueue) _refresh();
          }
        }

        TransferEngine.I.addListener(watch);
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

  // ── 文件 / 目录操作菜单 ───────────────────────────────────

  Future<void> _fileSheet(Entry e) async {
    final engine = TransferEngine.I;
    final canShare = ref.read(sessionProvider).role != 'guest'; // 访客只读
    final canManage = ref.read(sessionProvider).role != 'guest';
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
              // 复制服务器真实路径（改进建议 9.6-P1-3）：旧服务端无 abs_path 时回退逻辑路径
              ListTile(
                  leading: const Icon(Icons.copy_outlined),
                  title: const Text('复制路径'),
                  subtitle: Text(e.absPath ?? '/${e.path}',
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  onTap: () async {
                    Navigator.pop(ctx);
                    await Clipboard.setData(
                        ClipboardData(text: e.absPath ?? '/${e.path}'));
                    if (!mounted) return;
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                        content: Text('已复制路径'),
                        duration: Duration(seconds: 2)));
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
                      // ignore: discarded_futures
                      openLocalFile(context, downloaded.last.localPath);
                    }),
              if (canManage) ...[
                ListTile(
                    leading: const Icon(Icons.drive_file_rename_outline),
                    title: const Text('重命名'),
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      _rename(e);
                    }),
                ListTile(
                    leading: const Icon(Icons.drive_file_move_outlined),
                    title: const Text('移动到…'),
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      _transferTo([e], copy: false);
                    }),
                ListTile(
                    leading: const Icon(Icons.copy_all_outlined),
                    title: const Text('复制到…'),
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      _transferTo([e], copy: true);
                    }),
                ListTile(
                    leading: const Icon(Icons.delete_outline),
                    title: const Text('删除'),
                    iconColor: Theme.of(context).colorScheme.error,
                    textColor: Theme.of(context).colorScheme.error,
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      _delete([e]);
                    }),
              ],
              ListTile(
                  leading: const Icon(Icons.info_outline),
                  title: const Text('详情'),
                  onTap: () {
                    Navigator.pop(ctx);
                    _showDetails(e);
                  }),
              const SizedBox(height: 8),
            ])));
  }

  Future<void> _dirSheet(Entry e) async {
    final pinned = ref.read(settingsProvider).pinnedDirs.contains(e.path);
    final canManage = ref.read(sessionProvider).role != 'guest';
    await showModalBottomSheet(
        context: context,
        builder: (ctx) => SafeArea(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              Padding(
                  padding: const EdgeInsets.all(16),
                  child: Align(
                      alignment: Alignment.centerLeft,
                      child: Text(e.name,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontWeight: FontWeight.bold)))),
              const Divider(height: 1),
              ListTile(
                  leading: Icon(pinned ? Icons.star : Icons.star_border),
                  title: Text(pinned ? '从常用目录移除' : '钉为常用目录'),
                  onTap: () {
                    Navigator.pop(ctx);
                    // ignore: discarded_futures
                    ref.read(settingsProvider.notifier).togglePinnedDir(e.path);
                  }),
              if (canManage) ...[
                ListTile(
                    leading: const Icon(Icons.drive_file_rename_outline),
                    title: const Text('重命名'),
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      _rename(e);
                    }),
                ListTile(
                    leading: const Icon(Icons.drive_file_move_outlined),
                    title: const Text('移动到…'),
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      _transferTo([e], copy: false);
                    }),
                ListTile(
                    leading: const Icon(Icons.copy_all_outlined),
                    title: const Text('复制到…'),
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      _transferTo([e], copy: true);
                    }),
                ListTile(
                    leading: const Icon(Icons.delete_outline),
                    title: const Text('删除'),
                    iconColor: Theme.of(context).colorScheme.error,
                    textColor: Theme.of(context).colorScheme.error,
                    onTap: () {
                      Navigator.pop(ctx);
                      // ignore: discarded_futures
                      _delete([e]);
                    }),
              ],
              ListTile(
                  leading: const Icon(Icons.info_outline),
                  title: const Text('详情'),
                  onTap: () {
                    Navigator.pop(ctx);
                    _showDetails(e);
                  }),
              const SizedBox(height: 8),
            ])));
  }

  // ── 预览（0.12.0，Web 同款：点可预览文件直接进全屏查看器）──

  bool _opening = false; // 防重入：快速双击会 push 两个 viewer

  /// 可预览文件点击直接进预览器；预览器内左右滑在「当前目录全部可预览
  /// 文件」间翻页（Web FileViewer navItems 同款范围）。返回 true=已删除，
  /// 列表随之刷新。不可预览类型保持弹操作菜单。
  Future<void> _openFile(Entry e) async {
    if (_opening) return;
    if (!e.previewable || _base == null) {
      // ignore: discarded_futures
      _fileSheet(e);
      return;
    }
    // v0.17：文档类（md/pdf/docx/txt）走专用预览器，不进图/视频轮播
    if (e.isDoc) {
      _opening = true;
      try {
        await Navigator.push(context, MaterialPageRoute(
            builder: (_) => DocViewerPage(path: e.path, name: e.name)));
      } finally {
        _opening = false;
      }
      return;
    }
    // v0.18：音频走内建播放器（media_proxy 通道，支持拖进度）
    if (e.isAudio) {
      _opening = true;
      try {
        await Navigator.push(context, MaterialPageRoute(
            builder: (_) => AudioViewerPage(path: e.path, name: e.name)));
      } finally {
        _opening = false;
      }
      return;
    }
    _opening = true;
    try {
      final items =
          _sorted.where((x) => x.isImage || x.isVideo).map(_toPhotoItem).toList();
      var idx = items.indexWhere((p) => p.path == e.path);
      if (idx < 0) idx = 0; // 理论不可达（e 本身可预览），防御性兜底
      final deleted = await Navigator.push<bool>(context, MaterialPageRoute(
          builder: (_) =>
              PhotoViewerPage(base: _base!, items: items, initial: idx)));
      if (deleted == true) await _refresh();
    } finally {
      _opening = false;
    }
  }

  PhotoItem _toPhotoItem(Entry e) => PhotoItem(
      path: e.path,
      name: e.name,
      taken: e.modified.split('T').first,
      month: '',
      uploader: '',
      mediaType: e.isVideo ? 'video' : 'photo',
      cacheKey: e.modified);

  /// 缩略图 URL：图/视频且服务器地址已就绪才出（AuthImage 鉴权+磁盘缓存，
  /// 与照片墙同一条 /thumb/ 通道）。null=显示类型图标，不发请求。
  /// ?v=modified：同名覆盖后 URL 变化，绕开客户端 180 天磁盘缓存陈旧
  /// （服务端 FastAPI 忽略未知 query 参数，零服务端改动；对抗审查 P2）
  String? _thumbUrl(Entry e) {
    if (!e.previewable || _base == null) return null;
    return '${_base!}/thumb/${encPath(e.path)}?v='
        '${Uri.encodeComponent(e.modified)}';
  }

  // ── 多选批量操作 ──────────────────────────────────────────

  void _toggleSelect(Entry e) {
    setState(() => _selected.contains(e.path)
        ? _selected.remove(e.path)
        : _selected.add(e.path));
  }

  List<Entry> get _selectedEntries =>
      _current.where((e) => _selected.contains(e.path)).toList();

  Future<void> _downloadSelected() async {
    final files = _selectedEntries.where((e) => !e.isDir).toList();
    final engine = TransferEngine.I;
    var n = 0;
    for (final e in files) {
      final t = await engine.enqueueDownload(
          remotePath: e.path, name: e.name, size: e.size);
      if (t != null) n++;
    }
    setState(() => _selected.clear());
    widget.onGoTransfers?.call();
    _toast(n > 0 ? '已加入下载队列 $n 个文件' : '任务已在队列中');
  }

  void _shareSingle() {
    final only = _selectedEntries.single;
    setState(() => _selected.clear());
    // ignore: discarded_futures
    showShareCreate(context, path: only.path, name: only.name);
  }

  bool _zipping = false;

  /// 打包下载（0.8.0 对齐 Web doZip）：服务端打 zip → 下载到临时目录 →
  /// 系统分享面板（存文件/发微信）。大目录打包慢，全程有 toast 反馈。
  Future<void> _zipSelected() async {
    if (_zipping) return;
    final paths = _selectedEntries.map((e) => e.path).toList();
    if (paths.isEmpty) return;
    setState(() => _zipping = true);
    _toast('正在打包 ${paths.length} 项…');
    File? f;
    try {
      final dio = await ApiClient.I.dio();
      final r = await dio.post('/api/zip', data: {'paths': paths});
      final url = r.data['url'] as String?;
      final name = r.data['name'] as String? ?? 'share.zip';
      if (url == null || url.isEmpty) {
        _toast('打包失败：服务端未返回下载地址');
        return;
      }
      final tmp = await getTemporaryDirectory();
      // 时间戳前缀防同名覆盖；进程被杀的残留由系统随 cache 目录回收
      f = File(
          '${tmp.path}/zip-${DateTime.now().millisecondsSinceEpoch}-$name');
      await dio.download(url, f.path);
      _toast('打包完成');
      await Share.shareXFiles([XFile(f.path)], subject: name);
    } catch (e) {
      _toast('打包失败：${errDetail(e)}');
    } finally {
      // 不立刻删：shareXFiles 返回时接收方 App 才开始读 content URI，
      // 此刻删文件会让分享读到刚删除的目标（R1-P2）；残留由系统随
      // cache 目录回收（文件名已带时间戳，不冲突）
      if (mounted) setState(() => _zipping = false);
    }
  }

  // ── 构建 ─────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(settingsProvider);
    final canManage = ref.read(sessionProvider).role != 'guest';
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

  /// 双布局（0.12.0 Web 同款）：常用目录区两种布局共用（置顶），条目区
  /// 按设置在方形网格 / 横向列表间切换。CustomScrollView 统一承载，
  /// 网格/列表都是 Sliver 虚拟化（0.4.4 shrinkWrap 全量构建的教训不再犯）。
  Widget _buildBody(AppSettings settings) {
    final items = _sorted;
    final pinned = _path.isEmpty
        ? settings.pinnedDirs.where((p) => p.isNotEmpty).toList()
        : const <String>[];
    return CustomScrollView(slivers: [
      if (pinned.isNotEmpty) ...[
        SliverToBoxAdapter(
            child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                child: Text('常用目录',
                    style: const TextStyle(
                        fontSize: 12, fontWeight: FontWeight.bold)))),
        SliverList(
            delegate: SliverChildBuilderDelegate(
                (context, i) => _pinnedRow(pinned[i]),
                childCount: pinned.length)),
        const SliverToBoxAdapter(child: Divider(height: 1)),
      ],
      if (settings.filesGrid)
        SliverPadding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
            sliver: SliverGrid(
              gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                // 宽屏（平板/横屏）加列；手机竖屏 3 列与 Web 网格观感一致
                crossAxisCount: _gridColumns,
                mainAxisSpacing: 10,
                crossAxisSpacing: 10,
                childAspectRatio: 0.72,
              ),
              delegate: SliverChildBuilderDelegate(
                  (context, i) => _gridCard(items[i]),
                  childCount: items.length),
            ))
      else
        SliverList(
            delegate: SliverChildBuilderDelegate((context, i) => _row(items[i]),
                childCount: items.length)),
      const SliverToBoxAdapter(child: SizedBox(height: 88)), // FAB 底部留白
    ]);
  }

  int get _gridColumns {
    final w = MediaQuery.of(context).size.width;
    return w >= 640 ? 5 : w >= 440 ? 4 : 3;
  }

  Widget _pinnedRow(String p) => ListTile(
      dense: true,
      leading: const Icon(Icons.star, size: 20),
      title: Text(p.split('/').last,
          maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: IconButton(
          icon: const Icon(Icons.close, size: 18),
          tooltip: '移除常用',
          onPressed: () =>
              ref.read(settingsProvider.notifier).togglePinnedDir(p)),
      onTap: () => _push(p));

  /// 缩略图有则显示（Cover 裁切），失败/无则回退品牌类型图标——
  /// 与 Web「缩略图优先、图标兜底」同款策略
  Widget _leading(Entry e) {
    final thumb = _thumbUrl(e);
    if (thumb != null) {
      return ClipRRect(
          borderRadius: BorderRadius.circular(12),
          child: SizedBox(
              width: 40,
              height: 40,
              child: AuthImage(url: thumb,
                  fit: BoxFit.cover,
                  // 瞬时失败不闪红：静默回退类型图标（Web 同款占位）
                  errorBuilder: (_, _) => _iconFallback(e))));
    }
    return _iconFallback(e);
  }

  /// 0.8.4 品牌化图标：目录=色块+文件夹；文件=类型色圆角方块（FileTypeTile）
  Widget _iconFallback(Entry e) {
    final cs = Theme.of(context).colorScheme;
    if (e.isDir) {
      final isPriv = e.isPrivateRoot;
      return Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: isPriv
                ? cs.primary.withValues(alpha: 0.14)
                : cs.surfaceContainerHighest.withValues(alpha: 0.6),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Icon(isPriv ? Icons.lock : Icons.folder,
              size: 20,
              color: isPriv ? cs.primary : cs.onSurfaceVariant));
    }
    return FileTypeTile(
        ext: (e.ext ?? '').toLowerCase(), icon: _iconOf(e), size: 40);
  }

  Widget _row(Entry e) {
    final selected = _selected.contains(e.path);
    return ListTile(
      leading: selected
          ? Icon(Icons.check_circle,
              color: Theme.of(context).colorScheme.primary)
          : _leading(e),
      title: Text(e.name, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: e.isDir
          ? null
          : Text('${e.sizeHuman} · ${e.modified.split('T').first}',
              style: const TextStyle(fontSize: 12)),
      trailing: _selecting
          ? null
          : e.isDir
              ? const Icon(Icons.chevron_right)
              : null,
      selected: selected,
      onTap: () {
        if (_selecting) {
          _toggleSelect(e);
        } else if (e.isDir) {
          _push(e.path);
        } else {
          // ignore: discarded_futures
          _openFile(e);
        }
      },
      onLongPress: () {
        if (_selecting) return;
        if (e.isDir) {
          // ignore: discarded_futures
          _dirSheet(e);
        } else {
          _toggleSelect(e); // 文件长按直接进入多选（与照片墙一致）
        }
      },
    );
  }

  // ── 方形网格卡片（0.12.0 Web 同款 fgrid）──────────────────

  Widget _gridCard(Entry e) {
    final selected = _selected.contains(e.path);
    final cs = Theme.of(context).colorScheme;
    final thumb = _thumbUrl(e);
    final radius = BorderRadius.circular(14);
    return InkWell(
        onTap: () {
          if (_selecting) {
            _toggleSelect(e);
          } else if (e.isDir) {
            _push(e.path);
          } else {
            // ignore: discarded_futures
            _openFile(e);
          }
        },
        onLongPress: () {
          if (_selecting) return;
          // ignore: discarded_futures
          e.isDir ? _dirSheet(e) : _toggleSelect(e);
        },
        borderRadius: radius,
        child: Container(
            decoration: BoxDecoration(
                borderRadius: radius,
                border: Border.all(
                    color: selected ? cs.primary : Colors.transparent,
                    width: 2)),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                      child: Stack(fit: StackFit.expand, children: [
                    ClipRRect(
                        borderRadius: BorderRadius.vertical(
                            top: Radius.circular(12)),
                        child: thumb != null
                            ? AuthImage(
                                url: thumb,
                                fit: BoxFit.cover,
                                errorBuilder: (_, _) => _gridIcon(e))
                            : _gridIcon(e)),
                    // 视频角标：网格里不点开也能认出是视频
                    if (e.isVideo && thumb != null)
                      Positioned(
                          right: 6,
                          bottom: 6,
                          child: Container(
                              padding: const EdgeInsets.all(3),
                              decoration: BoxDecoration(
                                  color: Colors.black.withValues(alpha: 0.55),
                                  shape: BoxShape.circle),
                              child: const Icon(Icons.play_arrow_rounded,
                                  size: 16, color: Colors.white))),
                    if (selected)
                      Positioned(
                          right: 6,
                          top: 6,
                          child: Icon(Icons.check_circle,
                              size: 22,
                              color: cs.primary,
                              shadows: const [
                                Shadow(
                                    blurRadius: 4,
                                    color: Colors.black38,
                                    offset: Offset(0, 1)),
                              ])),
                  ])),
                  Padding(
                      // iOS 辅助功能特大字号（~3.1x）会把固定高度卡片的
                      // 文本区撑爆（Expanded 归零 RenderFlex overflow），
                      // 文本区字号钳制 1.3x（review 0.12.0 P3）
                      padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
                      child: MediaQuery.withClampedTextScaling(
                        maxScaleFactor: 1.3,
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(e.name,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(fontSize: 12.5)),
                              const SizedBox(height: 2),
                              Text(
                                  e.isDir
                                      ? e.modified.split('T').first
                                      : e.sizeHuman,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(
                                      fontSize: 11,
                                      color: cs.onSurfaceVariant)),
                            ]),
                      )),
                ])));
  }

  /// 网格卡片的图标兜底：铺满底色的类型图标（列表模式用色块，网格放大）
  Widget _gridIcon(Entry e) {
    final cs = Theme.of(context).colorScheme;
    if (e.isDir) {
      final isPriv = e.isPrivateRoot;
      return ColoredBox(
          color: isPriv
              ? cs.primary.withValues(alpha: 0.14)
              : cs.surfaceContainerHighest.withValues(alpha: 0.6),
          child: Center(
              child: Icon(isPriv ? Icons.lock : Icons.folder,
                  size: 36,
                  color: isPriv ? cs.primary : cs.onSurfaceVariant)));
    }
    return ColoredBox(
        color: cs.surfaceContainerHighest.withValues(alpha: 0.35),
        child: Center(
            child: FileTypeTile(
                ext: (e.ext ?? '').toLowerCase(),
                icon: _iconOf(e),
                size: 44)));
  }

  Widget _selectionBar(bool canManage) {
    final entries = _selectedEntries;
    final multi = entries.length > 1;
    final hasFile = entries.any((e) => !e.isDir);
    return SafeArea(
        child: Container(
            decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surface,
                border: Border(
                    top: BorderSide(
                        color: Theme.of(context).dividerColor, width: 0.5))),
            child: SingleChildScrollView(
                scrollDirection: Axis.horizontal, // 六键并排窄屏溢出（0.8.1 审查修复）
                child: Row(mainAxisAlignment: MainAxisAlignment.spaceAround,
                    children: [
                  if (hasFile)
                    _op(Icons.download_outlined, '下载', _downloadSelected),
                  if (multi)
                    _op(Icons.folder_zip_outlined, '打包', _zipSelected),
                  if (!multi && hasFile && canManage)
                    _op(Icons.link_outlined, '分享', _shareSingle),
                  if (canManage)
                    _op(Icons.drive_file_move_outlined, '移动',
                        () => _transferTo(entries, copy: false)),
                  if (canManage)
                    _op(Icons.copy_all_outlined, '复制',
                        () => _transferTo(entries, copy: true)),
                  if (canManage)
                    _op(Icons.delete_outline, '删除',
                        () => _delete(entries)),
                ]))));
  }

  Widget _op(IconData icon, String label, VoidCallback onTap) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(10),
          child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Icon(icon, size: 22),
                const SizedBox(height: 2),
                Text(label, style: const TextStyle(fontSize: 12)),
              ]))));
}
