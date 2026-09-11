// part of files_page.dart —— 服务端操作与对话框簇（B7 part 拆分）。
// 同 library 私有互通：extension on _FilesPageState，方法与拆分前逐字一致
//（零行为变化）。覆盖：新建/改名/删除/移动复制/详情/排序/上传/长单/打开/多选操作。

part of 'files_page.dart';

// extension on State 内调用 setState/mounted 等成员会触发 Flutter 的 protected
// 提示（该注解面向『子类外』场景）；本 extension 只应用于包内 _FilesPageState，
// 属受控用法，予以精准豁免。
// ignore_for_file: invalid_use_of_protected_member

extension _FilesPageActions on _FilesPageState {
  // ── 服务端操作（0.6.0 文件管理）───────────────────────────

  Future<void> _mkdir() async {
    final name = await _promptText('新建文件夹', '文件夹名称', '新建文件夹');
    if (name == null || name.trim().isEmpty || !mounted) return;
    try {
      await FilesApi.mkdirIn(_path, name);
      await _refresh();
    } catch (e) {
      showToast('创建失败：${_detail(e)}');
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
      await FilesApi.rename(e.path, name.trim());
      await _refresh();
    } catch (e2) {
      showToast('重命名失败：${_detail(e2)}');
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
    for (final it in items) {
      try {
        await FilesApi.delete(it.path);
        done++;
      } catch (_) {
        failed++;
      }
    }
    if (!mounted) return;
    setState(() => _deleting = false);
    showToast(failed > 0 ? '已删 $done 项，$failed 项失败' : '已删除 $done 项');
    await _refresh();
  }

  /// 移动/复制到所选目录
  Future<void> _transferTo(List<Entry> items, {required bool copy}) async {
    final dest = await Navigator.push<String>(context, MaterialPageRoute(
        builder: (_) => DirPickerPage(title: copy ? '复制到…' : '移动到…')));
    if (dest == null || !mounted) return;
    final sources = items.map((e) => e.path).toList();
    List<bool> results;
    try {
      results = copy
          ? await FilesApi.copy(sources, dest)
          : await FilesApi.move(sources, dest);
    } catch (e) {
      // 断网/服务端 5xx 时静默失败连刷新都不做，用户会以为成功（review 0.12.0 P2）
      showToast('${copy ? "复制" : "移动"}失败：${_detail(e)}');
      return;
    }
    if (!mounted) return;
    final okCount = results.where((v) => v).length;
    showToast(results.isEmpty
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
                  padding: EdgeInsets.all(Gap.sm),
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
              const SizedBox(height: Gap.xs),
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
                  padding: const EdgeInsets.all(Gap.md),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(e.name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.bold)),
                        const SizedBox(height: Gap.xxs),
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
              const SizedBox(height: Gap.xs),
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
                  padding: const EdgeInsets.all(Gap.md),
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
              const SizedBox(height: Gap.xs),
            ])));
  }

  // ── 预览（0.12.0，Web 同款：点可预览文件直接进全屏查看器）──

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
    showToast(n > 0 ? '已加入下载队列 $n 个文件' : '任务已在队列中');
  }

  void _shareSingle() {
    final only = _selectedEntries.single;
    setState(() => _selected.clear());
    // ignore: discarded_futures
    showShareCreate(context, path: only.path, name: only.name);
  }

  /// 打包下载（0.8.0 对齐 Web doZip）：服务端打 zip → 下载到临时目录 →
  /// 系统分享面板（存文件/发微信）。大目录打包慢，全程有 toast 反馈。
  Future<void> _zipSelected() async {
    if (_zipping) return;
    final paths = _selectedEntries.map((e) => e.path).toList();
    if (paths.isEmpty) return;
    setState(() => _zipping = true);
    showToast('正在打包 ${paths.length} 项…');
    File? f;
    try {
      final z = await FilesApi.zip(paths);
      final url = z.url;
      final name = z.name;
      if (url == null || url.isEmpty) {
        showToast('打包失败：服务端未返回下载地址');
        return;
      }
      final tmp = await getTemporaryDirectory();
      // 时间戳前缀防同名覆盖；进程被杀的残留由系统随 cache 目录回收
      f = File(
          '${tmp.path}/zip-${DateTime.now().millisecondsSinceEpoch}-$name');
      await FilesApi.downloadTo(url, f.path);
      showToast('打包完成');
      await Share.shareXFiles([XFile(f.path)], subject: name);
    } catch (e) {
      showToast('打包失败：${errDetail(e)}');
    } finally {
      // 不立刻删：shareXFiles 返回时接收方 App 才开始读 content URI，
      // 此刻删文件会让分享读到刚删除的目标（R1-P2）；残留由系统随
      // cache 目录回收（文件名已带时间戳，不冲突）
      if (mounted) setState(() => _zipping = false);
    }
  }

}
