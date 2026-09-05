// 服务器地址选择器（登录页与「我的页 → 切换服务器」共用，2026-09-03）。
// 分区：公网入口（服务端 /api/app/entries 下发的主/备隧道，置顶）→
// 历史地址（最近使用在前）→ 扫码添加 → 手动输入。选中即回调 onPick，
// 连通性预检与信任决策留在调用方（两处语义不同）。
import 'package:flutter/material.dart';

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

Future<void> showServerPicker(
  BuildContext context, {
  required String current,
  required void Function(String url) onPick,
  bool allowScan = true,
}) async {
  final controller = TextEditingController(text: current);
  final addrs = await Prefs.addresses();
  // 公网入口清单：服务器不可达/旧版服务端 → 静默隐藏该区（历史地址照用）
  final entries = await EntriesService.fetch(current);
  if (!context.mounted) {
    controller.dispose();
    return;
  }
  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => Padding(
      padding: EdgeInsets.fromLTRB(
          20, 20, 20, 20 + MediaQuery.of(ctx).viewInsets.bottom),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment:
          CrossAxisAlignment.start, children: [
        Text('连接服务器', style: Theme.of(ctx).textTheme.titleMedium),
        const SizedBox(height: 4),
        Text('同一网络可直连家里；出门在外用公网入口（主/备隧道任选）。',
            style: Theme.of(ctx).textTheme.bodySmall),
        const SizedBox(height: 12),
        // 公网入口区：服务端下发，主在前；当前正在用的地址不再重复列出
        for (final a in entries?.publicAddresses ?? const <String>[]) ...[
          if (a == current)
            ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                leading: const Icon(Icons.cloud_outlined, size: 22),
                title: Text(shortHost(a),
                    style: const TextStyle(fontSize: 14)),
                subtitle: Text(
                    a == entries?.pub ? '主隧道 · 当前' : '备用隧道 · 当前',
                    style: const TextStyle(fontSize: 11.5)),
                trailing: Icon(Icons.check_circle,
                    size: 18, color: Theme.of(ctx).colorScheme.primary)),
          if (a != current)
            ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                leading: const Icon(Icons.cloud_outlined, size: 22),
                title: Text(shortHost(a),
                    style: const TextStyle(fontSize: 14)),
                subtitle: Text(a == entries?.pub ? '主隧道 · 公网' : '备用隧道 · 公网',
                    style: const TextStyle(fontSize: 11.5)),
                onTap: () {
                  Navigator.pop(ctx);
                  onPick(a);
                }),
        ],
        // 历史地址（含局域网）
        for (final a in addrs) ...[
          if (a != current && !(entries?.publicAddresses.contains(a) ?? false))
            ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                leading: const Icon(Icons.dns_outlined, size: 22),
                title: Text(shortHost(a), style: const TextStyle(fontSize: 14)),
                subtitle: Text(a, style: const TextStyle(fontSize: 11.5)),
                onTap: () {
                  Navigator.pop(ctx);
                  onPick(a);
                }),
        ],
        if (allowScan) ...[
          const Divider(),
          ListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              leading: const Icon(Icons.qr_code_scanner, size: 22),
              title: const Text('扫码添加', style: TextStyle(fontSize: 14)),
              subtitle: const Text('扫电脑端「账号设置 → 手机 App 连接」的二维码',
                  style: TextStyle(fontSize: 11.5)),
              onTap: () async {
                final url = await Navigator.of(ctx).push<String>(
                    MaterialPageRoute(builder: (_) => const ScanPage()));
                if (!ctx.mounted) return;
                Navigator.pop(ctx);
                if (url != null) onPick(url);
              }),
        ],
        const SizedBox(height: 8),
        TextField(
          controller: controller,
          decoration: const InputDecoration(
              labelText: '手动输入地址',
              hintText: 'https://192.168.1.100:8765',
              isDense: true),
          keyboardType: TextInputType.url,
          autocorrect: false,
        ),
        const SizedBox(height: 12),
        SizedBox(
          width: double.infinity,
          child: FilledButton.tonal(
            onPressed: () {
              final v = controller.text.trim();
              Navigator.pop(ctx);
              if (v.isNotEmpty) onPick(v);
            },
            child: const Text('使用此地址'),
          ),
        ),
      ]),
    ),
  );
  // 控制器不 dispose 交给 GC：await 返回时面板退场动画未结束、TextField
  // 仍在树上且 viewInsets 变化会触发重建读到已 dispose 控制器（R1-P1，
  // 同 me_page 对话框处理）
}
