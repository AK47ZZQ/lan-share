// 已下载文件的"用其他应用打开"（文件页操作菜单与全屏查看器共用）。
// done 传输任务的本地副本可能被系统/用户清理而任务不会自动失效，
// 必须先做存在性校验；打开失败给中文 toast 而不是无声无息或直出
// 插件英文 message（review/对抗审查 0.12.0）。
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:open_filex/open_filex.dart';

Future<void> openLocalFile(BuildContext context, String path) async {
  if (!File(path).existsSync()) {
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('本地文件已被清理，请重新下载'),
        duration: Duration(seconds: 2)));
    return;
  }
  final r = await OpenFilex.open(path);
  if (r.type != ResultType.done && context.mounted) {
    // 插件的 message 是英文原文，映射成面向用户的中文文案
    final msg = switch (r.type) {
      ResultType.noAppToOpen => '手机上没有能打开这类文件的应用',
      ResultType.fileNotFound => '文件不存在',
      ResultType.permissionDenied => '没有打开权限',
      _ => '打开失败',
    };
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(msg), duration: const Duration(seconds: 2)));
  }
}
