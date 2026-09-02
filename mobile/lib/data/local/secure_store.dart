// 设备 Token 存储：优先 Keystore/Keychain 加密（flutter_secure_storage）；
// 部分安卓机（含模拟器）进程被杀后冷启动可能解密失败（PlatformException/
// AEADBadTag），此时降级读 SharedPreferences 副本。家用取舍：仅存可吊销的
// 设备 Token（服务端 90 天滑动过期，可随时吊销）。
// 双写双读：写入两路都写；读取 Keystore 优先、异常或为空时读副本。
// （互斥方案的教训：Keystore 写成功即清副本，冷启动读失败时无路可退→掉登录）
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

class SecureStore {
  static const _storage = FlutterSecureStorage(
      aOptions: AndroidOptions(encryptedSharedPreferences: true));
  static const _kToken = 'device_token';
  static const _kTokenFallback = 'device_token_fb';

  static Future<void> saveToken(String token) async {
    try {
      await _storage.write(key: _kToken, value: token);
    } catch (_) {}
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kTokenFallback, token);
  }

  static Future<String?> token() async {
    try {
      final v = await _storage.read(key: _kToken);
      if (v != null && v.isNotEmpty) return v;
    } catch (_) {}
    final sp = await SharedPreferences.getInstance();
    return sp.getString(_kTokenFallback);
  }

  // 通用键值（证书信任指纹等）。双写同 Token：冷启动 Keystore 解密失败时
  // 信任记录不丢（丢了会反复弹"信任此服务器"）。永不抛错。
  static Future<String?> string(String key) async {
    try {
      final v = await _storage.read(key: key);
      if (v != null && v.isNotEmpty) return v;
    } catch (_) {}
    try {
      final sp = await SharedPreferences.getInstance();
      return sp.getString('fb_$key');
    } catch (_) {
      return null;
    }
  }

  static Future<void> setString(String key, String value) async {
    try {
      await _storage.write(key: key, value: value);
    } catch (_) {}
    try {
      final sp = await SharedPreferences.getInstance();
      await sp.setString('fb_$key', value);
    } catch (_) {}
  }

  /// 只写 Keystore（EncryptedSharedPreferences），不落 SharedPreferences
  /// 明文副本。用于比"可吊销 Token"更敏感的数据（如登录密码存档）：
  /// SP 副本在 root 设备上是明文 XML，Token 可服务端吊销所以可容忍，
  /// 密码不行。读回时走 string()（Keystore 优先）。
  static Future<bool> setStringKeystoreOnly(String key, String value) async {
    try {
      await _storage.write(key: key, value: value);
      return true;
    } catch (_) {
      return false; // Keystore 不可用（罕见）：调用方决定降级策略
    }
  }

  /// 退出登录：只清 Token（两种存储都清），保留证书信任等
  static Future<void> clearToken() async {
    try {
      await _storage.delete(key: _kToken);
    } catch (_) {}
    final sp = await SharedPreferences.getInstance();
    await sp.remove(_kTokenFallback);
  }
}
