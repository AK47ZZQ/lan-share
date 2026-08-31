// 设备 Token 存储：优先 Keystore/Keychain 加密（flutter_secure_storage）；
// 部分安卓机 EncryptedSharedPreferences 会抛 PlatformException，此时降级
// SharedPreferences（家用取舍：仅存可吊销的设备 Token）。
// 两路互斥：Keystore 写成功→清 fallback；写失败→清 Keystore 键再写 fallback。
// 避免分裂双写（读旧写新的错位）。
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
      final sp = await SharedPreferences.getInstance();
      await sp.remove(_kTokenFallback);        // Keystore 为准，清掉降级副本
    } catch (_) {
      await _storage.delete(key: _kToken);      // 两路互斥：先清失败的 Keystore
      final sp = await SharedPreferences.getInstance();
      await sp.setString(_kTokenFallback, token);
    }
  }

  static Future<String?> token() async {
    try {
      final v = await _storage.read(key: _kToken);
      if (v != null) return v;
    } catch (_) {}
    final sp = await SharedPreferences.getInstance();
    return sp.getString(_kTokenFallback);
  }

  // 通用键值（证书信任指纹等）
  static Future<String?> string(String key) => _storage.read(key: key);

  static Future<void> setString(String key, String value) =>
      _storage.write(key: key, value: value);

  /// 退出登录：只清 Token（两种存储都清），保留证书信任等
  static Future<void> clearToken() async {
    try {
      await _storage.delete(key: _kToken);
    } catch (_) {}
    final sp = await SharedPreferences.getInstance();
    await sp.remove(_kTokenFallback);
  }
}
