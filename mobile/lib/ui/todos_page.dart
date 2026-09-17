// 效率舱 · 待办页（v1.1 M1）。
// 最终计划 v5 §4.2/§4.3：M1 只有待办一个视图 → 不显示分段控件（标题即"待办"，
// 分段控件随 M2 专注/M4 日历渐进出现）。
// · 今日聚合卡：今日待办 完成/总数（M2 接番茄环，M3 接日记提示）
// · 分组：今天（含逾期）/ 未来 7 天 / 以后 / 已完成（默认折叠）
// · 待办卡：勾选完成（3 秒撤销）· 优先级左色条 · 截止日红/橙 ·
//   家庭清单"全家"角标 + 指派成员头像；长按出操作单（改期/优先级/删除）
// · 清单管理：新建（个人/家庭）/重命名/删除——个人清单仅自己可见
// · 视觉：间距/圆角走令牌（Gap/Radii），字号走 textTheme，零字面量；
//   内容限宽走 AdaptiveContent（720）
import 'package:flutter/material.dart';

import '../data/api/errors.dart';
import '../data/api/todos_api.dart';
import 'theme/design_tokens.dart';
import 'theme/responsive.dart';
import 'widgets/kit.dart';

class TodosPage extends StatefulWidget {
  const TodosPage({super.key});

  @override
  State<TodosPage> createState() => _TodosPageState();
}

class _Todo {
  final int id;
  final int listId;
  final String listName;
  final String listKind; // personal | family
  final int listOwnerId;
  final String title;
  final String notes;
  final num? dueAt;
  final int priority;
  final int? assigneeId;
  final String status; // todo | done
  final num? doneAt;
  final int? doneBy;

  _Todo(Map m)
      : id = ((m['id'] as num?) ?? 0).toInt(),
        listId = ((m['list_id'] as num?) ?? 0).toInt(),
        listName = (m['list_name'] as String?) ?? '',
        listKind = (m['list_kind'] as String?) ?? 'personal',
        listOwnerId = ((m['list_owner_id'] as num?) ?? 0).toInt(),
        title = (m['title'] as String?) ?? '',
        notes = (m['notes'] as String?) ?? '',
        dueAt = m['due_at'] as num?,
        priority = ((m['priority'] as num?) ?? 0).toInt(),
        assigneeId = (m['assignee_id'] as num?)?.toInt(),
        status = (m['status'] as String?) ?? 'todo',
        doneAt = m['done_at'] as num?,
        doneBy = (m['done_by'] as num?)?.toInt();

  bool get done => status == 'done';

  _Todo copyWith({String? status, num? doneAt, int? doneBy}) => _Todo({
        'id': id, 'list_id': listId, 'list_name': listName,
        'list_kind': listKind, 'list_owner_id': listOwnerId,
        'title': title, 'notes': notes, 'due_at': dueAt,
        'priority': priority, 'assignee_id': assigneeId,
        'status': status ?? this.status, 'done_at': doneAt ?? this.doneAt,
        'done_by': doneBy ?? this.doneBy,
      });
}

class _TodoList {
  final int id;
  final String name;
  final String kind;
  final int ownerId;
  final int openCount;

  _TodoList(Map m)
      : id = ((m['id'] as num?) ?? 0).toInt(),
        name = (m['name'] as String?) ?? '',
        kind = (m['kind'] as String?) ?? 'personal',
        ownerId = ((m['owner_id'] as num?) ?? 0).toInt(),
        openCount = ((m['open_count'] as num?) ?? 0).toInt();

  bool get family => kind == 'family';
}

