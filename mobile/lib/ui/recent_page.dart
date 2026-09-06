// 最近文件 + 搜索（App v0.6.0）：/api/recent 全库按修改时间倒排；
// 搜索框走 /api/search（服务端 rglob 文件名子串匹配）。
// v0.15 重构：图/视频行首 /thumb/ 缩略图（AuthImage 同一条鉴权通道，
// ?v=modified 契约防同名覆盖缓存陈旧）、类型筛选 chips（全部/图片/视频/
// 文档）、可预览项点行直进全屏查看器（预览范围=当前筛选结果），其余类型
// 保持弹操作面板。与文件页 _openFile 同模式（防重入 _opening）。
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api/api_client.dart';
import '../data/local/prefs.dart';
import '../state/session.dart';
import '../transfer/transfer_engine.dart';
import '../transfer/transfer_task.dart';
import 'photo_models.dart';
import 'audio_viewer.dart';
import 'doc_viewer.dart';
import 'photo_viewer.dart';
import 'share_create_sheet.dart';
import 'widgets/auth_image.dart';
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

  static const imgExts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp',
      '.heic', '.heif'};
  static const vidExts = {'.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v',
      '.3gp'};
  static const _docExts = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt',
    '.md', '.csv', '.epub',
  };
  // v0.17/0.19：DocViewerPage 支持范围（与 doc_viewer.isDocPreviewable 口径一致）
  static const _docPreviewExts = {
    '.md', '.markdown', '.txt', '.log', '.json', '.csv', '.pdf', '.docx',
    '.doc', '.xlsx', '.pptx', '.rtf', '.html', '.htm',
    '.yaml', '.yml', '.xml', '.ini', '.conf', '.cfg', '.properties', '.py',
    '.js', '.ts', '.dart', '.css', '.sh', '.bat', '.ps1',
    '.sql', '.srt',
  };
  // v0.18：音频扩面（与 ui/audio_viewer.dart kAudioExts 同口径）
  static const _audioExts = {
    '.mp3', '.m4a', '.aac', '.flac', '.wav', '.ogg', '.oga', '.opus',
  };

  String get _ext => (ext ?? '').toLowerCase();
  bool get isImage => imgExts.contains(_ext);
  bool get isVideo => vidExts.contains(_ext);
  bool get isMedia => isImage || isVideo;
  /// PhotoViewer 走图/视频通道；文档类（v0.17）走 DocViewerPage；
  /// 音频/压缩包等仍无内建查看器，弹操作面板
  bool get isDocPreviewable => _docPreviewExts.contains(_ext);
  bool get isAudioPreviewable => _audioExts.contains(_ext);
  bool get previewable => isMedia || isDocPreviewable || isAudioPreviewable;
  // 与文件页同语义的类型分组（筛选 chips 联动）
  String get typeGroup => isImage
      ? 'image'
      : isVideo
          ? 'video'
          : _docExts.contains(_ext)
              ? 'doc'
              : 'other';
}

enum _TypeFilter { all, image, video, doc, other }

class _RecentPageState extends ConsumerState<RecentPage> {
  final _search = TextEditingController();
  List<_RecentItem>? _items;
  bool _searching = false;
  String? _error;
  _TypeFilter _filter = _TypeFilter.all;
  String? _base;
  bool _opening = false; // 防重入：快速双击 push 两个 viewer

  @override
  void initState() {
    super.initState();
    () async {
      final b = await Prefs.lastAddress();
      if (mounted) setState(() => _base = b);
    }();
    Prefs.addressNotifier.addListener(_onAddressChanged);
    _load();
  }

  @override
  void dispose() {
    Prefs.addressNotifier.removeListener(_onAddressChanged);
    _search.dispose();
    super.dispose();
  }

  void _onAddressChanged() {
    final b = Prefs.addressNotifier.value;
    if (mounted && b.isNotEmpty && b != _base) setState(() => _base = b);
  }

