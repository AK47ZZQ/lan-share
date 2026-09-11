// 反馈通道（v0.28）：想法/Bug 一键直达开发端。
// 落盘在服务器 data/反馈/（每条一个 md + 截图），开发端直接读写处理；
// 状态流转（已提交→处理中→已修复/已实装/已拒绝/已暂缓）由开发端改文件，
// 本页下拉刷新即可看到最新状态与备注。
import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../data/api/errors.dart';
import '../data/api/feedback_api.dart';
import 'widgets/kit.dart';

class FeedbackPage extends StatefulWidget {
  const FeedbackPage({super.key});

  @override
  State<FeedbackPage> createState() => _FeedbackPageState();
}

class _FeedbackPageState extends State<FeedbackPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tab = TabController(length: 2, vsync: this);

  // 提交面
  String _type = 'idea'; // idea | bug | other
  final _titleCtrl = TextEditingController();
  final _bodyCtrl = TextEditingController();
  final List<PlatformFile> _images = [];
  bool _sending = false;
  String _appVer = '';
  String _device = '';

  // 历史面
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  String? _error;

  static const _typeIcon = {'idea': Icons.lightbulb_outline, 'bug': Icons.bug_report_outlined, 'other': Icons.inventory_2_outlined};

  @override
  void initState() {
    super.initState();
    _loadEnv();
    _loadHistory();
  }

  @override
  void dispose() {
    _tab.dispose();
    _titleCtrl.dispose();
    _bodyCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadEnv() async {
    final info = await PackageInfo.fromPlatform();
    // 系统版本串很长（含内核/API 等冗余），取首段足够定位复现环境
    final os = Platform.operatingSystemVersion.split(',').first.trim();
    if (!mounted) return;
    setState(() {
      _appVer = '${info.version}+${info.buildNumber}';
      _device = '${Platform.operatingSystem == 'android' ? 'Android' : Platform.operatingSystem} · $os';
    });
  }

  Future<void> _loadHistory() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final rows = await FeedbackApi.list();
      if (!mounted) return;
      setState(() {
        _items = rows;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString().length > 120
            ? e.toString().substring(0, 120)
            : e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _pickImages() async {
    final room = 3 - _images.length;
    if (room <= 0) {
      showToast('最多 3 张截图');
      return;
    }
    final res = await FilePicker.platform.pickFiles(
        type: FileType.image, allowMultiple: true, withData: true);
    if (res == null || !mounted) return;
    setState(() {
      _images.addAll(res.files.take(room));
    });
  }

  Future<void> _submit() async {
    final title = _titleCtrl.text.trim();
    if (title.isEmpty) {
      showToast('先写个标题');
      return;
    }
    setState(() => _sending = true);
    try {
      await FeedbackApi.submit(
        title: title,
        type: _type,
        body: _bodyCtrl.text.trim(),
        appVer: _appVer,
        device: _device,
        images: [
          for (final i in _images)
            if (i.bytes != null)
              FeedbackImage(bytes: i.bytes!, name: i.name),
        ],
      );
      if (!mounted) return;
      showToast('已提交，处理状态会在历史里更新');
      setState(() {
        _titleCtrl.clear();
        _bodyCtrl.clear();
        _images.clear();
      });
      _tab.animateTo(1);
      _loadHistory();
    } catch (e) {
      if (!mounted) return;
      // 服务端 detail 优先（errors.dart 统一文案），其余回退通用提示
      final msg = errDetail(e);
      showToast(msg.isEmpty ? '提交失败，请稍后重试' : msg);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('反馈通道', style: TextStyle(fontSize: 16)),
          bottom: TabBar(controller: _tab, tabs: const [
            Tab(text: '提交反馈'),
            Tab(text: '历史记录'),
          ]),
          actions: [
            IconButton(icon: const Icon(Icons.refresh), onPressed: _loadHistory),
          ],
        ),
        body: TabBarView(controller: _tab, children: [
          _buildCompose(cs),
          _buildHistory(cs),
        ]),
      ),
    );
  }

  // ── 提交面 ──────────────────────────────────────────────
  Widget _buildCompose(ColorScheme cs) {
    return ListView(padding: const EdgeInsets.all(14), children: [
      SegmentedButton<String>(
        segments: const [
          ButtonSegment(value: 'idea', icon: Icon(Icons.lightbulb_outline), label: Text('想法')),
          ButtonSegment(value: 'bug', icon: Icon(Icons.bug_report_outlined), label: Text('Bug')),
          ButtonSegment(value: 'other', icon: Icon(Icons.inventory_2_outlined), label: Text('建议')),
        ],
        selected: {_type},
        onSelectionChanged: (s) => setState(() => _type = s.first),
      ),
      const SizedBox(height: 14),
      TextField(
          controller: _titleCtrl,
          maxLength: 80,
          decoration: InputDecoration(
              labelText: '标题（必填）',
              hintText: _type == 'bug'
                  ? '一句话说清问题，如：文件页多选后点下载没反应'
                  : '一句话说清你的想法',
              border: const OutlineInputBorder())),
      const SizedBox(height: 12),
      TextField(
          controller: _bodyCtrl,
          maxLength: 2000,
          minLines: 3,
          maxLines: 6,
          decoration: const InputDecoration(
              labelText: '详情（可选）',
              hintText: 'Bug 建议写：怎么操作能复现、期望什么、实际什么',
              border: OutlineInputBorder())),
      const SizedBox(height: 6),
      Row(children: [
        Text('截图（最多 3 张）', style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
        const Spacer(),
        TextButton.icon(
            onPressed: _pickImages,
            icon: const Icon(Icons.add_photo_alternate_outlined, size: 18),
            label: const Text('添加')),
      ]),
      if (_images.isNotEmpty)
        SizedBox(
            height: 84,
            child: ListView(scrollDirection: Axis.horizontal, children: [
              for (final img in _images)
                Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: Stack(children: [
                      ClipRRect(
                          borderRadius: BorderRadius.circular(10),
                          child: img.bytes != null
                              ? Image.memory(img.bytes!,
                                  width: 84, height: 84, fit: BoxFit.cover)
                              : const SizedBox(width: 84, height: 84)),
                      Positioned(
                          right: 0, top: 0,
                          child: InkWell(
                              onTap: () => setState(() => _images.remove(img)),
                              child: CircleAvatar(
                                  radius: 10,
                                  backgroundColor: cs.error,
                                  child: const Icon(Icons.close,
                                      size: 12, color: Colors.white)))),
                    ])),
            ])),
      const SizedBox(height: 10),
      FilledButton.icon(
          onPressed: _sending ? null : _submit,
          icon: _sending
              ? const SizedBox(
                  width: 16, height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.send_outlined),
          label: Text(_sending ? '提交中…' : '提交反馈')),
      const SizedBox(height: 8),
      Text('提交后直达开发端（会附带 App 版本与系统信息）。\n处理状态在「历史记录」里实时更新。',
          style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
    ]);
  }

  // ── 历史面 ──────────────────────────────────────────────
  Widget _buildHistory(ColorScheme cs) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 80),
        Center(child: Text(_error!, textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 12, color: Colors.grey))),
        Center(child: TextButton(onPressed: _loadHistory, child: const Text('重试'))),
      ]);
    }
    if (_items.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 100),
        Center(child: Text('还没有反馈记录', style: TextStyle(color: Colors.grey))),
      ]);
    }
    return RefreshIndicator(
        onRefresh: _loadHistory,
        child: ListView.builder(
            padding: const EdgeInsets.all(12),
            itemCount: _items.length,
            itemBuilder: (ctx, i) => _historyCard(_items[i], cs)));
  }

  Widget _historyCard(Map<String, dynamic> it, ColorScheme cs) {
    final status = (it['status'] as String?) ?? '已提交';
    final st = _statusStyle(status, cs);
    return Card(
        margin: const EdgeInsets.only(bottom: 10),
        child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: () => _showDetail(it, cs),
            child: Padding(
                padding: const EdgeInsets.fromLTRB(14, 11, 14, 11),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                  Row(children: [
                    Icon(_typeIcon[it['type']] ?? Icons.inventory_2_outlined,
                        size: 17, color: cs.primary),
                    const SizedBox(width: 6),
                    Expanded(
                        child: Text((it['title'] as String?) ?? '',
                            maxLines: 1, overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                                fontSize: 14, fontWeight: FontWeight.w600))),
                    Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                            color: st.$1.withValues(alpha: 0.14),
                            borderRadius: BorderRadius.circular(999)),
                        child: Text(status,
                            style: TextStyle(
                                fontSize: 11.5, color: st.$1,
                                fontWeight: FontWeight.w600))),
                  ]),
                  if (((it['note'] as String?) ?? '').isNotEmpty)
                    Padding(
                        padding: const EdgeInsets.only(top: 5),
                        child: Row(children: [
                          Icon(Icons.sticky_note_2_outlined,
                              size: 13, color: cs.onSurfaceVariant),
                          const SizedBox(width: 4),
                          Expanded(
                              child: Text(it['note'] as String,
                                  style: TextStyle(
                                      fontSize: 12, color: cs.onSurfaceVariant))),
                        ])),
                  const SizedBox(height: 4),
                  Row(children: [
                    Text((it['created'] as String?) ?? '',
                        style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                    const Spacer(),
                    if (((it['images'] as List?) ?? []).isNotEmpty)
                      Padding(
                          padding: const EdgeInsets.only(right: 6),
                          child: Icon(Icons.image_outlined,
                              size: 13, color: cs.onSurfaceVariant)),
                    Text('更新 ${it['updated'] ?? ''}',
                        style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                  ]),
                ]))));
  }

  Future<void> _showDetail(Map<String, dynamic> it, ColorScheme cs) async {
    await showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        isScrollControlled: true,
        builder: (bctx) => SafeArea(
            child: Container(
                constraints: BoxConstraints(
                    maxHeight: MediaQuery.of(bctx).size.height * 0.75),
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
                child: ListView(shrinkWrap: true, children: [
                  Row(children: [
                    Expanded(
                        child: Text((it['title'] as String?) ?? '',
                            style: const TextStyle(
                                fontSize: 16, fontWeight: FontWeight.bold))),
                    _statusChip((it['status'] as String?) ?? '已提交', cs),
                  ]),
                  const SizedBox(height: 4),
                  Text('${it['type']} · ${it['created']} · ${it['app']}',
                      style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                  if (((it['body'] as String?) ?? '').isNotEmpty)
                    Padding(
                        padding: const EdgeInsets.only(top: 10),
                        child: Text(it['body'] as String,
                            style: const TextStyle(fontSize: 13.5, height: 1.5))),
                  if (((it['note'] as String?) ?? '').isNotEmpty)
                    Padding(
                        padding: const EdgeInsets.only(top: 10),
                        child: Container(
                            width: double.infinity,
                            padding: const EdgeInsets.all(10),
                            decoration: BoxDecoration(
                                color: cs.primary.withValues(alpha: 0.08),
                                borderRadius: BorderRadius.circular(10)),
                            child: Text('开发备注：${it['note']}',
                                style: const TextStyle(fontSize: 12.5)))),
                  for (final url in ((it['images'] as List?) ?? []))
                    Padding(
                        padding: const EdgeInsets.only(top: 10),
                        child: ClipRRect(
                            borderRadius: BorderRadius.circular(10),
                            child: FutureBuilder<Uint8List?>(
                                future: _fetchImage(url as String),
                                builder: (ctx, snap) => snap.hasData
                                    ? Image.memory(snap.data!,
                                        fit: BoxFit.contain)
                                    : const SizedBox(
                                        height: 120,
                                        child: Center(
                                            child: CircularProgressIndicator(
                                                strokeWidth: 2)))))),
                ]))));
  }

  Future<Uint8List?> _fetchImage(String url) async {
    try {
      return await FeedbackApi.fetchBytes(url);
    } catch (_) {
      return null;
    }
  }

  Widget _statusChip(String status, ColorScheme cs) {
    final st = _statusStyle(status, cs);
    return Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
        decoration: BoxDecoration(
            color: st.$1.withValues(alpha: 0.14),
            borderRadius: BorderRadius.circular(999)),
        child: Text(status,
            style: TextStyle(
                fontSize: 12, color: st.$1, fontWeight: FontWeight.w600)));
  }

  // 状态 →（颜色）映射；未知状态回退灰色
  (Color,) _statusStyle(String status, ColorScheme cs) {
    const map = {
      '已提交': Colors.grey,
      '处理中': Colors.blue,
      '已修复': Colors.green,
      '已实装': Colors.teal,
      '已拒绝': Colors.red,
      '已暂缓': Colors.orange,
    };
    return (map[status] ?? Colors.grey,);
  }
}
