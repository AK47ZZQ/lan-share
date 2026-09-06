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
          // 0.9.0：中心云朵 logo（ECC 升 Q 保 25% 冗余）；与服务端
          // /api/qr 出的中心 logo 同一品牌语言
          Stack(alignment: Alignment.center, children: [
            Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(8)),
                child: QrImageView(
                    data: url,
                    version: QrVersions.auto,
                    size: 220.0,
                    errorCorrectionLevel: QrErrorCorrectLevel.Q,
                    backgroundColor: Colors.white)),
            Container(
                padding: const EdgeInsets.all(5),
                decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(10)),
                // v0.20：实心云朵，与服务端 /api/qr 更新后的品牌 logo 同源
                child: const Icon(Icons.cloud,
                    size: 30, color: Color(0xFF5B6CFF))),
          ]),
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
