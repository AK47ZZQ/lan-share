// 非敏感本地设置：服务器地址列表与上次所用地址（APP-DESIGN §6）。
// 敏感数据（设备 Token）一律走 SecureStore，不进这里。
import 'package:shared_preferences/shared_preferences.dart';

class Prefs {
  static const _kAddresses = 'server_addresses';
  static const _kLast = 'server_last';
  static const _kDeviceName = 'device_name';
  static const _kUsername = 'username';

  /// 当前账号（备份目标目录 incoming/<用户名>/手机相册 要在后台 isolate
  /// 里独立拼出，不依赖登录后的 Riverpod 会话状态）
  static Future<String?> username() async {
    final sp = await SharedPreferences.getInstance();
    return sp.getString(_kUsername);
  }

  static Future<void> setUsername(String v) async {
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kUsername, v);
  }

  /// 已保存的服务器地址（最近使用在前）。首用预填局域网地址。
  static Future<List<String>> addresses() async {
    final sp = await SharedPreferences.getInstance();
    final list = sp.getStringList(_kAddresses) ?? [];
    if (list.isEmpty) list.add('https://192.168.1.100:8765');
    return list;
  }

  static Future<String> lastAddress() async {
    final list = await addresses();
    final sp = await SharedPreferences.getInstance();
    return sp.getString(_kLast) ?? list.first;
  }

  static Future<void> rememberAddress(String url) async {
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kLast, url);
    final rest = (await addresses()).where((e) => e != url).toList();
    await sp.setStringList(_kAddresses, [url, ...rest.take(4)]);
  }

  /// 设备名：签发 Token 用（M-A1 换 device_info_plus 取真实机型）。
  static Future<String> deviceName() async {
    final sp = await SharedPreferences.getInstance();
    return sp.getString(_kDeviceName) ?? '我的手机';
  }

  static Future<void> setDeviceName(String name) async {
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kDeviceName, name);
  }
}
