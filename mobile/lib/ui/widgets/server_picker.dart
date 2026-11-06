// 服务器地址选择器（登录页与「我的页 → 切换服务器」共用，2026-09-03）。
// 分区：公网入口（服务端 /api/app/entries 下发的主/备隧道，置顶）→
// 历史地址（最近使用在前）→ 扫码添加 → 手动输入。选中即回调 onPick，
// 连通性预检与信任决策留在调用方（两处语义不同）。
//
// ## 2026-11-01：面板必须先弹，网络数据后到
//
// 原来这里是「await Prefs.addresses() → await EntriesService.fetch()
// → showModalBottomSheet()」——**拿到网络数据才弹面板**。而 fetch 的
// connect/receive 超时各 4 秒，服务端不可达时（开着 VPN、换了网络、
// 服务没起）最坏要空等 8 秒，用户点地址栏就是「点了没反应」。
//
// 现在：面板立即弹出，本地历史地址第一帧就可见；公网入口区先显示一行
// 「正在获取…」，数据回来再 setState 填进去。慢网络只影响那一小块。
import 'package:flutter/material.dart';

import '../../data/api/trust.dart';
import '../../data/entries_service.dart';
import '../../data/local/prefs.dart';
import '../scan_page.dart';

/// URL → 短显示名（host[:非默认端口]），解析失败截断原文
String shortHost(String raw) {
  try {
    final u = Uri.parse(raw);
    if (u.host.isEmpty) return raw;
    final p = u.port;
    final hasPort = p > 0 &&
        !((u.scheme == 'https' && p == 443) || (u.scheme == 'http' && p == 80));
    return hasPort ? '${u.host}:$p' : u.host;
  } catch (_) {
    return raw.length > 24 ? '${raw.substring(0, 24)}…' : raw;
  }
}

/// 把用户输入的服务器地址规范化成可用 URL。
///
/// 2026-11-02 主人指令：**别让用户手打 `https://`**。手机上敲这几个字符又慢
/// 又容易错，而本部署（Caddy）本来就只有 https 入口 —— 输入
/// `192.168.1.100:8765` 就该直接能连。
///
/// 规则：
///   没写协议   → 补 `https://`
///   写了 http  → 原样保留（主人明确要求取掉这个限制），由调用方提示风险
///   结尾斜杠   → 去掉，避免拼出 `//api/...`
String normalizeServerUrl(String raw) {
  var s = raw.trim();
  if (s.isEmpty) return s;
  // 用 '://' 判断协议，避免把 `192.168.1.100:8765` 里的冒号误当成协议分隔符
  if (!s.contains('://')) s = 'https://$s';
  while (s.endsWith('/') && s.length > 'https://'.length) {
    s = s.substring(0, s.length - 1);
  }
  return s;
}

Future<void> showServerPicker(
  BuildContext context, {
  required String current,
  required void Function(String url) onPick,
  bool allowScan = true,
}) async {
  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => _ServerPickerSheet(
        current: current, onPick: onPick, allowScan: allowScan),
  );
}

/// 选择器本体。做成 StatefulWidget 是为了让公网入口异步填充 ——
/// 本地数据（历史地址）第一帧就有，网络数据后到。
class _ServerPickerSheet extends StatefulWidget {
  const _ServerPickerSheet({
    required this.current,
    required this.onPick,
    required this.allowScan,
  });

  final String current;
  final void Function(String url) onPick;
  final bool allowScan;

  @override
  State<_ServerPickerSheet> createState() => _ServerPickerSheetState();
}

class _ServerPickerSheetState extends State<_ServerPickerSheet> {
  final TextEditingController _controller = TextEditingController();

  /// 本地历史地址。第一帧就填好，不依赖网络。
  List<String> _addrs = const [];

  /// 公网入口清单：null 且 loading=false 表示拉不到（旧版服务端/不可达）
  AppEntries? _entries;
  bool _entriesLoading = true;

  /// 指纹是否已在本机信任库里（决定公网入口要不要标「证书未验证」）
  bool _fpKnown = false;

