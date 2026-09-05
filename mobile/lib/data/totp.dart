// RFC 6238 TOTP 引擎 + otpauth:// URI 解析（App 内置验证器，v0.5.2）。
// HMAC-SHA1 用 package:crypto（已是依赖），Base32 手写（RFC 4648 A-Z2-7），
// 不为此新增第三方包。测试向量见 test/totp_test.dart。
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

const String _b32Chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

/// RFC 4648 Base32 解码（忽略空格/连字符/填充=，大小写不敏感）。
/// 非法字符或空串抛 FormatException。
Uint8List base32Decode(String input) {
  final clean = input.replaceAll(RegExp(r'[\s\-–=]'), '').toUpperCase();
  if (clean.isEmpty) throw const FormatException('空 Base32 串');
  var bits = 0;
  var value = 0;
  final out = BytesBuilder();
  for (final ch in clean.codeUnits) {
    final idx = _b32Chars.indexOf(String.fromCharCode(ch));
    if (idx < 0) throw FormatException('非法 Base32 字符: ${String.fromCharCode(ch)}');
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      out.addByte((value >> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return out.toBytes();
}

/// 一条验证器条目（otpauth 绑定信息）。
class TotpEntry {
  final String secret; // Base32
  final String label; // 展示用账号名（如 rcui）
  final String issuer; // 展示用签发方（如 LAN Share）
  final int digits;
  final int period; // 秒

  const TotpEntry({
    required this.secret,
    this.label = '',
    this.issuer = '',
    this.digits = 6,
    this.period = 30,
  });

  /// 生成当前动态码（[epoch] 秒级时间戳，默认现在；测试可注入）。
  String code({int? epoch}) =>
      totpCode(base32Decode(secret),
          digits: digits, period: period, epoch: epoch ?? DateTime.now().millisecondsSinceEpoch ~/ 1000);

  Map<String, dynamic> toJson() => {
        'secret': secret, 'label': label, 'issuer': issuer,
        'digits': digits, 'period': period,
      };

  factory TotpEntry.fromJson(Map<String, dynamic> j) => TotpEntry(
        secret: j['secret'] as String,
        label: (j['label'] as String?) ?? '',
        issuer: (j['issuer'] as String?) ?? '',
        // 存量数据同样不信任：早期版本曾把恶意二维码的 digits=0/period=0
        // 原样入库，不 clamp 会让验证器页每秒除零崩溃且无自愈路径（R2-P1）
        digits: _clampDigits(j['digits'] as int?),
        period: _clampPeriod(j['period'] as int?),
      );
}

/// RFC 6238：HMAC-SHA1(secret, counter=epoch/period) → 动态截断 → [digits] 位。
String totpCode(Uint8List secret,
    {int digits = 6, int period = 30, required int epoch}) {
  var counter = epoch ~/ period;
  // 计数器转 8 字节大端
  final msg = Uint8List(8);
  for (var i = 7; i >= 0; i--) {
    msg[i] = counter & 0xff;
    counter >>= 8; // dart 整数 64 位，右移安全
  }
  final hmac = Hmac(sha1, secret);
  final digest = hmac.convert(msg).bytes;
  // 动态截断（RFC 4226 §5.4）
  final offset = digest.last & 0x0f;
  final bin = ((digest[offset] & 0x7f) << 24) |
      ((digest[offset + 1] & 0xff) << 16) |
      ((digest[offset + 2] & 0xff) << 8) |
      (digest[offset + 3] & 0xff);
  final str = (bin % _pow10(digits)).toString().padLeft(digits, '0');
  return str;
}

int _pow10(int n) {
  var r = 1;
  for (var i = 0; i < n; i++) {
    r *= 10;
  }
  return r;
}

/// 解析 otpauth://totp/… 绑定二维码内容。非 otpauth/totp 或缺 secret 返回 null。
/// digits/period 来自外部二维码，不 clamp 的话 period=0 会让 `% e.period` /
/// `epoch ~/ period` 整数除零崩溃、非法 digits 抛 RangeError（R1-P2）。
int _clampPeriod(int? v) => (v == null || v <= 0) ? 30 : (v > 3600 ? 3600 : v);
int _clampDigits(int? v) => (v == null || v < 6 || v > 10) ? 6 : v;

TotpEntry? parseOtpauthUri(String raw) {
  final trimmed = raw.trim();
  if (!trimmed.startsWith('otpauth://totp/')) return null;
  try {
    final uri = Uri.parse(trimmed);
    final secret = uri.queryParameters['secret'];
    if (secret == null || secret.isEmpty) return null;
    // 入库前必须可解码（R2-P1）：hex secret 或被截断/伪造的 QR 会产生非法
    // Base32 字符——脏数据入库后每次 code() 都抛 FormatException，验证器页
    // 与登录页整页红屏且无法自愈
    base32Decode(secret.toUpperCase());
    // 路径形如 "/LAN%20Share:rcui" 或 "/alice"（注意前导斜杠）
    final rawPath = uri.path.startsWith('/') ? uri.path.substring(1) : uri.path;
    final pathLabel = Uri.decodeComponent(rawPath);
    var issuer = uri.queryParameters['issuer'] ?? '';
    var label = pathLabel;
    if (pathLabel.contains(':')) {
      final i = pathLabel.indexOf(':');
      issuer = issuer.isEmpty ? pathLabel.substring(0, i) : issuer;
      label = pathLabel.substring(i + 1);
    }
    return TotpEntry(
      secret: secret.toUpperCase(),
      label: label,
      issuer: issuer,
      digits: _clampDigits(int.tryParse(uri.queryParameters['digits'] ?? '')),
      period: _clampPeriod(int.tryParse(uri.queryParameters['period'] ?? '')),
    );
  } catch (_) {
    return null;
  }
}
