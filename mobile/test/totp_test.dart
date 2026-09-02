// TOTP 引擎单测：RFC 6238 附录 B 官方测试向量（SHA1/8 位）+ Base32 +
// otpauth:// URI 解析。纯逻辑，不需要 Flutter 绑定。
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/totp.dart';

Uint8List _rfcSecret() {
  // RFC 6238 附录 B 的 seed "12345678901234567890"（ASCII）的 Base32 形式
  return base32Decode('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ');
}

void main() {
  group('RFC 6238 测试向量（SHA1，8 位）', () {
    final cases = <int, String>{
      59: '94287082',
      1111111109: '07081804',
      1111111111: '14050471',
      1234567890: '89005924',
      2000000000: '69279037',
      20000000000: '65353130',
    };
    cases.forEach((t, expected) {
      test('T=$t → $expected', () {
        expect(totpCode(_rfcSecret(), digits: 8, epoch: t), expected);
      });
    });
  });

  group('Base32 解码', () {
    test('RFC 4648 标准向量（foo）', () {
      expect(base32Decode('MZXW6==='), utf8.encode('foo'));
      expect(base32Decode('MZXW6YTBOI======'), utf8.encode('foobar'));
    });
    test('小写+空格+连字符+填充容错', () {
      expect(base32Decode('gezd gnbv-gy=='), utf8.encode('123456'));
    });
    test('非法字符抛异常', () {
      expect(() => base32Decode('abc1!'), throwsFormatException);
    });
  });

  group('otpauth:// URI 解析', () {
    test('pyotp 形态（issuer 前缀 + 参数）', () {
      final e = parseOtpauthUri(
          'otpauth://totp/LAN%20Share:rcui?secret=JBSWY3DPEHPK3PXP&issuer=LAN%20Share&algorithm=SHA1&digits=6&period=30');
      expect(e, isNotNull);
      expect(e!.label, 'rcui');
      expect(e.issuer, 'LAN Share');
      expect(e.secret, 'JBSWY3DPEHPK3PXP');
      expect(e.digits, 6);
      expect(e.period, 30);
    });
    test('纯账号名路径', () {
      final e = parseOtpauthUri('otpauth://totp/alice?secret=JBSWY3DPEHPK3PXP');
      expect(e!.label, 'alice');
      expect(e.issuer, '');
    });
    test('非 otpauth / 缺 secret → null', () {
      expect(parseOtpauthUri('https://example.com'), isNull);
      expect(parseOtpauthUri('otpauth://totp/x?issuer=y'), isNull);
      expect(parseOtpauthUri('垃圾内容'), isNull);
    });
  });

  test('绑定 JSON 往返', () {
    final e = TotpEntry(secret: 'JBSWY3DPEHPK3PXP', label: 'rcui', issuer: 'LAN Share');
    final r = TotpEntry.fromJson(e.toJson());
    expect(r.secret, e.secret);
    expect(r.label, e.label);
    expect(r.digits, e.digits);
  });
}
