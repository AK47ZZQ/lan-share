// 自定义更新地址（3.96，对应服务端同名功能）。
//
// 为什么单独一页而不是塞进「关于」：这一块是**配置**，平时不看；关于页是
// 「看一眼就走」的信息页。混在一起的结果是每次看版本号都得划过一串输入框。
//
// 交互要点（和托盘面板保持一致）：
//   · 明确一个「+ 添加更新地址」按钮，点一下加一行并自动聚焦
//   · 每条右侧删除，左侧序号就是优先级
//   · 不填 = 直接走默认源（服务端 + GitHub），一次多余请求都不发
import 'package:flutter/material.dart';

import '../data/update_source.dart';
import 'theme/design_tokens.dart';

class UpdateSourcePage extends StatefulWidget {
  const UpdateSourcePage({super.key});

  @override
  State<UpdateSourcePage> createState() => _UpdateSourcePageState();
}

class _UpdateSourcePageState extends State<UpdateSourcePage> {
  final List<TextEditingController> _ctrls = [];
  final List<FocusNode> _focus = [];
  bool _loading = true;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final c in _ctrls) {
      c.dispose();
    }
    for (final f in _focus) {
      f.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    final urls = await UpdateSourceStore.load();
    if (!mounted) return;
    setState(() {
      for (final c in _ctrls) {
        c.dispose();
      }
      for (final f in _focus) {
        f.dispose();
      }
      _ctrls.clear();
      _focus.clear();
      for (final u in urls) {
        _addRow(value: u, focus: false);
      }
      _loading = false;
    });
  }

  /// 加一行。`focus: false` 用于从磁盘加载时不抢焦点。
  void _addRow({String value = '', bool focus = true}) {
    final c = TextEditingController(text: value);
    final f = FocusNode();
    _ctrls.add(c);
    _focus.add(f);
    if (focus) {
      // 等这一帧重建完再要焦点，否则新 TextField 还没挂到树上
      WidgetsBinding.instance.addPostFrameCallback((_) => f.requestFocus());
    }
  }

  void _removeRow(int i) {
    setState(() {
      _ctrls.removeAt(i).dispose();
      _focus.removeAt(i).dispose();
    });
  }

  Future<void> _save() async {
    final urls = _ctrls.map((c) => c.text.trim()).where((s) => s.isNotEmpty).toList();

    // 先本地校验，逐个指出是哪一条不对 —— 比「保存失败」有用得多
    for (var i = 0; i < urls.length; i++) {
      final err = UpdateSourceStore.validate(urls[i]);
      if (err != null) {
        _toast('第 ${i + 1} 条：$err');
        return;
      }
    }

    setState(() => _saving = true);
    try {
      await UpdateSourceStore.save(urls);
      if (!mounted) return;
      _toast(urls.isEmpty
          ? '已恢复默认：以后从服务端和 GitHub 检查更新'
          : '已保存 ${urls.length} 条，按上面的顺序尝试');
      Navigator.of(context).maybePop();
    } catch (e) {
      if (mounted) _toast('保存失败：$e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _toast(String s) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(s)));
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('更新地址')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(Gap.md),
              children: [
                Text(
                  '不填就用默认源（自己的服务端 + GitHub）。填了之后按顺序尝试，'
                  '第一条通了就不再试后面的 —— 所以把局域网地址写在最上面，'
                  '樱花隧道写第二条。',
                  style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant, height: 1.6),
                ),
                const SizedBox(height: Gap.md),

                if (_ctrls.isEmpty)
                  Container(
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      borderRadius: Radii.bControl,
                      border: Border.all(color: Theme.of(context).dividerColor, width: 0.5),
                    ),
                    child: Text('还没有自定义地址 —— 检查更新会问服务端和 GitHub',
                        style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                  ),

                for (var i = 0; i < _ctrls.length; i++)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.center,
                      children: [
                        // 序号不是装饰：它就是这个源的实际优先级
                        SizedBox(
                          width: 18,
                          child: Text('${i + 1}',
                              textAlign: TextAlign.right,
                              style: TextStyle(fontSize: 11, color: cs.outline)),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: TextField(
                            controller: _ctrls[i],
                            focusNode: _focus[i],
                            keyboardType: TextInputType.url,
                            autocorrect: false,
                            style: const TextStyle(fontFamily: 'monospace', fontSize: 12.5),
                            decoration: const InputDecoration(
                              isDense: true,
                              hintText: 'https://192.168.1.100:8765/updates',
                              border: OutlineInputBorder(),
                              contentPadding:
                                  EdgeInsets.symmetric(horizontal: 10, vertical: 12),
                            ),
                          ),
                        ),
                        IconButton(
                          tooltip: '删除这一条',
                          icon: const Icon(Icons.close, size: 18),
                          onPressed: () => _removeRow(i),
                        ),
                      ],
                    ),
                  ),

                const SizedBox(height: Gap.xs),
                OutlinedButton.icon(
                  onPressed: () => setState(() => _addRow()),
                  icon: const Icon(Icons.add, size: 18),
                  label: const Text('添加更新地址'),
                ),

                const SizedBox(height: Gap.lg),
                FilledButton(
                  onPressed: _saving ? null : _save,
                  child: Text(_saving ? '保存中…' : '保存'),
                ),
                const SizedBox(height: Gap.sm),
                TextButton(
                  onPressed: _saving
                      ? null
                      : () async {
                          // 恢复默认 = 清空配置
                          setState(() {
                            for (final c in _ctrls) {
                              c.dispose();
                            }
                            for (final f in _focus) {
                              f.dispose();
                            }
                            _ctrls.clear();
                            _focus.clear();
                          });
                          await _save();
                        },
                  child: const Text('恢复默认（只走服务端和 GitHub）'),
                ),
                const SizedBox(height: Gap.md),
                Text(
                  '只支持 https:// 开头。清单必须带 sha256，否则这个源会被忽略 —— '
                  '没有校验值就没法确认下载回来的是你发的那个包。',
                  style: TextStyle(fontSize: 11, color: cs.outline, height: 1.6),
                ),
              ],
            ),
    );
  }
}
