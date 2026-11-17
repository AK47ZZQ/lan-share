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
/// 信任探测的结果。
///
/// 为什么不是一个 bool：「能连」有两种完全不同的含义，调用方必须分清 ——
/// 同一台服务器的另一个入口可以继续用现有 Token，而别人的服务器必须清掉，
/// 否则设备凭据会跟着请求发给第三方。2026-11-17 review 发现 me_page 把
/// 两者当成同一件事，切个入口就把 Token 清掉（表现为「切过去被踢回登录页」）。
enum TrustOutcome {
  /// 连不上 / 用户拒绝信任 / 拿不到指纹
  failed,

  /// 指纹与已信任的一致 → 同一台服务器的另一个入口，Token 可保留
  sameServer,

  /// 系统信任的证书（握手直通，无法比对），或用户确认了一个新指纹 → 别人的服务器
  newServer,
}

Future<TrustOutcome> ensureAddressTrusted(
  BuildContext context,
  String url, {
  void Function()? onAutoTrusted,
}) async {
  final dio =
      Dio(
          BaseOptions(
            connectTimeout: const Duration(milliseconds: 6000),
            receiveTimeout: const Duration(milliseconds: 6000),
            validateStatus: (_) => true,
          ),
        )
        ..httpClientAdapter = IOHttpClientAdapter(
          createHttpClient: TrustStore.createHttpClient,
        );
  Uri uri;
  try {
    uri = Uri.parse(url.trim());
  } catch (_) {
    return TrustOutcome.failed;
  }
  try {
    await dio.get('$url/api/app/entries');
    return TrustOutcome.newServer; // 握手直通 = 系统信任的证书，无从比对同源
  } on DioException catch (e) {
    if (e.error is! HandshakeException) return TrustOutcome.failed; // 网络不通等
    final fp = TrustStore.I.pendingFor(uri.host, uri.port);
    if (fp == null) return TrustOutcome.failed;
    if (TrustStore.I.knowsFingerprint(fp)) {
      await TrustStore.I.trust(uri.host, uri.port, fp);
      onAutoTrusted?.call();
      return TrustOutcome.sameServer; // 同源指纹已入库 → 同一台服务器的另一入口
    }
    if (!context.mounted) return TrustOutcome.failed;
    final ok = await showTrustConfirmDialog(context, fp);
    if (ok) {
      await TrustStore.I.trust(uri.host, uri.port, fp);
      return TrustOutcome.newServer;
    }
    return TrustOutcome.failed;
  } catch (_) {
    return TrustOutcome.failed;
  } finally {
    dio.close(force: true);
  }
}

/// 连接预检（登录页服务器条）：任何 HTTP 状态码（含 401/404）都证明服务器
/// 在线，只有连接超时/失败才算不可达——无需服务端专门健康检查端点。
/// 自签证书放行（badCertificateCallback）：预检只回答「在不在线」，证书
/// 信不信任由正式登录的 TOFU 信任流（TrustStore）裁决。
/// 独立 Dio 实例：不带本机凭据、不影响 ApiClient 的服务器切换状态
/// （此时 ApiClient 尚未建立，属于登录前置探测，故落 data 层而非 UI）。
Future<bool> probeAddress(String addr) async {
  final dio =
      Dio(
          BaseOptions(
            receiveTimeout: const Duration(milliseconds: 2500),
            validateStatus: (_) => true,
          ),
        )
        ..httpClientAdapter = IOHttpClientAdapter(
          createHttpClient: () {
            final hc = HttpClient()
              ..connectionTimeout = const Duration(milliseconds: 2500)
              ..badCertificateCallback = ((cert, host, port) => true);
            return hc;
          },
        );
  try {
    await dio.get('$addr/api/auth/me');
    return true;
  } catch (_) {
    return false;
  } finally {
    dio.close(force: true); // 释放 HttpClient/Socket 池，频繁改地址不积压
  }
}

/// TOFU 确认框（与登录页「信任此服务器」同语义，抽出来给切换入口复用）
Future<bool> showTrustConfirmDialog(BuildContext context, String fp) async {
  final ok = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (ctx) => AlertDialog(
      title: const Text('信任此服务器？'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '该服务器的证书不是由公共 CA 签发的（家庭自建属正常），'
            '且指纹与本机已信任的服务器不一致。',
            style: Theme.of(ctx).textTheme.bodySmall,
          ),
          const SizedBox(height: 10),
          const Text('证书指纹 (SHA256)：', style: TextStyle(fontSize: 12)),
          SelectableText(
            TrustStore.pretty(fp),
            style: const TextStyle(fontSize: 12, fontFamily: 'monospace'),
          ),
          const SizedBox(height: 10),
          const Text(
            '请核对指纹与服务器管理员（你）记录的一致后再信任。',
            style: TextStyle(fontSize: 12),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(ctx, false),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(ctx, true),
          child: const Text('信任此服务器'),
        ),
      ],
    ),
  );
  return ok == true;
}
