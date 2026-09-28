import 'package:flutter/foundation.dart';

/// 底栏页签全局可见性信号（v1.1.13，15 号挂账 P2 收编）。
///
/// 照片墙等挂 IndexedStack 常驻的页面用它做"页签闸"：非当前页签时轮询
/// 静默跳过（真实 IO/网络请求省掉），回到页签时监听方自行补查一次。
/// 写点唯一：HomeShell._go（setState 与本通知器同步）。
final ValueNotifier<int> homeTabIndex = ValueNotifier<int>(0);

/// 照片墙所在页签的序号（HomeShell dests 顺序：首页 0 / 照片 1 / 效率 / 笔记 / 我的）
const int kPhotosTabIndex = 1;
