// 扫码填入服务器地址：Web 端「账号设置 → 手机 App 连接」展示地址二维码，
// 此处识别后原样回传给登录页（只接受 http(s) 链接，其他内容忽略继续扫）。
import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

class ScanPage extends StatefulWidget {
  const ScanPage({super.key});

  @override
  State<ScanPage> createState() => _ScanPageState();
}

class _ScanPageState extends State<ScanPage> {
  final _controller = MobileScannerController(
      formats: const [BarcodeFormat.qrCode], autoStart: true);
  bool _done = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _onDetect(BarcodeCapture capture) {
    if (_done) return;
    for (final bar in capture.barcodes) {
      final v = bar.rawValue;
      if (v != null && (v.startsWith('https://') || v.startsWith('http://'))) {
        _done = true; // 连续帧会反复回调，只取第一枚有效码
        if (!mounted) return; // 页面可能已被返回手势关闭
        Navigator.of(context).pop(v.trim());
        return;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('扫描服务器二维码')),
      body: Stack(
        children: [
          MobileScanner(
            controller: _controller,
            onDetect: _onDetect,
            errorBuilder: (context, error, _) => Center(
              child: Padding(
                padding: const EdgeInsets.all(32),
                child: Text(
                  '相机不可用：请在系统设置中允许 LAN Share 使用相机\n\n${error.errorCode.name}',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodyMedium),
              ),
            ),
          ),
          Positioned(
            left: 24, right: 24, bottom: 48,
            child: Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                  color: Colors.black54, borderRadius: BorderRadius.circular(12)),
              child: const Text(
                '对准电脑端「账号设置 → 手机 App 连接」里的二维码，识别后自动填入地址',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.white, fontSize: 13),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
