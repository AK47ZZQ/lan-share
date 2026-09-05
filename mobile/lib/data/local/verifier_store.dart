// 验证器条目持久化：SecureStore 只写 Keystore（R1-P2 收口）。
// 取舍变更：TOTP secret 与登录密码档同敏感级，且不同于可吊销的设备
// Token——secret 无法服务端轮换，落 SP 明文 XML 副本（root 设备可读）
// 的风险大于"Keystore 冷启动解密失败"（后果=重新扫码绑定，已有 catch
// 兜底视为未绑定）。与登录密码档同走 setStringKeystoreOnly。
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../totp.dart';
import 'secure_store.dart';

class VerifierStore {
  static const _key = 'totp_entries';
  static const _fbKey = 'fb_totp_entries'; // 旧版 SP 明文副本，迁移时清除

  /// 旧版双写的 SP 明文副本一次性清除。红队 P2：只在 load() 时清的话，
  /// 长期保持登录、不进验证器页的设备上明文残留无限期——App 启动即调用。
  static Future<void> purgeLegacySp() async {
    try {
      final sp = await SharedPreferences.getInstance();
      if (sp.containsKey(_fbKey)) await sp.remove(_fbKey);
    } catch (_) {}
  }

  static Future<List<TotpEntry>> load() async {
    await purgeLegacySp();
    try {
      final raw = await SecureStore.string(_key);
      if (raw == null || raw.isEmpty) return [];
      final list = (jsonDecode(raw) as List)
          .map((e) => TotpEntry.fromJson(e as Map<String, dynamic>))
          .toList();
      return list;
    } catch (_) {
      return []; // 数据损坏视为未绑定（可重新扫码）
    }
  }

  static Future<void> save(List<TotpEntry> entries) async {
    final raw = jsonEncode([for (final e in entries) e.toJson()]);
    final ok = await SecureStore.setStringKeystoreOnly(_key, raw);
    if (!ok) {
      // Keystore 不可用（罕见）：拒绝降级落明文副本，调用方按未绑定处理
      throw StateError('keystore_unavailable');
    }
    // 迁移清理：旧版双写留下的 SP 明文副本只删一次（失败无害）
    try {
      final sp = await SharedPreferences.getInstance();
      if (sp.containsKey(_fbKey)) await sp.remove(_fbKey);
    } catch (_) {}
  }

  /// 绑定（同 label 覆盖 = 同账号重复绑定不产生僵尸条目，与设备 Token 同思路）。
  static Future<void> upsert(TotpEntry entry) async {
    final list = await load();
    list.removeWhere((e) => e.label == entry.label);
    list.add(entry);
    await save(list);
  }

  static Future<void> remove(String label) async {
    final list = await load();
    list.removeWhere((e) => e.label == label);
    await save(list);
  }

  /// 找指定账号名的条目（登录页一键回填用）。
  static Future<TotpEntry?> findByLabel(String label) async {
    final list = await load();
    for (final e in list) {
      if (e.label == label) return e;
    }
    return null;
  }
}
