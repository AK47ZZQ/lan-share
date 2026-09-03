// 内网/公网地址判定（纯函数，多处共用）：备份省流跳过、公网徽标、
// 公网上传/更新下载提醒都以它为准。域名（非 .local/.lan）一律按公网——
// frp 隧道地址、公网域名走的就是外部流量，宁可信其有。

/// host 是否公网（非 RFC1918/环回/链路本地/ULA/.local/.lan）
bool isPublicHost(String host) {
  if (host.isEmpty) return true;
  var h = host;
  if (h.startsWith('[') && h.endsWith(']')) {
    h = h.substring(1, h.length - 1); // Uri.host 保留 IPv6 方括号，剥掉再判
  }
  if (h.contains(':')) {
    // IPv6：环回/链路本地/ULA(fc00::/7) 视为内网，其余（含 GUA）按公网
    final l = h.toLowerCase();
    return !(l == '::1' || l.startsWith('fe80') || l.startsWith('fc') || l.startsWith('fd'));
  }
  if (RegExp(r'^\d{1,3}(\.\d{1,3}){3}$').hasMatch(h)) {
    final p = h.split('.').map(int.parse).toList();
    if (p[0] == 10 || p[0] == 127 || (p[0] == 192 && p[1] == 168)) return false;
    if (p[0] == 172 && p[1] >= 16 && p[1] <= 31) return false;
    return true;
  }
  return !(h.endsWith('.local') || h.endsWith('.lan'));
}

/// 完整服务器地址（https://host:port）是否公网；解析失败按公网处理
bool isPublicAddress(String url) {
  try {
    final u = Uri.parse(url.trim());
    if (u.host.isEmpty) return true;
    return isPublicHost(u.host);
  } catch (_) {
    return true;
  }
}