  List<_RecentItem> get _filtered {
    final items = _items ?? const <_RecentItem>[];
    if (_filter == _TypeFilter.all) return items;
    final g = switch (_filter) {
      _TypeFilter.image => 'image',
      _TypeFilter.video => 'video',
      _TypeFilter.doc => 'doc',
      _ => 'other',
    };
    return items.where((e) => e.typeGroup == g).toList();
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

  /// 可预览项点行直进查看器（与文件页 _openFile 同模式）：预览范围 =
  /// 当前筛选结果里的全部可预览项，左右滑在它们之间翻页。
  /// 红队 P2-3：传条目不传 index——build 与 tap 之间 _load/搜索完成会让
  /// _filtered 变化，跨帧 index 会 RangeError 或静默打开别的文件
  Future<void> _openPreview(_RecentItem it) async {
    if (_opening || _base == null) return;
    // v0.17：文档类直进文档预览器
    if (it.isDocPreviewable) {
      _opening = true;
      try {
        await Navigator.push(context, MaterialPageRoute(
            builder: (_) => DocViewerPage(path: it.path, name: it.name)));
      } finally {
        _opening = false;
      }
      return;
    }
    // v0.18：音频直进内建播放器
    if (it.isAudioPreviewable) {
      _opening = true;
      try {
        await Navigator.push(context, MaterialPageRoute(
            builder: (_) => AudioViewerPage(path: it.path, name: it.name)));
      } finally {
        _opening = false;
      }
      return;
    }
    if (!it.previewable) return _itemSheet(it);
    final previewable = _filtered.where((e) => e.previewable).toList();
    if (previewable.isEmpty) return;
    _opening = true;
    try {
      final photos = previewable
          .map((e) => PhotoItem(
              path: e.path,
              name: e.name,
              taken: e.modified.split('T').first,
              month: '',
              uploader: '',
              mediaType: e.isVideo ? 'video' : 'photo',
              cacheKey: e.modified))
          .toList();
      var idx = photos.indexWhere((p) => p.path == it.path);
      if (idx < 0) idx = 0;
      // 必须 await：_opening 防重入靠它（未 await 时 finally 立即复位，
      // 快速双击会压两个 viewer——第一轮 review P2）
      final deleted = await Navigator.push<bool>(context, MaterialPageRoute(
          builder: (_) => PhotoViewerPage(
              base: _base!, items: photos, initial: idx),
          fullscreenDialog: true));
      if (deleted == true) await _load();
    } finally {
      _opening = false;
    }
  }

  Widget _leading(_RecentItem it) {
    final b = _base;
    if (it.isMedia && b != null && b.isNotEmpty) {
      return ClipRRect(
          borderRadius: BorderRadius.circular(8),
          child: SizedBox(
              width: 52,
              height: 52,
              child: AuthImage(
                  url: '$b/thumb/'
                      '${it.path.split("/").map(Uri.encodeComponent).join("/")}'
                      '?v=${Uri.encodeComponent(it.modified)}')));
    }
    // 非媒体按类型分色图标（文档蓝/音频紫/压缩包棕/其他灰）
    final (icon, color) = switch (it.typeGroup) {
      'doc' => (Icons.description_outlined, Colors.blue.shade600),
      'other' when _isAudio(it._ext) => (
          Icons.music_note_outlined,
          Colors.purple.shade400
        ),
      'other' when _isArchive(it._ext) => (
          Icons.folder_zip_outlined,
          Colors.brown.shade400
        ),
      _ => (Icons.insert_drive_file_outlined, Colors.grey.shade500),
    };
    return Container(
        width: 52,
        height: 52,
        decoration: BoxDecoration(
            color: color.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(8)),
        child: Icon(icon, size: 26, color: color));
  }

  static bool _isAudio(String ext) => const {
        '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav'
      }.contains(ext);
  static bool _isArchive(String ext) => const {
        '.zip', '.rar', '.7z', '.tar', '.gz'
      }.contains(ext);

  @override
  Widget build(BuildContext context) {
    final items = _filtered;
    final cs = Theme.of(context).colorScheme;
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
          // 类型筛选 chips（v0.15）：文件类型混杂时的一级收敛
          SizedBox(
              height: 40,
              child: ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 10),
                  children: [
                    for (final (f, label) in const [
                      (_TypeFilter.all, '全部'),
                      (_TypeFilter.image, '图片'),
                      (_TypeFilter.video, '视频'),
                      (_TypeFilter.doc, '文档'),
                      (_TypeFilter.other, '其他'),
                    ])
                      Padding(
                          padding: const EdgeInsets.only(right: 6),
                          child: ChoiceChip(
                              label: Text(label),
                              selected: _filter == f,
                              visualDensity: VisualDensity.compact,
                              onSelected: (_) =>
                                  setState(() => _filter = f))),
                  ])),
          Expanded(
            child: _searching || (_items == null && _error == null)
                ? const Center(child: CircularProgressIndicator())
                : (items.isEmpty && _error != null)
                    ? ListView(children: [
                        const SizedBox(height: 120),
                        Center(child: Text(_error!,
                            style: TextStyle(color: cs.onSurfaceVariant))),
                        const SizedBox(height: 8),
                        Center(child: TextButton(
                            onPressed: _load, child: const Text('刷新'))),
                      ])
                    : items.isEmpty
                        ? ListView(children: const [
                            SizedBox(height: 120),
                            Center(child: Text('没有匹配的文件',
                                style: TextStyle(color: Colors.grey))),
                          ])
                        : RefreshIndicator(
                            onRefresh: _load,
                            child: ListView.builder(
                                itemCount: items.length,
                                itemBuilder: (context, i) {
                                  final it = items[i];
                                  return ListTile(
                                      leading: _leading(it),
                                      title: Text(it.name,
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis),
                                      subtitle: Text(
                                          '${it.sizeHuman} · ${it.modified.split('T').first} · ${it.path.split('/').length > 2 ? it.path.split('/').sublist(0, it.path.split('/').length - 1).join('/') : '根目录'}',
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          style:
                                              const TextStyle(fontSize: 12)),
                                      onTap: () => it.previewable
                                          ? _openPreview(it)
                                          : _itemSheet(it));
                                })),
          ),
        ]));
  }
}