class _TodosPageState extends State<TodosPage> {
  bool _loading = true;
  bool _showDone = false;
  List<_Todo> _todos = const [];
  List<_TodoList> _lists = const [];
  Map<int, Map<String, String>> _members = const {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final all = await TodosApi.listAll();
      final listsRes = await TodosApi.lists();
      if (!mounted) return;
      setState(() {
        _todos = [for (final m in all.$1) _Todo(m as Map)];
        _members = all.$2;
        _lists = [for (final m in listsRes.$1) _TodoList(m as Map)];
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showToast(errDetail(e));
    }
  }

  double get _todayStart {
    final now = DateTime.now();
    return DateTime(now.year, now.month, now.day).millisecondsSinceEpoch / 1000;
  }

  // ── 分组（今天含逾期；无日期或远期归"以后"）────────────────
  Map<String, List<_Todo>> get _groups {
    final weekEnd = _todayStart + 7 * 86400;
    final today = <_Todo>[];
    final week = <_Todo>[];
    final later = <_Todo>[];
    final done = <_Todo>[];
    for (final t in _todos) {
      if (t.done) {
        done.add(t);
      } else if (t.dueAt != null && t.dueAt! < weekEnd) {
        // 逾期也算"今天"（红字标示），未逾期近 7 天进"未来 7 天"
        (t.dueAt! < _todayStart ? today : week).add(t);
      } else {
        later.add(t);
      }
    }
    int rank(_Todo t) =>
        (t.priority << 20) - ((t.dueAt ?? 1e12).toInt() ~/ 1000);
    today.sort((a, b) => rank(b).compareTo(rank(a)));
    week.sort((a, b) => rank(b).compareTo(rank(a)));
    later.sort((a, b) => rank(b).compareTo(rank(a)));
    done.sort((a, b) => (b.doneAt ?? 0).compareTo(a.doneAt ?? 0));
    return {'今天': today, '未来 7 天': week, '以后': later, '已完成': done};
  }

  // ── 操作 ─────────────────────────────────────────────────

  Future<void> _toggle(_Todo t) async {
    final target = t.done ? 'todo' : 'done';
    final updated = t.copyWith(
        status: target,
        doneAt: target == 'done'
            ? DateTime.now().millisecondsSinceEpoch / 1000
            : null,
        doneBy: target == 'done' ? null : null);
    setState(() {
      _todos = [for (final x in _todos) x.id == t.id ? updated : x];
    });
    try {
      await TodosApi.patchTodo(t.id, status: target);
    } catch (e) {
      if (mounted) showToast(errDetail(e));
      await _load();
      return;
    }
    if (target == 'done' && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text('已完成「${t.title}」'),
        duration: const Duration(seconds: 3),
        action: SnackBarAction(
          label: '撤销',
          onPressed: () async {
            try {
              await TodosApi.patchTodo(t.id, status: 'todo');
            } catch (_) {}
            if (mounted) await _load();
          },
        ),
      ));
    }
  }

  Future<void> _createTodo() async {
    if (!mounted) return;
    if (_lists.isEmpty) {
      showToast('先在右上角「清单管理」里建一个清单');
      return;
    }
    final created = await showModalBottomSheet<bool>(
        context: context,
        isScrollControlled: true,
        builder: (_) => _TodoEditSheet(lists: _lists, members: _members));
    if (created == true) await _load();
  }

  Future<void> _todoActions(_Todo t) async {
    final act = await showModalBottomSheet<String>(
        context: context,
        builder: (_) => SafeArea(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              ListTile(
                  leading: const Icon(Icons.event_outlined),
                  title: const Text('改期'),
                  onTap: () => Navigator.pop(context, 'due')),
              ListTile(
                  leading: const Icon(Icons.flag_outlined),
                  title: const Text('优先级'),
                  onTap: () => Navigator.pop(context, 'priority')),
              ListTile(
                  leading: const Icon(Icons.delete_outline),
                  title: const Text('删除'),
                  onTap: () => Navigator.pop(context, 'delete')),
            ])));
    if (!mounted || act == null) return;
    if (act == 'delete') {
      try {
        await TodosApi.deleteTodo(t.id);
      } catch (e) {
        if (mounted) showToast(errDetail(e));
        return;
      }
      await _load();
      return;
    }
    if (act == 'due') {
      final now = DateTime.now();
      final d = await showDatePicker(
          context: context,
          initialDate: now,
          firstDate: now.subtract(const Duration(days: 365)),
          lastDate: now.add(const Duration(days: 365 * 3)));
      if (d == null || !mounted) return;
      final due =
          DateTime(d.year, d.month, d.day, 23, 59).millisecondsSinceEpoch /
              1000;
      try {
        await TodosApi.patchTodo(t.id, dueAt: due);
      } catch (e) {
        if (mounted) showToast(errDetail(e));
        return;
      }
      await _load();
      return;
    }
    if (act == 'priority') {
      final p = await showDialog<int>(
          context: context,
          builder: (_) => SimpleDialog(
              title: const Text('优先级'),
              children: [
                for (final e in <(int, String)>[(0, '普通'), (1, '中'), (2, '高')])
                  SimpleDialogOption(
                      onPressed: () => Navigator.pop(context, e.$1),
                      child: Text(e.$2)),
              ]));
      if (p == null || !mounted) return;
      try {
        await TodosApi.patchTodo(t.id, priority: p);
      } catch (e) {
        if (mounted) showToast(errDetail(e));
        return;
      }
      await _load();
    }
  }

  Future<void> _manageLists() async {
    await showModalBottomSheet(
        context: context,
        isScrollControlled: true,
        builder: (_) => _ListsSheet(lists: _lists, members: _members,
            onChanged: () async {
              // 清单增删后同步内存里的清单表（待办由 sheet 内刷新回调拉全量）
              final r = await TodosApi.lists();
              if (!mounted) return;
              setState(() {
                _lists = [for (final m in r.$1) _TodoList(m as Map)];
                _members = r.$2;
              });
            }));
    await _load();
  }

  String _memberName(int? uid) {
    if (uid == null || uid <= 0) return '';
    final m = _members[uid];
    if (m == null) return '已停用成员';
    final n = m['nickname'] ?? '';
    return n.isNotEmpty ? n : (m['username'] ?? '');
  }

  // ── UI ───────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final g = _groups;
    final openToday = g['今天']!.length;
    final doneToday = g['已完成']!
        .where((t) => (t.doneAt ?? 0) >= _todayStart)
        .length;
    return Scaffold(
      appBar: AppBar(
        title: const Text('待办'),
        actions: [
          IconButton(
              icon: const Icon(Icons.playlist_add_outlined),
              tooltip: '清单管理',
              onPressed: _manageLists),
          IconButton(
              icon: const Icon(Icons.refresh_outlined),
              tooltip: '刷新',
              onPressed: _load),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _createTodo,
        icon: const Icon(Icons.add_task_outlined),
        label: const Text('新建待办'),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: AdaptiveContent(
                child: ListView(
                    padding:
                        const EdgeInsets.only(top: Gap.sm, bottom: Gap.xxxl),
                    children: [
                      _todayCard(cs, done: doneToday, total: doneToday + openToday),
                      for (final e in g.entries)
                        if (e.key != '已完成' || _showDone) _group(e.key, e.value),
                      if (g['已完成']!.isNotEmpty)
                        TextButton.icon(
                          onPressed: () =>
                              setState(() => _showDone = !_showDone),
                          icon: Icon(_showDone
                              ? Icons.expand_less_outlined
                              : Icons.expand_more_outlined),
                          label: Text(_showDone
                              ? '收起已完成'
                              : '展开已完成（${g['已完成']!.length}）'),
                        ),
                      if (_todos.isEmpty) _emptyState(cs),
                    ]),
              ),
            ),
    );
  }

  Widget _todayCard(ColorScheme cs, {required int done, required int total}) {
    return Card(
      margin: const EdgeInsets.only(bottom: Gap.md),
      child: Padding(
        padding: const EdgeInsets.all(Gap.md),
        child: Row(children: [
          Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
            Text('今日待办',
                style: Theme.of(context).textTheme.labelMedium),
            const SizedBox(height: Gap.xxs),
            Text('$done / $total',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                    fontWeight: FontWeight.w700, color: cs.primary)),
          ])),
          Text('效率舱', style: Theme.of(context).textTheme.labelSmall),
        ]),
      ),
    );
  }

  Widget _group(String title, List<_Todo> items) {
    final cs = Theme.of(context).colorScheme;
    final overdue = title == '今天';
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Padding(
          padding: const EdgeInsets.fromLTRB(Gap.xxs, Gap.sm, 0, Gap.xxs),
          child: Text(overdue && items.isNotEmpty ? '今天（含逾期）' : title,
              style: Theme.of(context)
                  .textTheme
                  .labelLarge
                  ?.copyWith(color: cs.onSurfaceVariant))),
      if (items.isEmpty)
        Padding(
            padding: const EdgeInsets.symmetric(vertical: Gap.xxs),
            child: Text('没有待办',
                style: Theme.of(context).textTheme.labelSmall))
      else
        ...items.map(_todoCard),
    ]);
  }

  Widget _todoCard(_Todo t) {
    final cs = Theme.of(context).colorScheme;
    final overdue = !t.done && t.dueAt != null && t.dueAt! < _todayStart;
    final dueToday = !t.done &&
        t.dueAt != null &&
        t.dueAt! >= _todayStart &&
        t.dueAt! < _todayStart + 86400;
    Color? dueColor;
    if (overdue) dueColor = cs.error;
    if (dueToday) dueColor = cs.tertiary;
    final priColor = switch (t.priority) {
      2 => cs.error,
      1 => cs.tertiary,
      _ => Colors.transparent,
    };
    final assignee = _memberName(t.assigneeId);
    return Card(
      margin: const EdgeInsets.only(bottom: Gap.xs),
      clipBehavior: Clip.antiAlias,
      child: IntrinsicHeight(
          child: Row(children: [
        Container(width: 3, color: priColor), // 优先级左色条
        Expanded(
            child: ListTile(
          onTap: () => _toggle(t),
          onLongPress: () => _todoActions(t),
          leading: Icon(
              t.done
                  ? Icons.check_circle_outlined
                  : Icons.radio_button_unchecked_outlined,
              color: t.done ? cs.primary : cs.onSurfaceVariant),
          title: Text(t.title,
              style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                  decoration: t.done ? TextDecoration.lineThrough : null,
                  color: t.done ? cs.onSurfaceVariant : cs.onSurface)),
          subtitle: Row(children: [
            if (t.listKind == 'family') ...[
              Icon(Icons.home_outlined, size: IconSizes.xs, color: cs.primary),
              const SizedBox(width: Gap.xxs),
              Text('全家',
                  style: Theme.of(context)
                      .textTheme
                      .labelSmall
                      ?.copyWith(color: cs.primary)),
              const SizedBox(width: Gap.sm),
            ],
            if (t.dueAt != null) ...[
              Icon(Icons.schedule_outlined,
                  size: IconSizes.xs, color: dueColor ?? cs.onSurfaceVariant),
              const SizedBox(width: Gap.xxs),
              Text(_dueText(t.dueAt!),
                  style: Theme.of(context).textTheme.labelSmall
                      ?.copyWith(color: dueColor ?? cs.onSurfaceVariant)),
              const SizedBox(width: Gap.sm),
            ],
            if (assignee.isNotEmpty && !t.done) ...[
              CircleAvatar(
                  radius: 9,
                  backgroundColor: cs.secondaryContainer,
                  child: Text(assignee.substring(0, 1),
                      style: Theme.of(context).textTheme.labelSmall
                          ?.copyWith(color: cs.onSecondaryContainer))),
              const SizedBox(width: Gap.xxs),
              Flexible(
                  child: Text(assignee,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.labelSmall
                          ?.copyWith(color: cs.onSurfaceVariant))),
            ],
          ]),
        )),
      ])),
    );
  }

  String _dueText(num dueAt) {
    final d = DateTime.fromMillisecondsSinceEpoch((dueAt * 1000).toInt());
    final now = DateTime.now();
    final day = DateTime(d.year, d.month, d.day);
    final today = DateTime(now.year, now.month, now.day);
    final diff = day.difference(today).inDays;
    if (diff == 0) return '今天';
    if (diff == 1) return '明天';
    if (diff == -1) return '昨天';
    return '${d.month}/${d.day}';
  }

  Widget _emptyState(ColorScheme cs) {
    return Padding(
        padding: const EdgeInsets.symmetric(vertical: Gap.xxxl),
        child: Column(children: [
          Icon(Icons.task_alt_outlined,
              size: IconSizes.display, color: cs.primaryContainer),
          const SizedBox(height: Gap.md),
          Text('今天没什么要做的',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: Gap.xxs),
          Text('加一件吧，或点右下角「新建待办」',
              style: Theme.of(context).textTheme.bodySmall),
        ]));
  }
}

