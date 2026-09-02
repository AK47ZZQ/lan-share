// 家里动态（0.6.2）：GET /api/activity 的手机端消费模型 + 纯文案逻辑。
// 与 Web 端 web/src/activity.ts 同一套语义（动作图标/文案/聚合/相对时间），
// 两端各自实现但字段契约一致：服务端只回 ts/username/action/target/detail/count。
// 全部函数纯无副作用，单测直接覆盖（batch062）。

class ActivityItem {
  final int ts; // 秒级时间戳
  final String username;
  final String action;
  final String target;
  final String detail;
  final int count; // 服务端 10 分钟窗口聚合条数

  const ActivityItem({
    required this.ts,
    required this.username,
    required this.action,
    required this.target,
    required this.detail,
    this.count = 1,
  });

  factory ActivityItem.fromMap(Map<String, dynamic> m) => ActivityItem(
        ts: (m['ts'] as num?)?.toInt() ?? 0,
        username: (m['username'] as String?) ?? '',
        action: (m['action'] as String?) ?? '',
        target: (m['target'] as String?) ?? '',
        detail: (m['detail'] as String?) ?? '',
        count: (m['count'] as num?)?.toInt() ?? 1,
      );

  static List<ActivityItem> listFrom(Object? data) => [
        for (final e in (data as List? ?? const []))
          if (e is Map) ActivityItem.fromMap(e.cast<String, dynamic>()),
      ];
}

/// 动作 → 中文动词（与 Web 端 ACTION_TEXT 一致）
String actionVerb(String action) => switch (action) {
      'upload' => '上传了',
      'upload_instant' => '秒传了',
      'mkdir' => '新建了目录',
      'share' => '创建了分享',
      'share_target' => '定向分享了',
      'share_download' => '的分享被下载',
      'invite_redeemed' => '加入了家庭空间',
      'trash' => '删除了',
      'restore' => '恢复了',
      'move' => '移动了',
      'rename' => '重命名了',
      _ => action,
    };

/// 操作者标注：审计早期行无用户名——链接下载显示"链接访客"，其余"早期记录"
String activityWho(ActivityItem n) {
  if (n.username.isNotEmpty) return n.username;
  return n.action == 'share_download' ? '链接访客' : '早期记录';
}

String _baseName(String target) =>
    target.split('/').where((s) => s.isNotEmpty).lastOrNull ?? target;

/// 一条动态的一句话文案（与 Web activityTitle 同语义）
String activityText(ActivityItem n) {
  final verb = actionVerb(n.action);
  if (n.count > 1) return '${activityWho(n)} $verb ${n.count} 项';
  if (n.action == 'share_target' && n.detail.isNotEmpty) {
    return '${activityWho(n)} 定向分享了 ${_baseName(n.target)} ${n.detail}';
  }
  return '${activityWho(n)} $verb ${_baseName(n.target)}';
}

/// 相对时间（nowMs 可注入，单测用）
String activityRelTime(int tsSeconds, {int? nowMs}) {
  final d = (nowMs ?? DateTime.now().millisecondsSinceEpoch) / 1000 - tsSeconds;
  if (d < 60) return '刚刚';
  if (d < 3600) return '${d ~/ 60} 分钟前';
  if (d < 86400) return '${d ~/ 3600} 小时前';
  if (d < 7 * 86400) return '${d ~/ 86400} 天前';
  final dt = DateTime.fromMillisecondsSinceEpoch(tsSeconds * 1000);
  final mm = dt.month.toString().padLeft(1);
  final hh = dt.hour.toString().padLeft(2, '0');
  final mi = dt.minute.toString().padLeft(2, '0');
  return '$mm-${dt.day} $hh:$mi';
}
