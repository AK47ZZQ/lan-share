// 更新失败的统一出口（2026-11-20）。
//
// 背景：更新链路出过一次「点更新 → 一屏 Java 栈 → 用户无路可走」的事故。
// 从那次学到的四条全部固化在这里：
//
//   1. 只给人话。`$e` 原样输出会带上整段 Java 栈和 `DexPathList[...]`，
//      对用户零信息量，还把 SnackBar 撑到占满屏幕。
//   2. 永远给一条手动下载兜底。自动更新再坏，用户也不该被永久卡在老版本。
//      清单里带直链（R2 / 自定义源，国内可达性实测过）就优先用它；没有才
//      退到 GitHub 发布页。直链只认 https —— 它来自可被替换的清单，不能
//      把用户指到一个明文或本地地址上去。
//   3. 能一键去下载。让用户"复制地址再自己粘到浏览器"是多一道无谓的门槛
//      （手机 App 里没有地址栏），所以主按钮直接拉起外部浏览器。
//   4. 内容可滚动。长原因 + 长直链在横屏 / 小屏上会把弹窗顶出屏幕。
import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show Clipboard, ClipboardData;
import 'package:url_launcher/url_launcher.dart';

import '../../data/update_service.dart';

Future<void> showUpdateErrorDialog(
  BuildContext context,
  String reason, {
  String? link,
}) {
  final candidate = (link != null && link.isNotEmpty) ? link : null;
  final url = candidate != null && Uri.tryParse(candidate)?.scheme == 'https'
      ? candidate
      : UpdateService.manualDownloadUrl;
  final messenger = ScaffoldMessenger.maybeOf(context);
  return showDialog<void>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('更新失败'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(reason),
            const SizedBox(height: 14),
            const Text(
              '可以先用浏览器打开下面这个地址，手动下载安装包（免登录）：',
              style: TextStyle(fontSize: 13),
            ),
            const SizedBox(height: 6),
            SelectableText(url, style: const TextStyle(fontSize: 13)),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () {
            Clipboard.setData(ClipboardData(text: url));
            Navigator.of(ctx).pop();
            messenger?.showSnackBar(
              const SnackBar(content: Text('下载地址已复制到剪贴板')),
            );
          },
          child: const Text('复制地址'),
        ),
        FilledButton(
          onPressed: () {
            Navigator.of(ctx).pop();
            _openInBrowser(url, messenger);
          },
          child: const Text('打开浏览器'),
        ),
      ],
    ),
  );
}

/// 用系统浏览器打开下载地址。
///
/// 有些 ROM 没有默认浏览器 / 链接被拦，`launchUrl` 会返回 false 或抛错——
/// 这时必须给提示，否则用户点了「打开浏览器」什么都不发生，就又是一次
/// 「点了没反应」的黑洞（关于页 `_openRepo` 踩过同样的坑）。
Future<void> _openInBrowser(String url, ScaffoldMessengerState? messenger) async {
  var ok = false;
  try {
    ok = await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
  } catch (_) {
    ok = false;
  }
  if (!ok) {
    messenger?.showSnackBar(
      const SnackBar(content: Text('打不开浏览器，请点「复制地址」后手动访问')),
    );
  }
}