// ── 新建待办表单 ──────────────────────────────────────────

class _TodoEditSheet extends StatefulWidget {
  final List<_TodoList> lists;
  final Map<int, Map<String, String>> members;

  const _TodoEditSheet({required this.lists, required this.members});

  @override
  State<_TodoEditSheet> createState() => _TodoEditSheetState();
}

class _TodoEditSheetState extends State<_TodoEditSheet> {
  final _title = TextEditingController();
  late _TodoList _list = widget.lists.first;
  num? _dueAt;
  int _priority = 0;
  int? _assignee;
  bool _saving = false;

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  Future<void> _pickDue() async {
    final now = DateTime.now();
    final d = await showDatePicker(
        context: context,
        initialDate: now,
        firstDate: now.subtract(const Duration(days: 365)),
        lastDate: now.add(const Duration(days: 365 * 3)));
    if (d == null) return;
    if (!mounted) return;
    setState(() {
      _dueAt =
          DateTime(d.year, d.month, d.day, 23, 59).millisecondsSinceEpoch / 1000;
    });
  }

  Future<void> _save() async {
    final title = _title.text.trim();
    if (title.isEmpty || _saving) return;
    setState(() => _saving = true);
    try {
      await TodosApi.createTodo(_list.id, title,
          dueAt: _dueAt, priority: _priority, assigneeId: _assignee);
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        setState(() => _saving = false);
        showToast(errDetail(e));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
        padding: EdgeInsets.only(
            left: Gap.md, right: Gap.md, top: Gap.md,
            bottom: MediaQuery.viewInsetsOf(context).bottom + Gap.md),
        child: Column(mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          TextField(
              controller: _title,
              autofocus: true,
              maxLines: 1,
              textInputAction: TextInputAction.done,
              onSubmitted: (_) => _save(),
              decoration: const InputDecoration(
                  hintText: '要做什么？', border: OutlineInputBorder())),
          const SizedBox(height: Gap.sm),
          SegmentedButton<String>(
              segments: [
                for (final l in widget.lists)
                  ButtonSegment(
                      value: '${l.id}',
                      label: Text(
                          l.family ? '全家·${l.name}' : l.name,
                          overflow: TextOverflow.ellipsis)),
              ],
              selected: {'${_list.id}'},
              onSelectionChanged: (s) => setState(() {
                    _list = widget.lists
                        .firstWhere((l) => '${l.id}' == s.first);
                    if (!_list.family) _assignee = null;
                  })),
          const SizedBox(height: Gap.sm),
          Row(children: [
            ActionChip(
                avatar: const Icon(Icons.event_outlined, size: 18),
                label: Text(_dueAt == null ? '截止日' : _dueText(_dueAt!)),
                onPressed: _pickDue),
            const SizedBox(width: Gap.sm),
            for (final e in <(int, String)>[(0, '普通'), (1, '中'), (2, '高')])
              Padding(
                  padding: const EdgeInsets.only(right: Gap.xs),
                  child: ChoiceChip(
                      label: Text(e.$2),
                      selected: _priority == e.$1,
                      onSelected: (_) => setState(() => _priority = e.$1))),
          ]),
          if (_list.family &&
              widget.members.values.any((m) => m.isNotEmpty)) ...[
            const SizedBox(height: Gap.sm),
            Wrap(spacing: Gap.xs, children: [
              for (final entry in widget.members.entries)
                ChoiceChip(
                    avatar: CircleAvatar(
                        radius: 9,
                        backgroundColor: cs.secondaryContainer,
                        child: Text(
                            _name(entry.key).isNotEmpty
                                ? _name(entry.key).substring(0, 1)
                                : '?',
                            style: TextStyle(
                                fontSize: 10, color: cs.onSecondaryContainer))),
                    label: Text(_name(entry.key)),
                    selected: _assignee == entry.key,
                    onSelected: (_) =>
                        setState(() => _assignee = entry.key)),
            ]),
          ],
          const SizedBox(height: Gap.md),
          FilledButton.icon(
              onPressed: _saving ? null : _save,
              icon: const Icon(Icons.add_task_outlined),
              label: const Text('添加')),
        ]));
  }

  String _name(int uid) {
    final m = widget.members[uid];
    if (m == null) return '已停用成员';
    final n = m['nickname'] ?? '';
    return n.isNotEmpty ? n : (m['username'] ?? '');
  }

  String _dueText(num dueAt) {
    final d = DateTime.fromMillisecondsSinceEpoch((dueAt * 1000).toInt());
    return '${d.month}/${d.day}';
  }
}

