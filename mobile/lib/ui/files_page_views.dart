// part of files_page.dart —— 视图层（B7 part 拆分）。
// 同 library 私有互通：extension on _FilesPageState（build override 留主类，
// extension 不能 override）。覆盖：_buildBody/钉住行/图标回退/列表行/网格卡/多选操作栏。

part of 'files_page.dart';

// extension on State 内调用 setState/mounted 等成员会触发 Flutter 的 protected
// 提示（该注解面向『子类外』场景）；本 extension 只应用于包内 _FilesPageState，
// 属受控用法，予以精准豁免。
// ignore_for_file: invalid_use_of_protected_member

extension _FilesPageViews on _FilesPageState {
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
                padding: const EdgeInsets.fromLTRB(Gap.md, Gap.xs, Gap.md, 0),
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
            padding: const EdgeInsets.fromLTRB(Gap.sm, Gap.xs, Gap.sm, 0),
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

  /// 网格列数（v1.0 统一到全局断点：手机 3 / 横屏 4 / 平板 5）。
  /// 原实现用 440/640 两档自定义阈值，与照片墙/专辑的断点不一致；
  /// 文件格子带文件名标签，故不用照片墙的 6 列（避免列宽过窄）
  int get _gridColumns => context.gridColumns(compact: 3, medium: 4, expanded: 5);

  Widget _pinnedRow(String p) => ListTile(
      dense: true,
      leading: const Icon(Icons.star, size: IconSizes.md),
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
          borderRadius: Radii.bControl,
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
            borderRadius: Radii.bControl,
          ),
          child: Icon(isPriv ? Icons.lock : Icons.folder,
              size: IconSizes.md,
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
    final radius = Radii.bMd;
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
                            top: Radius.circular(Radii.control)),
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
                                  size: IconSizes.sm, color: Colors.white))),
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
                      padding: const EdgeInsets.fromLTRB(Gap.xs, 6, Gap.xs, Gap.xs),
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

  /// 复制所选条目的服务器真实路径（abs_path 优先，旧服务端回退逻辑路径）；
  /// 多选每行一条，方便批量交给脚本/Agent 处理。文件夹同样适用。
  Future<void> _copySelectedPaths() async {
    final entries = _selectedEntries;
    final text = entries.map((e) => e.absPath ?? '/${e.path}').join('\n');
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    showToast(entries.length > 1 ? '已复制 ${entries.length} 个路径' : '已复制路径');
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
                  _op(Icons.terminal, '复制路径', () => _copySelectedPaths()),
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
          borderRadius: Radii.bSm,
          child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: Gap.sm, vertical: 6),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Icon(icon, size: 22),
                const SizedBox(height: 2),
                Text(label, style: const TextStyle(fontSize: 12)),
              ]))));
}

