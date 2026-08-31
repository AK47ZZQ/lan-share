/// Set-Cookie 头解析（纯 Dart，可单测）：登录响应的会话 Cookie 只在换
/// 设备 Token 那一步用（APP-DESIGN §3），这里取 "名=值" 对并拼接 Cookie 头。
library;

/// 把若干 Set-Cookie 值折算成请求用的 Cookie 头值（取每个 cookie 的首对名值）。
String cookieHeaderFrom(List<String>? setCookieValues) {
  if (setCookieValues == null) return '';
  return setCookieValues
      .map((c) => c.split(';').first.trim())
      .where((p) => p.contains('='))
      .join('; ');
}
