// 跨入口信任流（2026-09-03 主/备隧道）：登录页之外也要建立信任——
// 「我的页 → 切换服务器」是最典型场景。流程：
//   1) 用 TrustStore 策略的 Dio 试连新地址 → 通了说明已信任，直接过
//   2) TLS 被拒 → pending 里是新入口出示的指纹：
//      · 与本机任一已信任指纹同源（同一服务器的主/备入口）→ 静默信任
//      · 陌生指纹 → 弹确认框（与登录页 TOFU 同文案），用户裁决
// 探测本身用 validateStatus 全放行：只关心 TLS 层结果，不关心 HTTP 状态码。
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import 'package:flutter/material.dart';

import 'trust.dart';

/// 确保地址已被信任；返回 true=可以安全使用该地址发起业务请求。
/// [onAutoTrusted] 在静默信任发生时回调（UI 层用来提示「已自动信任」）。
Future<bool> ensureAddressTrusted(BuildContext context, String url,
    {void Function()? onAutoTrusted}) async {
  final dio = Dio(BaseOptions(
      connectTimeout: const Duration(milliseconds: 6000),
      receiveTimeout: const Duration(milliseconds: 6000),
      validateStatus: (_) => true))
    ..httpClientAdapter =
        IOHttpClientAdapter(createHttpClient: TrustStore.createHttpClient);
  Uri uri;
  try {
    uri = Uri.parse(url.trim());
  } catch (_) {
    return false;
  }
  try {
    await dio.get('$url/api/app/entries');
    return true; // 证书已信任（或系统信任），握手直通
  } on DioException catch (e) {
    if (e.error is! HandshakeException) return false; // 网络不通等，非信任问题
    final fp = TrustStore.I.pendingFor(uri.host, uri.port);
    if (fp == null) return false;
    if (TrustStore.I.knowsFingerprint(fp)) {
      await TrustStore.I.trust(uri.host, uri.port, fp);
      onAutoTrusted?.call();
      return true; // 同源指纹已入库，业务请求可直接走（不必再探测一轮）
    }
    if (!context.mounted) return false;
    final ok = await showTrustConfirmDialog(context, fp);
    if (ok) {
      await TrustStore.I.trust(uri.host, uri.port, fp);
      return true;
    }
    return false;
  } catch (_) {
    return false;
  } finally {
    dio.close(force: true);
  }
}

/// TOFU 确认框（与登录页「信任此服务器」同语义，抽出来给切换入口复用）
Future<bool> showTrustConfirmDialog(BuildContext context, String fp) async {
  final ok = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (ctx) => AlertDialog(
      title: const Text('信任此服务器？'),
      content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment:
          CrossAxisAlignment.start, children: [
        Text('该服务器的证书不是由公共 CA 签发的（家庭自建属正常），'
            '且指纹与本机已信任的服务器不一致。',
            style: Theme.of(ctx).textTheme.bodySmall),
        const SizedBox(height: 10),
        const Text('证书指纹 (SHA256)：', style: TextStyle(fontSize: 12)),
        SelectableText(TrustStore.pretty(fp),
            style: const TextStyle(fontSize: 12, fontFamily: 'monospace')),
        const SizedBox(height: 10),
        const Text('请核对指纹与服务器管理员（你）记录的一致后再信任。',
            style: TextStyle(fontSize: 12)),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
        FilledButton(onPressed: () => Navigator.pop(ctx, true),
            child: const Text('信任此服务器')),
      ],
    ),
  );
  return ok == true;
}
