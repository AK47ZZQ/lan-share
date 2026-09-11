// 手机上传二维码（v0.20 对齐 Web"📲 手机上传"）：App 端展示 /up 上传页
// 入口二维码（局域网 = 当前连接地址；公网 = 服务端配置的主/备隧道），
// 家人扫码即开网页上传页直传到自己的 incoming/。支持把二维码图（含中心
// 云朵 logo，与服务端 /api/qr 同款）一键保存进系统相册转发到群里。
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../data/api/errors.dart';
import '../data/api/home_api.dart';
import '../data/local/gallery_saver.dart';
import '../data/local/prefs.dart';

class _QrTarget {
  final String label;
  final String url;
  const _QrTarget(this.label, this.url);
}

/// 展示"手机上传"二维码底部弹层。入口：文件页右上菜单。
Future<void> showUploadQrSheet(BuildContext context) async {
  final targets = <_QrTarget>[];
  String? err;
  try {
    // 局域网：当前连接地址（无论身处哪个入口，扫码开的都是同一个 /up）
    final base = await Prefs.lastAddress();
    if (base.isNotEmpty) {
      final u = Uri.parse(base);
      final origin = '${u.scheme}://${u.authority}';
      targets.add(_QrTarget('局域网入口', '$origin/up'));
    }
    // 公网：管理端配置的主/备隧道（家庭外扫码用）
    final pub = await HomeApi.publicUrls();
    final main = pub.main;
    final alt = pub.alt;
    if (main.isNotEmpty) targets.add(_QrTarget('公网入口（主）', '$main/up'));
    if (alt.isNotEmpty && alt != main) {
      targets.add(_QrTarget('公网入口（备）', '$alt/up'));
    }
  } catch (e) {
    err = errDetail(e, max: 120);
  }
  if (!context.mounted) return;
  await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => SafeArea(
          child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
              child: Column(mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(children: [
                      const Icon(Icons.qr_code_2),
                      const SizedBox(width: 8),
                      const Expanded(
                          child: Text('手机扫码上传',
                              style: TextStyle(
                                  fontWeight: FontWeight.w700, fontSize: 15))),
                      IconButton(
                          icon: const Icon(Icons.close),
                          onPressed: () => Navigator.pop(ctx)),
                    ]),
                    Text(
                        '扫码打开网页上传页，选好的照片/文件直接传到上传者的'
                        ' incoming 目录（需先在该浏览器登录）。',
                        style: TextStyle(
                            fontSize: 12,
                            color: Theme.of(ctx).colorScheme.onSurfaceVariant)),
                    const SizedBox(height: 12),
                    if (err != null)
                      Text('读取地址失败：$err',
                          style: TextStyle(
                              fontSize: 12,
                              color: Theme.of(ctx).colorScheme.error)),
                    for (final t in targets)
                      Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: _QrCard(target: t)),
                  ]))));
}

class _QrCard extends StatelessWidget {
  final _QrTarget target;
  const _QrCard({required this.target});

  @override
  Widget build(BuildContext context) {
    return Card(
        elevation: 0,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
            side: BorderSide(
                color: Theme.of(context).dividerColor, width: 0.5)),
        child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(children: [
              Text(target.label,
                  style: const TextStyle(
                      fontWeight: FontWeight.w600, fontSize: 13)),
              const SizedBox(height: 8),
              Stack(alignment: Alignment.center, children: [
                Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(8)),
                    child: QrImageView(
                        data: target.url,
                        version: QrVersions.auto,
                        size: 200,
                        // ECC-Q：25% 冗余覆盖 28% 中心 logo（与服务端一致）
                        errorCorrectionLevel: QrErrorCorrectLevel.Q,
                        backgroundColor: Colors.white)),
                Container(
                    padding: const EdgeInsets.all(5),
                    decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(10)),
                    child: const Icon(Icons.cloud,
                        size: 28, color: Color(0xFF5B6CFF))),
              ]),
              const SizedBox(height: 6),
              SelectableText(target.url,
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 11, color: Colors.grey)),
              const SizedBox(height: 6),
              OutlinedButton.icon(
                  onPressed: () => _save(context),
                  icon: const Icon(Icons.save_alt, size: 18),
                  label: const Text('保存二维码图片到相册')),
            ])));
  }

  /// 快速保存（v0.20）：离屏画布渲染二维码 PNG（白底 + 中心云朵 logo，
  /// 与服务端 /api/qr 出图同款），落系统相册 LAN Share 目录
  Future<void> _save(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      const size = 900.0;
      final rec = ui.PictureRecorder();
      final canvas = Canvas(rec);
      canvas.drawColor(Colors.white, BlendMode.srcOver);
      final painter = QrPainter(
          data: target.url,
          version: QrVersions.auto,
          errorCorrectionLevel: QrErrorCorrectLevel.Q,
          gapless: true);
      painter.paint(canvas, const Size(size, size));
      // 中心 logo：白圆角块 + 实心云朵（MaterialIcons 字形直接画进位图）
      final box = Rect.fromCenter(
          center: Offset(size / 2, size / 2),
          width: size * 0.28,
          height: size * 0.28);
      canvas.drawRRect(
          RRect.fromRectAndRadius(box, Radius.circular(size * 0.055)),
          Paint()..color = Colors.white);
      final tp = TextPainter(
          text: TextSpan(
              text: String.fromCharCode(Icons.cloud.codePoint),
              style: const TextStyle(
                  fontFamily: 'MaterialIcons',
                  fontSize: 160,
                  color: Color(0xFF5B6CFF))),
          textDirection: TextDirection.ltr)
        ..layout();
      tp.paint(
          canvas,
          Offset(size / 2 - tp.width / 2, size / 2 - tp.height / 2));
      final img = await rec.endRecording().toImage(size.toInt(), size.toInt());
      final bd = await img.toByteData(format: ui.ImageByteFormat.png);
      if (bd == null) throw Exception('渲染二维码失败');
      final tmp = File(
          '${(await getTemporaryDirectory()).path}'
          '/lanshare-upload-qr-${DateTime.now().millisecondsSinceEpoch}.png');
      await tmp.writeAsBytes(bd.buffer.asUint8List(), flush: true);
      final saveErr = await GallerySaver.save(tmp.path, isVideo: false);
      // 临时文件无论如何都清掉（GallerySaver 已把内容拷进 MediaStore）
      try {
        await tmp.delete();
      } catch (_) {}
      if (saveErr != null) {
        messenger.showSnackBar(SnackBar(content: Text(saveErr)));
      } else {
        messenger.showSnackBar(const SnackBar(
            content: Text('已保存到相册「LAN Share」目录'),
            duration: Duration(seconds: 2)));
      }
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('保存失败：$e')));
    }
  }
}
