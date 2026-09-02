// 生物识别一键登录的凭据存档（SecureStore：Keystore 加密 + SP 降级副本，
// 与设备 Token 同级保护）。家用取舍：设备 Token 本就是长效凭据，此处多存
// 一份密码仅为"登出/掉线后免重输"；用户主动退出登录时清除存档。
import 'dart:convert';

import 'secure_store.dart';

class SavedCredential {
  final String server;
  final String username;
  final String password;
  const SavedCredential(
      {required this.server, required this.username, required this.password});

  Map<String, dynamic> _toJson() =>
      {'server': server, 'username': username, 'password': password};

  static SavedCredential? _fromJson(Map<String, dynamic> m) {
    final s = m['server'], u = m['username'], p = m['password'];
    if (s is! String || u is! String || p is! String || s.isEmpty) return null;
    return SavedCredential(server: s, username: u, password: p);
  }
}

class SavedCredentialStore {
  static const _key = 'saved_credential';

  static Future<SavedCredential?> load() async {
    final raw = await SecureStore.string(_key);
    if (raw == null || raw.isEmpty) return null;
    try {
      return SavedCredential._fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } catch (_) {
      return null;
    }
  }

  /// 密码属敏感数据：只写 Keystore，不落 SharedPreferences 明文副本
  /// （Token 可服务端吊销才允许 SP 降级副本；密码写失败则视为未保存，
  /// 退出登录后靠重新输密码，不做明文兜底）。
  static Future<void> save(SavedCredential c) async {
    await SecureStore.setStringKeystoreOnly(_key, jsonEncode(c._toJson()));
  }

  /// 写空串即清除（SecureStore 读到空串按 null 处理）
  static Future<void> clear() async {
    try {
      // 走通用清法：两路都清（Keystore 删 key，SP 副本即使历史遗留也清掉）
      await SecureStore.setStringKeystoreOnly(_key, '');
      await SecureStore.setString(_key, '');
    } catch (_) {}
  }
}