  @override
  void initState() {
    super.initState();
    _controller.text = widget.current;
    _loadLocal();
    _loadEntries();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _loadLocal() async {
    final addrs = await Prefs.addresses();
    if (!mounted) return;
    setState(() => _addrs = addrs);
  }

  Future<void> _loadEntries() async {
    // 公网入口清单：服务器不可达/旧版服务端 → 静默隐藏该区（历史地址照用）
    final entries = await EntriesService.fetch(widget.current);
    if (!mounted) return;
    setState(() {
      _entries = entries;
      _entriesLoading = false;
      // 指纹未入过本机信任库 → 入口标注「证书未验证」（红队 P1-1）：清单可能
      // 被链路内攻击者篡改，自报指纹只是提示——真正的裁决在选中后的
      // ensureAddressTrusted 探测（对真实证书做同源比对/TOFU）
      _fpKnown = TrustStore.I.knowsFingerprint(entries?.certSha256 ?? '');
    });
  }

  /// 删除一条历史地址。删之前问一句 —— 地址是手打过或扫来的，
  /// 误删了要重新找很烦；但它只是个本地候选，不涉及服务器。
  Future<void> _confirmForget(String url) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('移除这个地址？'),
        content: Text('$url\n\n只从这台手机的候选列表里删掉，不影响服务器上的任何数据。'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('取消')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('移除')),
        ],
      ),
    );
    if (ok != true) return;
    await Prefs.forgetAddress(url);
    if (!mounted) return;
    setState(() => _addrs = _addrs.where((e) => e != url).toList());
  }

  void _pick(String url) {
    Navigator.pop(context);
    widget.onPick(url);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final current = widget.current;
    final entries = _entries;

    return Padding(
      padding: EdgeInsets.fromLTRB(
          20, 20, 20, 20 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('连接服务器', style: theme.textTheme.titleMedium),
            const SizedBox(height: 4),
            Text('同一网络可直连家里；出门在外用公网入口。',
                style: theme.textTheme.bodySmall),
            const SizedBox(height: 12),

            // ── 公网入口区：这一块要等网络 ──
            // 加载中只占一行小字，不用转圈铺满 —— 列表本来就有内容，
            // 大转圈会让人以为整个面板都在等，其实本地地址已经能选了。
            if (_entriesLoading)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Row(children: [
                  SizedBox(
                      width: 12,
                      height: 12,
                      child: CircularProgressIndicator(
                          strokeWidth: 1.6, color: theme.colorScheme.outline)),
                  const SizedBox(width: 8),
                  Text('正在获取公网入口…',
                      style: TextStyle(
                          fontSize: 12, color: theme.colorScheme.outline)),
                ]),
              ),
            for (final a in entries?.publicAddresses ?? const <String>[]) ...[
              if (a == current)
                ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: const Icon(Icons.cloud_outlined, size: 22),
                    title: Text(shortHost(a),
                        style: const TextStyle(fontSize: 14)),
                    subtitle: const Text('公网入口 · 当前',
                        style: TextStyle(fontSize: 11.5)),
                    trailing: Icon(Icons.check_circle,
                        size: 18, color: theme.colorScheme.primary)),
              if (a != current)
                ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: const Icon(Icons.cloud_outlined, size: 22),
                    title: Text(shortHost(a),
                        style: const TextStyle(fontSize: 14)),
                    subtitle: Text(
                        '公网入口${_fpKnown ? '' : ' · 证书未验证'}',
                        style: TextStyle(
                            fontSize: 11.5,
                            color: _fpKnown ? null : theme.colorScheme.error)),
                    onTap: () => _pick(a)),
            ],

            // ── 历史地址（含局域网），第一帧就有 ──
            for (final a in _addrs) ...[
              if (a != current &&
                  !(entries?.publicAddresses.contains(a) ?? false))
                ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    leading: const Icon(Icons.dns_outlined, size: 22),
                    title: Text(shortHost(a),
                        style: const TextStyle(fontSize: 14)),
                    subtitle: Text(a, style: const TextStyle(fontSize: 11.5)),
                    onTap: () => _pick(a),
                    // 历史列表只增不减，换过 IP 之后僵尸地址会一直排在这儿。
                    // 长按和右侧 × 都能删 —— 长按是熟手的快捷方式，
                    // × 是给不知道有这个功能的人看的。
                    onLongPress: () => _confirmForget(a),
                    trailing: IconButton(
                        icon: const Icon(Icons.close, size: 18),
                        tooltip: '从候选列表移除',
                        visualDensity: VisualDensity.compact,
                        onPressed: () => _confirmForget(a))),
            ],

            if (widget.allowScan) ...[
              const Divider(),
              ListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  leading: const Icon(Icons.qr_code_scanner, size: 22),
                  title: const Text('扫码添加', style: TextStyle(fontSize: 14)),
                  subtitle: const Text('扫电脑端「账号设置 → 手机 App 连接」的二维码',
                      style: TextStyle(fontSize: 11.5)),
                  onTap: () async {
                    // 先拿到 Navigator 再 await：await 之后 context 可能失效，
                    // lint 会判 use_build_context_synchronously（跨 async gap 用 context）
                    final nav = Navigator.of(context);
                    final url = await nav.push<String>(
                        MaterialPageRoute(builder: (_) => const ScanPage()));
                    if (!mounted) return;
                    nav.pop();                       // 用上面抓的 nav，别再用 context
                    if (url != null) widget.onPick(url);
                  }),
            ],

            const SizedBox(height: 8),
            TextField(
              controller: _controller,
              decoration: const InputDecoration(
                  labelText: '手动输入地址',
                  // 用占位符写法而不是具体 IP：这是给公开分发的 App，
                  // 任何写死的地址都会被当成「作者的服务器」。
                  hintText: 'https://你的服务器IP:8765',
                  helperText: '局域网和公网都用 8765 端口，填哪个都行',
                  isDense: true),
              keyboardType: TextInputType.url,
              autocorrect: false,
            ),
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: FilledButton.tonal(
                onPressed: () {
                  final v = _controller.text.trim();
                  Navigator.pop(context);
                  if (v.isNotEmpty) widget.onPick(v);
                },
                child: const Text('使用此地址'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
