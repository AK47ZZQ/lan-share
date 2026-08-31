// 骨架期单元测试：Cookie 头解析（登录换 Token 的关键纯逻辑）。
// 传输协议层的单测从这里起步（APP-DESIGN §2：协议 100% Dart 可单测）。
import 'package:flutter_test/flutter_test.dart';

import 'package:lanshare_app/data/api/cookies.dart';

void main() {
  test('Set-Cookie 折算 Cookie 头：取首对名值并拼接', () {
    final header = cookieHeaderFrom([
      'ls_session=abc123; Path=/; HttpOnly; SameSite=Lax; Secure',
      'other=x; Path=/',
    ]);
    expect(header, 'ls_session=abc123; other=x');
  });

  test('无 Cookie / 空值返回空串', () {
    expect(cookieHeaderFrom(null), '');
    expect(cookieHeaderFrom([]), '');
  });

  test('畸形值（无 =）被丢弃', () {
    expect(cookieHeaderFrom(['garbage']), '');
  });
}