// ── 清单管理 ──────────────────────────────────────────────

class _ListsSheet extends StatefulWidget {
  final List<_TodoList> lists;
  final Map<int, Map<String, String>> members;
  final Future<void> Function() onChanged;

  const _ListsSheet(
      {required this.lists, required this.members, required this.onChanged});

  @override
  State<_ListsSheet> createState() => _ListsSheetState();
}

class _ListsSheetState extends State<_ListsSheet> {
  Future<void> _create() async {
    final nameCtl = TextEditingController();
    var kind = 'personal';
    final ok = await showModalBottomSheet<bool>(
        context: context,
        isScrollControlled: true,
        builder: (_) => Padding(
            padding: EdgeInsets.only(
                left: Gap.md, right: Gap.md, top: Gap.md,
                bottom: MediaQuery.viewInsetsOf(context).bottom + Gap.md),
            child: StatefulBuilder(builder: (bctx, setS) => Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextField(
                      controller: nameCtl,
                      autofocus: true,
                      decoration: const InputDecoration(
                          labelText: '清单名称', border: OutlineInputBorder())),
                  const SizedBox(height: Gap.sm),
                  SegmentedButton<String>(
                      segments: const [
                        ButtonSegment(
                            value: 'personal',
                            icon: Icon(Icons.lock_outline),
                            label: Text('仅自己')),
                        ButtonSegment(
                            value: 'family',
                            icon: Icon(Icons.home_outlined),
                            label: Text('全家共享')),
                      ],
                      selected: {kind},
                      onSelectionChanged: (s) => setS(() => kind = s.first)),
                  const SizedBox(height: Gap.md),
                  FilledButton(
                      onPressed: () =>
                          Navigator.pop(bctx, nameCtl.text.trim().isNotEmpty),
                      child: const Text('创建')),
                ]))));
    if (ok != true || !mounted) return;
    try {
      await TodosApi.createList(nameCtl.text.trim(), kind);
    } catch (e) {
      if (mounted) showToast(errDetail(e));
      return;
    }
    await widget.onChanged();
    if (mounted) Navigator.pop(context); // 关闭清单管理，回到主页刷新
  }

  Future<void> _confirmDelete(_TodoList l) async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (_) => AlertDialog(
            title: const Text('删除清单'),
            content: Text('删除「${l.name}」及其全部待办？此操作不可撤销。'),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(context, false),
                  child: const Text('取消')),
              FilledButton(
                  onPressed: () => Navigator.pop(context, true),
                  child: const Text('删除')),
            ]));
    if (ok != true || !mounted) return;
    try {
      await TodosApi.deleteList(l.id);
    } catch (e) {
      if (mounted) showToast(errDetail(e));
      return;
    }
    await widget.onChanged();
    if (mounted) Navigator.pop(context);
  }

  Future<void> _rename(_TodoList l) async {
    final ctl = TextEditingController(text: l.name);
    final ok = await showDialog<bool>(
        context: context,
        builder: (_) => AlertDialog(
            title: const Text('重命名清单'),
            content: TextField(controller: ctl, autofocus: true),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(context, false),
                  child: const Text('取消')),
              FilledButton(
                  onPressed: () => Navigator.pop(context, true),
                  child: const Text('确定')),
            ]));
    if (ok != true || !mounted) return;
    final name = ctl.text.trim();
    if (name.isEmpty || name == l.name) return;
    try {
      await TodosApi.renameList(l.id, name);
    } catch (e) {
      if (mounted) showToast(errDetail(e));
      return;
    }
    await widget.onChanged();
    if (mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
      Padding(padding: const EdgeInsets.all(Gap.md),
          child: Row(children: [
            Text('清单管理', style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            FilledButton.tonalIcon(
                onPressed: _create,
                icon: const Icon(Icons.add_outlined),
                label: const Text('新建')),
          ])),
      Flexible(
          child: ListView(shrinkWrap: true, children: [
        for (final l in widget.lists)
          ListTile(
              leading: Icon(
                  l.family ? Icons.home_outlined : Icons.lock_outline,
                  color: l.family ? cs.primary : cs.onSurfaceVariant),
              title: Text(l.name),
              subtitle: Text(
                  l.family ? '全家共享 · 未完成 ${l.openCount}' : '仅自己 · 未完成 ${l.openCount}',
                  style: Theme.of(context).textTheme.labelSmall),
              trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                IconButton(icon: const Icon(Icons.edit_outlined),
                    tooltip: '重命名', onPressed: () => _rename(l)),
                IconButton(icon: const Icon(Icons.delete_outline),
                    tooltip: '删除', onPressed: () => _confirmDelete(l)),
              ])),
      ])),
      const SizedBox(height: Gap.sm),
    ]));
  }
}
