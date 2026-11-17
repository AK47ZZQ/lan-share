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

  /// 把 CHANGELOG 正文洗成手机能直接读的纯文本。
  ///
  /// 放在页面类（public）而不是 `_HistoryEntryState` 里，是为了能被测试直接
  /// 调用 —— 这套清洗规则踩过两次真机 bug（`> ` 残留、软换行被当硬换行），
  /// 塞在私有类里就只能靠肉眼看截图。
  static String cleanNotes(String raw) {
    var s = raw.replaceAll('\r\n', '\n');
    // ⚠️ 这里的空白一律用 [ \t]，**不用 \s**：Dart 的 \s 包含 \n，
    // 配 multiLine 的 ^ 时会跨行吃掉前面的空行，相邻段落就被粘成一段。
    // 2026-11-17 真机截图 + 单元测试一起抓到的（空行凭空消失）。
    s = s.replaceAll(RegExp(r'^#{1,6}[ \t]*', multiLine: true), '');   // 标题井号
    // 引用块：CHANGELOG 里「> 你报的问题…」这类复述很常见，行首的 > 要清掉
    s = s.replaceAll(RegExp(r'^>[ \t]?', multiLine: true), '');
    s = s.replaceAll('**', '');                                      // 加粗
    s = s.replaceAllMapped(RegExp(r'`([^`]*)`'),
        (m) => m.group(1) ?? '');                                    // 行内代码
    s = s.replaceAll(RegExp(r'^[ \t]*[-*][ \t]+', multiLine: true), '· '); // 列表
    s = s.replaceAll(
        RegExp(r'^[ \t]*\|.*\|[ \t]*$', multiLine: true), '');       // 表格行
    s = s.replaceAll(RegExp(r'\n{3,}'), '\n\n');                      // 多余空行

    // 合并软换行。CHANGELOG 源码为了写窄会折行，markdown 里那是同一段，
    // 但 Text 会把每个 \n 都渲染成硬换行 —— 于是手机上一句话被劈成三截、
    // 第二三行还带着缩进。判据：不以「· 」开头的行接到上一行。
    final out = <String>[];
    for (final line in s.split('\n')) {
      final t = line.trim();
      if (t.isEmpty) {
        if (out.isNotEmpty && out.last.isNotEmpty) out.add('');
        continue;
      }
      if (t.startsWith('· ') || out.isEmpty || out.last.isEmpty) {
        out.add(t);
      } else {
        out[out.length - 1] = out.last + t;
      }
    }
    return out.join('\n').trim();
  }

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

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final h = widget.item;
    final full = UpdateHistoryPage.cleanNotes(h.notes);

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
              // build 为 0 说明这条数据没带 build（自定义源的清单可能没写），
              // 此时**绝不能**跟同样为 0 的 localBuild/remoteBuild 判等 ——
              // 否则整页每一行都会挂上「最新」（2026-11-08 实机踩到）。
              if (widget.localBuild > 0 && h.build == widget.localBuild)
                _tag(context, '当前', Colors.green)
              else if ((widget.remoteBuild ?? 0) > 0 &&
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
