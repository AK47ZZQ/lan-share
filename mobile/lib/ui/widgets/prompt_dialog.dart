// 单行文本输入对话框（photos_page 新建专辑 / files_page 新建文件夹、重命名共用）。
// 之前的写法是调用方 new TextEditingController() → await showDialog → 立即
// dispose：route.popped 在退场动画开始时就完成，TextField 仍挂在退场动画的
// 树上引用 controller，此时 dispose 会触发 framework 断言
// '_dependents.isEmpty' 崩溃红屏（v0.13 E2E 实测，点"下一步"必崩）。
// 改由对话框自身持有并在 dispose 释放，生命周期随子树安全回收。
import 'package:flutter/material.dart';

class PromptDialog extends StatefulWidget {
  final String title;
  final String label;
  final String hint;
  final String? initial;
  final bool obscure; // 密码类输入（管理面板重置密码等）
  final String okText; // 确认键文案（管理面板用'保存'等）
  final bool trimOnSubmit; // 提交时去首尾空格（密码类勿开）

  const PromptDialog({super.key, required this.title, required this.label,
      required this.hint, this.initial,
      this.obscure = false, this.okText = '确定', this.trimOnSubmit = false});

  @override
  State<PromptDialog> createState() => _PromptDialogState();
}

class _PromptDialogState extends State<PromptDialog> {
  late final TextEditingController _c =
      TextEditingController(text: widget.initial ?? '');

  void _submit() {
    final v = widget.trimOnSubmit ? _c.text.trim() : _c.text;
    Navigator.pop(context, v);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
        title: Text(widget.title),
        content: TextField(
            controller: _c,
            autofocus: true,
            obscureText: widget.obscure,
            onSubmitted: (_) => _submit(),
            decoration:
                InputDecoration(labelText: widget.label, hintText: widget.hint)),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('取消')),
          FilledButton(
              onPressed: _submit,
              child: Text(widget.okText)),
        ]);
  }
}
