// 备份轮次/队列状态 → 用户可读文案（0.7.3 可观测性）。
// 数据源两个（均为 JSON 字符串，读不到/解析失败一律返回 null，UI 静默跳过）：
//   backup_last_round   = 引擎每轮结束写 {status, reason, uploaded, failed, pending, ts}
//   backup_worker_state = Kotlin Worker/入队写 {status, reason, ts}
// 0.7.3 前整条链路十几处静默 return，"点了没反应"完全无法排障。
import 'dart:convert';

class BackupRoundView {
  final String text;
  final bool warn; // true = 需要用户注意（失败/暂停/等待约束）

  const BackupRoundView(this.text, {this.warn = false});
}

Map<String, Object?>? _parse(String? raw) {
  if (raw == null || raw.isEmpty) return null;
  try {
    final v = jsonDecode(raw);
    return v is Map<String, Object?> ? v : null;
  } catch (_) {
    return null;
  }
}

String _ago(int ts, int now) {
  final d = now - ts;
  if (d < 60_000) return '刚刚'; // 含时钟回拨（负差），别显示负数
  if (d < 3_600_000) return '${d ~/ 60_000} 分钟前';
  if (d < 86_400_000) return '${d ~/ 3_600_000} 小时前';
  return '${d ~/ 86_400_000} 天前';
}

const _pauseReasonText = {
  'wifi_lost': '等待 Wi-Fi',
  'unplugged': '等待充电',
  'system_stop': '系统叫停，稍后自动继续',
  'disabled': '备份已关闭',
};

const _skipReasonText = {
  'disabled': '备份已关闭',
  'not_logged_in': '未登录',
  'public_server': '当前为公网地址，默认不备份',
  'cooldown': '失败冷却中，稍后自动重试',
  'no_username': '未获取到用户名',
  'bad_username': '用户名异常',
  'scan_or_ledger_unavailable': '媒体库/账本暂不可用',
  'busy': '上一轮仍在进行',
};

/// 引擎最近一轮 → "5 分钟前 · 已备份 12 张，2 张失败" 样式；无数据返回 null。
BackupRoundView? roundView(String? roundJson, {int? nowMs}) {
  final m = _parse(roundJson);
  if (m == null) return null;
  final ts = (m['ts'] as num?)?.toInt();
  final when = ts == null ? '' : '${_ago(ts, nowMs ?? ts)} · ';
  final status = m['status'] as String? ?? '';
  final uploaded = (m['uploaded'] as num?)?.toInt() ?? 0;
  final failed = (m['failed'] as num?)?.toInt() ?? 0;
  final reason = m['reason'] as String?;

  String text;
  var warn = false;
  switch (status) {
    case 'done':
      text = uploaded == 0 && failed == 0
          ? '没有需要备份的内容'
          : '已备份 $uploaded 张${failed > 0 ? '，$failed 张失败' : ''}';
    case 'paused':
      text = '已暂停：${_pauseReasonText[reason] ?? reason ?? "未知原因"}';
      warn = true;
    case 'skip':
      text = '未运行：${_skipReasonText[reason] ?? reason ?? "未知原因"}';
      warn = reason != 'disabled';
    case 'cooldown':
      text = '连续失败，冷却中（30 分钟后自动重试）';
      warn = true;
    case 'trust_required':
      text = '服务器证书待确认，请打开 App 处理';
      warn = true;
    case 'error':
      text = '失败：${reason ?? "未知错误"}';
      warn = true;
    default:
      text = status.isEmpty ? '未知状态' : '状态：$status';
      warn = true;
  }
  return BackupRoundView('$when$text', warn: warn);
}

/// Worker/队列状态 → 提示行。只在"有等待语义"时返回（queued_wait/busy），
/// 其余状态由 roundView 覆盖，返回 null 让 UI 不重复显示。
BackupRoundView? queueView(String? workerJson, {int? nowMs}) {
  final m = _parse(workerJson);
  if (m == null) return null;
  final status = m['status'] as String? ?? '';
  if (status == 'queued_wait') {
    final waiting = (m['reason'] as String? ?? '')
        .split(',')
        .where((e) => e.isNotEmpty)
        .map((e) => e == 'charging' ? '充电' : (e == 'wifi' ? 'Wi-Fi' : e))
        .toList();
    return BackupRoundView('已排队：等待${waiting.join('、')}（满足条件自动运行）',
        warn: true);
  }
  if (status == 'busy') {
    return const BackupRoundView('备份已在进行中', warn: false);
  }
  return null;
}

/// 设置页诊断卡：Worker 任意状态 → 完整描述行（比 queueView 覆盖全）。
BackupRoundView? workerView(String? workerJson, {int? nowMs}) {
  final m = _parse(workerJson);
  if (m == null) return null;
  final ts = (m['ts'] as num?)?.toInt();
  final when = ts == null ? '' : '${_ago(ts, nowMs ?? ts)} · ';
  final status = m['status'] as String? ?? '';
  final reason = m['reason'] as String?;
  const runningTriggers = {
    'periodic': '周期任务',
    'media_change': '相册变更',
    'manual': '手动立即备份',
  };
  String text;
  var warn = false;
  switch (status) {
    case 'queued':
      text = '已排队（条件满足，即将运行）';
    case 'queued_manual':
      text = '已排队（手动立即备份）';
    case 'queued_wait':
      return queueView(workerJson, nowMs: nowMs) ??
          const BackupRoundView('已排队（等待条件）', warn: true);
    case 'running':
      text = '运行中（${runningTriggers[reason] ?? reason ?? "触发"}）';
    case 'busy':
      text = '跳过：上一轮仍在进行';
    case 'skip_disabled':
      text = '跳过：备份开关未开启';
      warn = true;
    case 'skip_idle':
      text = '跳过：没有待备份内容';
    case 'done':
      text = '本轮完成';
    case 'paused':
      text = '已暂停：${_pauseReasonText[reason] ?? reason ?? "未知原因"}';
      warn = true;
    case 'skip':
      text = '未运行：${_skipReasonText[reason] ?? reason ?? "未知原因"}';
      warn = reason != 'disabled';
    case 'cooldown':
      text = '连续失败，冷却中';
      warn = true;
    case 'trust_required':
      text = '服务器证书待确认';
      warn = true;
    case 'error':
      text = '失败：${reason ?? "未知错误"}';
      warn = true;
    default:
      text = status.isEmpty ? '未知状态' : status;
      warn = true;
  }
  return BackupRoundView('$when$text', warn: warn);
}
