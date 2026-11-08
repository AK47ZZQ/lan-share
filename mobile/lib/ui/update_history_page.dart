// 更新记录页：把关于页里那一长串历史搬出来单独一页。
//
// 为什么要拆：历史条数会随发版一直长（服务端线当前 85 个版本、App 线 40 个），
// 全塞在「关于」页里等于把一个阅读型内容混进设置页，滑半天找不到许可入口。
// 现在关于页只留最近 3 条 + 一个「查看全部」入口，想看全的点进来。
//
// 数据来源不变：`UpdateService.history()` → 服务端 `App安装包/versions.json`
// （由 release.py 从 CHANGELOG 全量生成）。**App 只看 App 这条线**，服务端
// 的历史在托盘面板里看，两边不混。
import 'package:flutter/material.dart';

import '../data/update_service.dart';
import 'theme/design_tokens.dart';

class UpdateHistoryPage extends StatelessWidget {
  const UpdateHistoryPage({
    super.key,
    required this.items,
    required this.localBuild,
    this.remoteBuild,
  });

  final List<UpdateHistoryItem> items;
  final int localBuild;
  final int? remoteBuild;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('更新记录')),
      body: items.isEmpty
          ? Center(
              child: Text('还没有更新记录',
                  style: TextStyle(
                      fontSize: 12.5,
                      color: theme.colorScheme.onSurfaceVariant)))
          : ListView.builder(
              padding: const EdgeInsets.fromLTRB(Gap.md, Gap.sm, Gap.md, Gap.lg),
              // 用 builder 而不是 children：几十上百条时一次性布局会把页面卡死
              itemCount: items.length + 1,
              itemBuilder: (context, i) {
                if (i == 0) {
                  return Padding(
                    padding: const EdgeInsets.only(bottom: Gap.xs),
                    child: Text('共 ${items.length} 个版本',
                        style: TextStyle(
                            fontSize: 11.5,
                            color: theme.colorScheme.onSurfaceVariant)),
                  );
                }
                return HistoryEntry(
                  item: items[i - 1],
                  localBuild: localBuild,
                  remoteBuild: remoteBuild,
                );
              },
            ),
    );
  }
}

/// 更新历史里的一条。
///
/// 为什么要单独拆出来：
/// 1. GitHub Release 的 body 是 markdown，直接塞进 Text 会把 `###`、`**`、
///    "| 表 |" 全露出来，手机上像乱码。
/// 2. 一个版本的说明可能上千字，七个版本加起来七八千字，一屏看不完 ——
///    用户要的「能看全」不是「全部展开堆在一起」，是「每个版本都在、
///    都能点到，但默认不糊一屏」。
/// 所以：清洗标记 + 默认只显示前几行，点一下展开全文。
class HistoryEntry extends StatefulWidget {
  const HistoryEntry({
    super.key,
    required this.item,
    required this.localBuild,
    required this.remoteBuild,
  });

  final UpdateHistoryItem item;
  final int localBuild;
  final int? remoteBuild;

  @override
  State<HistoryEntry> createState() => _HistoryEntryState();
}

class _HistoryEntryState extends State<HistoryEntry> {
  bool _expanded = false;

  /// markdown → 纯文本。只做最朴素的清洗：不引第三方渲染库，
  /// 因为这里要的只是「能读」，不是「好看」。
  static String cleanNotes(String raw) {
    var s = raw.replaceAll('\r\n', '\n');
    s = s.replaceAll(RegExp(r'^#{1,6}\s*', multiLine: true), '');   // 标题井号
    s = s.replaceAll('**', '');                                      // 加粗
    s = s.replaceAllMapped(RegExp(r'`([^`]*)`'),
        (m) => m.group(1) ?? '');                                    // 行内代码
    s = s.replaceAll(RegExp(r'^\s*[-*]\s+', multiLine: true), '· ');  // 列表
    s = s.replaceAll(RegExp(r'^\s*\|.*\|\s*$', multiLine: true), ''); // 表格行
    s = s.replaceAll(RegExp(r'\n{3,}'), '\n\n');                      // 多余空行
    return s.trim();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final h = widget.item;
    final full = cleanNotes(h.notes);

    // 超过这个长度才折叠。短说明（几个字）折叠反而多一步操作。
    const collapseOver = 140;
    final needsCollapse = full.length > collapseOver;
    final shown = (!needsCollapse || _expanded) ? full : _preview(full);

    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Text('v${h.version}',
                  style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w700,
                      color: theme.colorScheme.primary)),
              const SizedBox(width: Gap.xs),
              if (h.date.isNotEmpty)
                Text(h.date,
                    style: TextStyle(
                        fontSize: 11.5,
                        color: theme.colorScheme.onSurfaceVariant)),
              const SizedBox(width: 6),
              if (h.build == widget.localBuild)
                _tag(context, '当前', Colors.green)
              else if (widget.remoteBuild != null &&
                  h.build == widget.remoteBuild)
                _tag(context, '最新', theme.colorScheme.primary),
            ]),
            if (shown.isNotEmpty) ...[
              const SizedBox(height: 3),
              Text(shown,
                  style: TextStyle(
                      fontSize: 12,
                      height: 1.45,
                      color: theme.colorScheme.onSurfaceVariant)),
            ],
            if (needsCollapse)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: GestureDetector(
                  onTap: () => setState(() => _expanded = !_expanded),
                  child: Text(_expanded ? '收起' : '展开全文',
                      style: TextStyle(
                          fontSize: 11.5,
                          color: theme.colorScheme.primary)),
                ),
              ),
          ]),
    );
  }

  /// 折叠时显示的内容：标题行 + 前三条要点。
  /// 按行切而不是按字数切 —— 切在半句话中间比多显示一行更难看。
  static String _preview(String full) {
    final lines = full.split('\n').where((l) => l.trim().isNotEmpty).toList();
    if (lines.length <= 4) return full;
    return lines.take(4).join('\n');
  }

  Widget _tag(BuildContext context, String text, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(4)),
      child: Text(text, style: TextStyle(fontSize: 10, color: color)),
    );
  }
}
