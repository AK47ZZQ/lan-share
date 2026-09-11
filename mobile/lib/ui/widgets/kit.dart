// UI 共享组件与帮助函数（v1.0 · 2026-09-11）
//
// 定位：收编散落在各页面的重复件。此前的实际情况是——`_toast` 在 11 个页面
// 各写一份，且已发生**微漂移**：admin_page 多传了 `width: 360`、feedback_page
// 少了 `mounted` 检查。这类漂移不会报错、不会被静态分析抓住，只会让"同一个
// 动作在两个页面表现不同"，属于最难发现的一致性损失。
//
// 依赖方向：本文件只依赖 Flutter 基础库与 design_tokens，**不 import 任何页面**，
// 避免与页面形成循环依赖。后续重复件（确认对话框、状态徽章、空数据占位等）
// 继续收编到这里，页面里不要再各写一份。
import 'package:flutter/material.dart';

import '../theme/responsive.dart';

/// 根 ScaffoldMessenger：挂在 MaterialApp 上，让"轻提示"不依赖具体页面的
/// BuildContext。
///
/// 为什么不用 `ScaffoldMessenger.of(context)`：
/// ① 调用点大多在 `await` 之后（上传完成、网络返回、删除响应），显式传
///    context 会触发 91 处 `use_build_context_synchronously` 警告——而把
///    `mounted` 检查搬进函数内部，静态分析并不跨函数认账，只能在各调用点
///    重复加检查，收编就失去意义；
/// ② 页面在 await 期间被 pop 掉时，原实现（`if (!mounted) return`）会**静默
///    丢弃提示**——用户点了删除、顺手返回列表，反而看不到"已移入回收站"。
///    挂在 App 级的 messenger 不受页面生命周期影响，提示照常送达。
final GlobalKey<ScaffoldMessengerState> rootMessengerKey =
    GlobalKey<ScaffoldMessengerState>();

/// 轻提示：2 秒自动消失的 SnackBar（全 App 统一时长与样式）。
///
/// 收编了原先 11 份各写一份的 `_toast`——它们已发生微漂移（admin_page 多传
/// `width: 360`、feedback_page 漏了 `mounted` 检查）。
///
/// 用法：`showToast('已移入回收站')`
void showToast(String msg) {
  final st = rootMessengerKey.currentState;
  if (st == null) return;
  // 宽屏（平板/横屏）限制最大宽度：floating 且不带 width 时提示会横跨整屏，
  // 原先 admin_page 自己带 `width: 360` 就是为这个场景——收编时若只做"统一"，
  // 会把这个平板优化一起抹掉。窄屏返回 null 即保持铺满的默认行为。
  final w = MediaQuery.maybeSizeOf(st.context)?.width ?? 0;
  st.showSnackBar(SnackBar(
    content: Text(msg),
    duration: const Duration(seconds: 2),
    width: w > Breakpoints.medium ? 480 : null,
  ));
}

// 说明：曾准备提供 showToastLong（4 秒停留），但核查后发现当前所有失败文案
// 都短于 20 字、2 秒足够读完——**没有真实调用点的抽象就是负担**，故不提供。
// 将来真出现"需要用户读完的长错误"时，按那时的需求设计（大概率要带 action
// 按钮而非单纯延长时长）。

/// 危险操作二次确认（统一文案形态与按钮顺序：取消在左、危险动作在右）。
///
/// 返回 true 表示用户确认。`danger` 为 true 时确认按钮用 error 色，
/// 让"删除/清空/退出"在视觉上与其他确认区分开。
Future<bool> showConfirmDialog(
  BuildContext context, {
  required String title,
  required String message,
  String confirmText = '确定',
  String cancelText = '取消',
  bool danger = false,
}) async {
  final ok = await showDialog<bool>(
    context: context,
    builder: (ctx) {
      final cs = Theme.of(ctx).colorScheme;
      return AlertDialog(
        title: Text(title),
        content: Text(message),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(cancelText)),
          FilledButton(
              style: danger
                  ? FilledButton.styleFrom(backgroundColor: cs.error)
                  : null,
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(confirmText)),
        ],
      );
    },
  );
  return ok == true;
}
