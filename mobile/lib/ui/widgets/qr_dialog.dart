// 分享二维码弹窗（0.6.0）：qr_flutter 本机渲染取件链接，面对面扫码取件。
// 链接地址由调用方决定（局域网/公网），弹窗内展示明文便于核对。
import 'package:flutter/material.dart';
import 'package:qr_flutter/qr_flutter.dart';

class QrDialog extends StatelessWidget {
  final String url;
  final String title;
  const QrDialog({super.key, required this.url, required this.title});

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
        title: Text(title, style: const TextStyle(fontSize: 16)),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                  color: Colors.white, borderRadius: BorderRadius.circular(8)),
              child: QrImageView(
                  data: url,
                  version: QrVersions.auto,
                  size: 220.0,
                  backgroundColor: Colors.white)),
          const SizedBox(height: 10),
          SelectableText(url,
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 12, color: Colors.grey)),
          const SizedBox(height: 4),
          const Text('手机浏览器扫码直接取件',
              style: TextStyle(fontSize: 11, color: Colors.grey)),
        ]),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context), child: const Text('关闭')),
        ]);
  }
}
