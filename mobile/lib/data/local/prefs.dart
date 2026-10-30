// 非敏感本地设置：服务器地址列表与上次所用地址（APP-DESIGN §6）。
// 敏感数据（设备 Token）一律走 SecureStore，不进这里。
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

class Prefs {
  static const _kAddresses = 'server_addresses';
  static const _kLast = 'server_last';
  static const _kDeviceName = 'device_name';
  static const _kUsername = 'username';
  static const _kLastLoginUser = 'last_login_user';

  /// 当前服务器地址的进程级单一事实源（0.12.0 对抗审查 P1）：
  /// 登录/会话内切换主备隧道时更新（rememberAddress / lastAddress 首读
  /// 播种）。挂在 IndexedStack 里不重建的页面（文件页/照片墙）监听它
  /// 同步 _base——否则切换入口后缩略图与预览永久打旧地址且无自愈。
  static final ValueNotifier<String> addressNotifier = ValueNotifier('');

  /// 上次在本机登录过的用户名（登录页预填用，非敏感）
  static Future<String?> lastLoginUser() async {
    final sp = await SharedPreferences.getInstance();
    return sp.getString(_kLastLoginUser);
  }

  static Future<void> setLastLoginUser(String v) async {
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kLastLoginUser, v);
  }

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

  /// 已保存的服务器地址（最近使用在前）。
  ///
  /// **首用不再预填任何地址。** 早期版本在这里塞了一个
  /// `https://192.168.1.100:8765` 当示例，问题是：装作"已经连上了"，
  /// 新手看不出这是占位符，直接点登录然后困惑于连不上；而 App 是公开分发的，
  /// 任何预设地址都会被当成"作者家的服务器"。
  /// 现在的约定是登录页地址框留空，由使用者自己填自己那台机器的地址。
  static Future<List<String>> addresses() async {
    final sp = await SharedPreferences.getInstance();
    return sp.getStringList(_kAddresses) ?? const [];
  }

  /// 上次使用的地址；从未用过时返回空串（不是 null，调用方按空串处理）。
  ///
  /// 空串只可能出现在「装了从没登录过」这一种情况——登录成功必然 rememberAddress。
  /// 已登录路径（api_client / transfer / backup / about）读到的都是有值的历史地址。
  static Future<String> lastAddress() async {
    final list = await addresses();
    final sp = await SharedPreferences.getInstance();
    final stored = sp.getString(_kLast);
    final a = (stored == null || stored.isEmpty)
        ? (list.isEmpty ? '' : list.first)
        : stored;
    if (a.isNotEmpty && addressNotifier.value != a) addressNotifier.value = a;
    return a;
  }

  static Future<void> rememberAddress(String url) async {
    // 去尾部斜杠归一：UI 拼接是 '$base/thumb/...' 字符串直拼，
    // 存入 'https://host:8766/' 会产出 //thumb/... 全 404（对抗审查 P3）
    final a = url.trim().replaceAll(RegExp(r'/+$'), '');
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kLast, a);
    final rest = (await addresses()).where((e) => e != a).toList();
    await sp.setStringList(_kAddresses, [a, ...rest.take(4)]);
    if (addressNotifier.value != a) addressNotifier.value = a;
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
