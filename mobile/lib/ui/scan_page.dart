// 扫码：Web 端「账号设置 → 手机 App 连接」的地址二维码（默认模式），
// 或「我的 → 验证器」绑定两步验证的 otpauth:// 二维码（otpauth 模式）。
// 识别后原样回传（pop 值），不匹配的内容忽略继续扫。
import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'theme/design_tokens.dart';

enum ScanMode { server, otpauth }

class ScanPage extends StatefulWidget {
  const ScanPage({super.key, this.mode = ScanMode.server});

  final ScanMode mode;

  @override
  State<ScanPage> createState() => _ScanPageState();
}

class _ScanPageState extends State<ScanPage> {
  final _controller = MobileScannerController(
      formats: const [BarcodeFormat.qrCode], autoStart: true);
  bool _done = false;

  bool _accept(String v) {
    if (widget.mode == ScanMode.otpauth) return v.startsWith('otpauth://totp/');
    // 只收 https（红队 P2-1）：http 二维码 = 明文上送账密，无任何告警
    return v.startsWith('https://');
  }

  String get _hint => widget.mode == ScanMode.otpauth
      ? '对准电脑端「账号设置 → 两步验证 → 启用」里的二维码，自动绑定本机验证器'
      : '对准电脑端「账号设置 → 手机 App 连接」里的二维码，识别后自动填入地址';

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _onDetect(BarcodeCapture capture) {
    if (_done) return;
    for (final bar in capture.barcodes) {
      final v = bar.rawValue;
      if (v != null && _accept(v.trim())) {
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
      appBar: AppBar(title: Text(widget.mode == ScanMode.otpauth
          ? '扫描两步验证二维码' : '扫描服务器二维码')),
      body: Stack(
        children: [
          MobileScanner(
            controller: _controller,
            onDetect: _onDetect,
            errorBuilder: (context, error, _) => Center(
              child: Padding(
                padding: const EdgeInsets.all(Gap.xxl),
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
                  color: Colors.black54, borderRadius: Radii.bControl),
              child: Text(_hint,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white, fontSize: 13),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
